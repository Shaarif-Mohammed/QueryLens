"""
QueryLens — AI-Powered Marketplace Analytics Copilot
SQL Generator

Turns a plain-English business question into one read-only PostgreSQL query against
the warehouse, using Google Gemini with the metadata-grounded system prompt.

This module only generates SQL. It does not validate or execute it: every query must
pass the SQL validator before it reaches the database.
"""

from __future__ import annotations

import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Literal

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field

from app.llm.prompt_builder import PROJECT_ROOT, build_system_prompt, build_user_prompt

load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_MODEL = "gemini-3.5-flash"
# Tried in order, with the same provider and credentials, when the main model stays unavailable.
# GEMINI_FALLBACK_MODELS in .env overrides this (comma-separated; empty disables).
DEFAULT_FALLBACK_MODELS = "gemini-3.8-flash"

# Transient errors worth retrying: rate limited, internal error, overloaded, timeout.
RETRYABLE_STATUS_CODES = {429, 500, 503, 504}
# Model retired or unknown: skip straight to the next model.
SKIP_MODEL_STATUS_CODES = {404}
# Rate limited (429). What it means depends on the backend:
# - Gemini Developer API key: a per-minute or per-day quota is used up; a retry
#   seconds later cannot succeed and may use up more quota, so move to the next
#   model at once.
# - Vertex AI pay-as-you-go: the model's shared capacity was busy for a moment, and
#   Google recommends retrying after a short wait. The main model usually answers on a
#   retry, and the backup is slower and more likely to stall, so the main model is
#   retried with longer waits first (see call_with_fallback).
QUOTA_STATUS_CODES = {429}
# Request ran past its deadline (server 504 or client timeout). The same model is
# likely to stall again, so hand the question to the next model at once; only the
# last model is retried. Each stall already cost REQUEST_TIMEOUT_SECONDS.
STALL_CODES = {504, "timeout/network"}
ATTEMPTS_PER_MODEL = 3
BASE_DELAY_SECONDS = 2.0
MAX_DELAY_SECONDS = 20.0
# A single request that takes longer than this is treated as a failed attempt and
# retried or passed to the backup. A healthy SQL call usually finishes within 20 s, so a
# much longer wait is almost always a stuck request. GEMINI_REQUEST_TIMEOUT_SECONDS overrides it.
REQUEST_TIMEOUT_SECONDS = int(os.getenv("GEMINI_REQUEST_TIMEOUT_SECONDS", "25"))


class SqlResponse(BaseModel):
    """Structured output the model must return."""

    status: Literal["ok", "cannot_answer"]
    sql: str = Field(description="One read-only SELECT query, no trailing semicolon. Empty if cannot_answer.")
    sources: list[str] = Field(description="Warehouse relations the query reads, schema-qualified.")
    measure_used: str = Field(description="One sentence naming the measure and scope used.")
    assumptions: list[str] = Field(description="Interpretation choices, defaults applied and caveats.")
    cannot_answer_reason: str = Field(description="Empty unless status is cannot_answer.")
    closest_alternative: str = Field(description="Empty unless status is cannot_answer.")
    follow_up_questions: list[str] = Field(
        default_factory=list,
        description="Two short next questions this data can answer; empty if cannot_answer.",
    )


@dataclass
class GenerationResult:
    question: str
    response: SqlResponse | None
    model: str
    attempts: int
    latency_seconds: float
    prompt_tokens: int | None = None
    cached_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None  # billed at the output rate
    error: str | None = None
    raw_text: str | None = None
    retry_log: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None and self.response is not None


class SqlGenerator:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        fallback_models: list[str] | None = None,
    ):
        self.models = configured_models(model, fallback_models)
        self.model = self.models[0]
        self.client = _make_client(api_key)
        self.system_prompt = build_system_prompt()
        self.config = types.GenerateContentConfig(
            system_instruction=self.system_prompt,
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=SqlResponse,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=thinking_config("GEMINI_SQL_THINKING_BUDGET"),
        )

    def count_prompt_tokens(self) -> int:
        """Exact token count of the system prompt as Gemini sees it."""
        result = self.client.models.count_tokens(model=self.model, contents=self.system_prompt)
        return result.total_tokens

    def generate(self, question: str, previous: dict[str, str] | None = None) -> GenerationResult:
        """Try the main model with retries, then each fallback model in turn.
        `result.model` records the model that actually answered."""
        user_prompt = build_user_prompt(question, previous)
        started = time.monotonic()
        call = call_with_fallback(self.client, self.models, user_prompt, self.config)
        if call.response is None:
            return GenerationResult(
                question=question, response=None, model=call.model, attempts=call.attempts,
                latency_seconds=time.monotonic() - started, error=call.error, retry_log=call.retry_log,
            )
        return self._build_result(question, call.model, call.attempts, started, call.retry_log, call.response)

    @staticmethod
    def _build_result(question, model, attempts, started, retry_log, response) -> GenerationResult:
        usage = response.usage_metadata
        result = GenerationResult(
            question=question,
            response=None,
            model=model,
            attempts=attempts,
            latency_seconds=time.monotonic() - started,
            prompt_tokens=getattr(usage, "prompt_token_count", None),
            cached_tokens=getattr(usage, "cached_content_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
            thinking_tokens=getattr(usage, "thoughts_token_count", None),
            raw_text=response.text,
            retry_log=retry_log,
        )
        parsed = _parse(response)
        if parsed is None:
            result.error = "Model response was not valid JSON for the expected schema."
            return result
        parsed.sql = clean_sql(parsed.sql)
        parsed.follow_up_questions = (
            clean_follow_ups(parsed.follow_up_questions, question) if parsed.status == "ok" else []
        )
        result.response = parsed
        return result


MAX_FOLLOW_UPS = 2
MAX_FOLLOW_UP_CHARS = 120
# Wording that implies a statistical test the data has not had. The explanation step
# flags the same terms; a suggestion is a question shown as written, so it is dropped
# rather than reworded.
BANNED_FOLLOW_UP_TERMS = re.compile(r"\b(?:significant(?:ly)?|statistical(?:ly)?)\b", re.IGNORECASE)


def clean_follow_ups(questions: list[str] | None, asked: str) -> list[str]:
    """At most two distinct, short questions, none repeating the one just asked and
    none using wording that overstates the evidence ("significantly", "statistically")."""
    def key(text: str) -> str:
        return re.sub(r"\s+", " ", text.strip().lower()).rstrip(" ?.!")

    kept: list[str] = []
    seen = {key(asked)}
    for q in questions or []:
        q = re.sub(r"\s+", " ", str(q)).strip()
        if not q or len(q) > MAX_FOLLOW_UP_CHARS or key(q) in seen:
            continue
        if BANNED_FOLLOW_UP_TERMS.search(q):
            continue
        seen.add(key(q))
        kept.append(q if q.endswith("?") else q.rstrip(".!") + "?")
        if len(kept) == MAX_FOLLOW_UPS:
            break
    return kept


@dataclass
class ModelCall:
    """Outcome of one request tried across the configured models."""

    response: object | None  # the SDK response; None when every attempt failed
    model: str
    attempts: int
    retry_log: list[str]
    error: str | None = None


def call_with_fallback(client, models: list[str], contents: str, config,
                       retry_rate_limits: bool | None = None) -> ModelCall:
    """Send one request, retrying and moving to backup models as the error requires.
    Shared by every model call in QueryLens, so all calls fail and recover the same way.
    Errors that are neither API errors nor network errors are re-raised.

    `retry_rate_limits`: retry a 429 on the same model (Vertex AI, where it means busy
    for a moment) instead of moving on at once (API key, where it means a used-up
    quota). Defaults to whichever backend .env selects."""
    if retry_rate_limits is None:
        retry_rate_limits = using_vertex_ai()
    retry_log: list[str] = []
    total_attempts = 0
    last_error = "No model was tried."

    for model in models:
        for attempt in range(1, ATTEMPTS_PER_MODEL + 1):
            total_attempts += 1
            try:
                response = client.models.generate_content(model=model, contents=contents, config=config)
            except Exception as exc:  # noqa: BLE001 — classified below, anything else re-raised
                if isinstance(exc, errors.APIError):
                    code = getattr(exc, "code", None)
                    last_error = f"Gemini API error {code} on {model}: {exc}"
                elif _is_network_error(exc):
                    code = "timeout/network"
                    last_error = f"{type(exc).__name__} on {model} (no response within {REQUEST_TIMEOUT_SECONDS}s or connection failed)"
                else:
                    raise
                if code in SKIP_MODEL_STATUS_CODES:
                    retry_log.append(f"{model}: {code}, model not available, skipping")
                    break
                if code != "timeout/network" and code not in RETRYABLE_STATUS_CODES:
                    # Bad request, bad key, permission: another model will not help.
                    return ModelCall(None, model, total_attempts, retry_log, last_error)
                if code in QUOTA_STATUS_CODES and not retry_rate_limits:
                    retry_log.append(f"{model} attempt {attempt}: {code} quota/rate limit, moving to next model")
                    break
                if code in STALL_CODES and model != models[-1]:
                    retry_log.append(f"{model} attempt {attempt}: {code} (stalled), moving to next model")
                    break
                if attempt < ATTEMPTS_PER_MODEL:
                    # A busy (429) model gets longer waits (about 2-4 s, then 4-8 s):
                    # Google advises against retrying shared capacity immediately.
                    busy = code in QUOTA_STATUS_CODES
                    delay = _backoff_delay(attempt + 1 if busy else attempt)
                    reason = f"{code} busy" if busy else f"{code}"
                    retry_log.append(f"{model} attempt {attempt}: {reason}, retrying in {delay:.1f}s")
                    time.sleep(delay)
                    continue
                retry_log.append(f"{model} attempt {attempt}: {code}, moving to next model")
                break
            return ModelCall(response, model, total_attempts, retry_log)

    return ModelCall(
        None, models[-1], total_attempts, retry_log, f"All models unavailable. Last error: {last_error}"
    )


def configured_models(model: str | None = None, fallback_models: list[str] | None = None) -> list[str]:
    """Main model first, then each backup, from the arguments or .env."""
    main = model or os.getenv("GEMINI_MODEL") or DEFAULT_MODEL
    if fallback_models is None:
        raw = os.getenv("GEMINI_FALLBACK_MODELS", DEFAULT_FALLBACK_MODELS)
        fallback_models = [m.strip() for m in raw.split(",") if m.strip()]
    return [main] + [m for m in fallback_models if m != main]


def thinking_config(env_name: str) -> types.ThinkingConfig | None:
    """Optional thinking budget (tokens) from .env; unset keeps the model's default.
    A model that does not accept the value answers 400, which stops the request."""
    raw = os.getenv(env_name, "").strip()
    return types.ThinkingConfig(thinking_budget=int(raw)) if raw else None


def using_vertex_ai() -> bool:
    """True when .env selects Vertex AI (GOOGLE_GENAI_USE_VERTEXAI=true)."""
    return os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() in {"1", "true", "yes"}


def _make_client(api_key: str | None = None) -> genai.Client:
    """Gemini client for one of two backends, chosen in .env:
    - Vertex AI (GOOGLE_GENAI_USE_VERTEXAI=true): billed to the Google Cloud project,
      authenticated with a service account (GOOGLE_APPLICATION_CREDENTIALS).
    - Gemini Developer API (default): authenticated with GEMINI_API_KEY.
    Both serve the same Gemini models through the same SDK, so nothing else changes."""
    http_options = types.HttpOptions(timeout=REQUEST_TIMEOUT_SECONDS * 1000)  # milliseconds
    if using_vertex_ai():
        project = os.getenv("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT is not set (check .env).")
        return genai.Client(
            vertexai=True,
            project=project,
            location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
            http_options=http_options,
        )
    api_key = api_key or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set (check .env).")
    return genai.Client(api_key=api_key, http_options=http_options)


def _is_network_error(exc: Exception) -> bool:
    """Timeouts and dropped connections from the SDK's HTTP layer. Matched by class
    name so it works whichever HTTP library version the SDK installs."""
    names = {k.__name__ for k in type(exc).__mro__}
    return bool(names & {"TimeoutException", "TransportError", "TimeoutError", "ConnectionError"})


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff with equal jitter: half the ceiling plus a random half,
    so a wait is never close to zero. Ceiling = min(cap, base * 2^(n-1))."""
    ceiling = min(MAX_DELAY_SECONDS, BASE_DELAY_SECONDS * 2 ** (attempt - 1))
    return ceiling / 2 + random.uniform(0, ceiling / 2)


def _parse(response) -> SqlResponse | None:
    return _parse_model(response, SqlResponse)


def _parse_model(response, schema: type[BaseModel]):
    """The structured answer as `schema`, from the SDK's parsed value or the raw JSON text."""
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, schema):
        return parsed
    text = response.text or ""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return schema.model_validate_json(text)
    except Exception:
        return None


def clean_sql(sql: str) -> str:
    """Remove markdown fences and trailing semicolons. Safety checks belong to the validator."""
    sql = (sql or "").strip()
    sql = re.sub(r"^```(?:sql)?\s*", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s*```$", "", sql)
    return sql.strip().rstrip(";").strip()

"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Question pipeline

Takes one plain-English question through every step that produces data:

    question -> SQL generator -> SQL validator -> query executor -> DataFrame -> profile

and stops at the first step that cannot continue. Nothing reaches the database unless
the model produced a query (not a refusal) and that query passed the validator. The
result keeps every intermediate output, so the interface can show the SQL, the
assumptions and any warnings alongside the data, and explain exactly where a question
stopped.

The written explanation is a separate step (explain_answer), so the interface can show
the SQL, table and chart as soon as the data arrives and add the explanation after.
A failed explanation never hides the data: the table is still the answer.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Literal

import pandas as pd

from app.analytics.analyzer import ResultProfile, profile_result
from app.database.query_executor import ExecutionResult, execute_validated
from app.validation.sql_validator import ValidationResult, validate_sql

if TYPE_CHECKING:
    from app.llm.response_generator import ExplanationGenerator, ExplanationResult
    from app.llm.sql_generator import GenerationResult, SqlGenerator

logger = logging.getLogger(__name__)

# Longer input is refused before any model call: real questions are short, and a
# pasted document would only add cost.
MAX_QUESTION_CHARS = 1000

Status = Literal[
    "answered",           # data returned (possibly zero rows)
    "invalid_question",   # empty or too long; nothing was called
    "generation_failed",  # the model could not be reached or returned unusable output
    "cannot_answer",      # the model declined: data not available, off-topic or unsafe
    "rejected",           # the generated SQL failed validation and was not run
    "execution_failed",   # the database timed out, was unreachable or refused the query
]

# Interface wording: always offer a next step, never blame the user, stay honest.
# Each stop has a short title (no closing punctuation) and a plain next-step line; the
# interface shows them as a message box, and `message` joins them for logs and scripts.
NEXT_STEP = "You can keep exploring the quick insights below."
MESSAGE_PARTS: dict[str, tuple[str, str]] = {
    "empty_question": ("Type a question to get started", "Or pick one of the quick insights below."),
    "long_question": (
        "That question is a bit long",
        f"Questions under {MAX_QUESTION_CHARS:,} characters work best; try trimming it to the core of what you want to know.",
    ),
    "generation_failed": ("QueryLens couldn't reach its language model just now", f"Please try again in a moment. {NEXT_STEP}"),
    "rejected": (
        "This query was held back",
        "QueryLens wrote a query for this, but it didn't pass the safety checks, so it wasn't run. "
        "Rephrasing the question usually helps.",
    ),
    "not_validated": (
        "This query was held back",
        "QueryLens wrote a query for this, but it didn't pass the safety checks, so it wasn't run. "
        "Rephrasing the question usually helps.",
    ),
    "timeout": (
        "This one took too long to run",
        "QueryLens stopped it. Narrowing it down usually helps, for example to fewer months or a single category.",
    ),
    "connection": ("QueryLens couldn't reach the warehouse just now", f"Please try again in a moment. {NEXT_STEP}"),
    "database": ("The warehouse couldn't run this query", "Rephrasing the question often helps."),
    "cannot_answer": ("This one isn't in the dataset", "This question is outside what the marketplace data covers."),
}
MESSAGES = {key: f"{title}. {body}" for key, (title, body) in MESSAGE_PARTS.items()}
# Inline notes shown with an answer rather than in place of one.
MESSAGES["explanation_failed"] = "The written summary isn't available this time, but the table and chart show the full answer."
MESSAGES["explanation_flagged"] = (
    "A few figures in this summary couldn't be matched exactly to the result, so rely on the table for exact numbers."
)


@dataclass
class PipelineResult:
    question: str
    status: Status
    message: str = ""  # one plain-English line for the interface; empty when answered
    message_key: str = ""  # which MESSAGE_PARTS entry `message` came from (empty for a model's refusal reason)
    generation: GenerationResult | None = None
    validation: ValidationResult | None = None
    execution: ExecutionResult | None = None
    profile: ResultProfile | None = None  # set when answered
    explanation: ExplanationResult | None = None  # set by explain_answer
    timings: dict[str, float] = field(default_factory=dict)  # seconds per step

    @property
    def ok(self) -> bool:
        return self.status == "answered"

    @property
    def dataframe(self) -> pd.DataFrame | None:
        return self.execution.dataframe if self.execution else None

    @property
    def sql(self) -> str:
        """The SQL that ran, or else the last SQL produced (for display when a step failed)."""
        if self.execution and self.execution.sql:
            return self.execution.sql
        if self.validation and self.validation.sql:
            return self.validation.sql
        response = self.generation.response if self.generation else None
        return response.sql if response else ""

    def follow_up_context(self) -> dict[str, str] | None:
        """Context for the next question: this question and the SQL that answered it.
        Only an answered question can be followed up."""
        if not self.ok:
            return None
        return {"question": self.question, "sql": self.execution.sql}


_default_generator: SqlGenerator | None = None
_default_explainer: ExplanationGenerator | None = None


def get_generator() -> SqlGenerator:
    """One shared generator: building it loads the metadata and creates the API client."""
    global _default_generator
    if _default_generator is None:
        from app.llm.sql_generator import SqlGenerator  # imported here so tests need no API client

        _default_generator = SqlGenerator()
    return _default_generator


def get_explainer() -> ExplanationGenerator:
    """One shared explanation generator, reusing the SQL generator's API client."""
    global _default_explainer
    if _default_explainer is None:
        from app.llm.response_generator import ExplanationGenerator

        _default_explainer = ExplanationGenerator(client=get_generator().client)
    return _default_explainer


def answer_question(
    question: str,
    previous: dict[str, str] | None = None,
    *,
    generator=None,
    execute: Callable[[ValidationResult], ExecutionResult] = execute_validated,
    on_step: Callable[[str], None] | None = None,
) -> PipelineResult:
    """Run one question through generation, validation and execution.

    `previous` is the follow_up_context() of the last answered question, if any.
    `generator` and `execute` are replaceable for tests. `on_step` is called with
    "generation", "validation" and "execution" as each step starts, so the interface
    can show real progress; a failing callback never stops the question."""
    question = (question or "").strip()
    result = PipelineResult(question=question, status="answered")

    if not question:
        return _stop(result, "invalid_question", MESSAGES["empty_question"], key="empty_question")
    if len(question) > MAX_QUESTION_CHARS:
        return _stop(result, "invalid_question", MESSAGES["long_question"], key="long_question")

    # 1. Generate SQL.
    _notify(on_step, "generation")
    started = time.monotonic()
    try:
        generation = (generator or get_generator()).generate(question, previous)
    except Exception as exc:  # noqa: BLE001 — any failure here must stop cleanly
        logger.exception("SQL generation raised")
        result.timings["generation"] = time.monotonic() - started
        return _stop(result, "generation_failed", MESSAGES["generation_failed"], detail=str(exc),
                     key="generation_failed")
    result.generation = generation
    result.timings["generation"] = time.monotonic() - started

    if not generation.ok:
        return _stop(result, "generation_failed", MESSAGES["generation_failed"], detail=generation.error,
                     key="generation_failed")

    response = generation.response
    if response.status == "cannot_answer":
        # A refusal never reaches the validator, even if the model also wrote SQL.
        reason = response.cannot_answer_reason or MESSAGE_PARTS["cannot_answer"][1]
        return _stop(result, "cannot_answer", reason, key="cannot_answer")

    # 2. Validate.
    _notify(on_step, "validation")
    started = time.monotonic()
    validation = validate_sql(response.sql)
    result.validation = validation
    result.timings["validation"] = time.monotonic() - started
    if not validation.ok:
        return _stop(result, "rejected", MESSAGES["rejected"], detail="; ".join(validation.errors), key="rejected")

    # 3. Execute. Only the validated SQL is passed on, never the generated text.
    _notify(on_step, "execution")
    execution = execute(validation)
    result.execution = execution
    result.timings["execution"] = execution.elapsed_seconds
    if not execution.ok:
        key = execution.error_kind if execution.error_kind in MESSAGE_PARTS else "database"
        return _stop(result, "execution_failed", MESSAGES[key], detail=execution.error, key=key)

    result.profile = profile_result(
        execution.dataframe, execution.sql, response.measure_used, execution.truncated
    )
    logger.info(
        "answered in %.1fs (%s rows): %s",
        sum(result.timings.values()), execution.row_count, question[:80],
    )
    return result


def explain_answer(result: PipelineResult, *, explainer=None) -> PipelineResult:
    """Add the written explanation to an answered question. Does nothing otherwise.
    A failure is recorded in result.explanation and never changes result.status."""
    if not result.ok or result.profile is None:
        return result
    response = result.generation.response
    started = time.monotonic()
    try:
        result.explanation = (explainer or get_explainer()).explain(
            result.question, response.measure_used, list(response.assumptions),
            result.dataframe, result.profile,
        )
    except Exception as exc:  # noqa: BLE001 — the data is still the answer
        logger.exception("Explanation raised")
        from app.llm.response_generator import ExplanationResult

        result.explanation = ExplanationResult(
            response=None, model="", attempts=0, latency_seconds=time.monotonic() - started, error=str(exc)
        )
    result.timings["explanation"] = time.monotonic() - started
    if not result.explanation.ok:
        logger.warning("explanation failed: %s | question: %s", result.explanation.error, result.question[:80])
    elif result.explanation.ungrounded_numbers:
        logger.warning(
            "explanation has numbers not found in the result: %s | question: %s",
            result.explanation.ungrounded_numbers, result.question[:80],
        )
    return result


def _notify(on_step: Callable[[str], None] | None, step: str) -> None:
    if on_step is None:
        return
    try:
        on_step(step)
    except Exception:  # noqa: BLE001 — a display problem must never stop the question
        logger.exception("on_step callback raised for step %s", step)


def _stop(result: PipelineResult, status: Status, message: str, detail: str | None = None,
          key: str = "") -> PipelineResult:
    result.status = status
    result.message = message
    result.message_key = key
    if detail:
        logger.warning("%s: %s | question: %s", status, detail, result.question[:80])
    return result

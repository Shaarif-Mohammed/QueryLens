"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Answers, quick insights and saved answers

One `Answer` shape for everything the interface shows, whether it was produced live
by the pipeline a moment ago or loaded from the saved answers file:
- the question and how far it got (status, message),
- the SQL that ran, the rows, the measure and notes the SQL step wrote,
- the written explanation and the result of its checks.

The quick insights on the landing screen are answered ahead of time by running
them through the same pipeline (scripts/build_cached_answers.py) and saved to
metadata/cached_answers.json. A saved answer stores the data, not the chart: the
profile and chart are rebuilt from the rows when it is shown, so chart changes apply
to saved answers without running the questions again.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.analytics.analyzer import ResultProfile, profile_result

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = PROJECT_ROOT / "metadata" / "cached_answers.json"
CACHE_VERSION = 1


# ------------------------------------------------------------------ quick insights

@dataclass(frozen=True)
class Example:
    """A quick insight: a key business question answered ahead of time."""
    question: str
    label: str  # short name on its pill; the full question shows on hover
    follow_up: str | None = None  # prepared follow-up, offered after this question
    pill: bool = True  # shown as a pill; False = prepared but only reached by typing it
    default: bool = False  # one of the pills shown before "More"


EXAMPLES: list[Example] = [
    Example("Which product categories bring in the most revenue?", "Top categories by revenue", default=True),
    Example("How has monthly revenue changed since January 2017?", "Monthly revenue trend", default=True),
    Example("Which states have the most revenue?", "Revenue by state", follow_up="How is delivery there?"),
    Example("For sellers with at least 50 delivered orders, how does revenue relate to on-time delivery?",
            "Seller revenue vs on-time delivery", default=True),
    Example("How many customers are in each RFM segment?", "Customer segments"),
    Example("How does revenue for the top 10 categories vary by month in 2017?", "Category seasonality"),
    Example("What share of our customers buy more than once?", "Repeat buyers"),
    Example("Do late deliveries get worse reviews than on-time ones?", "Late delivery and reviews"),
    Example("Do customers who pay in instalments spend more per order?", "Instalments and order value"),
    Example("Which products get returned most often?", "Product returns", pill=False),
]


def pill_examples(default: bool | None = None) -> list[Example]:
    """Quick insights shown as pills; `default=True` for the first few, False for the rest."""
    return [e for e in EXAMPLES if e.pill and (default is None or e.default == default)]


def suggested_follow_up(question: str) -> str | None:
    """The follow-up offered after a quick insight, if it has one."""
    key = normalise(question)
    return next((e.follow_up for e in EXAMPLES if normalise(e.question) == key), None)


def suggestions_for(answer: "Answer") -> list[str]:
    """Up to two next questions: the SQL step's suggestions, else the prepared
    follow-up of a quick insight, else a refusal's closest alternative."""
    found = list(answer.follow_ups)
    if not answer.follow_up_of:
        prepared = suggested_follow_up(answer.question)
        if prepared and answer.ok:
            found.insert(0, prepared)
    if answer.closest_alternative:
        found.append(answer.closest_alternative)
    unique = []
    for q in found:
        if q and normalise(q) not in {normalise(u) for u in unique} and normalise(q) != normalise(answer.question):
            unique.append(q)
    return unique[:2]


def normalise(question: str | None) -> str:
    """Case, spacing and closing punctuation do not change the question."""
    return re.sub(r"\s+", " ", (question or "").strip().lower()).rstrip(" ?.!")


# ------------------------------------------------------------------------ answer shape

@dataclass
class ExplanationView:
    ok: bool
    headline: str = ""
    text: str = ""
    caveats: list[str] = field(default_factory=list)
    ungrounded_numbers: list[str] = field(default_factory=list)
    banned_terms: list[str] = field(default_factory=list)
    regenerated: bool = False
    error: str | None = None

    @property
    def flagged(self) -> bool:
        """True when the checks still found a problem after the one rewrite."""
        return bool(self.ungrounded_numbers or self.banned_terms)


@dataclass
class Answer:
    question: str
    status: str
    message: str = ""
    message_key: str = ""  # MESSAGE_PARTS entry for a stopped question
    sql: str = ""
    dataframe: pd.DataFrame | None = None
    truncated: bool = False
    measure_used: str = ""
    assumptions: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # validator warnings
    closest_alternative: str = ""
    follow_ups: list[str] = field(default_factory=list)  # suggested next questions
    explanation: ExplanationView | None = None
    timings: dict[str, float] = field(default_factory=dict)
    model: str = ""
    follow_up_of: str | None = None  # the question this one followed up, if any
    from_cache: bool = False
    saved_at: str | None = None
    profile: ResultProfile | None = None  # rebuilt from the rows, never saved

    @property
    def ok(self) -> bool:
        return self.status == "answered"

    def follow_up_context(self) -> dict[str, str] | None:
        """Same contract as PipelineResult.follow_up_context(): only an answered
        question can be followed up, and the context is its question and SQL."""
        if not self.ok:
            return None
        return {"question": self.question, "sql": self.sql}


def from_pipeline(result, previous: dict[str, str] | None = None) -> Answer:
    """An Answer from a PipelineResult (explanation added later, if at all)."""
    response = result.generation.response if result.generation else None
    execution = result.execution
    answer = Answer(
        question=result.question,
        status=result.status,
        message=result.message,
        message_key=getattr(result, "message_key", ""),
        sql=result.sql,
        dataframe=result.dataframe if result.ok else None,
        truncated=bool(execution and execution.truncated),
        measure_used=response.measure_used if response else "",
        assumptions=list(response.assumptions) if response else [],
        sources=list(response.sources) if response else [],
        warnings=list(result.validation.warnings) if result.validation else [],
        closest_alternative=(response.closest_alternative or "") if response else "",
        follow_ups=list(getattr(response, "follow_up_questions", None) or []) if response else [],
        timings=dict(result.timings),
        model=result.generation.model if result.generation else "",
        follow_up_of=previous["question"] if previous else None,
        profile=result.profile,
    )
    if result.explanation is not None:
        answer.explanation = explanation_view(result.explanation)
    return answer


def explanation_view(explanation) -> ExplanationView:
    """An ExplanationView from an ExplanationResult."""
    if explanation is None or not explanation.ok:
        return ExplanationView(ok=False, error=getattr(explanation, "error", None) or "No explanation.")
    r = explanation.response
    return ExplanationView(
        ok=True,
        headline=r.headline,
        text=r.explanation,
        caveats=list(r.caveats),
        ungrounded_numbers=list(explanation.ungrounded_numbers),
        banned_terms=list(explanation.banned_terms),
        regenerated=explanation.regenerated,
    )


# ------------------------------------------------------------------------ JSON format

def answer_to_dict(answer: Answer) -> dict:
    data = {
        "question": answer.question,
        "status": answer.status,
        "message": answer.message,
        "message_key": answer.message_key,
        "sql": answer.sql,
        "truncated": answer.truncated,
        "measure_used": answer.measure_used,
        "assumptions": answer.assumptions,
        "sources": answer.sources,
        "warnings": answer.warnings,
        "closest_alternative": answer.closest_alternative,
        "follow_ups": answer.follow_ups,
        "timings": answer.timings,
        "model": answer.model,
        "follow_up_of": answer.follow_up_of,
        "saved_at": answer.saved_at or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "rows": _frame_to_dict(answer.dataframe),
        "explanation": None,
    }
    if answer.explanation is not None:
        e = answer.explanation
        data["explanation"] = {
            "ok": e.ok, "headline": e.headline, "text": e.text, "caveats": e.caveats,
            "ungrounded_numbers": e.ungrounded_numbers, "banned_terms": e.banned_terms,
            "regenerated": e.regenerated, "error": e.error,
        }
    return data


def answer_from_dict(data: dict) -> Answer:
    explanation = data.get("explanation")
    answer = Answer(
        question=data["question"],
        status=data["status"],
        message=data.get("message", ""),
        message_key=data.get("message_key", ""),
        sql=data.get("sql", ""),
        dataframe=_frame_from_dict(data.get("rows")),
        truncated=bool(data.get("truncated")),
        measure_used=data.get("measure_used", ""),
        assumptions=list(data.get("assumptions", [])),
        sources=list(data.get("sources", [])),
        warnings=list(data.get("warnings", [])),
        closest_alternative=data.get("closest_alternative", ""),
        follow_ups=list(data.get("follow_ups", [])),
        explanation=ExplanationView(**explanation) if explanation else None,
        timings=dict(data.get("timings", {})),
        model=data.get("model", ""),
        follow_up_of=data.get("follow_up_of"),
        from_cache=True,
        saved_at=data.get("saved_at"),
    )
    if answer.ok and answer.dataframe is not None:
        # Same call the pipeline makes, so a saved answer is profiled exactly like a live one.
        answer.profile = profile_result(answer.dataframe, answer.sql, answer.measure_used, answer.truncated)
    return answer


def _frame_to_dict(df: pd.DataFrame | None) -> dict | None:
    """Columns, a kind per column and the rows. Kinds keep text that looks like a
    number ('1', '12+') as text and dates as dates when the rows are read back."""
    if df is None:
        return None
    kinds = {name: _kind(df[name]) for name in df.columns}
    rows = []
    for values in df.itertuples(index=False, name=None):
        row = []
        for name, value in zip(df.columns, values):
            if pd.isna(value):
                row.append(None)
            elif kinds[name] == "date":
                row.append(pd.Timestamp(value).isoformat())
            elif kinds[name] == "int":
                row.append(int(value))
            elif kinds[name] == "float":
                row.append(float(value))
            elif kinds[name] == "bool":
                row.append(bool(value))
            else:
                row.append(str(value))
        rows.append(row)
    return {"columns": list(df.columns), "kinds": [kinds[n] for n in df.columns], "rows": rows}


def _frame_from_dict(data: dict | None) -> pd.DataFrame | None:
    if data is None:
        return None
    df = pd.DataFrame(data["rows"], columns=data["columns"])
    for name, kind in zip(data["columns"], data["kinds"]):
        if kind == "date":
            df[name] = pd.to_datetime(df[name])
        elif kind == "float":
            df[name] = pd.to_numeric(df[name], errors="coerce").astype(float)
        elif kind == "int":
            values = pd.to_numeric(df[name], errors="coerce")
            df[name] = values.astype("int64") if values.notna().all() else values.astype(float)
        elif kind == "bool":
            df[name] = df[name].astype("boolean") if df[name].isna().any() else df[name].astype(bool)
        else:
            df[name] = df[name].astype(object)
    return df


def _kind(series: pd.Series) -> str:
    if pd.api.types.is_datetime64_any_dtype(series):
        return "date"
    if pd.api.types.is_bool_dtype(series):
        return "bool"
    if pd.api.types.is_integer_dtype(series):
        return "int"
    if pd.api.types.is_float_dtype(series):
        return "float"
    return "text"


# ------------------------------------------------------------------------ saved answers

class AnswerCache:
    """Saved answers, looked up by question and by the question it follows up.

    A standalone question matches only a standalone saved answer. A follow-up such as
    "How is delivery there?" matches only when the previous question is the one it was
    saved after, because its meaning depends on that question."""

    def __init__(self, answers: list[Answer] | None = None):
        self._answers: dict[tuple[str, str], Answer] = {}
        for answer in answers or []:
            self._answers[_key(answer.question, answer.follow_up_of)] = answer

    def __len__(self) -> int:
        return len(self._answers)

    def get(self, question: str, previous: dict[str, str] | None = None) -> Answer | None:
        if previous:
            hit = self._answers.get(_key(question, previous.get("question")))
            if hit is not None:
                return hit
        return self._answers.get(_key(question, None))

    def answers(self) -> list[Answer]:
        return list(self._answers.values())

    @classmethod
    def load(cls, path: Path = CACHE_PATH) -> "AnswerCache":
        """Missing or unreadable file: an empty cache, so every question runs live."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if data.get("version") != CACHE_VERSION:
                logger.warning("Saved answers file has version %s, expected %s; ignored.",
                               data.get("version"), CACHE_VERSION)
                return cls()
            return cls([answer_from_dict(item) for item in data.get("answers", [])])
        except FileNotFoundError:
            return cls()
        except Exception:  # noqa: BLE001 — a bad file must not stop the app
            logger.exception("Saved answers file could not be read; ignored.")
            return cls()

    def save(self, path: Path = CACHE_PATH) -> None:
        payload = {"version": CACHE_VERSION, "answers": [answer_to_dict(a) for a in self._answers.values()]}
        path = Path(path)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def put(self, answer: Answer) -> None:
        self._answers[_key(answer.question, answer.follow_up_of)] = answer


def _key(question: str, follow_up_of: str | None) -> tuple[str, str]:
    return normalise(question), normalise(follow_up_of)

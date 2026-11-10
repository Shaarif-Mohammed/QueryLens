"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Session rules

What one visitor's session remembers and allows, kept apart from the interface code
so it can be tested on its own:

- Live questions per session are capped (LIVE_QUESTION_SESSION_CAP, default 18).
  Quick insights (saved answers) never count, so they always work.
- Follow-up memory is one turn: the last answered question and the SQL that answered
  it. A question that is not answered clears it, and nothing older is ever kept.
- The browser's back and forward buttons move between answers already shown in the
  visit (AnswerHistory). That is a display history only: the model still sees just the
  answer on screen as the follow-up context.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from collections import OrderedDict

from app.answers import Answer, AnswerCache, normalise

DEFAULT_SESSION_CAP = 18

# Shown only once every typed question in the session has been used; no counter before that.
CAP_MESSAGE = (
    "You've reached this session's question limit",
    "Thanks for exploring QueryLens. You can keep exploring the quick insights below.",
)


def session_cap() -> int:
    """LIVE_QUESTION_SESSION_CAP from the environment; the default when unset or invalid."""
    raw = os.getenv("LIVE_QUESTION_SESSION_CAP", "").strip()
    try:
        return max(0, int(raw)) if raw else DEFAULT_SESSION_CAP
    except ValueError:
        return DEFAULT_SESSION_CAP


@dataclass
class SessionState:
    live_questions: int = 0  # questions sent to the model this session
    previous: dict[str, str] | None = None  # follow-up context: last answered question + SQL

    def remaining(self, cap: int) -> int:
        return max(0, cap - self.live_questions)

    def record(self, answer: Answer, *, live: bool) -> None:
        """Update the session after a question. A question refused before any model
        call (empty or too long) changes nothing: it costs nothing and does not
        replace the question being followed up."""
        if answer.status == "invalid_question":
            return
        if live:
            self.live_questions += 1
        self.previous = answer.follow_up_context()

    def clear_follow_up(self) -> None:
        self.previous = None


@dataclass
class Plan:
    kind: Literal["cached", "live", "capped"]
    answer: Answer | None = None  # set for "cached"


def plan_question(question: str, previous: dict[str, str] | None, cache: AnswerCache,
                  state: SessionState, cap: int) -> Plan:
    """Decide how to answer: a saved answer if there is one, otherwise live while the
    session has questions left."""
    saved = cache.get(question, previous)
    if saved is not None:
        return Plan("cached", saved)
    if state.remaining(cap) <= 0:
        return Plan("capped")
    return Plan("live")


# ------------------------------------------------------------------ browser back and forward

HISTORY_SIZE = 30  # answers kept for back/forward in one visit


class AnswerHistory:
    """Answers already shown in this visit, by question, so the browser's back and
    forward buttons can bring one back instantly without asking the model again.
    The latest answer to a question wins; the oldest is dropped past HISTORY_SIZE."""

    def __init__(self, size: int = HISTORY_SIZE):
        self.size = size
        self._answers: OrderedDict[str, Answer] = OrderedDict()

    def add(self, answer: Answer) -> None:
        if answer.status == "invalid_question":
            return
        key = normalise(answer.question)
        self._answers.pop(key, None)
        self._answers[key] = answer
        while len(self._answers) > self.size:
            self._answers.popitem(last=False)

    def get(self, question: str) -> Answer | None:
        return self._answers.get(normalise(question))

    def __len__(self) -> int:
        return len(self._answers)


@dataclass
class Visit:
    kind: Literal["landing", "shown", "not_asked"]
    answer: Answer | None = None


def resolve_address(question: str | None, history: AnswerHistory, cache: AnswerCache) -> Visit:
    """What to show for the question in the page address (after back, forward, a
    reload or an opened link): the landing page, an answer already at hand, or the
    question waiting for the visitor to press Ask. Never runs the model."""
    question = (question or "").strip()
    if not question:
        return Visit("landing")
    seen = history.get(question)
    if seen is not None:
        return Visit("shown", seen)
    prepared = cache.get(question)
    if prepared is not None:
        return Visit("shown", prepared)
    return Visit("not_asked")

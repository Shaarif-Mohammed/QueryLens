"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Session rule tests

Offline. They check the live-question cap, that saved answers never count toward it,
and that follow-up memory holds exactly one answered question.
"""

from app.answers import Answer, AnswerCache
from app.session import (
    DEFAULT_SESSION_CAP,
    AnswerHistory,
    SessionState,
    plan_question,
    resolve_address,
    session_cap,
)


def answer(question="Which states have the most revenue?", status="answered", sql="SELECT 1"):
    return Answer(question=question, status=status, sql=sql)


SAVED = answer("What share of our customers buy more than once?")


def test_cap_from_environment(monkeypatch):
    monkeypatch.delenv("LIVE_QUESTION_SESSION_CAP", raising=False)
    assert session_cap() == DEFAULT_SESSION_CAP == 18
    monkeypatch.setenv("LIVE_QUESTION_SESSION_CAP", "5")
    assert session_cap() == 5
    monkeypatch.setenv("LIVE_QUESTION_SESSION_CAP", "lots")
    assert session_cap() == DEFAULT_SESSION_CAP
    monkeypatch.setenv("LIVE_QUESTION_SESSION_CAP", "-3")
    assert session_cap() == 0


def test_live_questions_count_and_saved_answers_do_not():
    state = SessionState()
    state.record(answer(), live=True)
    state.record(answer(status="execution_failed"), live=True)  # still cost a model call
    state.record(SAVED, live=False)
    assert state.live_questions == 2 and state.remaining(18) == 16


def test_question_refused_before_any_call_changes_nothing():
    state = SessionState()
    state.record(answer(), live=True)
    before = state.previous
    state.record(answer("", status="invalid_question"), live=True)
    assert state.live_questions == 1 and state.previous == before


def test_cap_reached_then_only_saved_answers():
    cache, state = AnswerCache([SAVED]), SessionState()
    for _ in range(18):
        assert plan_question("Which sellers deliver late?", None, cache, state, 18).kind == "live"
        state.record(answer("Which sellers deliver late?"), live=True)
    assert plan_question("Which sellers deliver late?", None, cache, state, 18).kind == "capped"
    plan = plan_question(SAVED.question, None, cache, state, 18)
    assert plan.kind == "cached" and plan.answer is SAVED


def test_saved_answer_is_used_before_the_model():
    plan = plan_question(SAVED.question.upper(), None, AnswerCache([SAVED]), SessionState(), 18)
    assert plan.kind == "cached"


def test_follow_up_memory_is_one_answered_question():
    state = SessionState()
    state.record(answer("Which states have the most revenue?", sql="SELECT state"), live=True)
    assert state.previous == {"question": "Which states have the most revenue?", "sql": "SELECT state"}
    state.record(answer("How is delivery there?", sql="SELECT delivery"), live=True)
    assert state.previous == {"question": "How is delivery there?", "sql": "SELECT delivery"}


def test_follow_up_cleared_after_a_failed_question():
    state = SessionState()
    state.record(answer(), live=True)
    state.record(answer("What is our profit margin?", status="cannot_answer"), live=True)
    assert state.previous is None


def test_clear_follow_up():
    state = SessionState()
    state.record(answer(), live=True)
    state.clear_follow_up()
    assert state.previous is None and state.live_questions == 1


# ------------------------------------------------------------------ back and forward


def test_history_returns_the_latest_answer_and_forgets_the_oldest():
    history = AnswerHistory(size=2)
    first, second, third = answer("A"), answer("B"), answer("C")
    for a in (first, second, third):
        history.add(a)
    assert history.get("a") is None and history.get("B ") is second and len(history) == 2
    newer_b = answer("B", sql="SELECT 2")
    history.add(newer_b)
    assert history.get("B") is newer_b


def test_history_skips_questions_refused_before_any_call():
    history = AnswerHistory()
    history.add(answer("", status="invalid_question"))
    assert len(history) == 0


def test_address_resolves_to_landing_history_prepared_or_waiting():
    history, cache = AnswerHistory(), AnswerCache([SAVED])
    shown = answer("Which states have the most revenue?")
    history.add(shown)
    assert resolve_address("", history, cache).kind == "landing"
    assert resolve_address(None, history, cache).kind == "landing"
    assert resolve_address("which states have the most revenue", history, cache).answer is shown
    assert resolve_address(SAVED.question, history, cache).answer is SAVED
    # An unknown question from an opened link never runs the model by itself.
    assert resolve_address("Which sellers ship fastest?", history, cache).kind == "not_asked"

"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Answer and saved-answer tests

Offline: the model and database are replaced with fakes. They check that a saved
answer reads back exactly as it was written (types included), that it is profiled
like a live answer, and that a follow-up only matches the question it was saved after.
"""

import json
from dataclasses import dataclass, field

import pandas as pd

from app.analytics.analyzer import profile_result
from app.answers import (
    EXAMPLES,
    Answer,
    AnswerCache,
    ExplanationView,
    answer_from_dict,
    answer_to_dict,
    from_pipeline,
    normalise,
    pill_examples,
    suggested_follow_up,
    suggestions_for,
)
from app.database.query_executor import ExecutionResult
from app.pipeline import MAX_QUESTION_CHARS, answer_question

STATES_SQL = ("SELECT state, total_revenue, total_orders FROM warehouse.vw_regional_analysis "
              "ORDER BY total_revenue DESC, state LIMIT 10")


def states_frame():
    return pd.DataFrame({"state": ["SP", "RJ", "MG"], "total_revenue": [5769703.15, 2055401.57, 1819321.70],
                         "total_orders": [40501, 12350, 11354]})


def mixed_frame():
    return pd.DataFrame({
        "order_month": pd.to_datetime(["2017-01-01", "2017-02-01", None]),
        "score_label": ["1", "12+", None],          # text that looks like a number stays text
        "total_revenue": [127482.37, float("nan"), 0.5],
        "orders": [10, 20, 30],
        "is_top_20_pct": [True, False, True],
    })


def answered(question="Which states have the most revenue?", df=None, sql=STATES_SQL,
             follow_up_of=None, explanation=None) -> Answer:
    df = states_frame() if df is None else df
    return Answer(question=question, status="answered", sql=sql, dataframe=df,
                  measure_used="total_revenue", follow_up_of=follow_up_of,
                  explanation=explanation or ExplanationView(ok=True, headline="SP leads."))


def round_trip(answer: Answer) -> Answer:
    return answer_from_dict(json.loads(json.dumps(answer_to_dict(answer))))


# ------------------------------------------------------------------ JSON round trip

def test_rows_read_back_with_their_types():
    original = mixed_frame()
    restored = round_trip(answered(df=original)).dataframe
    assert list(restored.columns) == list(original.columns)
    assert pd.api.types.is_datetime64_any_dtype(restored["order_month"])
    assert restored["order_month"].iloc[0] == pd.Timestamp("2017-01-01") and pd.isna(restored["order_month"].iloc[2])
    assert restored["score_label"].tolist()[:2] == ["1", "12+"] and pd.isna(restored["score_label"].iloc[2])
    assert pd.api.types.is_float_dtype(restored["total_revenue"]) and pd.isna(restored["total_revenue"].iloc[1])
    assert restored["total_revenue"].iloc[0] == 127482.37
    assert pd.api.types.is_integer_dtype(restored["orders"]) and restored["orders"].tolist() == [10, 20, 30]
    assert restored["is_top_20_pct"].tolist() == [True, False, True]


def test_saved_answer_is_profiled_like_a_live_one():
    answer = answered()
    restored = round_trip(answer)
    live = profile_result(states_frame(), STATES_SQL, "total_revenue")
    assert restored.from_cache and restored.profile is not None
    assert restored.profile.primary_measure == live.primary_measure == "total_revenue"
    assert restored.profile.facts == live.facts


def test_explanation_and_notes_survive_the_round_trip():
    explanation = ExplanationView(ok=True, headline="h", text="t", caveats=["c"],
                                  ungrounded_numbers=["9.9%"], regenerated=True)
    answer = answered(explanation=explanation)
    answer.assumptions, answer.sources = ["note"], ["warehouse.vw_regional_analysis"]
    restored = round_trip(answer)
    assert restored.explanation == explanation and restored.explanation.flagged
    assert restored.assumptions == ["note"] and restored.sources == ["warehouse.vw_regional_analysis"]


def test_refusal_round_trip_keeps_the_alternative_and_no_rows():
    refusal = Answer(question="Which products get returned most often?", status="cannot_answer",
                     message="Returns are not recorded.",
                     closest_alternative="Which product categories have the lowest average review scores?")
    restored = round_trip(refusal)
    assert restored.dataframe is None and restored.profile is None and restored.explanation is None
    assert restored.closest_alternative.startswith("Which product categories")
    assert restored.follow_up_context() is None


# ------------------------------------------------------------------ from the pipeline

@dataclass
class FakeResponse:
    status: str = "ok"
    sql: str = STATES_SQL
    sources: list = field(default_factory=lambda: ["warehouse.vw_regional_analysis"])
    measure_used: str = "total_revenue"
    assumptions: list = field(default_factory=lambda: ["Revenue includes freight."])
    cannot_answer_reason: str = ""
    closest_alternative: str = ""


@dataclass
class FakeGeneration:
    response: FakeResponse | None = None
    error: str | None = None
    model: str = "fake-model"

    @property
    def ok(self):
        return self.error is None and self.response is not None


class FakeGenerator:
    def __init__(self, response):
        self.response = response

    def generate(self, question, previous=None):
        return FakeGeneration(self.response)


def run(response, question="Which states have the most revenue?", previous=None):
    execution = ExecutionResult(True, states_frame(), sql=STATES_SQL, row_count=3)
    result = answer_question(question, previous, generator=FakeGenerator(response),
                             execute=lambda validation: execution)
    return from_pipeline(result, previous)


def test_from_pipeline_keeps_everything_the_interface_shows():
    answer = run(FakeResponse())
    assert answer.ok and not answer.from_cache and answer.model == "fake-model"
    assert answer.sql == STATES_SQL and answer.dataframe is not None and answer.profile is not None
    assert answer.assumptions == ["Revenue includes freight."] and "generation" in answer.timings
    assert answer.follow_up_context() == {"question": "Which states have the most revenue?", "sql": STATES_SQL}


def test_from_pipeline_refusal_keeps_the_alternative():
    response = FakeResponse(status="cannot_answer", sql="", cannot_answer_reason="No returns data.",
                            closest_alternative="Which categories get the most 1-star reviews?")
    answer = run(response, "Which products get returned most often?")
    assert answer.status == "cannot_answer" and answer.message == "No returns data."
    assert answer.closest_alternative == "Which categories get the most 1-star reviews?"
    assert answer.dataframe is None and answer.follow_up_context() is None


def test_from_pipeline_records_what_was_followed_up():
    previous = {"question": "Which states have the most revenue?", "sql": STATES_SQL}
    answer = run(FakeResponse(), "How is delivery there?", previous)
    assert answer.follow_up_of == "Which states have the most revenue?"


# ------------------------------------------------------------------ saved-answer lookup

def test_lookup_ignores_case_spacing_and_closing_punctuation():
    cache = AnswerCache([answered()])
    assert cache.get("  which STATES have the most   revenue ") is not None
    assert normalise("How is delivery there?") == normalise("how is delivery there")


def test_follow_up_matches_only_after_its_own_question():
    parent = answered()
    child = answered("How is delivery there?", follow_up_of=parent.question)
    cache = AnswerCache([parent, child])
    assert cache.get("How is delivery there?", parent.follow_up_context()) is child
    assert cache.get("How is delivery there?") is None
    other = {"question": "Which product categories bring in the most revenue?", "sql": "SELECT 1"}
    assert cache.get("How is delivery there?", other) is None


def test_standalone_answer_matches_whatever_came_before():
    cache = AnswerCache([answered()])
    other = {"question": "Which product categories bring in the most revenue?", "sql": "SELECT 1"}
    assert cache.get("Which states have the most revenue?", other) is not None


def test_missing_bad_or_old_file_gives_an_empty_cache(tmp_path):
    assert len(AnswerCache.load(tmp_path / "missing.json")) == 0
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert len(AnswerCache.load(bad)) == 0
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"version": 0, "answers": []}), encoding="utf-8")
    assert len(AnswerCache.load(old)) == 0


def test_save_and_load(tmp_path):
    path = tmp_path / "cached_answers.json"
    parent = answered()
    AnswerCache([parent, answered("How is delivery there?", follow_up_of=parent.question)]).save(path)
    loaded = AnswerCache.load(path)
    assert len(loaded) == 2
    assert loaded.get("How is delivery there?", parent.follow_up_context()).from_cache


# ------------------------------------------------------------------ examples

def test_examples_are_distinct_and_short_enough():
    questions = [normalise(e.question) for e in EXAMPLES]
    assert len(questions) == len(set(questions)) and 8 <= len(EXAMPLES) <= 10
    assert all(len(e.question) <= MAX_QUESTION_CHARS for e in EXAMPLES)


def test_suggested_follow_up():
    assert suggested_follow_up("which states have the most revenue") == "How is delivery there?"
    assert suggested_follow_up("What share of our customers buy more than once?") is None


# ------------------------------------------------------------------ quick insights and suggestions


def test_pills_three_defaults_and_returns_not_a_pill():
    assert [e.label for e in pill_examples(default=True)] == [
        "Top categories by revenue", "Monthly revenue trend", "Seller revenue vs on-time delivery"]
    assert len(pill_examples()) == 9 and len(pill_examples(default=False)) == 6
    assert "Product returns" not in [e.label for e in pill_examples()]
    assert all(e.label and len(e.label) <= 40 for e in EXAMPLES)


def test_follow_ups_and_message_key_survive_the_round_trip():
    answer = answered()
    answer.follow_ups, answer.message_key = ["How is delivery there?", "Which states have the most sellers?"], ""
    restored = round_trip(answer)
    assert restored.follow_ups == answer.follow_ups and restored.message_key == ""


def test_old_saved_answers_without_new_fields_still_load():
    data = answer_to_dict(answered())
    del data["follow_ups"], data["message_key"]
    restored = answer_from_dict(data)
    assert restored.follow_ups == [] and restored.message_key == ""


def test_suggestions_prefer_the_sql_step_then_prepared_then_alternative():
    a = answered()
    assert suggestions_for(a) == ["How is delivery there?"]  # prepared follow-up of a quick insight
    a.follow_ups = ["Which states have the most sellers?", "How is delivery in these states?"]
    assert suggestions_for(a) == ["How is delivery there?", "Which states have the most sellers?"]
    refusal = Answer(question="Which products get returned most often?", status="cannot_answer",
                     closest_alternative="Which categories get the most 1-star reviews?")
    assert suggestions_for(refusal) == ["Which categories get the most 1-star reviews?"]
    follow = answered("How is delivery there?", follow_up_of="Which states have the most revenue?")
    assert suggestions_for(follow) == []

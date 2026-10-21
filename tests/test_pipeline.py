"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Pipeline tests

The model and the database are replaced with fakes, so these tests run offline and
cost nothing. They check that the pipeline stops at the right step and that nothing
reaches the executor except validated SQL.
"""

from dataclasses import dataclass, field

import pandas as pd

from app.database.query_executor import ExecutionResult
from app.pipeline import MAX_QUESTION_CHARS, answer_question
from app.validation.sql_validator import ValidationResult

GOOD_SQL = "SELECT segment, COUNT(*) AS customers FROM warehouse.vw_rfm GROUP BY segment ORDER BY segment"


@dataclass
class FakeResponse:
    status: str = "ok"
    sql: str = GOOD_SQL
    sources: list = field(default_factory=lambda: ["warehouse.vw_rfm"])
    measure_used: str = "customers per segment"
    assumptions: list = field(default_factory=list)
    cannot_answer_reason: str = ""
    closest_alternative: str = ""


@dataclass
class FakeGeneration:
    response: FakeResponse | None = None
    error: str | None = None
    model: str = "fake"

    @property
    def ok(self):
        return self.error is None and self.response is not None


class FakeGenerator:
    def __init__(self, generation=None, raises=None):
        self.generation = generation or FakeGeneration(FakeResponse())
        self.raises = raises
        self.calls = []

    def generate(self, question, previous=None):
        self.calls.append((question, previous))
        if self.raises:
            raise self.raises
        return self.generation


class FakeExecutor:
    def __init__(self, result=None):
        self.result = result or ExecutionResult(True, pd.DataFrame({"segment": ["Loyal"], "customers": [3]}),
                                                sql=GOOD_SQL + "\nLIMIT 500", row_count=1)
        self.received = []

    def __call__(self, validation):
        self.received.append(validation)
        return self.result


def ask(question="How many customers are in each RFM segment?", generation=None, raises=None,
        execution=None, previous=None):
    generator, executor = FakeGenerator(generation, raises), FakeExecutor(execution)
    result = answer_question(question, previous, generator=generator, execute=executor)
    return result, generator, executor


def test_answered_question_runs_only_validated_sql():
    result, _, executor = ask()
    assert result.status == "answered" and result.ok and result.message == ""
    assert len(executor.received) == 1
    passed = executor.received[0]
    assert isinstance(passed, ValidationResult) and passed.ok
    assert passed.sql.endswith("LIMIT 500")  # the validator's output, not the generated text
    assert result.dataframe is not None and set(result.timings) == {"generation", "validation", "execution"}


def test_cannot_answer_stops_before_validation():
    response = FakeResponse(status="cannot_answer", sql="", cannot_answer_reason="No cost data.",
                            closest_alternative="What is revenue by category?")
    result, _, executor = ask(generation=FakeGeneration(response))
    assert result.status == "cannot_answer" and result.message == "No cost data."
    assert result.validation is None and executor.received == []


def test_cannot_answer_with_sql_still_does_not_run():
    response = FakeResponse(status="cannot_answer", sql=GOOD_SQL)
    result, _, executor = ask(generation=FakeGeneration(response))
    assert result.status == "cannot_answer" and executor.received == []


def test_generation_error_stops():
    result, _, executor = ask(generation=FakeGeneration(None, error="All models unavailable."))
    assert result.status == "generation_failed" and result.validation is None and executor.received == []


def test_generator_exception_stops_cleanly():
    result, _, executor = ask(raises=RuntimeError("GOOGLE_CLOUD_PROJECT is not set"))
    assert result.status == "generation_failed" and executor.received == []
    assert "generation" in result.timings


def test_unsafe_sql_is_rejected_and_not_run():
    response = FakeResponse(sql="DELETE FROM warehouse.dim_order")
    result, _, executor = ask(generation=FakeGeneration(response))
    assert result.status == "rejected" and executor.received == []
    assert result.validation.errors and result.sql == "DELETE FROM warehouse.dim_order"


def test_ok_status_with_empty_sql_is_rejected():
    result, _, executor = ask(generation=FakeGeneration(FakeResponse(sql="")))
    assert result.status == "rejected" and executor.received == []


def test_execution_failure_is_reported_with_its_kind():
    failed = ExecutionResult(False, None, sql=GOOD_SQL, error="stopped", error_kind="timeout")
    result, _, _ = ask(execution=failed)
    assert result.status == "execution_failed" and "too long" in result.message
    assert result.dataframe is None and result.follow_up_context() is None


def test_follow_up_context_carries_question_and_executed_sql():
    result, _, _ = ask()
    assert result.follow_up_context() == {
        "question": "How many customers are in each RFM segment?",
        "sql": GOOD_SQL + "\nLIMIT 500",
    }


def test_previous_context_reaches_the_generator():
    previous = {"question": "Which states have the most revenue?", "sql": "SELECT 1"}
    _, generator, _ = ask("How is delivery there?", previous=previous)
    assert generator.calls == [("How is delivery there?", previous)]


def test_empty_and_overlong_questions_never_reach_the_model():
    for question in ["", "   ", "x" * (MAX_QUESTION_CHARS + 1)]:
        result, generator, executor = ask(question)
        assert result.status == "invalid_question" and generator.calls == [] and executor.received == []


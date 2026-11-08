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
from app.pipeline import MAX_QUESTION_CHARS, MESSAGE_PARTS, answer_question, explain_answer
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


# ------------------------------------------------------------ profile and explanation


@dataclass
class FakeExplanation:
    response: object = "explained"
    error: str | None = None
    ungrounded_numbers: list = field(default_factory=list)

    @property
    def ok(self):
        return self.error is None and self.response is not None


class FakeExplainer:
    def __init__(self, result=None, raises=None):
        self.result, self.raises, self.calls = result or FakeExplanation(), raises, []

    def explain(self, question, measure_used, assumptions, df, profile):
        self.calls.append((question, measure_used, assumptions, profile))
        if self.raises:
            raise self.raises
        return self.result


def test_answered_question_gets_a_profile():
    result, _, _ = ask()
    assert result.profile is not None and result.profile.primary_measure == "customers"
    assert result.profile.complete  # 1 row under the appended LIMIT 500


def test_explanation_receives_question_measure_notes_and_profile():
    result, _, _ = ask()
    explainer = FakeExplainer()
    explain_answer(result, explainer=explainer)
    assert result.explanation.ok and "explanation" in result.timings
    question, measure, notes, profile = explainer.calls[0]
    assert question == result.question and measure == "customers per segment" and profile is result.profile


def test_failed_explanation_keeps_the_answer():
    result, _, _ = ask()
    explain_answer(result, explainer=FakeExplainer(FakeExplanation(response=None, error="All models unavailable.")))
    assert result.status == "answered" and result.dataframe is not None and not result.explanation.ok


def test_explainer_exception_keeps_the_answer():
    result, _, _ = ask()
    explain_answer(result, explainer=FakeExplainer(raises=RuntimeError("boom")))
    assert result.status == "answered" and not result.explanation.ok and "boom" in result.explanation.error


def test_no_explanation_for_unanswered_questions():
    response = FakeResponse(status="cannot_answer", sql="", cannot_answer_reason="No cost data.")
    result, _, _ = ask(generation=FakeGeneration(response))
    explainer = FakeExplainer()
    explain_answer(result, explainer=explainer)
    assert explainer.calls == [] and result.explanation is None


# ------------------------------------------------------------ progress and message parts


def test_steps_are_reported_in_order():
    steps = []
    generator, executor = FakeGenerator(), FakeExecutor()
    answer_question("How many customers are in each RFM segment?", generator=generator, execute=executor,
                    on_step=steps.append)
    assert steps == ["generation", "validation", "execution"]


def test_steps_stop_where_the_question_stops():
    steps = []
    response = FakeResponse(status="cannot_answer", sql="", cannot_answer_reason="No returns data.")
    answer_question("Which products get returned most?", generator=FakeGenerator(FakeGeneration(response)),
                    execute=FakeExecutor(), on_step=steps.append)
    assert steps == ["generation"]


def test_a_failing_progress_callback_never_stops_the_question():
    def broken(step):
        raise RuntimeError("display failed")
    result = answer_question("How many customers are in each RFM segment?", generator=FakeGenerator(),
                             execute=FakeExecutor(), on_step=broken)
    assert result.status == "answered"


def test_every_stop_names_its_message():
    failed = ExecutionResult(False, None, sql=GOOD_SQL, error="stopped", error_kind="timeout")
    result, _, _ = ask(execution=failed)
    assert result.message_key == "timeout" and result.message.startswith(MESSAGE_PARTS["timeout"][0])
    refusal = FakeResponse(status="cannot_answer", sql="", cannot_answer_reason="No returns data.")
    result, _, _ = ask(generation=FakeGeneration(refusal))
    assert result.message_key == "cannot_answer" and result.message == "No returns data."
    result, _, _ = ask("")
    assert result.message_key == "empty_question"
    result, _, _ = ask()
    assert result.message_key == ""

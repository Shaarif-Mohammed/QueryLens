"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Tests: SQL generator (offline)

Run from the project root:  python -m pytest tests/test_sql_generator.py -v

No network calls and no cost: the Gemini client is replaced by a fake that returns
scripted answers or errors, so the retry, backup-model and parsing logic can be tested
exactly.
"""

import sys
from pathlib import Path

import pytest
from google.genai import errors

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "metadata").is_dir())
sys.path.insert(0, str(ROOT))

import app.llm.sql_generator as sg  # noqa: E402

MAIN, BACKUP = "main-model", "backup-model"

GOOD_JSON = (
    '{"status": "ok", "sql": "SELECT 1 FROM warehouse.vw_rfm;", "sources": ["warehouse.vw_rfm"], '
    '"measure_used": "m", "assumptions": ["a"], "cannot_answer_reason": "", "closest_alternative": ""}'
)


class FakeResponse:
    def __init__(self, text=GOOD_JSON):
        self.text = text
        self.parsed = None
        self.usage_metadata = None


class NetworkTimeout(Exception):
    """Stands in for the HTTP library's timeout (matched by class name)."""


NetworkTimeout.__name__ = "TimeoutException"


def api_error(code):
    return errors.APIError(code, {"error": {"code": code, "message": "scripted", "status": "X"}})


@pytest.fixture
def make_generator(monkeypatch):
    """Build a generator whose model calls follow a script: {model: [outcome, ...]}."""
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "false")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(sg.time, "sleep", lambda s: None)

    def build(script, models=(MAIN, BACKUP)):
        gen = sg.SqlGenerator(model=models[0], fallback_models=list(models[1:]))
        calls = []

        def fake_generate(model, contents, config):
            calls.append(model)
            outcome = script[model].pop(0) if len(script[model]) > 1 else script[model][0]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        gen.client.models.generate_content = fake_generate
        gen.calls = calls
        return gen

    return build


# --------------------------------------------------------------- happy path
def test_answer_from_main_model(make_generator):
    gen = make_generator({MAIN: [FakeResponse()], BACKUP: [FakeResponse()]})
    r = gen.generate("Who are our top customers?")
    assert r.ok and r.model == MAIN and r.attempts == 1
    assert gen.calls == [MAIN]


def test_trailing_semicolon_and_fences_removed(make_generator):
    fenced = GOOD_JSON.replace("SELECT 1 FROM warehouse.vw_rfm;", "```sql\\nSELECT 1 FROM warehouse.vw_rfm;\\n```")
    gen = make_generator({MAIN: [FakeResponse(fenced)], BACKUP: [FakeResponse()]})
    assert gen.generate("q").response.sql == "SELECT 1 FROM warehouse.vw_rfm"


def test_cannot_answer_is_returned_not_treated_as_error(make_generator):
    refusal = (
        '{"status": "cannot_answer", "sql": "", "sources": [], "measure_used": "", "assumptions": [], '
        '"cannot_answer_reason": "No cost data.", "closest_alternative": "Revenue by category?"}'
    )
    gen = make_generator({MAIN: [FakeResponse(refusal)], BACKUP: [FakeResponse()]})
    r = gen.generate("What is our profit margin?")
    assert r.ok and r.response.status == "cannot_answer" and r.response.sql == ""


def test_invalid_json_is_an_error(make_generator):
    gen = make_generator({MAIN: [FakeResponse("not json")], BACKUP: [FakeResponse()]})
    r = gen.generate("q")
    assert not r.ok and "not valid JSON" in r.error


# --------------------------------------------------------- retries and backups
def test_503_is_retried_on_the_same_model(make_generator):
    gen = make_generator({MAIN: [api_error(503), FakeResponse()], BACKUP: [FakeResponse()]})
    r = gen.generate("q")
    assert r.ok and r.model == MAIN
    assert gen.calls == [MAIN, MAIN]


def test_503_three_times_moves_to_backup(make_generator):
    gen = make_generator({MAIN: [api_error(503)], BACKUP: [FakeResponse()]})
    r = gen.generate("q")
    assert r.ok and r.model == BACKUP
    assert gen.calls == [MAIN] * sg.ATTEMPTS_PER_MODEL + [BACKUP]


@pytest.mark.parametrize("code", [429, 504])
def test_quota_and_stall_skip_straight_to_backup(make_generator, code):
    gen = make_generator({MAIN: [api_error(code)], BACKUP: [FakeResponse()]})
    r = gen.generate("q")
    assert r.ok and r.model == BACKUP
    assert gen.calls == [MAIN, BACKUP]


def test_client_timeout_skips_straight_to_backup(make_generator):
    gen = make_generator({MAIN: [NetworkTimeout("slow")], BACKUP: [FakeResponse()]})
    r = gen.generate("q")
    assert r.ok and r.model == BACKUP
    assert any("timeout/network" in line for line in r.retry_log)


def test_retired_model_404_is_skipped(make_generator):
    gen = make_generator({MAIN: [api_error(404)], BACKUP: [FakeResponse()]})
    r = gen.generate("q")
    assert r.ok and r.model == BACKUP and gen.calls == [MAIN, BACKUP]


@pytest.mark.parametrize("code", [400, 403])
def test_bad_request_or_permission_stops_immediately(make_generator, code):
    gen = make_generator({MAIN: [api_error(code)], BACKUP: [FakeResponse()]})
    r = gen.generate("q")
    assert not r.ok and gen.calls == [MAIN]


def test_all_models_unavailable(make_generator):
    gen = make_generator({MAIN: [api_error(503)], BACKUP: [api_error(503)]})
    r = gen.generate("q")
    assert not r.ok and r.error.startswith("All models unavailable")


def test_unexpected_exception_is_not_swallowed(make_generator):
    gen = make_generator({MAIN: [ValueError("bug")], BACKUP: [FakeResponse()]})
    with pytest.raises(ValueError):
        gen.generate("q")


# ------------------------------------------------------------------ helpers
def test_backoff_never_close_to_zero():
    for attempt in (1, 2, 3, 4, 5):
        ceiling = min(sg.MAX_DELAY_SECONDS, sg.BASE_DELAY_SECONDS * 2 ** (attempt - 1))
        delay = sg._backoff_delay(attempt)
        assert ceiling / 2 <= delay <= ceiling


def test_follow_up_includes_previous_question():
    from app.llm.prompt_builder import build_user_prompt

    prompt = build_user_prompt("How is delivery there?", {"question": "Top states?", "sql": "SELECT 1"})
    assert "PREVIOUS QUESTION" in prompt and "Top states?" in prompt and prompt.endswith("How is delivery there?")


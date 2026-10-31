"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Response generator tests

No network calls: a fake client returns scripted model output. Covers the prompt the
model receives, parsing, the shared retry behaviour, and the check that every number
in the explanation appears in the result.
"""

import json

import pandas as pd
import pytest
from google.genai import errors

import app.llm.sql_generator as sg
from app.analytics.analyzer import profile_result
from app.llm.response_generator import (
    ExplanationGenerator,
    build_explanation_prompt,
    drop_inapplicable_caveats,
    find_ungrounded_numbers,
    nearest_figure_hint,
)

DF = pd.DataFrame({"delivery_status": ["On-Time", "Late"], "avg_review_score": [4.29, 2.57],
                   "total_orders": [88644, 7826]})
SQL = ("SELECT dimension_value AS delivery_status, avg_review_score, total_orders FROM warehouse.vw_delivery_experience "
       "WHERE dimension = 'On-time vs Late' ORDER BY avg_review_score DESC\nLIMIT 500")
MEASURE = "avg_review_score and total_orders by delivery status from vw_delivery_experience"
NOTES = ["The comparison shows an association, not proof of cause.",
         "Rows under about 30 orders are small samples."]


def answer(headline="On-time orders average 4.29 stars; late orders 2.57.",
           explanation="Average review score per delivered order.", caveats=("Association, not cause.",)):
    return json.dumps({"headline": headline, "explanation": explanation, "caveats": list(caveats)})


class FakeResponse:
    def __init__(self, text):
        self.text, self.parsed, self.usage_metadata = text, None, None


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def generate_content(self, model, contents, config):
        self.calls.append((model, contents))
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)


class FakeClient:
    def __init__(self, *outcomes):
        self.models = FakeModels(outcomes)


def explain(client):
    gen = ExplanationGenerator(model="main", fallback_models=["backup"], client=client)
    return gen.explain("Do late deliveries get worse reviews?", MEASURE, NOTES, DF, profile_result(DF, SQL, MEASURE))


def test_prompt_carries_question_notes_facts_and_rows():
    prompt = build_explanation_prompt("Do late deliveries get worse reviews?", MEASURE, NOTES, DF,
                                      profile_result(DF, SQL, MEASURE))
    assert "QUESTION:\nDo late deliveries get worse reviews?" in prompt
    assert "- The comparison shows an association, not proof of cause." in prompt
    assert "Every row has at least 30 total_orders" in prompt  # lets the model drop the small-sample note
    assert "On-Time,4.29,88644" in prompt and "(2 of 2 rows shown, CSV)" in prompt


def test_explanation_is_parsed_and_grounded():
    result = explain(FakeClient(answer()))
    assert result.ok and result.model == "main"
    assert result.response.headline.startswith("On-time orders average 4.29")
    assert result.ungrounded_numbers == []


def test_invented_or_calculated_numbers_are_flagged():
    result = explain(FakeClient(answer(headline="Late orders score 1.72 points lower, a 40% drop.")))
    assert result.ok and result.ungrounded_numbers == ["1.72", "40%"]


def test_shared_retry_logic_moves_to_the_backup(monkeypatch):
    monkeypatch.setattr(sg.time, "sleep", lambda s: None)
    unavailable = errors.APIError(503, {"error": {"code": 503, "message": "x", "status": "UNAVAILABLE"}})
    client = FakeClient(unavailable, unavailable, unavailable, answer())
    result = explain(client)
    assert result.ok and result.model == "backup" and result.attempts == 4


def test_invalid_json_is_an_error():
    result = explain(FakeClient("not json"))
    assert not result.ok and "not valid JSON" in result.error


def test_bad_request_stops_with_an_error():
    bad = errors.APIError(400, {"error": {"code": 400, "message": "x", "status": "INVALID_ARGUMENT"}})
    result = explain(FakeClient(bad))
    assert not result.ok and "400" in result.error


@pytest.mark.parametrize("written, source", [
    ("Churn is 22.6%.", "churn_rate_pct\n22.61"),                     # rounded to one decimal
    ("Revenue was about BRL 985k.", "985491.64"),                      # thousands
    ("Revenue was BRL 985,491.64.", "985491.64"),                      # thousands separators
    ("Revenue was BRL 1.0 million.", "985491.64"),                     # millions, one decimal
    ("Loyal holds 26,407 customers.", "Loyal,26407"),
    ("The top 10 sellers and 3 segments.", "nothing numeric"),         # small counting numbers ignored
    ("In August 2018 revenue fell.", "order_month\n2018-08-01"),
])
def test_grounded_numbers(written, source):
    assert find_ungrounded_numbers(written, source) == []


def test_whole_number_rounding_is_grounded():
    assert find_ungrounded_numbers("Churn is 23%.", "22.61") == []  # 22.61 rounds to 23


@pytest.mark.parametrize("written, source, flagged", [
    ("Revenue grew 15%.", "100\n115", ["15%"]),            # calculated growth
    ("About 1.72 points apart.", "4.29\n2.57", ["1.72"]),  # calculated difference
    ("Revenue was BRL 990k.", "985491.64", ["990k"]),      # wrong rounding
    ("Churn is 31.4%.", "22.61", ["31.4%"]),               # invented
])
def test_ungrounded_numbers(written, source, flagged):
    assert find_ungrounded_numbers(written, source) == flagged


def test_seller_ids_are_not_read_as_numbers():
    assert find_ungrounded_numbers("Seller b1b39487… is first.", "b1b39487…") == []


# ------------------------------------------------- bounds, rounding, rewrites and caveats
STATES = "SP,5769703.15,40501\nRJ,2055401.57,12350\nRS,861472.79,5345\nES,317657.93,1995"


@pytest.mark.parametrize("written, source", [
    ("About 88,600 on-time orders.", "On-Time,4.29,88644"),           # trailing zeros, within 0.5%
    ("SP earned BRL 5.77 million.", STATES),                          # three significant figures
    ("Loyal holds about 26,400 customers.", "Loyal,26407"),
])
def test_honest_rounding_is_grounded(written, source):
    assert find_ungrounded_numbers(written, source) == []


@pytest.mark.parametrize("written, source, flagged", [
    ("Across more than 88,000 orders.", "On-Time,4.29,88644", ["88,000"]),
    ("SP earned over BRL 5.7 million.", STATES, ["5.7 million"]),
    ("The rest each earned under BRL 900,000.", STATES, ["900,000"]),   # a threshold, not a figure
])
def test_bounds_from_the_live_run_are_flagged(written, source, flagged):
    assert find_ungrounded_numbers(written, source) == flagged


def test_ungrounded_answer_is_rewritten_once():
    client = FakeClient(answer(explanation="Across more than 88,000 orders."), answer())
    result = explain(client)
    assert result.ok and result.regenerated and result.ungrounded_numbers == []
    assert len(client.models.calls) == 2
    second_prompt = client.models.calls[1][1]
    assert "YOUR PREVIOUS ANSWER used figures that are not in RESULT or FACTS: 88,000" in second_prompt


def test_still_ungrounded_after_the_rewrite_is_reported():
    bad = answer(explanation="Across more than 88,000 orders.")
    client = FakeClient(bad, bad)
    result = explain(client)
    assert result.ok and result.regenerated and result.ungrounded_numbers == ["88,000"]
    assert len(client.models.calls) == 2  # never more than one rewrite


def test_grounded_answer_is_not_rewritten():
    client = FakeClient(answer())
    result = explain(client)
    assert not result.regenerated and len(client.models.calls) == 1


def test_late_2016_caveat_dropped_when_rows_are_not_a_2016_series():
    late = "Overall averages include thin late-2016 data."
    client = FakeClient(answer(caveats=(late, "Association, not cause.")))
    assert explain(client).response.caveats == ["Association, not cause."]


def test_late_2016_caveat_kept_for_a_series_that_includes_2016():
    df = pd.DataFrame({"order_month": pd.to_datetime(["2016-10-01", "2016-11-01", "2017-01-01"]),
                       "total_revenue": [100.0, 200.0, 300.0]})
    profile = profile_result(df, "SELECT ...\nLIMIT 500", "total_revenue")
    late = "Activity in late-2016 months is very thin."
    assert drop_inapplicable_caveats([late], df, profile) == [late]


def test_overstated_wording_triggers_one_rewrite():
    client = FakeClient(answer(headline="Delivery varies significantly across states, 4.29 vs 2.57."), answer())
    result = explain(client)
    assert result.ok and result.regenerated and result.banned_terms == []
    assert '"significantly", which implies a statistical test' in client.models.calls[1][1]


def test_overstated_wording_that_remains_is_reported():
    bad = answer(explanation="A statistically significant gap.")
    result = explain(FakeClient(bad, bad))
    assert result.banned_terms == ["statistically", "significant"]


def test_both_problems_are_named_in_one_rewrite():
    client = FakeClient(answer(explanation="Significantly more than 88,000 orders."), answer())
    explain(client)
    note = client.models.calls[1][1]
    assert "88,000" in note and '"significantly"' in note and "; it also " in note


def test_rewrite_note_names_the_nearest_real_figure():
    source = "BA,18.90,85.96\nSC,14.50,90.24"
    assert nearest_figure_hint("85.9%", source) == "85.9% (nearest figure in the data: 85.96)"
    client = FakeClient(answer(explanation="Bahia is lowest at 85.9%."), answer())
    explain(client)
    assert "nearest figure in the data" in client.models.calls[1][1]
    assert "leave it out" in client.models.calls[1][1]

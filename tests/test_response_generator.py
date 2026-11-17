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
    is_round_milestone,
    nearest_figure_hint,
    ungrounded_note,
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


@pytest.mark.parametrize("written, flagged", [
    ("Five categories exceeded BRL 1 million in sales.", ["1 million"]),   # a bound, not a rounding
    ("Revenue topped BRL 1.0 million in May.", ["1.0 million"]),
    ("Three months had more than 1,120,000 in revenue.", ["1,120,000"]),
])
def test_bounds_must_be_figures_from_the_data(written, flagged):
    """'BRL 1 million' alone may round 1,032,723.77; written as a bound it is a threshold."""
    source = "category,total_revenue\nhealth_beauty,1412089.53\ncomputers_accessories,1032723.77"
    assert find_ungrounded_numbers(written, source) == flagged


@pytest.mark.parametrize("written", [
    "Health beauty earned BRL 1.41 million, over BRL 1.03 million for computers.",   # bound = real figure
    "Every row has at least 30 reviews.",                                            # threshold from FACTS
    "Computers earned about BRL 1.03 million.",
])
def test_bounds_that_are_real_figures_pass(written):
    source = ("FACTS:\n- Every row has at least 30 total_reviews.\n"
              "RESULT (2 of 2 rows shown, CSV):\ncategory,total_revenue\nhealth_beauty,1412089.53\n"
              "computers_accessories,1032723.77")
    assert find_ungrounded_numbers(written, source) == []


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
    from app.llm.response_generator import nearest_figure_hint

    source = "BA,18.90,85.96\nSC,14.50,90.24"
    assert nearest_figure_hint("85.9%", source) == "85.9% (nearest figure in the data: 85.96)"
    client = FakeClient(answer(explanation="Bahia is lowest at 85.9%."), answer())
    explain(client)
    assert "nearest figure in the data" in client.models.calls[1][1]
    assert "leave it out" in client.models.calls[1][1]

# --------------------------------------------- figures in the CSV rows


def _monthly_prompt():
    df = pd.DataFrame({
        # 143.46 is neither the highest nor the lowest, so it appears only in the rows.
        "order_month": pd.to_datetime(["2016-09-01", "2016-10-01", "2016-11-01", "2017-11-01"]),
        "total_revenue": [143.46, 46490.66, 0.0, 1153364.20],
    })
    sql = "SELECT order_month, total_revenue FROM warehouse.vw_new_vs_returning_revenue ORDER BY order_month"
    profile = profile_result(df, sql, "total_revenue by month")
    return build_explanation_prompt("How has monthly revenue changed?", "total_revenue by month", [], df, profile)


def test_csv_field_comma_is_not_read_as_a_thousands_separator():
    # In the rows "2016-09-01,143.46" the comma separates two fields; 143.46 is in the data.
    prompt = _monthly_prompt()
    assert "2016-09-01,143.46" in prompt
    assert find_ungrounded_numbers("Revenue started at BRL 143.46 in September 2016.", prompt) == []


def test_figures_merged_across_a_csv_comma_are_not_available():
    # "01,143.46" is not a figure in the data, so 1,143.46 must still be flagged.
    prompt = _monthly_prompt()
    assert find_ungrounded_numbers("Revenue was BRL 1,143.46 that month.", prompt) == ["1,143.46"]


def test_facts_keep_thousands_separators():
    # FACTS write 1,153,364.20 with commas; the grouped reading still applies there.
    prompt = _monthly_prompt()
    assert find_ungrounded_numbers("The peak was BRL 1,153,364.20 (BRL 1.15 million).", prompt) == []


def test_nearest_figure_hint_reads_csv_rows_plainly():
    assert nearest_figure_hint("143.5", _monthly_prompt()) == "143.5 (nearest figure in the data: 143.46)"

# --------------------------------------------- round milestones in the rewrite note


def test_round_milestones_are_recognised():
    assert all(is_round_milestone(t) for t in ["1.00 million", "90.0%", "900,000", "5 million", "BRL 1m"])
    assert not any(is_round_milestone(t) for t in ["985,491.64", "85.9%", "5.7 million", "1.15 million", "0"])


def test_rewrite_note_tells_the_model_to_delete_a_milestone():
    note = ungrounded_note(["1.00 million"], _monthly_prompt())
    assert note.startswith("used figures that are not in RESULT or FACTS")
    assert '"1.00 million" is a round threshold you chose' in note and "Delete the words" in note
    assert "nearest figure" not in note


def test_rewrite_note_keeps_the_nearest_figure_for_a_near_miss():
    note = ungrounded_note(["143.5", "1.00 million"], _monthly_prompt())
    assert "143.5 (nearest figure in the data: 143.46)" in note and "round threshold" in note

# --------------------------------------------- two-way tables reach the model whole


def _grid_prompt():
    rows = []
    months = ["January", "February", "March", "April", "May", "June", "July", "August",
              "September", "October", "November", "December"]
    for k, month in enumerate(months, start=1):
        for j in range(10):
            rows.append({"order_month_num": k, "order_month": month, "category": f"category_{j}",
                         "monthly_revenue": 1000.0 * k + 10.0 * j + 0.25})
    df = pd.DataFrame(rows)
    hint = "monthly_revenue (BRL) grouped by purchase month and product category"
    profile = profile_result(df, "SELECT ...\nLIMIT 500", hint)
    return build_explanation_prompt("How does revenue vary by month?", hint, [], df, profile)


def test_grid_result_is_sent_whole():
    prompt = _grid_prompt()
    assert "RESULT (all 120 rows, as a grid of category by order_month, CSV):" in prompt
    assert "category,January,February,March,April,May,June,July,August,September,October,November,December" in prompt


def test_cell_beyond_the_first_50_rows_is_grounded():
    # December, category_9 is row 120 of the long list, beyond the first 50 rows.
    assert find_ungrounded_numbers("December peaked at BRL 12,090.25 for category 9.", _grid_prompt()) == []

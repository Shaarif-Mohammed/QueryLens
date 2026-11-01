"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Live response generator checks

These call the real model, so they are skipped unless asked for:

    QUERYLENS_LIVE_LLM_TESTS=1 python -m pytest tests/test_live_response_generator.py -v

They need no database: each test gives the explanation step a fixed result taken from
the real warehouse, so the expected figures are known. They check structure, never
exact wording: every number comes from the result, no overstated wording, and the
caveats that the rows require are present (and the ones they rule out are absent).
Four short model calls; well under USD 0.01 per run.
"""

import os

import pandas as pd
import pytest

from app.analytics.analyzer import profile_result

pytestmark = pytest.mark.skipif(
    os.getenv("QUERYLENS_LIVE_LLM_TESTS") != "1",
    reason="calls the live model; set QUERYLENS_LIVE_LLM_TESTS=1 to run",
)


@pytest.fixture(scope="module")
def explainer():
    from app.llm.response_generator import ExplanationGenerator

    return ExplanationGenerator()


def explain(explainer, question, df, sql, measure, notes):
    result = explainer.explain(question, measure, notes, df, profile_result(df, sql, measure))
    assert result.ok, result.error
    assert result.ungrounded_numbers == [], result.response
    assert result.banned_terms == [], result.response
    return result.response


def caveats_text(response) -> str:
    return " ".join(response.caveats).lower()


def test_comparison_keeps_association_caveat_and_drops_small_sample_note(explainer):
    df = pd.DataFrame({"delivery_status": ["On-Time", "Late"], "avg_review_score": [4.29, 2.57],
                       "total_orders": [88644, 7826]})
    r = explain(
        explainer, "Do late deliveries get worse reviews than on-time ones?", df,
        "SELECT dimension_value AS delivery_status, avg_review_score, total_orders FROM warehouse.vw_delivery_experience "
        "WHERE dimension = 'On-time vs Late' ORDER BY avg_review_score DESC\nLIMIT 500",
        "avg_review_score and total_orders by delivery status from vw_delivery_experience",
        ["The comparison shows an association, not proof of cause.",
         "Rows under about 30 orders are small samples whose averages can swing on a few orders."],
    )
    assert "4.29" in r.headline + r.explanation and "2.57" in r.headline + r.explanation
    assert "cause" in caveats_text(r)
    assert "small sample" not in caveats_text(r)  # FACTS say every row has thousands of orders


def test_ranking_on_small_samples_keeps_small_sample_caveat(explainer):
    df = pd.DataFrame({
        "seller_id": ["e2a1ac9bf33e5549a2a4f834e70df2f8", "e882b2a25a10b9c057cc49695f222c19",
                      "2addf05f476d0637864454e93ba673d5"],
        "performance_score": [97.2, 96.44, 95.8], "total_orders": [5, 57, 12],
    })
    r = explain(
        explainer, "Who are the best sellers?", df,
        "SELECT seller_id, performance_score, total_orders FROM warehouse.vw_seller_performance "
        "WHERE total_orders >= 5 ORDER BY performance_score DESC, seller_id ASC LIMIT 10",
        "performance_score from vw_seller_performance",
        ["Sellers with fewer than 30 orders are small samples whose scores can swing on a few orders."],
    )
    assert "small" in caveats_text(r) or "few orders" in caveats_text(r)


def test_non_time_series_has_no_late_2016_caveat(explainer):
    df = pd.DataFrame({"state": ["SP", "RJ", "MG"], "avg_delivery_days": [8.3, 14.8, 11.5],
                       "on_time_rate": [94.11, 86.53, 94.39], "total_orders": [40501, 12350, 11354]})
    r = explain(
        explainer, "How is delivery in the top revenue states?", df,
        "SELECT state, avg_delivery_days, on_time_rate, total_orders FROM warehouse.vw_regional_analysis "
        "ORDER BY total_revenue DESC, state ASC LIMIT 10",
        "avg_delivery_days and on_time_rate from vw_regional_analysis",
        ["While these states have very high order volumes, overall averages include thin late-2016 data."],
    )
    assert "2016" not in caveats_text(r)


def test_complete_segment_counts_use_the_real_total(explainer):
    df = pd.DataFrame({"segment": ["Loyal", "About to Sleep", "Champions"],
                       "customer_count": [26407, 15422, 3800]})
    r = explain(
        explainer, "How many customers are in each RFM segment?", df,
        "SELECT segment, COUNT(customer_unique_id) AS customer_count FROM warehouse.vw_rfm "
        "GROUP BY segment ORDER BY customer_count DESC, segment\nLIMIT 500",
        "customer_count per RFM segment from vw_rfm",
        ["Segment sizes can vary slightly between runs because tied scores are split arbitrarily."],
    )
    assert "Loyal" in r.headline + r.explanation
    assert "vary" in caveats_text(r) or "between runs" in caveats_text(r)

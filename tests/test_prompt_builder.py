"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Tests: prompt builder

Run from the project root:  python -m pytest tests/test_prompt_builder.py -v
"""

import sys
from pathlib import Path

# Make the project root importable, wherever this test file sits inside the project.
ROOT = next(p for p in Path(__file__).resolve().parents if (p / "metadata").is_dir())
sys.path.insert(0, str(ROOT))

from app.llm.prompt_builder import (  # noqa: E402
    METADATA_DIR,
    STRIPPED_KEYS,
    build_system_prompt,
    load_metadata,
    strip_keys,
)

PROMPT = build_system_prompt()

ALL_VIEWS = [
    "vw_rfm", "vw_clv", "vw_retention", "vw_seller_performance", "vw_regional_analysis",
    "vw_customer_health", "vw_seller_health", "vw_new_vs_returning_revenue",
    "vw_pareto_analysis", "vw_delivery_experience", "vw_product_customer_affinity",
    "vw_payment_behaviour", "vw_review_response_intelligence",
]
ALL_TABLES = [
    "dim_date", "dim_customer", "dim_seller", "dim_product", "dim_geolocation", "dim_order",
    "fact_order_items", "fact_payments", "fact_reviews",
    "ml_seller_clusters", "ml_seller_outliers", "ml_category_clusters",
    "ml_review_score_feature_importance",
]

# Figures that appear ONLY inside evaluation_reference values in the metadata.
# If any of these shows up in the prompt, stripping has failed.
LEAK_CANARIES = [
    "312,989.25",                         # ml_seller_outliers revenue exposure
    "b1b3948701c5c72445495bd161b83a4c",   # ml_seller_outliers sharpest flag
    "80.85",                              # feature importance, delivery_days
    "0.1491",                             # feature importance test R2
    "1,525",                              # ml_seller_clusters Elite size
    "71.6%",                              # Strong-tier cluster finding
    "57.83",                              # five-star share
    "76.93",                              # credit card share of orders
    "4.29",                               # on-time review average
    "~49% of customers",                  # customer Pareto
    "26,165",                             # stable Loyal segment count (vw_rfm)
]


def test_stripped_keys_absent_from_prompt():
    for key in STRIPPED_KEYS:
        assert key not in PROMPT, f"'{key}' leaked into the system prompt"


def test_stripped_keys_absent_from_every_level():
    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in STRIPPED_KEYS, f"'{k}' survived stripping"
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for tree in load_metadata().values():
        walk(tree)


def test_evaluation_figures_do_not_leak():
    leaked = [c for c in LEAK_CANARIES if c in PROMPT]
    assert not leaked, f"evaluation figures leaked into the prompt: {leaked}"


def test_canaries_really_exist_in_source_files():
    # Guards the test above: if a canary is not in the raw files, it proves nothing.
    raw = "".join(p.read_text(encoding="utf-8") for p in METADATA_DIR.glob("*.yaml"))
    missing = [c for c in LEAK_CANARIES if c not in raw]
    assert not missing, f"canaries not found in the metadata files: {missing}"


def test_every_relation_is_described():
    for name in ALL_VIEWS + ALL_TABLES:
        assert name in PROMPT, f"{name} is missing from the prompt"


def test_core_rules_present():
    for phrase in ["2018-09-01", "canonical_source", "not_available_in_the_data",
                   "ranking_defaults", "ambiguity_policy", "cluster_name"]:
        assert phrase in PROMPT, f"'{phrase}' is missing from the prompt"


def test_strip_keys_nested():
    tree = {"a": {"evaluation_reference": 1, "b": [{"owner_review": 2, "c": 3}]}}
    assert strip_keys(tree) == {"a": {"b": [{"c": 3}]}}


def test_yaml_comments_dropped():
    # Header comments and inline notes are for maintainers, not the model.
    assert "Marketplace Analytics Platform repository" not in PROMPT
    assert "verified against the live schema" not in PROMPT

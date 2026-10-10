# QueryLens — AI-Powered Marketplace Analytics Copilot
## Metadata Layer

QueryLens never answers a question from the language model's general knowledge. It
writes SQL against the warehouse, runs it, and explains the result. What makes that SQL
correct is not the model alone: it is the **metadata layer**, three YAML files in
`metadata/` that describe the warehouse, its analytical views, and the business
definitions an analyst would apply. Every SQL-generation request includes them.

This document explains what the files contain, how they were checked, how they reach
the model, and how to change them safely.

---

## 1. The three files

| File | Describes | Contents |
|---|---|---|
| `views_reference.yaml` | The 13 analytical views (`vw_*`): the **primary query surface** | Business question each view answers, grain, complete column list with types, row counts, discriminator columns and their exact values, calculation logic, known limitations, and naming traps between views |
| `business_metrics.yaml` | Business definitions and the rules for using them | 33 metrics with aliases, definitions, source columns and SQL; global conventions; consistency rules; ranking defaults; the list of questions the data cannot answer |
| `schema_dictionary.yaml` | The 9 dimension and fact tables (fallback only) and the 4 `ml_*` model-output tables | Columns, types, keys, row counts, data-quality quirks, common join paths, and rules for writing SQL against raw tables |

The order matters. The model is told to **check the views first** and fall back to raw
tables only when no view answers the question. The views already contain validated
business logic (order-level deduplication, delivered-order filters, review
deduplication), so reusing them avoids re-deriving that logic and getting it subtly
wrong.

---

## 2. What the business rules cover

`business_metrics.yaml` holds the decisions an analyst would otherwise make silently.

**Conventions** (apply to every answer)
- The reference date is **2018-09-01**: "last month" is August 2018 and "this year" is
  January–August 2018. `CURRENT_DATE` and `NOW()` are never used.
- Delivered orders are the default scope; money is in BRL; percentage columns are on a
  0–100 scale.
- A customer is a `customer_unique_id`, never the order-scoped `customer_id`.
- An ambiguous term with a default uses the default, names it, and mentions the
  alternative. QueryLens does not ask clarifying questions when a default exists.

**Ranking defaults** (what "top", "best" and "high" mean when the user does not say)
- Top customers: highest `total_spend`.
- Top sellers: highest `total_revenue`. Best or top-performing sellers: highest
  `performance_score` among scored sellers.
- Top categories: highest `total_revenue`; most popular: most orders.
- "High" revenue or volume with no number given: the top quarter.

**Minimum volume for rankings.** When sellers are ranked by an average or a rate, at
least 5 observations are required in that measure's own base: delivered orders for
delivery measures, reviews for review measures, total orders for the performance score
and cancellation rate. Results built on fewer than about 30 orders or reviews are still
flagged as small samples.

**Consistency rules.** Some measures can be produced by more than one view, and the
views do not always agree to the last unit. The rules name one **canonical source** per
measure (for example, RFM segment counts come from `vw_rfm`, revenue by category from
`vw_product_customer_affinity`) and require one source per answer. Other sections cover
columns with the same name but different meanings, zeros that mean "no data",
small-sample flags, deterministic ordering, and what to do when unsure.

**Not available in the data.** Profit, margin, refunds, demographics, marketing data,
inventory, seller replies to reviews, and text sentiment are not in the warehouse.
Questions that need them are declined, with the closest question the data *can* answer
offered instead.

---

## 3. How the files were verified

The metadata is only useful if it is exactly right: a column name spelled wrongly, or a
discriminator value in the wrong case, silently returns zero rows.

- **Against the live warehouse.** Every column name and type, every row count, and every
  categorical and discriminator value was checked by querying the warehouse as the
  application's read-only role. `scripts/inspect_warehouse_schema.py` produces the
  listing used for this comparison.
- **Against the view SQL.** Every metric definition was checked against the SQL of the
  view that computes it. This settled points that names alone get wrong: revenue
  includes freight; `avg_response_hours` is the customer's survey response time, not a
  seller's reply time (earlier documentation had misread it);
  `vw_payment_behaviour.total_payment_value` is order value rather than the amount paid.
- **Source columns.** All 109 `source_columns` referenced by the metrics exist in the
  warehouse, and no alias maps to two different metrics.

**Known data quirks are documented rather than hidden.** Examples:
- About 97% of customers placed a single order, so most tie on frequency in the RFM
  scoring and the ties are split arbitrarily. Segment sizes can shift slightly between
  runs, so segment answers are best read as shares, and `vw_clv.rfm_segment` is not
  compared with `vw_rfm.segment`.
- `vw_seller_performance` stores missing values as 0, so rankings on review score or
  delivery require the matching volume column to be above zero.
- The `ml_*` tables are point-in-time model outputs, not live calculations.

---

## 4. How the metadata reaches the model

`app/llm/prompt_builder.py` builds the system prompt from the three files in this
order: views, business metrics, then the schema dictionary.

Two kinds of content never reach the model:

- **Audit-only keys.** Keys named `evaluation_reference` (figures quoted from earlier
  analysis, kept to check answers during evaluation) and `owner_review` (review notes)
  are removed at any depth. If those figures were in the prompt, the model could quote
  them instead of querying the warehouse. `tests/test_prompt_builder.py` fails if either
  key name, or any of those figures, appears in the built prompt.
- **YAML comments.** The files are parsed and re-serialised, so comments for
  maintainers (headers, inline notes) are dropped.

The assembled prompt is about 28,000 tokens. It is sent in full with every question;
because it is identical each time, most of it is served from the model provider's
prompt cache.

---

## 5. Changing the metadata

1. **Edit the YAML.** Copy names and categorical values exactly as they appear in the
   warehouse, including case and spelling (category names keep their source typos).
2. **Re-verify anything structural** (columns, row counts, values) against the live
   warehouse, using `scripts/inspect_warehouse_schema.py` as the read-only role.
3. **Keep audit material in audit keys.** Figures quoted from documents go under
   `evaluation_reference`; review notes go under `owner_review`. Never put an expected
   answer in a field the model can see.
4. **Run the tests:** `python -m pytest tests/ -v`.
5. **Re-run sample questions** that touch the changed definition, and run the generated
   SQL to confirm the results still make sense.

When a business definition changes (for example, what counts as churn), record the
decision and its reason in the metric's `definition` or `notes`, so the reasoning
travels with the rule.

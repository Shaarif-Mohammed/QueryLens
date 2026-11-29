# QueryLens — AI-Powered Marketplace Analytics Copilot
## Testing

QueryLens puts a language model between a business question and a production warehouse, so it has to be trustworthy in two ways: the code around the model must handle every case correctly, including failures that cannot be produced on demand, and the model itself must keep behaving the way the code expects. QueryLens is tested from both sides. **Offline tests** replace the model and the database with scripted fakes and check the code's logic exactly, for free, in seconds. **Live tests** run against the real model and the real database and check that they still behave as assumed.

Tests alone cannot tell whether an answer is *good*. Most of what made QueryLens trustworthy came from running real questions end to end, checking every answer by hand against the data, and turning each problem found into a rule, a code check and a test. This document records that loop: **what we found → what we changed → what guards it now.** The graded question set used to judge final answer quality is in [05_evaluation_results.md](05_evaluation_results.md); the limits that remain are in [06_limitations_and_future_work.md](06_limitations_and_future_work.md).

**300 tests** (281 offline across 12 files, 19 live across 3 files) · **about 15 rounds** of live testing with every answer checked by hand · **57 findings** that changed how QueryLens behaves, each guarded by a test, a code check or a written rule.

---

## The strongest findings

| What we found | What we changed | What guards it now |
|---|---|---|
| Asked which sellers have high revenue but poor delivery, QueryLens ranked first a seller with a "33% on-time rate" — built on 3 delivered orders. | The minimum volume for a ranking is measured on each figure's own base (delivered orders for delivery rates, reviews for review scores). "High" without a number means the top quarter. | [Live SQL tests](../tests/test_live_sql_generator.py) check the threshold and the volume floor. |
| A huge but perfectly valid query (`generate_series` over two billion rows) passed the SQL validator, which checks safety, not cost. | Every query runs under a time limit set inside its own database transaction; the database stops it, whatever the SQL. | [Live database tests](../tests/test_live_database.py) prove the limit holds on the real database (stopped at 0.5 s). |
| The SQL step wrote caveats such as "these states have very high order volumes" before any data existed. | Facts — row counts, highest and lowest values, shares, small samples — are computed from the rows in code. The explanation keeps a caveat the facts support and drops one they contradict. | [Analyzer tests](../tests/test_analyzer.py), [explanation tests](../tests/test_response_generator.py). |
| Explanations wrote bounds and round thresholds instead of figures: "over BRL 5.7 million", "under BRL 900,000". | Every number in an explanation is checked against the result; only honest rounding passes. A flagged figure triggers one automatic rewrite that names the nearest real figure; a figure after "over", "above" or "exceeded" must match a real figure. | [Explanation tests](../tests/test_response_generator.py). |
| Commas inside the result rows were read as thousands separators, so real figures looked invented to the numbers check. | Figures in the result are read with a plain number reader that treats commas as field separators. | [Explanation tests](../tests/test_response_generator.py). |
| The model broke two written rules twice in a row: it called differences "significant" and added a caveat about thin late-2016 data to answers with no 2016 in them. | A rule the model breaks twice is enforced in code: overstated wording triggers the rewrite, and the late-2016 caveat is removed unless the rows include 2016. | [Explanation tests](../tests/test_response_generator.py). |
| A monthly revenue table showed **+649,657%** growth for January 2017, because December 2016 had about BRL 20 of sales. | Growth compared with an almost empty period is blanked in the table and in what the model sees, with a short note. The stored data is never changed. | [Analyzer tests](../tests/test_analyzer.py), [formatting tests](../tests/test_formatting.py). |
| "Who are our top customers?" was drawn as a line chart, because each customer row carried a last-order date. | A line chart needs one row per period and nothing else naming the rows; rankings are drawn as bars. | [Chart tests](../tests/test_charts.py). |
| A question about revenue was answered — headline, explanation and chart — in order counts. Every figure was real, so the numbers check passed. The SQL step had named the measure `total_revenue`, as the view calls it, while the query returned it as `revenue_brl`. | The measure named by the SQL step is matched to the result's columns by meaning, and nothing is guessed when two columns could match. Prepared answers are also checked by hand against the question, not only against the rows. | [Analyzer tests](../tests/test_analyzer.py). |
| When the model's shared capacity was busy, questions were handed straight to a slower backup model, which then stalled: 3 of 33 questions failed. | A busy response is retried on the main model with short, growing waits before the backup is tried. In the next run, 0 of 18 failed. | [SQL generator tests](../tests/test_sql_generator.py). |

---

## The full record

Every finding that changed behaviour, by part of the product. Each section opens with how it was tested.

<details>
<summary><strong>Writing SQL</strong> — 14 findings</summary>

A fixed set of business questions (top customers, churn, best sellers, last month's revenue, profit margin, customer segments, late vs on-time reviews, high revenue with poor delivery), a follow-up pair and a prompt-injection attempt were run after every change to the instructions. The instructions were revised three times; after each revision the full set was re-run and every generated query was also run by hand against the warehouse.

| What we found | What we changed | What guards it now |
|---|---|---|
| A seller with 3 delivered orders ranked first on a "33% on-time rate"; the volume floor counted all orders, not delivered ones. | The volume floor applies to each measure's own base. | [Live SQL tests](../tests/test_live_sql_generator.py) |
| "High revenue" was read as above the median, which returned mostly small sellers. | "High" without a number means the top quarter; the median is named as the looser alternative. | [Live SQL tests](../tests/test_live_sql_generator.py) |
| A ranking by score or rate came back without a note that some rows rested on very few orders. | A checklist of caveats runs before every answer; rankings by an average or rate always carry the small-sample note. | [Live SQL tests](../tests/test_live_sql_generator.py) |
| "Top customers" came back with a customer-segment column nobody asked for, labelling one-order customers "Loyal". | Segments are returned only when the question is about segments, always with a note that labels do not necessarily mean repeat purchases. | [Live SQL tests](../tests/test_live_sql_generator.py) |
| Customer segment counts changed between runs minutes apart, because ties in the scoring were split differently each time. | Ties are broken by a unique ID in the warehouse views, and each overlapping measure has one official source. | [Live SQL tests](../tests/test_live_sql_generator.py) check the official source; segment counts were graded exactly in evaluation. |
| Column names came back as display labels with spaces and brackets ("Total Revenue (BRL)"). | Columns are always plain snake_case names; the interface turns them into readable titles. | Written rule; [formatting tests](../tests/test_formatting.py) |
| The suggested alternative on a declined question was a SQL statement. | The alternative is always one plain-English question. | [Live SQL tests](../tests/test_live_sql_generator.py) |
| A declined question suggested "Which analytical views are available?" | The alternative is always a business question about the marketplace, never about the database. | Written rule; checked in evaluation. |
| Caveats stated facts about results the SQL step had not seen ("very high order volumes, so highly reliable"). | Caveats say what to check, never what the result contains; the explanation step verifies each one against the facts. | Written rule; [explanation tests](../tests/test_response_generator.py) |
| Suggested follow-up questions needed raw tables or invented thresholds ("high-value orders"), and spelled "instalment" inconsistently. | Each suggestion must be answerable directly from one view, with no invented thresholds, in the data's own spelling. | [Live SQL tests](../tests/test_live_sql_generator.py) |
| A suggested follow-up said "differ significantly", wording the explanation is not allowed to use. | Suggestions containing "significant(ly)" or "statistical(ly)" are dropped in code; the next clean suggestion takes their place. | [SQL generator tests](../tests/test_sql_generator.py) |
| "Which month was weakest, and why?" returned a single row and nothing to compare it with. | A "why" question returns the item with the rows to compare it with and the measures that moved alongside it, and says that causes are not recorded. | Written rule; checked in evaluation. |
| "Below average" had no fixed meaning, and the answer changed with the reading. | It means below the weighted overall average, as defined in the data description. | Data description; checked in evaluation. |
| A note that "high churn is normal" appeared on answers that were not about churn, and a note about recent customer cohorts on monthly revenue. | These notes apply only to the answers they describe: churn rates and cohort results. | Written rules; checked in evaluation. |

Built in from the start rather than found: the description of the data sent to the model is assembled in code, and audit-only reference figures are stripped from it so the model can never quote an expected answer ([prompt builder tests](../tests/test_prompt_builder.py)).

</details>

<details>
<summary><strong>Checking and running SQL</strong> — 2 findings</summary>

The validator was built as an allow-list: only one read-only `SELECT` on known warehouse views and tables passes. Its tests run 18 real generated queries that must pass and 33 unsafe or off-limits queries that must be rejected ([validator tests](../tests/test_sql_validator.py), 60 tests). The query runner was tested with a scripted connection for every failure path ([query runner tests](../tests/test_query_executor.py)) and against the real database ([live database tests](../tests/test_live_database.py)).

| What we found | What we changed | What guards it now |
|---|---|---|
| A valid query generating two billion rows passed the validator, which checks safety, not cost. | Every query runs under a database time limit; the validator stays focused on safety. | [Live database tests](../tests/test_live_database.py) (stopped at 0.5 s), [query runner tests](../tests/test_query_executor.py) |
| Setting the time limit when the connection opens is rejected by pooled connections on hosted databases. | The limit is set inside each query's own transaction, which works on any connection. | [Query runner tests](../tests/test_query_executor.py), [live database tests](../tests/test_live_database.py) |

</details>

<details>
<summary><strong>Written explanations</strong> — 14 findings</summary>

The explanation step was built over four rounds; each re-ran the same answers and checked every figure by hand against its table. The evaluation then added a further round of fixes and re-runs.

| What we found | What we changed | What guards it now |
|---|---|---|
| Caveats written by the SQL step before any data existed were repeated as facts. | The explanation receives facts computed from the rows in code and keeps or drops each caveat against them. | [Analyzer tests](../tests/test_analyzer.py), [explanation tests](../tests/test_response_generator.py) |
| Notes the data contradicted survived: "a high share of lost customers is normal" on a result where lost customers were the smallest group. | Notes the rows contradict, or that concern rows not in the answer, are dropped. | Written rule; checked in evaluation. |
| Bounds instead of figures: "more than 88,000" for 88,644, "over BRL 5.7 million". | Every number is checked against the input; only honest rounding passes; a flagged figure triggers one rewrite. | [Explanation tests](../tests/test_response_generator.py) |
| A threshold the data never mentioned: "under BRL 900,000". | Thresholds are flagged like any invented figure. | [Explanation tests](../tests/test_response_generator.py) |
| After the rewrite, "85.9%" (truncated from 85.96) and "above 90.0%" were still there; the rewrite note did not say what was right. | The rewrite note names the nearest real figure for each flagged number and asks for thresholds to be removed. | [Explanation tests](../tests/test_response_generator.py) |
| "Exceeded BRL 1 million" passed as a rounding of 1.03 million. | A figure after a bound word (over, above, under, exceeded, at least…) must be within 0.5% of a real figure. | [Explanation tests](../tests/test_response_generator.py) (6 tests) |
| A round milestone ("1.00 million") survived the rewrite. | Round milestones are named in the rewrite note for removal. | [Explanation tests](../tests/test_response_generator.py) |
| Commas in the result rows were read as thousands separators. | Figures in the result are read with a plain number reader. | [Explanation tests](../tests/test_response_generator.py) |
| "Significantly worse" — implying a statistical test — after a written rule against it, twice. | Overstated wording triggers the same single rewrite. | [Explanation tests](../tests/test_response_generator.py) |
| A caveat about thin late-2016 data on answers with no 2016 rows, twice. | The caveat is removed in code unless the rows are a series that includes 2016. | [Explanation tests](../tests/test_response_generator.py) |
| Filler caveats ("values are in BRL", "revenue includes freight"). | Definitions and currencies are stated once in the explanation, not as caveats. | Written rule. |
| "Tapering slightly" for a 27% fall; "gradual" for a series with dips. | Size words only when the figures bear them out; a rise with dips is described as such. | Written rule; checked in evaluation. |
| A claim about all 500 rows from the 50 the model was shown. | When fewer rows are shown than the result has, only the rows shown are described. | Written rule; checked in evaluation. |
| 0.43% and 0.40% both written as "0.4%". | Percentages under 1% keep two decimals. | Written rule; checked in evaluation. |

When a flagged figure survives the single rewrite, the answer is still shown, with a short note to rely on the table. See [06_limitations_and_future_work.md](06_limitations_and_future_work.md).

</details>

<details>
<summary><strong>Analysis and charts</strong> — 12 findings</summary>

Every result is profiled in code before it is explained or drawn: which column is the measure, which names the rows, and whether the rows form a ranking, a time series, a two-way table or a pair of measures. The chart type follows from that shape, never from the model. Each finding below came from a real answer whose chart or summary was wrong or weak.

| What we found | What we changed | What guards it now |
|---|---|---|
| "Top customers" drawn as a line because each row carried a date. | A line needs one row per period and nothing else naming the rows. | [Chart tests](../tests/test_charts.py) |
| +649,657% growth for January 2017 after an almost empty December. | Growth compared with an almost empty period is blanked, with a note, in the table and in what the model sees. | [Analyzer tests](../tests/test_analyzer.py), [formatting tests](../tests/test_formatting.py) |
| A 120-cell table (category by month) was summarised from its first 50 rows only. | Two-way results are sent whole, as a grid, with the highest and lowest cell and totals by row and column. | [Analyzer tests](../tests/test_analyzer.py), [explanation tests](../tests/test_response_generator.py) |
| A revenue-vs-on-time comparison across sellers was summarised as a link that the data did not show. | Pairs of measures get a comparison computed in code: the median of one for the top and bottom quarter of the other. The answer now says there is no clear relationship. | [Analyzer tests](../tests/test_analyzer.py) |
| No small-sample note when the count itself was the measure (11–20 retained customers per cohort). | Counts under 30 are flagged when the count is the measure. | [Analyzer tests](../tests/test_analyzer.py) |
| A top-10 list of months drawn as a line through non-consecutive months. | When the query picked its top rows by something other than the date, they are drawn as bars in the query's order. | [Chart tests](../tests/test_charts.py) |
| No chart for rows named by a year or group number. | Year, quintile, band and group columns are treated as names, and get bars. | [Analyzer tests](../tests/test_analyzer.py), [chart tests](../tests/test_charts.py) |
| A comparison of 2017 and 2018 by month showed only 2017. | Rows named by a number with two to four measures of the same unit get one line per measure. | [Chart tests](../tests/test_charts.py) |
| Monthly revenue (about 1 million) and average order value (about 150) drawn on one axis. | Totals and averages are never mixed on one chart. | [Chart tests](../tests/test_charts.py) |
| A follow-up about review scores charted revenue, because revenue was named as the basis of the top 10. | A column named only as the basis of a selection ("top 10 by revenue") is not the measure. | [Analyzer tests](../tests/test_analyzer.py) |
| A revenue question described and charted in order counts: the measure was named as the view calls it while the query used its own alias. | Measure names are matched to columns by meaning; nothing is guessed when two columns could match. | [Analyzer tests](../tests/test_analyzer.py) |
| The same answer called a total across ten categories "marketplace-wide". | Totals in a two-way table are described in code as covering only the rows in the result. | [Analyzer tests](../tests/test_analyzer.py) |

</details>

<details>
<summary><strong>The interface and quick insights</strong> — 6 findings</summary>

The interface was checked by hand on a laptop screen and a phone width, and every prepared answer (quick insight) was built by the real pipeline and checked by hand, figure by figure, after every rebuild.

| What we found | What we changed | What guards it now |
|---|---|---|
| The model wrote category codes in prose ("bed_bath_table"). | Names taken from the result are shown as readable names ("Bed bath table"). | [Formatting tests](../tests/test_formatting.py) |
| The headline highlighted a year ("2017,") instead of the figure. | Only a real figure is highlighted: one with a unit, a decimal point or a thousands separator. | [Interface tests](../tests/test_ui.py) |
| Reformatting the SQL for display capitalised a column named `segment`, as if it were a keyword. | SQL is re-indented for display only; its words are left exactly as written. | [Formatting tests](../tests/test_formatting.py) |
| A rebuilt quick insight contained an invented threshold ("900,000"). | A prepared answer is saved only if its numbers check passed; otherwise the previous version stays. | Quick-insight build script |
| A rebuilt quick insight was worse than the one it replaced, while still passing every automatic check. | Each rebuilt answer is accepted on its own merits after a hand check; the previous version stays when the new one is worse. | Release step for prepared answers. |
| A shared or bookmarked link with a question in it could start a paid model call on its own. | A question in the page address waits for the Ask button; only quick insights load straight away. | [Session tests](../tests/test_session.py) |

</details>

<details>
<summary><strong>Model reliability</strong> — 9 findings</summary>

Every model call — writing SQL and writing the explanation — goes through one shared routine that decides, for each kind of failure, whether to wait and retry, move to a backup model, or stop. The rules come from failures seen in live use, and each is tested with a scripted model that fails exactly as the real one did ([SQL generator tests](../tests/test_sql_generator.py)).

| What we found | What we changed | What guards it now |
|---|---|---|
| The model was briefly unavailable (a server error) on some calls. | Retry the same model with growing, randomised waits, never close to zero. | [SQL generator tests](../tests/test_sql_generator.py) |
| A model stayed unavailable for several attempts. | After three attempts, move to the backup model. | [SQL generator tests](../tests/test_sql_generator.py) |
| Some requests ran past their deadline; the same model usually stalled again. | A stalled request moves straight to the backup; only the last model is retried. | [SQL generator tests](../tests/test_sql_generator.py) |
| A request could hang with no answer at all. | Each request has its own time limit and counts as a failed attempt when it passes. | [SQL generator tests](../tests/test_sql_generator.py) |
| A model version was retired and returned "not found". | A retired model is skipped at once. | [SQL generator tests](../tests/test_sql_generator.py) |
| A bad request or a permissions error was retried on every model, wasting time. | These stop at once: another model will not help. | [SQL generator tests](../tests/test_sql_generator.py) |
| Busy-capacity responses were handed straight to a slower backup, which stalled: 3 of 33 questions failed. | A busy response retries the main model with longer waits before the backup. In the next run, 0 of 18 failed. | [SQL generator tests](../tests/test_sql_generator.py) (3 tests) |
| A model that answered from one region was not offered in another. | Models and region are set in configuration, and a setup check tests every configured model before use. | Setup check script |
| A slow explanation's retries were not visible when diagnosing it. | Every retry and model switch is recorded with the answer and printed by the diagnostic runner. | Diagnostic runner output |

</details>

<details>
<summary><strong>Evaluation</strong></summary>

After the build, QueryLens was evaluated on 31 business questions in seven groups — simple lookups, ambiguous wording, overlapping data sources, missing data, multi-step questions, follow-up pairs and attempts to break it — with every expected figure verified directly against the warehouse first. The first run produced **no wrong answers**; after the fixes it exposed, **31 of 31 were correct**. Those fixes are included in the sections above. The full method, question set and grades are in [05_evaluation_results.md](05_evaluation_results.md).

</details>

---

## Running the tests

**Offline tests** need no model, no database and no cost. Run them from the project folder:

```bash
python -m pytest tests/
```

**Live tests** are skipped unless switched on, one switch per real service:

```bash
# Real database (free): read-only session and query time limit
QUERYLENS_LIVE_DB_TESTS=1 python -m pytest tests/test_live_database.py

# Real language model (paid, about USD 0.12 per full run)
QUERYLENS_LIVE_LLM_TESTS=1 python -m pytest tests/test_live_sql_generator.py tests/test_live_response_generator.py
```

Live model tests check structure — the source used, the filters, the required caveats, refusals, that figures come from the result — never exact wording, because a correct answer can be phrased differently from run to run. Run them after any change to the model's instructions, the data description or the model settings, and before every deployment.

**The evaluation** re-runs the full question set through the real pipeline (paid) and writes one output file per question:

```bash
bash scripts/run_evaluation.sh
```

### Test files

| Part of the product | Offline tests | Live tests |
|---|---|---|
| Data description sent to the model | [test_prompt_builder.py](../tests/test_prompt_builder.py) (8) | — |
| Writing SQL and model calls | [test_sql_generator.py](../tests/test_sql_generator.py) (25) | [test_live_sql_generator.py](../tests/test_live_sql_generator.py) (12) |
| SQL validator | [test_sql_validator.py](../tests/test_sql_validator.py) (60) | — |
| Running queries | [test_query_executor.py](../tests/test_query_executor.py) (16) | [test_live_database.py](../tests/test_live_database.py) (3) |
| Question-to-answer pipeline | [test_pipeline.py](../tests/test_pipeline.py) (20) | — |
| Result analysis | [test_analyzer.py](../tests/test_analyzer.py) (35) | — |
| Charts | [test_charts.py](../tests/test_charts.py) (22) | — |
| Formatting | [test_formatting.py](../tests/test_formatting.py) (8) | — |
| Written explanations | [test_response_generator.py](../tests/test_response_generator.py) (49) | [test_live_response_generator.py](../tests/test_live_response_generator.py) (4) |
| Quick insights | [test_answers.py](../tests/test_answers.py) (18) | — |
| Session limits and page address | [test_session.py](../tests/test_session.py) (11) | — |
| Page fragments | [test_ui.py](../tests/test_ui.py) (9) | — |
| **Total** | **281** | **19** |

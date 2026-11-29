# QueryLens — AI-Powered Marketplace Analytics Copilot
## Product Requirements Document (PRD)

**Related:** [Architecture](03_architecture.md) for technical design · [Evaluation results](05_evaluation_results.md) for answer quality

---

## 1. Why this matters

Most organisations today sit on top of good data infrastructure (a modelled warehouse, validated views, BI dashboards) and still have a bottleneck at the last mile: turning a question someone actually has into an answer. Dashboards only answer the questions they were built to answer. Anything outside that fixed set routes through an analyst, which is slow and doesn't scale as the number of stakeholders grows.

This is why natural-language interfaces to data ("ask your warehouse a question in plain English") have become one of the most active areas across the BI and data platform space (Tableau Pulse, Power BI Copilot, ThoughtSpot Spotter, Snowflake Cortex Analyst, and similar). The demand is real: business users want self-service answers without learning SQL, and analysts want to stop being a query-writing bottleneck for repetitive ad hoc requests.

The hard part isn't generating SQL from a question; language models already do that reasonably well. The hard part is doing it **safely and reliably inside an existing analytics ecosystem**: not hallucinating numbers, not bypassing governed business logic, not querying tables that were never meant to be queried directly, and not becoming a black box that erodes trust in the data team's work rather than extending it.

QueryLens is built around exactly that discipline: a natural-language layer that sits *on top of* an already-validated warehouse and view layer, rather than replacing or bypassing it. Every answer traces back to a real, governed query, never to the model's own memory.

---

## 2. Problem and opportunity

The Marketplace Analytics Platform (the product QueryLens builds on) already answers real business questions (revenue trends, seller performance, delivery delays, customer behaviour) through a validated PostgreSQL warehouse, 13 analytical SQL views, and Tableau dashboards.

The gap: **getting an answer still requires SQL fluency or a pre-built dashboard that happens to cover the question.** A category manager asking "which sellers in São Paulo have high revenue but poor delivery performance?" has to learn SQL or wait on an analyst. Neither is fast, and no fixed dashboard set can anticipate every question.

QueryLens closes this gap by letting someone ask the question directly and get back a real, validated, data-grounded answer, without bypassing the governed analytics layer underneath it.

---

## 3. Target users

QueryLens is designed around people who already trust the underlying warehouse and dashboards, but hit friction the moment their question falls outside what a pre-built view or dashboard was designed to answer.

**Marketplace Operations Manager**
- **Goal:** Keep delivery performance, fulfilment SLAs, and regional operations healthy day to day.
- **Pain point today:** Operational questions are often *specific to the moment* ("what happened in the Northeast region last week?"): too narrow and too frequent to justify a new dashboard each time, but too urgent to wait in an analyst's queue.
- **How they'd use QueryLens:** Ask a direct operational question, get the number and the trend behind it in under a minute, and decide whether it needs escalation.
- **Value delivered:** Faster operational awareness without becoming SQL-literate or waiting in an analyst's backlog.

**Category / Seller Performance Lead**
- **Goal:** Identify which sellers or product categories are over- or under-performing, and why.
- **Pain point today:** Comparative questions ("which sellers have high revenue but poor delivery?") cut across multiple dimensions that no single dashboard filter combination covers cleanly.
- **How they'd use QueryLens:** Ask multi-dimensional comparison questions in plain English, then drill in with a natural follow-up ("how does that look for electronics specifically?").
- **Value delivered:** Real ad hoc exploration, not limited to whatever slice-and-dice the dashboard happened to expose.

**Non-Technical Executive / Stakeholder**
- **Goal:** Walk into a meeting or review with a fast, credible answer, not a data request that resolves two days later.
- **Pain point today:** No time or inclination to learn SQL or navigate a BI tool; needs to *trust* a number instantly, without verifying it themselves.
- **How they'd use QueryLens:** Ask the question live, see the generated SQL and underlying data alongside the answer (even if they don't read the SQL themselves, its presence signals the answer isn't invented).
- **Value delivered:** Speed without sacrificing the credibility a spreadsheet or a "someone told me" answer wouldn't have.

**Data/Business Analyst (power user, secondary)**
- **Goal:** Offload repetitive, low-complexity ad hoc requests so time goes to higher-value analysis.
- **Pain point today:** A meaningful share of an analyst's time goes to answering variations of questions that are conceptually simple but require someone who knows the schema.
- **How they'd use QueryLens:** Point stakeholders to the tool directly for straightforward questions; reserve their own time for the novel analysis QueryLens can't (and shouldn't) attempt.
- **Value delivered:** A lighter request queue, without giving up control over what's "official": the tool answers from the validated views whenever one covers the question.

---

## 4. Use cases

Grouped by the kind of value each cluster of use cases demonstrates.

**Operational / day-to-day**
- **As an operations manager**, I want to ask "which regions have the worst on-time delivery rate?" in plain English, so that I can flag it without waiting on an analyst.
- **As an operations manager**, I want to ask a fast follow-up like "and how does that compare to last month?" so that I get trend context without re-typing the full question.
- **As a category lead**, I want to ask "which product categories had the sharpest revenue drop this quarter?" so that I can prioritise where to dig in next.

**Strategic / comparative**
- **As a category lead**, I want to ask "which sellers have high revenue but poor delivery performance?" so that I can identify sellers who need support before it affects customer trust.
- **As a category lead**, I want to ask a follow-up like "how does that look for the electronics category specifically?" and have the product understand what "that" refers to, so that I don't have to restate the full question.
- **As an executive**, I want to ask "how did customer satisfaction trend over the last six months?" so that I can bring a data point into a leadership discussion without prep time.

**Trust & verification**
- **As a non-technical stakeholder**, I want to see the actual SQL and data behind every answer, so that I can trust it wasn't invented by the AI.
- **As an analyst**, I want confidence that the tool uses the validated views whenever they cover the question and never writes to the database, so that I can point stakeholders to it without worrying about data integrity.

**First-time / evaluative**
- **As a first-time user**, I want to click a quick insight on the landing screen and immediately see a working result, so that I understand what the product can do before I invest effort writing my own question.
- **As a first-time user**, I want an honest response when I ask something the system can't answer well (e.g., a question no view covers), so that I trust the tool's confident answers precisely because it doesn't bluff on the ones it can't handle.

---

## 5. Success criteria

- **Trust**: every answer is traceable to a real query against real data, never the model answering from memory
- **Self-service**: a first-time user gets a correct, understandable answer within their first 1-2 questions
- **Governed by design**: the product prefers the existing validated views over raw tables, and never gains write access
- **Availability**: the product stays usable when the language model is busy or a usage limit is reached; prepared answers to common questions always load

The detailed technical evaluation, covering SQL accuracy, groundedness and view-first compliance across 31 questions, is documented separately in [05_evaluation_results.md](05_evaluation_results.md).

---

## 6. Scope

**In scope:** natural-language questions answered via real SQL against the existing warehouse (views first); a grounded plain-English explanation and chart/table for each result; one-turn-back follow-up context; a public, guardrailed deployment.

**Out of scope:** multi-turn conversational memory or agent-style architecture; RAG over unstructured documents; model fine-tuning; user authentication; any write access. QueryLens remains a read-only product by design.

**Key assumption:** the underlying warehouse, views, and data are already validated: QueryLens checks the SQL generated against them, not the data itself.

# QueryLens — AI-Powered Marketplace Analytics Copilot
## Limitations and Future Work

QueryLens answers business questions in plain English by writing SQL, checking it, running it against the warehouse and explaining the result. Every answer comes from a real query on real data, never from the language model's memory. That principle holds everywhere in this document.

This document describes where QueryLens stops today: what it does not do by design, what the underlying data cannot support, and where answers can still be imperfect. It closes with where the product goes next. Evaluation results are in [05_evaluation_results.md](05_evaluation_results.md); how each safeguard was found and tested is in [04_testing.md](04_testing.md).

---

## 1. Limits by design

These are deliberate choices, made to keep answers traceable and the product safe to open to anyone.

**It remembers one question back.**
A follow-up such as "How is delivery there?" works because QueryLens passes the previous question and its SQL to the model. It carries no further memory: a third question does not see the first, and refreshing the page starts fresh. Longer conversations make it harder to see what an answer was based on, so the boundary is intentional.

**It answers from a fixed snapshot of marketplace data.**
QueryLens runs on a sample e-commerce dataset covering September 2016 to August 2018. The database is not live, so "today" is fixed at 1 September 2018: "last month" means August 2018 and "this year" means January to August 2018. A question about a later period gets an answer that says where the data ends.

**It can only read, and only from the warehouse.**
Every query must be a single read-only `SELECT` against the warehouse's approved views and tables. Anything else — changes to data, other schemas, system tables — is rejected before it reaches the database, and the database session itself is read-only.

**It works in English.**
Questions, answers and explanations are in English.

**Usage is limited on the public deployment.**
Each visitor can ask up to 18 typed questions per session, and the hosted service has a monthly spending limit on model calls. If either limit is reached, typed questions pause, but the quick insights on the landing page keep working because they are prepared answers that need no model call.

---

## 2. Limits of the data

QueryLens can only answer what the data records. When a question goes beyond it, QueryLens says so and suggests the closest question it can answer.

**No costs, profit, refunds or returns.**
The dataset records prices, freight, payments, deliveries and reviews, but no cost of goods, margins, refunds or product returns. Questions about profit or returns are declined with a clear reason and a nearby alternative, such as revenue by category or the share of low reviews.

**Customer segment names can overstate loyalty.**
Customer segments (Champions, Loyal, At Risk and others) come from a recency, frequency and monetary (RFM) scoring. About 97% of customers in this dataset placed a single order, so a label like "Loyal" does not necessarily mean repeat purchases. QueryLens adds this note whenever it reports segments, and only shows segments when the question asks for them.

**Seller and category clusters are saved results.**
Clusters and outlier flags were produced once by machine-learning models and stored in the warehouse. Because the database is a fixed snapshot rather than a live system, they are not recalculated; QueryLens reports them as computed at that time.

**"Response time" is the customer's, not the seller's.**
The review data measures how long customers took to answer a satisfaction survey. It does not record how quickly sellers responded to customers, so QueryLens either declines that question or states clearly what the figure measures.

**The earliest months are very thin.**
Late 2016 has very few orders. Trends and growth rates that start there can look extreme, so QueryLens leaves out growth figures that compare with an almost empty month and notes when early months are thin.

---

## 3. Limits of the answers

The language model writes the SQL and the explanation; code checks both. These checks catch most problems, and the few that remain are small and visible. In every case the SQL, the measure used and the full result table are available alongside the explanation, so a reader can always check the answer against the data.

**An explanation may occasionally state a difference it worked out itself.**
Every figure in an explanation is checked against the result. Occasionally the explanation mentions a difference between two figures, such as "a gap of 9.0 days", that it calculated itself rather than read from the result. The check flags these, and the answer appears with a short note to rely on the table. In evaluation this happened in 2 of about 45 explanations; the figures themselves were correct.

**Some wording nuances can slip through.**
Code checks every number and blocks overstated wording such as "significantly". Finer points of phrasing are harder to check automatically: a fall of 13% described as "slightly downward", or "nine of the ten" where all ten applied. These do not change the figures, which always come from the result, but they show why the table stays next to the words.

**"Why" questions are answered with what changed, not with causes.**
The data records what happened, not why. Asked why a month was weak, QueryLens compares it with the months around it and shows what moved alongside it (new customers, order value, order count), and says that the data does not record reasons.

**Suggested follow-up questions are a starting point.**
Each answer comes with two suggested next questions, written to be answerable from a single view of the data. Occasionally a suggestion needs more than one (1 of 26 in evaluation); QueryLens still answers it, though the answer may be less direct.

**Checks confirm the figures, while the choice of measure stays visible.**
The numbers check proves that every figure in an explanation appears in the query result. It cannot judge whether the measure chosen is the best reading of the question. That is why every answer states the measure it used and shows the SQL and the full table behind it.

---

## 4. Operational limits

**Answers take a few seconds.**
A typed question goes through several steps: writing the SQL, checking it, running it, and explaining the result. Most answers arrive in about 10 to 20 seconds. The page shows each step as it happens, and the quick insights load instantly.

**Busy moments can slow things down.**
The language model runs on shared capacity. When it is busy, QueryLens waits briefly and tries again, and if needed hands the question to a backup model. Answers still arrive, though at peak times they can take up to about 40 seconds.

**The first question after a quiet period can be slower.**
The hosted database pauses when idle and takes a few seconds to wake up. Later questions are not affected.

---

## 5. Future work: where QueryLens goes next

QueryLens is built on a sample marketplace dataset, but the approach is not specific to it. The same design — a governed description of the data, view-first SQL, checks in code, and explanations grounded in the result — applies to any organisation that keeps its data in a warehouse. This section describes what that could look like.

### Live data, live answers

Connected to a live warehouse, QueryLens would answer questions about today rather than a fixed past period. "Last month" would mean last month. Answers to the same question would change as the business changes, and the prepared quick insights would refresh on a schedule. The checks would stay exactly as they are: every figure would still trace back to a query run at that moment.

### A governed semantic layer for the whole business

QueryLens already relies on a written description of the data: what each table and view contains, how each metric is defined, which source is canonical when two disagree, and what the data cannot answer. In an enterprise this becomes the contract between the data team and the rest of the business. Metric owners approve definitions, new views extend what QueryLens can answer, and every department gets the same answer to the same question. Improving QueryLens then means improving the shared definitions of the business, which benefits dashboards and reports as well.

### Richer data unlocks harder questions

Many questions QueryLens declines today are limits of the sample data, not of the product. With the right data behind it, the same pipeline could answer:

- **Profitability:** with costs and margins, margin by category, seller or region, and where growth is profitable.
- **Returns and service:** with returns, refunds and support tickets, which products come back, why customers contact support, and what that costs.
- **Marketing:** with campaign and spend data, acquisition cost, return on ad spend, and which channels bring customers who stay.
- **Operations:** with inventory and logistics data, stock-outs, carrier performance and delivery risk before it reaches the customer.

Each new source needs only an entry in the data description and, where useful, a validated view. The safety and checking steps do not change.

### Enterprise readiness

- **Access control:** single sign-on, and permissions that follow the user, so each person sees only the data they are allowed to see (for example, a regional manager sees their region).
- **Audit trail:** every question, the SQL that ran, the result and the explanation already exist for each answer; storing them gives a complete record of what was asked and what was answered.
- **Where people already work:** answers delivered in team chat, email or existing dashboards, and shared as links rather than screenshots.

### From answering questions to watching the business

Today QueryLens answers when asked. The same pipeline could run questions on a schedule and raise an alert when something changes, such as an on-time delivery rate falling below target in one region or a category's revenue dropping against the same month last year. Users could save answers, follow them over time, and build a personal set of the questions that matter to their role.

### Better answers over time

- **More done in code:** differences, growth rates and comparisons computed before the model writes a word, so explanations can only quote them.
- **Clearer uncertainty:** where the data allows, ranges and sample sizes shown alongside averages, so a figure based on a handful of orders is clearly marked as such.
- **Learning from use:** answers that users mark as unhelpful go to a review queue; each confirmed problem becomes a new test case, and the evaluation set grows with real questions.
- **Model choice:** the model can be swapped or combined across providers for reliability and cost, with the evaluation set as the acceptance test before any change goes live.
- **Longer conversations, kept traceable:** a multi-turn conversation where each answer still shows exactly which earlier questions and results it built on.

### What this means for an organisation

Most analytics teams spend much of their time on small, repeated requests: one number, one breakdown, one comparison. QueryLens lets business users get those answers themselves, with the SQL and data shown so they can trust what they see. Analysts are freed to build the views, definitions and deeper analyses that make every later answer better. The product does not replace analytical judgment; it puts governed data within reach of the people who need it, and makes every answer checkable.

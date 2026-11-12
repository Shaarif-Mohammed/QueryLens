"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Interface

    streamlit run app/main.py

Two views on one page:
- Landing: the big animated logo, welcome line, headline and description, then the
  ask bar and quick insights. The entrance plays once per visit.
- Answer: the logo moves to a slim strip pinned at the top with the ask bar; below it,
  the explanation beside the chart, then tabs for the table, the SQL and how the
  answer was produced, then follow-up suggestions and quick insights.

A typed question runs the full pipeline (question -> SQL -> safety checks ->
warehouse -> result -> explanation) and the page shows each real step while it runs.
Quick insights are key questions answered ahead of time by the same pipeline; they
open at once and look exactly like any other answer.

The rules (prepared-answer lookup, the session limit, one-turn follow-up memory) live
in app/answers.py and app/session.py; the look lives in app/ui/.
"""

from __future__ import annotations

import html
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from app.answers import (  # noqa: E402
    CACHE_PATH,
    EXAMPLES,
    Answer,
    AnswerCache,
    explanation_view,
    from_pipeline,
    normalise,
    pill_examples,
    suggestions_for,
)
from app.pipeline import MESSAGE_PARTS, MESSAGES, answer_question, explain_answer  # noqa: E402
from app.session import (  # noqa: E402
    CAP_MESSAGE,
    AnswerHistory,
    SessionState,
    Visit,
    plan_question,
    resolve_address,
    session_cap,
)
from app.ui import brand  # noqa: E402
from app.ui.styles import ARRIVAL_CSS, BASE_CSS, INTRO_CSS, style_figure  # noqa: E402
from app.visualization.charts import build_chart  # noqa: E402
from app.visualization.formatting import column_title, display_table, format_value, pretty_sql, readable_names  # noqa: E402

logger = logging.getLogger(__name__)

st.set_page_config(page_title="QueryLens", page_icon=":material/query_stats:", layout="wide",
                   initial_sidebar_state="collapsed")

PLACEHOLDER = "What would you like to know about the marketplace?"
WIDE_CHARTS = {"heatmap"}  # too dense for half the page
STEP_NAMES = {"generation": "Writing the SQL", "validation": "Safety checks",
              "execution": "Running the query", "explanation": "Writing the summary"}
MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$])")
LABEL_TO_QUESTION = {e.label: e.question for e in EXAMPLES}


def plain(text: str | None) -> str:
    """Text for a button label, shown exactly as written (no markdown, links or maths)."""
    return MARKDOWN_SPECIAL.sub(r"\\\1", text or "")


# --------------------------------------------------------------------------- state

@st.cache_resource
def saved_answers() -> AnswerCache:
    return AnswerCache.load(CACHE_PATH)


def session() -> SessionState:
    if "session" not in st.session_state:
        st.session_state.session = SessionState()
    return st.session_state.session


def history() -> AnswerHistory:
    if "history" not in st.session_state:
        st.session_state.history = AnswerHistory()
    return st.session_state.history


def ask(question: str, previous: dict[str, str] | None = None) -> None:
    """Callback: answer this question on the next run, show it in the ask bar, and put
    it in the page address (which adds a browser-history entry)."""
    st.session_state.pending = (question, previous)
    st.session_state.question_box = question
    st.session_state.q = question


def go_home() -> None:
    """Callback: back to the landing page. Clearing the address adds a history entry,
    so the browser's back button returns to the answer."""
    st.session_state.q = ""
    st.session_state.question_box = ""
    st.session_state.pop("pending", None)
    session().clear_follow_up()


def ask_typed() -> None:
    text = (st.session_state.get("question_box") or "").strip()
    if text:
        st.session_state.pending = (text, session().previous)
        st.session_state.q = text


def pick_pill(key: str) -> None:
    label = st.session_state.get(key)
    st.session_state[key] = None
    if label in LABEL_TO_QUESTION:
        ask(LABEL_TO_QUESTION[label], None)


# --------------------------------------------------------------------------- page

def main() -> None:
    st.html(BASE_CSS)
    # The question on screen lives in the page address (?q=...). Streamlit adds a browser
    # history entry when it changes and restores it on back/forward, so the browser's own
    # buttons move between answers. The widget is hidden; it only carries the address.
    with st.container(key="address"):
        st.text_input("Question in the address", key="q", bind="query-params", label_visibility="collapsed")

    cap, state, cache = session_cap(), session(), saved_answers()
    pending = st.session_state.pop("pending", None)
    visit = None
    if not pending:
        current = st.session_state.get("current")
        address = st.session_state.get("q") or ""
        if current is not None and normalise(current.question) == normalise(address):
            visit = Visit("shown", current)
        else:
            visit = resolve_address(address, history(), cache)
        if visit.kind == "landing":
            if current is not None:  # came back to the start: clear the bar and the follow-up context
                st.session_state.question_box = ""
                state.clear_follow_up()
            st.session_state.current = None
            landing()
            return
        moved = current is None or normalise(current.question) != normalise(address)
        if moved:
            # Back/forward or an opened link: the bar shows that question, and follow-ups
            # build on the answer now on screen. (Not on other reruns, so a half-typed
            # question in the bar is never overwritten.)
            st.session_state.question_box = address
            st.session_state.current = visit.answer
            if visit.answer is not None:
                state.previous = visit.answer.follow_up_context()

    if not st.session_state.get("arrived"):
        st.html(ARRIVAL_CSS)
        st.session_state.arrived = True
    with st.container(key="topstrip"):
        home, logo, bar = st.columns([0.32, 1, 4.5], vertical_alignment="center", gap="small")
        with home:
            st.button("", icon=":material/home:", key="home", help="Home", on_click=go_home, type="tertiary")
        logo.html(brand.small_brand())
        with bar:
            ask_form()

    if pending:
        answer = run(pending, cache, state, cap)
        if answer is not None:
            st.session_state.current = answer
            history().add(answer)
    elif visit.kind == "shown":
        show_answer(visit.answer)
    else:
        st.html(brand.message_box("Ready when you are",
                                  "Press Ask to run this question on the warehouse."))

    quick_insights("answer")
    st.html(brand.footer())


def landing() -> None:
    play = not st.session_state.get("intro_played")
    if play:
        st.html(INTRO_CSS)
        st.session_state.intro_played = True
    st.html(brand.landing_header(play))
    with st.container(key="landing-ask"):
        ask_form()
    with st.container(key="landing-pills"):
        quick_insights("landing")
    st.html(brand.footer())


def ask_form() -> None:
    with st.container(key="askform"):
        with st.form("ask", border=False, clear_on_submit=False):
            text, button = st.columns([8, 1.2], vertical_alignment="center", gap="small")
            text.text_input("Question", key="question_box", max_chars=1000, placeholder=PLACEHOLDER,
                            label_visibility="collapsed")
            button.form_submit_button("Ask", type="primary", on_click=ask_typed)


def quick_insights(where: str) -> None:
    st.html('<div class="ql-section ql-center">Quick insights</div>')
    first = f"pills-{where}-first"
    st.pills("Quick insights", [e.label for e in pill_examples(default=True)], key=first,
             label_visibility="collapsed", on_change=pick_pill, args=(first,))
    rest = f"pills-{where}-rest"
    with st.container(key=f"more-{where}"):
        with st.expander("More"):
            st.pills("More quick insights", [e.label for e in pill_examples(default=False)], key=rest,
                     label_visibility="collapsed", on_change=pick_pill, args=(rest,))


# --------------------------------------------------------------------------- answering

def run(pending: tuple[str, dict | None], cache: AnswerCache, state: SessionState, cap: int) -> Answer | None:
    """Answer one question, showing each real step while it runs. Returns the answer to
    keep on screen, or None to keep the previous one."""
    question, previous = pending
    plan = plan_question(question, previous, cache, state, cap)
    if plan.kind == "capped":
        st.html(brand.message_box(*CAP_MESSAGE))
        return None
    if plan.kind == "cached":
        state.record(plan.answer, live=False)
        show_answer(plan.answer)
        return plan.answer

    progress = st.empty()
    progress.html(brand.loading("generation"))
    try:
        result = answer_question(question, previous, on_step=lambda step: progress.html(brand.loading(step)))
        answer = from_pipeline(result, previous)
        state.record(answer, live=True)
        progress.empty()
        if answer.status == "invalid_question":
            show_stopped(answer)
            return None
        if not answer.ok:
            show_stopped(answer)
            show_suggestions(answer)
            return answer

        chart = chart_for(answer)
        if chart.kind in WIDE_CHARTS:  # explanation above, chart across the page
            writing = st.empty()
            show_chart(answer, chart)
        else:
            left, right = st.columns([1, 1.15], gap="large")
            with right:
                show_chart(answer, chart)
            with left:
                writing = st.empty()
        writing.html(brand.loading("explanation", compact=True))
        explain_answer(result)
        answer.explanation = explanation_view(result.explanation)
        answer.timings = dict(result.timings)
        with writing.container():
            show_explanation(answer)
        show_tabs(answer)
        show_suggestions(answer)
        return answer
    except Exception:  # noqa: BLE001 — the page must never show a traceback
        logger.exception("Interface error while answering: %s", question[:80])
        progress.empty()
        st.html(brand.message_box("Something went wrong on QueryLens's side",
                                  "Please try again in a moment. You can keep exploring the quick insights below."))
        return None


def show_answer(answer: Answer) -> None:
    """An answer already in hand (a quick insight, or kept from an earlier run)."""
    if not answer.ok:
        show_stopped(answer)
        show_suggestions(answer)
        return
    chart = chart_for(answer)
    if chart.kind in WIDE_CHARTS:  # explanation above, chart across the page
        show_explanation(answer)
        show_chart(answer, chart)
    else:
        left, right = st.columns([1, 1.15], gap="large")
        with left:
            show_explanation(answer)
        with right:
            show_chart(answer, chart)
    show_tabs(answer)
    show_suggestions(answer)


def show_stopped(answer: Answer) -> None:
    title, body = MESSAGE_PARTS.get(answer.message_key, ("QueryLens couldn't answer this one", answer.message))
    if answer.message_key == "cannot_answer":
        body = answer.message  # the model's reason: what the data does not record
    st.html(brand.message_box(title, body))


def show_explanation(answer: Answer) -> None:
    e = answer.explanation
    if e is None:
        return
    if not e.ok:
        st.html(brand.inline_note(MESSAGES["explanation_failed"]))
        return
    df = answer.dataframe
    st.html(brand.headline(readable_names(e.headline, df))
            + brand.summary(readable_names(e.text, df), [readable_names(c, df) for c in e.caveats]))
    if e.flagged:
        st.html(brand.inline_note(MESSAGES["explanation_flagged"]))


def chart_for(answer: Answer):
    return build_chart(answer.dataframe, answer.profile)


def show_chart(answer: Answer, chart=None) -> None:
    df, profile = answer.dataframe, answer.profile
    with st.container(key="chartpanel"):
        if df is None or df.empty:
            st.html('<div class="ql-quiet">No rows matched this question. Widening it, for example to more '
                    "months or all categories, usually helps.</div>")
            return
        chart = chart or build_chart(df, profile)
        if chart.figure is not None:
            st.plotly_chart(style_figure(chart.figure), width="stretch", theme=None,
                            config={"displayModeBar": False})
        elif len(df) == 1 and profile is not None and profile.primary_measure:
            st.html(kpi_card(df, profile))
        else:
            st.html(f'<div class="ql-quiet">{html.escape(chart.reason)} The full result is in the Table tab below.</div>')


def kpi_card(df, profile) -> str:
    measure = profile.primary_measure
    row = df.iloc[0]
    details = [f"{column_title(c, profile)}: {format_value(c, row[c], profile)}"
               for c in df.columns if c != measure and profile.role(c) in {"count", "measure", "percent"}][:2]
    return brand.kpi(column_title(measure, profile), format_value(measure, row[measure], profile), details)


TINY_BASE_NOTE = "– marks growth measured against a period with almost no activity, which is not meaningful."


def show_tabs(answer: Answer) -> None:
    table, sql, how = st.tabs(["Table", "SQL", "How this was answered"])
    with table:
        df = answer.dataframe
        if df is not None and not df.empty:
            st.dataframe(display_table(df, answer.profile), hide_index=True, width="stretch")
            if answer.truncated:
                st.caption(f"Showing the first {len(df)} rows; the full result is larger.")
            if answer.profile is not None and answer.profile.tiny_base:
                st.caption(TINY_BASE_NOTE)
        else:
            st.caption("No rows.")
    with sql:
        st.code(pretty_sql(answer.sql), language="sql", wrap_lines=True)
    with how:
        st.html(how_html(answer))


def how_html(answer: Answer) -> str:
    """The same account for every answer: measure, choices, data used, checks, step times."""
    esc = html.escape
    parts = []
    if answer.measure_used:
        parts.append(f"<p><b>Measure</b><br>{esc(answer.measure_used)}</p>")
    if answer.assumptions:
        items = "".join(f"<li>{esc(a)}</li>" for a in answer.assumptions)
        parts.append(f"<p><b>Choices made when writing the query</b></p><ul>{items}</ul>")
    if answer.sources:
        parts.append(f"<p><b>Data used</b><br>{esc(', '.join(answer.sources))}</p>")
    for warning in answer.warnings:
        parts.append(f"<p><b>Safety check note</b><br>{esc(warning)}</p>")
    e = answer.explanation
    if e is not None and e.ok:
        if e.ungrounded_numbers:
            check = "Not matched to the result: " + esc(", ".join(e.ungrounded_numbers))
        else:
            check = "Every figure in the summary was matched to the result."
        if e.regenerated:
            check += " The summary was rewritten once to fix a figure or a phrase."
        if e.banned_terms:
            check += " Wording such as " + esc(", ".join(e.banned_terms)) + " suggests a statistical test; none was run."
        parts.append(f"<p><b>Number check</b><br>{check}</p>")
    if answer.timings:
        steps = " · ".join(f"{STEP_NAMES.get(k, k)} {v:.1f} s" for k, v in answer.timings.items())
        parts.append(f'<div class="ql-steps">{esc(steps)}</div>')
    return f'<div class="ql-detail">{"".join(parts)}</div>'


def show_suggestions(answer: Answer) -> None:
    suggestions = suggestions_for(answer)
    if not suggestions:
        return
    st.html(brand.section_label("Follow up" if answer.ok else "Questions the data can answer"))
    context = answer.follow_up_context()
    with st.container(key="followups"):
        for i, question in enumerate(suggestions):
            st.button(plain(question) + "  →", key=f"followup_{i}", on_click=ask, args=(question, context),
                      width="stretch")


main()

"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Brand and page fragments

The logo, the landing header, the loading lens, message boxes and other small pieces
of the page, as HTML strings for st.html. Pure functions: every piece of text that
comes from a question or a model is escaped here, so nothing in it is ever rendered
as markup.

Animations are CSS only (st.html does not run scripts). The landing entrance plays
once per visit; afterwards the same header is drawn without it.
"""

from __future__ import annotations

import base64
import html
import re

# Figures the headline may lead with: "BRL 5.77 million", "3.0%", "26,165".
FIGURE = re.compile(
    r"(?:BRL\s?)?\d[\d,]*(?:\.\d+)?(?:\s?(?:%|million|billion|thousand|[kKM]\b))?"
)

WELCOME = "Welcome to QueryLens — AI-Powered Marketplace Analytics Copilot"
HEADLINE = "Your marketplace, one question away"
DESCRIPTION = ("A natural-language analytics copilot for marketplace teams. Checked SQL, real warehouse "
               "data and plain-English explanations, in one place.")
DATA_SCOPE = "Olist marketplace · Sep 2016 – Aug 2018 · BRL"

STEP_STATUS = {
    "generation": "Writing the SQL…",
    "validation": "Checking it's safe…",
    "execution": "Running it on the warehouse…",
    "explanation": "Writing the summary…",
}

# Typing schedule for "QueryLens" (ms from the start of typing): ~250 ms a letter,
# a longer pause between the two words.
_TYPING = [("Q", 0), ("u", 260), ("e", 500), ("r", 760), ("y", 1000),
           ("L", 1500), ("e", 1760), ("n", 2000), ("s", 2260)]
_TYPING_START_MS = 1100  # after the lens has drawn itself


_LENS_CSS = {
    # Bars rise and fall in turn; always on.
    "idle": ".b{transform-box:fill-box;transform-origin:bottom;animation:r 2.4s ease-in-out infinite}"
            "@keyframes r{0%,100%{transform:scaleY(.45)}50%{transform:scaleY(1)}}",
    # Faster bars while a question is being answered.
    "busy": ".b{transform-box:fill-box;transform-origin:bottom;animation:r 1.1s ease-in-out infinite}"
            "@keyframes r{0%,100%{transform:scaleY(.35)}50%{transform:scaleY(1)}}",
    # First view: the lens appears from a blur, the ring and handle draw, the bars pop up.
    "intro": "@keyframes m{0%{opacity:0;transform:scale(.6);filter:blur(3px)}100%{opacity:1;transform:none;filter:none}}"
             "@keyframes d{from{stroke-dashoffset:88}to{stroke-dashoffset:0}}"
             "@keyframes h{from{stroke-dashoffset:14}to{stroke-dashoffset:0}}"
             "@keyframes p{from{transform:scaleY(0)}to{transform:scaleY(1)}}"
             "@keyframes r{0%,100%{transform:scaleY(.45)}50%{transform:scaleY(1)}}"
             ".g{transform-box:fill-box;transform-origin:center;animation:m .7s ease-out both}"
             ".o{stroke-dasharray:88;animation:d .7s ease-out both}"
             ".k{stroke-dasharray:14;animation:h .25s ease-out .6s both}"
             ".b{transform-box:fill-box;transform-origin:bottom;"
             "animation:p .3s ease-out var(--p) both,r 2.4s ease-in-out calc(var(--p) + .3s) infinite}",
}
_REDUCED = "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"


def lens_svg(mode: str = "idle") -> str:
    """The lens-and-bars mark as a standalone SVG with its own animation."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="2 2 40 40">'
        f"<style>{_LENS_CSS[mode]}{_REDUCED}</style>"
        '<g class="g"><circle class="o" cx="19" cy="19" r="14" fill="rgba(91,140,255,0.10)" stroke="#A9C3FF" '
        'stroke-width="2.5" transform="rotate(-90 19 19)"/>'
        '<line class="k" x1="29.5" y1="29.5" x2="39" y2="39" stroke="#A9C3FF" stroke-width="3.5" stroke-linecap="round"/>'
        '<rect class="b" style="--p:.75s" x="12" y="18" width="3.5" height="8" rx="1" fill="#2F4F9E"/>'
        '<rect class="b" style="--p:.85s;animation-delay:.3s" x="17.3" y="14" width="3.5" height="12" rx="1" fill="#5B8CFF"/>'
        '<rect class="b" style="--p:.95s;animation-delay:.6s" x="22.6" y="11" width="3.5" height="15" rx="1" fill="#A9C3FF"/>'
        "</g></svg>"
    )


def _lens(size: int, mode: str = "idle") -> str:
    """The mark as an <img>: st.html only allows HTML, so inline SVG would be removed."""
    data = base64.b64encode(lens_svg(mode).encode()).decode()
    return f'<img class="ql-mark" alt="" width="{size}" height="{size}" src="data:image/svg+xml;base64,{data}">'


def _wordmark(typed: bool) -> str:
    letters = []
    for i, (ch, t) in enumerate(_TYPING):
        lens = i >= 5
        classes = ["ql-l"] + (["ql-g"] if lens else []) + (["ql-last"] if i == len(_TYPING) - 1 else [])
        nxt = _TYPING[i + 1][1] if i + 1 < len(_TYPING) else None
        style = f"--t:{t + _TYPING_START_MS}ms;--w:{(i - 5) * 0.18 if lens else 0:.2f}s"
        if nxt is not None:
            style += f";--d:{nxt - t}ms"
        letters.append(f'<span class="{" ".join(classes)}" style="{style}">{ch}</span>')
    return f'<span class="ql-wm{" ql-typed" if typed else ""}">{"".join(letters)}</span>'


def landing_header(play_intro: bool) -> str:
    """Big logo, welcome line, headline and description, centred."""
    intro = " ql-intro" if play_intro else ""
    mark = _lens(92, "intro" if play_intro else "idle")
    return (
        f'<div class="ql-hero{intro}">'
        f'<div class="ql-brand ql-brand-lg">{mark}{_wordmark(play_intro)}</div>'
        f'<div class="ql-welcome">{html.escape(WELCOME)}</div>'
        f'<h1 class="ql-headline">{html.escape(HEADLINE)}</h1>'
        f'<p class="ql-description">{html.escape(DESCRIPTION)}</p>'
        "</div>"
    )


def small_brand() -> str:
    """The small logo for the top strip once a question has been asked."""
    return f'<div class="ql-brand ql-brand-sm">{_lens(30)}{_wordmark(False)}</div>'


def loading(step: str, compact: bool = False) -> str:
    """The lens with busy bars and the current real step."""
    status = html.escape(STEP_STATUS.get(step, "Working…"))
    size = 34 if compact else 64
    cls = "ql-loading ql-loading-sm" if compact else "ql-loading"
    mark = _lens(size, "busy")
    return f'<div class="{cls}" role="status">{mark}<div class="ql-status">{status}</div></div>'


def message_box(title: str, body: str) -> str:
    """A calm notice: short bold line, then what to do next."""
    return (
        '<div class="ql-msg" role="note"><span class="ql-i" aria-hidden="true">i</span><div>'
        f'<div class="ql-msg-title">{html.escape(title)}</div>'
        f'<div class="ql-msg-body">{html.escape(body)}</div></div></div>'
    )


def inline_note(text: str) -> str:
    return f'<div class="ql-note"><span class="ql-i ql-i-sm" aria-hidden="true">i</span>{html.escape(text)}</div>'


def headline(text: str) -> str:
    """The answer's headline with its first figure picked out in the highlight colour."""
    text = text or ""
    # A real figure has a unit, decimals or a thousands separator; "top 10", "at least
    # 50" and years are counting words, not the answer.
    match = next((m for m in FIGURE.finditer(text)
                  if re.search(r"BRL|%|million|billion|thousand|[kKM]$|\d[.,]\d", m.group(0))), None)
    if not match:
        return f'<h2 class="ql-answer-headline">{html.escape(text)}</h2>'
    before, figure, after = text[:match.start()], match.group(0), text[match.end():]
    return (
        f'<h2 class="ql-answer-headline">{html.escape(before)}'
        f'<span class="ql-figure">{html.escape(figure)}</span>{html.escape(after)}</h2>'
    )


def summary(text: str, caveats: list[str]) -> str:
    parts = [f'<p class="ql-summary">{html.escape(text or "")}</p>']
    if caveats:
        items = "".join(f"<li>{html.escape(c)}</li>" for c in caveats)
        parts.append(f'<div class="ql-keep"><div class="ql-keep-title">Keep in mind</div><ul>{items}</ul></div>')
    return "".join(parts)


def kpi(label: str, value: str, details: list[str] | None = None) -> str:
    """One figure, large, for single-row answers."""
    extra = "".join(f'<div class="ql-kpi-detail">{html.escape(d)}</div>' for d in (details or []))
    return (f'<div class="ql-kpi"><div class="ql-kpi-label">{html.escape(label)}</div>'
            f'<div class="ql-kpi-value">{html.escape(value)}</div>{extra}</div>')


def section_label(text: str) -> str:
    return f'<div class="ql-section">{html.escape(text)}</div>'


def footer() -> str:
    return f'<div class="ql-footer">{html.escape(DATA_SCOPE)}</div>'

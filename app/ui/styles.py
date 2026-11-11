"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Page styling

The look of the page on top of the theme in .streamlit/config.toml: the drifting
glow, frosted panels and their sheen, the pill-shaped ask bar and quick insights,
the pinned top strip, and the logo animations.

Streamlit elements are targeted by their data-testid and by container keys
(st.container(key="x") adds the class "st-key-x"). These selectors depend on
Streamlit's page structure, which can change between Streamlit versions.
"""

from __future__ import annotations

INK = {
    "page": "#0A0E18", "panel": "#121A2B", "line": "#1F2A40",
    "text": "#E8ECF5", "body": "#B9C2D6", "muted": "#8A96B0", "hint": "#6C7894",
    "accent": "#5B8CFF", "light": "#A9C3FF", "lens": "#7FA4FF", "deep": "#2F4F9E",
}
DATA_RAMP = ["#5B8CFF", "#A9C3FF", "#2F4F9E"]
FONT = "'Plus Jakarta Sans', sans-serif"

BASE_CSS = """
<style>
@keyframes qlDrift{0%{background-position:0% 0%,100% 100%,0 0}50%{background-position:100% 30%,0% 70%,0 0}100%{background-position:0% 0%,100% 100%,0 0}}
@keyframes qlSheen{0%{left:-60%}60%,100%{left:130%}}
@keyframes qlRise{0%,100%{transform:scaleY(.45)}50%{transform:scaleY(1)}}
@keyframes qlBusy{0%,100%{transform:scaleY(.35)}50%{transform:scaleY(1)}}
@keyframes qlWave{0%,70%,100%{color:#7FA4FF;text-shadow:none}35%{color:#E8ECF5;text-shadow:0 0 12px rgba(169,195,255,.55)}}
@keyframes qlFadeUp{0%{opacity:0;transform:translateY(8px)}100%{opacity:1;transform:none}}

[data-testid="stApp"]{
  background:
    radial-gradient(circle at 80% 0%, rgba(91,140,255,.30), transparent 45%),
    radial-gradient(circle at 10% 100%, rgba(47,79,158,.40), transparent 50%),
    #0A0E18;
  background-size:180% 180%,180% 180%,100% 100%;
  background-attachment:fixed;
  animation:qlDrift 14s ease-in-out infinite;
}
[data-testid="stHeader"]{display:none;}
.st-key-address,div:has(> .st-key-address){display:none!important;}
[data-testid="stMainBlockContainer"]{max-width:1120px;padding-top:0;padding-bottom:3rem;}

/* logo */
.ql-brand{display:inline-flex;align-items:center;gap:8px;}
.ql-mark{flex:none;}
.ql-wm{font-family:'Plus Jakarta Sans',sans-serif;font-weight:800;letter-spacing:-.3px;color:#E8ECF5;line-height:1.05;white-space:nowrap;}
.ql-l{display:inline-block;position:relative;}
.ql-g{color:#7FA4FF;animation:qlWave 3.2s ease-in-out calc(1.5s + var(--w)) infinite;}
.ql-brand-lg .ql-wm{font-size:60px;}
.ql-brand-lg{gap:10px;}
.ql-brand-sm .ql-wm{font-size:18px;}
.ql-brand-sm{gap:6px;}

/* landing */
.ql-hero{text-align:center;padding:5vh 0 1.4rem;}
.ql-welcome{color:#A9C3FF;font-size:14px;font-weight:600;letter-spacing:.2px;margin-top:22px;}
.ql-headline{color:#E8ECF5;font-size:30px;font-weight:700;letter-spacing:-.4px;margin:10px 0 8px;padding:0;}
.ql-description{color:#8A96B0;font-size:15px;line-height:1.6;max-width:560px;margin:0 auto;}

/* ask bar: one pill holding the input and the button */
.st-key-askform [data-testid="stForm"]{border:none;padding:0;background:transparent;}
.st-key-askform [data-testid="stHorizontalBlock"]{
  border-radius:999px;border:1px solid rgba(169,195,255,.24);background:rgba(18,26,43,.6);
  backdrop-filter:blur(10px);padding:4px 5px 4px 18px;align-items:center;gap:6px;
  box-shadow:0 0 0 1px rgba(91,140,255,.0);transition:box-shadow .2s,border-color .2s;}
.st-key-askform [data-testid="stHorizontalBlock"]:focus-within{border-color:#5B8CFF;box-shadow:0 0 18px rgba(91,140,255,.25);}
.st-key-askform [data-testid="stTextInputRootElement"]{background:transparent;border:none;}
.st-key-askform [data-testid="InputInstructions"]{display:none;}
.st-key-askform [data-testid="stTextInputRootElement"] input{background:transparent;font-size:15px;color:#E8ECF5;}
.st-key-askform [data-testid="stTextInputRootElement"] > div{background:transparent;border:none;}
.st-key-askform [data-testid="stBaseButton-primaryFormSubmit"]{
  border-radius:999px;border:none;background:linear-gradient(180deg,#8FB0FF,#4F7DF0);color:#060A14;
  font-weight:700;padding:.45rem 1.4rem;width:100%;}
.st-key-askform [data-testid="stBaseButton-primaryFormSubmit"] p{color:#060A14;font-weight:700;}
.st-key-landing-ask{max-width:720px;margin:0 auto;}

/* quick insights */
.ql-section{color:#7FA4FF;font-size:12.5px;font-weight:600;margin:1.1rem 0 .4rem;}
.ql-center{text-align:center;}
[data-testid="stElementContainer"]:has(> [data-testid="stButtonGroup"]){align-self:center;}
[data-testid="stButtonGroup"] [role="radiogroup"]{justify-content:center;}
[data-testid="stBaseButton-pills"],[data-testid="stBaseButton-pillsActive"],[data-testid="stButtonGroup"] button[data-variant="pills"]{
  border-radius:999px!important;border:1px solid rgba(169,195,255,.28)!important;background:rgba(18,26,43,.45)!important;color:#B9C2D6!important;
  backdrop-filter:blur(6px);}
[data-testid="stButtonGroup"] button[data-variant="pills"]:hover{border-color:#5B8CFF!important;color:#E8ECF5!important;background:rgba(91,140,255,.14)!important;}
[class*="st-key-more-"] [data-testid="stExpander"],[class*="st-key-more-"] details{border:none!important;background:transparent!important;box-shadow:none!important;}
[class*="st-key-more-"] summary{background:transparent!important;width:fit-content;margin:0 auto;color:#8A96B0;font-size:13px;padding:.15rem .6rem!important;border-radius:999px;}
[class*="st-key-more-"] summary:hover{color:#E8ECF5;background:rgba(91,140,255,.10)!important;}
[class*="st-key-more-"] [data-testid="stExpanderDetails"]{padding-top:.3rem;}
[class*="st-key-more-"] [data-testid="stExpanderDetails"] [data-testid="stVerticalBlock"]{align-items:center;}
.st-key-landing-pills,.st-key-landing-ask{max-width:760px;margin-left:auto;margin-right:auto;}

/* pinned top strip after a question */
/* Streamlit wraps each container in a box of its own height, so the wrapper is what sticks */
div:has(> .st-key-topstrip){position:sticky;top:0;z-index:50;}
.st-key-topstrip{background:rgba(10,14,24,.86);backdrop-filter:blur(14px);
  margin:0 0 1.4rem;padding:.8rem 0 .7rem;border-bottom:1px solid rgba(169,195,255,.12);
  box-shadow:0 0 0 100vmax rgba(10,14,24,.86);clip-path:inset(0 -100vmax);}

/* home button in the top strip */
.st-key-home button{width:38px;height:38px;min-height:38px;padding:0;border-radius:999px;
  border:1px solid rgba(169,195,255,.28);background:rgba(18,26,43,.5);color:#A9C3FF;}
.st-key-home button:hover{border-color:#5B8CFF;background:rgba(91,140,255,.14);color:#E8ECF5;}

/* answer */
.ql-answer-headline{color:#E8ECF5;font-size:24px;font-weight:700;line-height:1.3;letter-spacing:-.3px;margin:0 0 .6rem;padding:0;}
.ql-figure{color:#A9C3FF;}
.ql-summary{color:#B9C2D6;font-size:15px;line-height:1.7;margin:0;}
.ql-keep{margin-top:1rem;}
.ql-keep-title{color:#7FA4FF;font-size:12.5px;font-weight:600;margin-bottom:.25rem;}
.ql-keep ul{margin:0;padding-left:1.1rem;color:#8A96B0;font-size:13.5px;line-height:1.6;}
.st-key-chartpanel{position:relative;overflow:hidden;border-radius:14px;border:1px solid rgba(169,195,255,.18);
  background:linear-gradient(180deg,rgba(169,195,255,.06),rgba(18,26,43,.6));padding:.6rem .7rem;}
.st-key-chartpanel::after{content:"";position:absolute;top:0;bottom:0;width:40%;pointer-events:none;
  background:linear-gradient(100deg,transparent,rgba(197,210,230,.10),transparent);animation:qlSheen 6s ease-in-out infinite;}
.ql-kpi{text-align:center;padding:2.2rem 1rem;}
.ql-kpi-label{color:#8A96B0;font-size:13px;}
.ql-kpi-value{color:#E8ECF5;font-size:46px;font-weight:800;letter-spacing:-1px;margin:.2rem 0;}
.ql-kpi-detail{color:#B9C2D6;font-size:13.5px;}
.ql-quiet{color:#6C7894;font-size:13px;text-align:center;padding:3rem 1rem;}
[data-testid="stTabs"] [role="tab"]{border-radius:999px;padding:.25rem .9rem;margin-right:.3rem;}
[data-testid="stTabs"] [data-baseweb="tab-highlight"],[data-testid="stTabs"] [data-baseweb="tab-border"]{display:none;}
[data-testid="stTabs"] [role="tab"][aria-selected="true"]{background:rgba(91,140,255,.18);border:1px solid #5B8CFF;}
.ql-detail{color:#B9C2D6;font-size:14px;line-height:1.65;}
.ql-detail b{color:#E8ECF5;font-weight:600;}
.ql-detail ul{margin:.2rem 0 .6rem;padding-left:1.1rem;}
.ql-steps{color:#6C7894;font-size:12.5px;margin-top:.6rem;}

/* follow-ups */
.st-key-followups button{width:100%;justify-content:space-between;text-align:left;border-radius:12px;
  border:1px solid rgba(169,195,255,.25);background:rgba(18,26,43,.5);padding:.55rem .9rem;}
.st-key-followups button:hover{border-color:#5B8CFF;background:rgba(91,140,255,.12);}
.st-key-followups button p{color:#E8ECF5;text-align:left;}
.st-key-followups button > div{justify-content:flex-start!important;width:100%;}

/* loading, messages, notes, footer */
.ql-loading{display:flex;flex-direction:column;align-items:center;gap:12px;padding:3.5rem 0;}
.ql-loading-sm{flex-direction:row;padding:1rem 0;gap:10px;justify-content:flex-start;}
.ql-status{color:#A9C3FF;font-size:14px;}
.ql-msg{display:flex;gap:12px;align-items:flex-start;border-radius:14px;border:1px solid rgba(169,195,255,.2);
  background:rgba(18,26,43,.6);padding:1rem 1.1rem;margin:.4rem 0 .6rem;}
.ql-i{width:20px;height:20px;border-radius:50%;border:1.5px solid #7FA4FF;color:#A9C3FF;font-size:12px;
  display:inline-flex;align-items:center;justify-content:center;flex:none;font-weight:700;}
.ql-i-sm{width:15px;height:15px;font-size:9.5px;margin-right:7px;}
.ql-msg-title{color:#E8ECF5;font-weight:600;font-size:15px;margin-bottom:2px;}
.ql-msg-body{color:#B9C2D6;font-size:14px;line-height:1.6;}
.ql-note{display:flex;align-items:center;color:#8A96B0;font-size:13px;margin-top:.8rem;}
.ql-footer{color:#6C7894;font-size:12px;text-align:center;margin-top:2.5rem;}

/* Laptop is the primary target; phones keep the same layout, scaled down. */
@media (max-width:640px){
  [data-testid="stMainBlockContainer"]{padding-left:1rem;padding-right:1rem;}
  .ql-hero{padding:3vh 0 1rem;}
  .ql-brand-lg .ql-wm{font-size:40px;}
  .ql-brand-lg img{width:64px;height:64px;}
  .ql-welcome{font-size:12.5px;margin-top:16px;}
  .ql-headline{font-size:23px;}
  .ql-description{font-size:14px;}
  .st-key-askform [data-testid="stHorizontalBlock"],.st-key-topstrip [data-testid="stHorizontalBlock"]{
    flex-wrap:nowrap!important;align-items:center;}
  .st-key-askform [data-testid="stHorizontalBlock"]{padding-left:14px;}
  .st-key-askform [data-testid="stColumn"],.st-key-topstrip [data-testid="stColumn"]{min-width:0!important;width:auto!important;}
  .st-key-askform [data-testid="stColumn"]:first-child,.st-key-topstrip [data-testid="stColumn"]:last-child{flex:1 1 auto!important;}
  .st-key-askform [data-testid="stColumn"]:last-child,.st-key-topstrip [data-testid="stColumn"]:not(:last-child){flex:0 0 auto!important;}
  .st-key-askform [data-testid="stBaseButton-primaryFormSubmit"]{padding:.4rem .8rem;min-height:2.2rem;}
  .st-key-askform [data-testid="stHorizontalBlock"]{padding-right:4px;}
  .st-key-askform [data-testid="stColumn"]:last-child{max-width:4.6rem;}
  .st-key-askform [data-testid="stTextInputRootElement"] input{font-size:13.5px;}
  .ql-brand-sm .ql-wm{display:none;}
  .ql-answer-headline{font-size:20px;}
  .ql-summary{font-size:14.5px;}
  .ql-kpi-value{font-size:36px;}
}

@media (prefers-reduced-motion:reduce){
  [data-testid="stApp"],.ql-g,.st-key-chartpanel::after{animation:none!important;}
}
</style>
"""

# Played only on the first view of the landing page.
INTRO_CSS = """
<style>
@keyframes qlAppear{to{opacity:1}}
@keyframes qlOn{0%,100%{opacity:1}}
@keyframes qlBlink{0%,49%{opacity:1}50%,100%{opacity:0}}
.ql-typed .ql-l{opacity:0;animation:qlAppear .01s linear var(--t) both;}
.ql-typed .ql-g{animation:qlAppear .01s linear var(--t) both,qlWave 3.2s ease-in-out calc(6.3s + var(--w)) infinite;}
.ql-typed .ql-l::after{content:"";position:absolute;right:-3px;top:5px;bottom:5px;width:3px;background:#5B8CFF;opacity:0;
  animation:qlOn var(--d) linear var(--t);}
.ql-typed .ql-last::after{animation:qlBlink .8s step-end var(--t) 3;}
@keyframes qlLift{0%{transform:translateY(var(--lift)) scale(1.25)}100%{transform:none}}
/* The logo starts larger in the middle of the screen; once the name is typed it lifts into place. */
.ql-intro .ql-brand-lg{--lift:calc(45vh - 46px);transform-origin:center;animation:qlLift 1.1s cubic-bezier(.65,0,.35,1) 3.7s both;}
.ql-intro .ql-welcome,.ql-intro .ql-headline,.ql-intro .ql-description{opacity:0;animation:qlFadeUp .8s ease-out both;}
.ql-intro .ql-welcome{animation-delay:4.6s;}
.ql-intro .ql-headline{animation-delay:4.8s;}
.ql-intro .ql-description{animation-delay:5.0s;}
.st-key-landing-ask{opacity:0;animation:qlFadeUp .8s ease-out 5.4s both;}
.st-key-landing-pills{opacity:0;animation:qlFadeUp .8s ease-out 5.7s both;}
.ql-footer{opacity:0;animation:qlFadeUp .8s ease-out 6.0s both;}
@media (max-width:640px){.ql-intro .ql-brand-lg{--lift:calc(42vh - 32px);}}
@media (prefers-reduced-motion:reduce){
  .ql-typed .ql-l,.ql-typed .ql-l::after,.ql-intro *,.ql-intro .ql-brand-lg,.st-key-landing-ask,.st-key-landing-pills,.ql-footer{
    animation:none!important;opacity:1!important;filter:none!important;transform:none!important;}
  .ql-typed .ql-l::after{opacity:0!important;}
}
</style>
"""

# Played once when the page switches from the landing view to an answer.
ARRIVAL_CSS = """
<style>
@keyframes qlSlideUp{0%{opacity:0;transform:translateY(60px)}100%{opacity:1;transform:none}}
@keyframes qlShrink{0%{opacity:0;transform:scale(1.8) translateX(40%)}100%{opacity:1;transform:none}}
.st-key-topstrip{animation:qlSlideUp .55s cubic-bezier(.2,.8,.3,1) both;}
.st-key-topstrip .ql-brand{animation:qlShrink .6s cubic-bezier(.2,.8,.3,1) both;}
@media (prefers-reduced-motion:reduce){.st-key-topstrip,.st-key-topstrip .ql-brand{animation:none!important;}}
</style>
"""


def style_figure(fig):
    """Midnight-ink look for a Plotly figure: transparent background, theme fonts,
    the blue data ramp, quiet grid lines."""
    if fig is None:
        return fig
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, color=INK["body"], size=13),
        colorway=DATA_RAMP,
        margin=dict(l=8, r=16, t=10, b=8),
        hoverlabel=dict(bgcolor=INK["panel"], bordercolor=INK["accent"], font=dict(family=FONT, color=INK["text"])),
    )
    fig.update_xaxes(gridcolor="rgba(169,195,255,.08)", zerolinecolor="rgba(169,195,255,.12)",
                     linecolor="rgba(169,195,255,.12)", title_font=dict(color=INK["muted"]))
    fig.update_yaxes(gridcolor="rgba(169,195,255,.08)", zerolinecolor="rgba(169,195,255,.12)",
                     linecolor="rgba(169,195,255,.12)", title_font=dict(color=INK["muted"]))
    for trace in fig.data:  # charts set their own colours; only fill in what is missing
        if trace.type in {"bar", "scatter"} and getattr(trace.marker, "color", None) is None:
            trace.marker.color = INK["accent"]
    return fig

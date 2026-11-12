"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Interface fragment tests

Offline. The page itself is checked by a manual run; these check the pieces that
carry text from a question or a model: everything is escaped, the headline's key
figure is picked out correctly, and progress shows real step names.
"""

import plotly.graph_objects as go

from app.ui import brand
from app.ui.styles import style_figure


def test_headline_highlights_the_first_figure():
    out = brand.headline("São Paulo leads with BRL 5.77 million in revenue")
    assert '<span class="ql-figure">BRL 5.77 million</span>' in out
    assert '<span class="ql-figure">3.0%</span>' in brand.headline("Only 3.0% of customers buy again")
    assert '<span class="ql-figure">26,165</span>' in brand.headline("Loyal is largest with 26,165 customers")
    assert "ql-figure" not in brand.headline("Delivery varies widely across states")


def test_model_and_question_text_is_escaped():
    out = brand.headline('<img src=x onerror=alert(1)> leads with 5%')
    assert "<img" not in out and "&lt;img" in out
    assert "<script>" not in brand.summary("<script>x</script>", ["<b>note</b>"])
    assert "<b>" not in brand.message_box("<b>t</b>", "<i>b</i>")
    assert "<u>" not in brand.inline_note("<u>n</u>")
    assert "<em>" not in brand.kpi("<em>l</em>", "1", ["<em>d</em>"])


def test_loading_names_the_real_step():
    assert "Writing the SQL" in brand.loading("generation")
    assert "Running it on the warehouse" in brand.loading("execution")
    assert "ql-loading-sm" in brand.loading("explanation", compact=True)


def test_landing_intro_only_when_asked():
    assert "ql-intro" in brand.landing_header(True) and "ql-typed" in brand.landing_header(True)
    assert "ql-intro" not in brand.landing_header(False) and "ql-typed" not in brand.landing_header(False)
    assert "Your marketplace, one question away" in brand.landing_header(False)


def test_wordmark_spells_querylens():
    import re
    letters = re.findall(r'class="ql-l[^"]*"[^>]*>(\w)<', brand.small_brand())
    assert "".join(letters) == "QueryLens"


def test_figures_get_the_dark_transparent_look():
    fig = style_figure(go.Figure(go.Bar(x=[1, 2], y=["a", "b"], orientation="h")))
    assert fig.layout.paper_bgcolor == "rgba(0,0,0,0)" and fig.data[0].marker.color == "#5B8CFF"
    assert style_figure(None) is None


def test_lens_is_an_image_so_the_page_sanitiser_keeps_it():
    import base64
    out = brand.small_brand()
    assert "<svg" not in out and 'src="data:image/svg+xml;base64,' in out
    svg = base64.b64decode(out.split("base64,")[1].split('"')[0]).decode()
    assert svg.startswith("<svg") and "prefers-reduced-motion" in svg
    assert "1.1s" in brand.lens_svg("busy") and "stroke-dasharray:88" in brand.lens_svg("intro")


def test_headline_skips_counting_words_and_years():
    out = brand.headline("Monthly revenue for the top 10 categories in 2017 peaked at BRL 104,964 in November")
    assert '<span class="ql-figure">BRL 104,964</span>' in out and '<span class="ql-figure">10</span>' not in out
    out = brand.headline("Among sellers with at least 50 delivered orders, on-time rates sit near 93.2%")
    assert '<span class="ql-figure">93.2%</span>' in out
    assert "ql-figure" not in brand.headline("The top 3 states lead in 2018")


def test_headline_skips_a_year_followed_by_a_comma():
    from app.ui.brand import headline
    out = headline("In 2017, revenue peaked at BRL 727,824.17 in November.")
    assert '<span class="ql-figure">BRL 727,824.17</span>' in out

"""Guard for the HTML-email number-spacing fix.

HTML-email invoices (e.g. Hetzner) put numbers in cells carrying inline
``letter-spacing`` + a smaller ``font-size``. The email sanitizer strips
font-family but keeps letter-spacing / word-spacing / font-size, and the
single-email reader path ``.email-reader-body.html-body`` has no descendant
typography reset like the thread bubble path does, so digit runs render
tracked-out and re-sized (the prose, which has no such inline styles, looks
fine). fork.css neutralizes the spacing + size for that reader path.

Source guards — exercising real rendering needs a headless browser; the rule
is declarative, so we assert it's present, correctly scoped, and that the
sanitizer indeed lets these props through (which is *why* the CSS reset is the
fix rather than a sanitizer change).
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_fork_css_resets_email_reader_number_spacing():
    css = (ROOT / "static" / "fork.css").read_text(encoding="utf-8")
    # Scoped to the single-email HTML reader path (not bubbles / plain text).
    assert ".email-reader-body.html-body *:not(code):not(pre):not(kbd):not(samp)" in css
    # Neutralizes the per-digit gap (the decisive symptom) ...
    assert "letter-spacing: normal !important" in css
    assert "word-spacing: normal !important" in css
    # ... and the shrunk numeric font-size.
    assert "font-size: inherit !important" in css


def test_sanitizer_passes_spacing_props_through():
    # The fix lives in CSS precisely because the sanitizer does NOT strip these
    # inline props; this documents that contract (if a future change adds them
    # to STRIP_CSS_PROPS the CSS reset becomes redundant-but-harmless).
    utils = (ROOT / "static" / "js" / "emailLibrary" / "utils.js").read_text(encoding="utf-8")
    assert "STRIP_CSS_PROPS" in utils
    # Find the STRIP_CSS_PROPS literal block and confirm it omits the spacing/size props.
    start = utils.index("STRIP_CSS_PROPS")
    block = utils[start:start + 300]
    for not_stripped in ("letter-spacing", "word-spacing", "font-size"):
        assert not_stripped not in block, f"{not_stripped} is now stripped — the CSS reset may be redundant"

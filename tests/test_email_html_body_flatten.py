"""Scheduled-send / agent-draft emails carry a PLAIN-TEXT body.

Regression: when an LLM ignored the "plain text body" tool contract and wrapped
the body in `<html><body>…</body></html>`, the scheduled-send poller shipped the
raw tags in the text/plain part AND HTML-escaped them into the text/html part —
so the recipient saw the literal markup `<html><body>Hello this is a test</body>
</html>` in BOTH parts (a plain client showed the raw string; an HTML client
rendered the escaped `&lt;html&gt;…` entities as visible text).

`_plaintext_email_body` flattens an accidentally-HTML body back to text so both
MIME parts render cleanly; plain bodies pass through untouched.
"""
import base64

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import html as _html

from routes.email_helpers import _plaintext_email_body


def test_plain_body_passes_through_unchanged():
    assert _plaintext_email_body("Hello this is a test") == "Hello this is a test"
    # Plain text with a stray angle bracket but no real tag stays intact.
    assert _plaintext_email_body("use a < b and c > d") == "use a < b and c > d"
    # Inline-ambiguous opener with no closing tag is NOT mistaken for HTML.
    assert _plaintext_email_body("the variable a<b is set") == "the variable a<b is set"
    assert _plaintext_email_body("ranges: 1<x<2 and y>0") == "ranges: 1<x<2 and y>0"
    # Multi-line plain prose is preserved verbatim.
    assert _plaintext_email_body("Line one\n\nLine two") == "Line one\n\nLine two"


def test_html_wrapped_body_is_flattened_to_text():
    assert _plaintext_email_body(
        "<html><body>Hello this is a test</body></html>"
    ) == "Hello this is a test"


def test_flatten_preserves_paragraph_breaks_and_unescapes_entities():
    out = _plaintext_email_body(
        "<html><body><p>Hi &amp; welcome</p><p>Line two<br>still two</p></body></html>"
    )
    assert "&amp;" not in out and "Hi & welcome" in out
    assert "Line two\nstill two" in out
    assert "<" not in out and ">" not in out


def test_flatten_drops_script_and_style_content():
    out = _plaintext_email_body(
        "<html><body>Hi<script>alert(1)</script><style>p{}</style> there</body></html>"
    )
    assert "alert" not in out and "p{}" not in out
    assert "Hi" in out and "there" in out


def _build_like_poller(body):
    """Mirror routes/email_pollers.py:1049-1055 (post-fix) exactly."""
    outer = MIMEMultipart("alternative")
    outer["Subject"] = "test"
    outer["From"] = "me@x"
    outer["To"] = "me@x"
    pbody = _plaintext_email_body(body or "")
    outer.attach(MIMEText(pbody, "plain", "utf-8"))
    html_body = _html.escape(pbody).replace("\n", "<br>\n")
    outer.attach(MIMEText(f"<html><body>{html_body}</body></html>", "html", "utf-8"))
    return outer


def _decoded_parts(outer):
    plain, htmlp = outer.get_payload()
    return (
        base64.b64decode(plain.get_payload()).decode(),
        base64.b64decode(htmlp.get_payload()).decode(),
    )


def test_scheduled_send_with_html_body_renders_no_literal_markup():
    plain, htmlp = _decoded_parts(
        _build_like_poller("<html><body>Hello this is a test</body></html>")
    )
    # text/plain part: clean text, not the raw HTML wrapper.
    assert plain == "Hello this is a test"
    # text/html part: the ONLY <html>/<body> are the structural wrapper; the
    # body text is NOT present as escaped entities (which would render as the
    # literal tags the user reported).
    assert "&lt;html&gt;" not in htmlp and "&lt;body&gt;" not in htmlp
    assert htmlp == "<html><body>Hello this is a test</body></html>"


def test_scheduled_send_with_plain_body_still_clean():
    plain, htmlp = _decoded_parts(_build_like_poller("Hello this is a test"))
    assert plain == "Hello this is a test"
    assert htmlp == "<html><body>Hello this is a test</body></html>"

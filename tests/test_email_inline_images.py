from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_inline_image_attachments_expose_cid_metadata_source():
    source = (ROOT / "routes" / "email_helpers.py").read_text()

    assert 'part.get("Content-ID"' in source
    assert 'part.get("Content-Location"' in source
    assert '"content_id": content_id' in source
    assert '"content_location": content_location' in source
    assert '"is_image": ct.lower().startswith("image/")' in source
    assert '"is_inline": "inline" in cd.lower() or bool(content_id)' in source


def test_email_reader_rewrites_cid_images_before_sanitizing():
    source = (ROOT / "static" / "js" / "emailLibrary.js").read_text()

    assert "function _rewriteInlineEmailImages" in source
    assert "att.content_id" in source
    assert "att.content_location" in source
    assert "/api/email/attachment/" in source
    assert "_sanitizeHtml(_rewriteInlineEmailImages(data.body_html, data))" in source
    assert "querySelectorAll('[srcset]')" in source

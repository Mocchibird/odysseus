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


# The reader-side cid: rewrite (_rewriteInlineEmailImages) was retired in the
# 23rd upstream sync: upstream now renders inline images as placeholders that
# load on demand (per-account "view inline images" setting), which supersedes it.

import pytest
from types import SimpleNamespace

from src.chat_handler import ChatHandler


class _UploadHandler:
    def resolve_upload(self, *_args, **_kwargs):
        raise AssertionError("attachments must not be resolved when tool preprocessing is disabled")

    def is_image_file(self, *_args, **_kwargs):
        raise AssertionError("images must not be inspected when tool preprocessing is disabled")


@pytest.mark.asyncio
async def test_preprocess_can_skip_external_context_and_attachment_work(monkeypatch):
    async def _fail_transcript(*_args, **_kwargs):
        raise AssertionError("YouTube transcripts must not be fetched")

    async def _fail_comments(*_args, **_kwargs):
        raise AssertionError("YouTube comments must not be fetched")

    monkeypatch.setattr("src.chat_handler.extract_transcript_async", _fail_transcript)
    monkeypatch.setattr("src.chat_handler.fetch_youtube_comments", _fail_comments)
    monkeypatch.setattr(
        "src.chat_handler.model_supports_vision",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("vision support must not be probed")
        ),
    )

    handler = ChatHandler(
        session_manager=None,
        memory_manager=None,
        chat_processor=None,
        research_handler=None,
        preset_manager=None,
        upload_handler=_UploadHandler(),
    )
    sess = SimpleNamespace(model="text-only", endpoint_url="", owner="user", id="session")

    enhanced, user_content, text_ctx, youtube, attachment_meta, _vision_override = await handler.preprocess_message(
        "Do not use tools. https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        ["image-id"],
        sess,
        auto_opened_docs=[],
        allow_tool_preprocessing=False,
    )

    assert enhanced.startswith("Do not use tools.")
    assert user_content == enhanced
    assert text_ctx == enhanced
    assert youtube == []
    assert attachment_meta == []


@pytest.mark.asyncio
async def test_image_on_text_only_model_routes_to_default_vision_model(monkeypatch):
    """A text-only session model + an image → preprocess returns a vision_override
    (the admin default vision model) AND keeps the raw image (no OCR-strip), so
    THIS message is answered by the vision model and reverts afterward."""
    class _UH:
        def resolve_upload(self, att_id, owner=None):
            return {"id": att_id, "name": "label.png", "mime": "image/png", "path": "/tmp/label.png"}

        def is_image_file(self, _name, _mime):
            return True

    monkeypatch.setattr("src.chat_handler.model_supports_vision", lambda *_a, **_k: False)
    monkeypatch.setattr(
        "src.settings.get_setting",
        lambda key, default=None: {"vision_enabled": True, "vision_model": "qwen-vl"}.get(key, default),
    )
    monkeypatch.setattr(
        "src.document_processor._resolve_vl_model",
        lambda configured, owner=None: ("http://vision.test", "qwen-vl", {"X": "1"}),
    )

    def _fail_vl(*_a, **_k):
        raise AssertionError("must NOT OCR-inject when routing to the vision model")

    monkeypatch.setattr("src.chat_handler.analyze_image_with_vl_result", _fail_vl)

    multimodal = [
        {"type": "text", "text": "How many calories?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]
    monkeypatch.setattr("src.chat_handler.build_user_content", lambda *a, **k: list(multimodal))

    handler = ChatHandler(
        session_manager=None, memory_manager=None, chat_processor=None,
        research_handler=None, preset_manager=None, upload_handler=_UH(),
    )
    sess = SimpleNamespace(
        model="gemma-text", endpoint_url="http://chat.test", owner="alice", id="s1", headers={}
    )

    _enh, user_content, _ctx, _yt, _meta, vision_override = await handler.preprocess_message(
        "How many calories?", ["img-1"], sess, auto_opened_docs=[], allow_tool_preprocessing=True,
    )

    # Routed to the admin's default vision model for THIS message...
    assert vision_override == ("http://vision.test", "qwen-vl", {"X": "1"})
    # ...and the raw image is preserved (not stripped to a text-only payload).
    assert isinstance(user_content, list)
    assert any(p.get("type") == "image_url" for p in user_content)

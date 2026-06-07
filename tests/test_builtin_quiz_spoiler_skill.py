import asyncio
import sys
from unittest.mock import MagicMock


if "bs4" not in sys.modules:
    sys.modules["bs4"] = MagicMock()

from services.memory.skills import QUIZ_SPOILER_MARKDOWN_SKILL_NAME, SkillsManager
from src.chat_processor import ChatProcessor
from src.tool_implementations import do_manage_skills


def test_builtin_quiz_spoiler_skill_is_in_agent_index(tmp_path):
    sm = SkillsManager(str(tmp_path))

    index = sm.index_for(owner="alice")
    match = next((s for s in index if s["name"] == QUIZ_SPOILER_MARKDOWN_SKILL_NAME), None)

    assert match is not None
    assert match["category"] == "iris"
    assert "hidden answers" in match["description"]


def test_builtin_quiz_spoiler_skill_matches_quiz_requests(tmp_path):
    sm = SkillsManager(str(tmp_path))

    results = sm.get_relevant_skills(
        "make me a quiz with spoiler hidden answers and flashcards",
        skills=sm.load(owner="alice"),
        owner="alice",
    )

    assert results
    assert results[0]["name"] == QUIZ_SPOILER_MARKDOWN_SKILL_NAME
    assert results[0]["_builtin"] is True


def test_manage_skills_can_view_builtin_quiz_spoiler_skill(tmp_path, monkeypatch):
    import src.constants as constants
    import src.tool_implementations as tool_impl

    monkeypatch.setattr(constants, "DATA_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(tool_impl, "DATA_DIR", str(tmp_path), raising=False)

    result = asyncio.run(do_manage_skills(
        '{"action":"view","name":"quiz-spoiler-markdown"}',
        owner="alice",
    ))

    md = result["results"]
    assert "Never write `quiz-spoiler-markdown:`" in md
    assert "{{C) Saul}}" in md
    assert "||spoiler text||" in md
    assert "{{hidden answer}}" in md
    assert "{{c1::hidden answer::hint text}}" in md
    assert "[[front question::back answer]]" in md


def test_quiz_spoiler_skill_rule_is_injected_when_relevant(tmp_path):
    sm = SkillsManager(str(tmp_path))
    cp = ChatProcessor(memory_manager=MagicMock(), personal_docs_manager=MagicMock(), skills_manager=sm)

    preface, _rag, _web = cp.build_context_preface(
        message="make me a quiz with hidden answers",
        session=MagicMock(),
        use_web=False,
        use_rag=False,
        use_memory=False,
        owner="alice",
        agent_mode=True,
        use_skills=True,
    )

    combined = "\n".join(m.get("content", "") for m in preface)
    assert "Built-in skill quiz-spoiler-markdown is relevant" in combined
    assert "Never write the skill name as visible text" in combined
    assert "`{{answer}}`" in combined

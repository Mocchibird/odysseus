# src/fork_chat_prompt.py
"""Fork-only system-prompt preface additions for ChatProcessor.

Kept OUT of upstream's ``ChatProcessor`` methods in src/chat_processor.py so
those methods stay byte-identical to upstream and merge cleanly on every
upstream sync. chat_processor.py imports these helpers and folds their output
into the ``preface`` list with a single ``preface.extend(...)`` seam per block.

Each helper RETURNS a ``list[dict]`` of system messages (empty list when the
block is not applicable), preserving the original inline behaviour exactly —
same logic, same try/except, same logging. Every method-local name a block used
is passed in as a parameter; this module must NOT import src.chat_processor
(would be circular). Upstream-package imports (src.i18n, services.memory.skills)
stay function-local exactly as they were inline.

See docs/fork-additive-policy.md for the pattern.
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def fork_language_preface(owner: Any, agent_mode: bool) -> List[Dict[str, str]]:
    """Per-user answering-language directive appended to ``preface``.

    Stable per user, so KV-cache safe. Agent mode gets the directive inside its
    own assembled prompt, so it is skipped here. Returns ``[]`` when there is no
    directive to add (agent mode, or no language configured / import failure).
    """
    # Per-user answering language (stable per user, so KV-cache safe).
    # Agent mode gets the directive inside its own assembled prompt.
    if not agent_mode:
        try:
            from src.i18n import get_user_language, language_directive
            _lang_line = language_directive(get_user_language(owner))
        except Exception:
            _lang_line = ""
        if _lang_line:
            return [{"role": "system", "content": _lang_line}]
    return []


def fork_quiz_spoiler_preface(message: Any, skills_manager: Any, owner: Any) -> List[Dict[str, str]]:
    """Quiz-spoiler-markdown skill-relevance directive appended to ``preface``.

    When the built-in ``quiz-spoiler-markdown`` skill is among the relevant
    skills for ``message``, append a system message instructing the model to use
    Iris reveal syntax directly. Returns ``[]`` when the skill is not relevant
    (or relevant-skills lookup failed).
    """
    try:
        from services.memory.skills import QUIZ_SPOILER_MARKDOWN_SKILL_NAME
        relevant_skills = skills_manager.get_relevant_skills(
            message,
            skills_manager.load(owner=owner),
            max_items=3,
            owner=owner,
        )
    except Exception as e:
        logger.debug(f"Relevant skills unavailable: {e}")
        relevant_skills = []
    if any(s.get("name") == QUIZ_SPOILER_MARKDOWN_SKILL_NAME for s in relevant_skills):
        return [{
            "role": "system",
            "content": (
                "Built-in skill quiz-spoiler-markdown is relevant. "
                "For quiz/self-test answers, write Iris reveal syntax directly: "
                "`{{answer}}` for a hidden answer, `||spoiler text||` for inline spoilers, "
                "and `[[front::back]]` for flashcards. Never write the skill name as visible "
                "text or as a pseudo-call such as `quiz-spoiler-markdown: **C) Saul**`."
            ),
        }]
    return []

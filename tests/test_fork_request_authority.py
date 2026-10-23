"""Fork tools under upstream's request authority.

Upstream grants a tool only when the request's semantic family includes it
(src.turn_contract.FAMILY_TOOLS). The fork registers its own families in
src/turn_contract_fork.py; without that, send_ping / manage_health /
manage_gallery / search_files / manage_files are offered to the model and then
refused at dispatch on every turn. These tests keep that integration found.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from src.agent_runtime.authority import (
    ExactOperation,
    create_request_authority,
    restore_task_authority,
    task_operation,
)

FORK_TOOLS = {"send_ping", "manage_health", "manage_gallery", "search_files", "manage_files"}


def _granted(text):
    return {g.tool for g in create_request_authority(text, owner="u").grants}


@pytest.mark.parametrize("text, tools", [
    ("ping me when the build is done", {"send_ping"}),
    ("log my weight 70kg", {"manage_health"}),
    ("add my lunch: chicken salad 450 kcal", {"manage_health"}),
    ("show my photos from the beach album", {"manage_gallery"}),
    ("find the pdf I uploaded about taxes", {"search_files", "manage_files"}),
    ("notify me to drink water", {"send_ping"}),
    ("ping me with the weather", {"send_ping", "web_search"}),
])
def test_a_request_about_a_fork_domain_grants_its_tool(text, tools):
    assert tools <= _granted(text)


@pytest.mark.parametrize("text", [
    "hello there",
    "log this",
    "add a note: buy milk",
    "what's the weather",
    "whats openai's latest video",
    "I weighed 70kg",
])
def test_unrelated_requests_grant_no_fork_tool(text):
    assert not (_granted(text) & FORK_TOOLS)


def test_granted_fork_operation_is_admitted():
    authority = create_request_authority("ping me when the build is done", owner="u")
    assert authority.permits(ExactOperation.normalize("send_ping", '{"message": "done"}'))


def test_registration_is_idempotent_and_private():
    from src import turn_contract
    from src.turn_contract_fork import FORK_FAMILY_TOOLS, register

    before = turn_contract.requested_capabilities
    register()
    assert turn_contract.requested_capabilities is before
    # Fork words must not leak into upstream's own routing tables.
    assert not set(FORK_FAMILY_TOOLS) & set(turn_contract._FAMILY_WORDS)
    assert not set(FORK_FAMILY_TOOLS) & set(turn_contract._FUZZY_FAMILY_TERMS)


def test_legacy_scheduled_tasks_are_sealed_once(monkeypatch, tmp_path):
    import core.database as cdb

    engine = create_engine(f"sqlite:///{tmp_path / 'tasks.db'}", poolclass=NullPool)
    cdb.Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(cdb, "SessionLocal", sessionmaker(bind=engine))

    db = cdb.SessionLocal()
    db.add(cdb.ScheduledTask(id="legacy-action", owner="u", name="Cleanup",
                             task_type="action", action="cleanup_temp_files"))
    db.add(cdb.ScheduledTask(id="legacy-llm", owner="u", name="Brief",
                             task_type="llm", prompt="ping me a reminder to stretch"))
    db.add(cdb.ScheduledTask(id="sealed", owner="u", name="Kept",
                             task_type="llm", prompt="x", request_authority_json="KEEP"))
    db.commit()
    db.close()

    cdb._migrate_fork_seal_legacy_task_authority()

    db = cdb.SessionLocal()
    rows = {t.id: t for t in db.query(cdb.ScheduledTask).all()}
    db.close()
    assert rows["sealed"].request_authority_json == "KEEP"

    action = rows["legacy-action"]
    authority = restore_task_authority(action.request_authority_json, action.prompt,
                                       action.task_type, action.action, owner="u")
    assert authority.permits(task_operation(action.task_type, action.action, action.prompt))

    llm = rows["legacy-llm"]
    authority = restore_task_authority(llm.request_authority_json, llm.prompt,
                                       llm.task_type, llm.action, owner="u")
    assert authority.permits(ExactOperation.normalize("send_ping", '{"message": "hi"}'))

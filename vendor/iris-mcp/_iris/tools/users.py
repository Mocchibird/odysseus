"""User-management MCP tools — display name + per-user scheduled briefs.

These let Iris persist changes to a user's row when the user makes a
request like "call me X" or "send me a morning brief at 6:30". The
``users`` table is the source of truth; these tools mutate it.

Every tool takes a ``user_id`` argument that defaults to ``None`` (the
current speaker, resolved via ``resolve_user_id`` → falls back to the
owner). The bot's system prompt is responsible for plumbing the
correct speaker's user_id from the Discord context.

Safety: a non-owner user can only modify THEIR OWN row. The
``is_owner`` flag is never set/cleared here (it's anchored by the DB-
level partial unique index + the ``IRIS_OWNER_DISCORD_ID`` env var on
schema init). And the owner row can't be renamed by someone else — if
the speaker's user_id doesn't match the target row's id AND the
speaker isn't the owner, the tool refuses.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from .. import mcp
from ..core import (
    authorize_user_access,
    coerce_channel_id,
    get_vault_index,
    maybe_reload_db_plugin,
    resolve_user_id,
)


_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
# Reasonable bound on display-name length so a runaway transcript or
# emoji-spam can't end up as someone's persistent name.
_MAX_DISPLAY_NAME = 80


def _validate_time(s: str) -> Optional[str]:
    """Return a normalised ``HH:MM`` or None if the string isn't a valid
    24h time."""
    m = _TIME_RE.match((s or "").strip())
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return f"{hh:02d}:{mm:02d}"


# ─── Display name ───────────────────────────────────────────────────────────


@mcp.tool()
def user_set_display_name(
    name: str,
    user_id: Optional[int] = None,
) -> str:
    """Persist the display name Iris should use for a user.

    Call this whenever the user says "call me X" / "my name is X" /
    similar. The new name is stored in ``users.display_name`` and is
    immediately reflected in the per-message "Discord context" block
    of Iris's system prompt — so subsequent replies will address the
    speaker by the new name without a restart.

    Args:
        name: New display name. Trimmed; max 80 chars. Cannot be empty.
        user_id: The row to update. Defaults to the current speaker
            (via ``resolve_user_id``). The owner may pass an explicit
            ``user_id`` to rename a different user; any other speaker
            can only rename their own row.

    Returns ok/err. On success, includes the old → new name in the
    confirmation so Iris can echo it.
    """
    name_clean = (name or "").strip()
    if not name_clean:
        return "err: name required"
    if len(name_clean) > _MAX_DISPLAY_NAME:
        return f"err: name too long (max {_MAX_DISPLAY_NAME} chars)"

    idx = get_vault_index()
    c = idx.conn
    target_uid, denial = authorize_user_access(user_id)
    if target_uid is None:
        return "err: no user resolved — multi-user not configured?"
    if denial:
        return denial

    row = c.execute(
        "SELECT id, display_name FROM users WHERE id = ?", (target_uid,),
    ).fetchone()
    if row is None:
        return f"err: no user with id={target_uid}"
    old = row["display_name"]
    if old == name_clean:
        return f"ok no-change · display_name={old!r}"
    now = datetime.now().isoformat(timespec="seconds")
    c.execute(
        "UPDATE users SET display_name = ?, updated_at = ? WHERE id = ?",
        (name_clean, now, target_uid),
    )
    c.commit()
    maybe_reload_db_plugin()
    return f"ok renamed · {old!r} → {name_clean!r} (user_id={target_uid})"


# ─── Per-user scheduled briefs ──────────────────────────────────────────────


@mcp.tool()
def user_set_morning_brief(
    at: str = "",
    channel_id: str = "",
    timezone: str = "",
    user_id: Optional[int] = None,
) -> str:
    """Set a user's daily morning-brief time + channel.

    The bot's scheduled-briefings loop fires this brief once a day at
    ``at`` (HH:MM, 24h, in ``timezone`` or the user's stored timezone
    or HOME_TZ). Send empty ``at`` to disable.

    Args:
        at: 'HH:MM' local time, or '' / 'off' to disable.
        channel_id: Discord channel ID where the brief lands. When
            omitted and a previous setting exists, the previous channel
            is preserved. First-time setup requires this.
        timezone: IANA tz name (e.g. 'Asia/Seoul'). Empty inherits the
            user's stored timezone or HOME_TZ.
        user_id: Target user. Owner may set this for any user; other
            speakers may only set their own.
    """
    return _set_brief_field(
        "brief_morning_at", at, channel_id, timezone, user_id,
        label="morning brief",
    )


@mcp.tool()
def user_set_evening_wrapup(
    at: str = "",
    channel_id: str = "",
    timezone: str = "",
    user_id: Optional[int] = None,
) -> str:
    """Set a user's daily evening wrap-up time + channel.

    Same shape as :func:`user_set_morning_brief` but fires the evening
    wrap-up instead. Send empty ``at`` to disable."""
    return _set_brief_field(
        "brief_evening_at", at, channel_id, timezone, user_id,
        label="evening wrap-up",
    )


def _set_brief_field(
    col: str,
    at: str,
    channel_id: str,
    timezone: str,
    user_id: Optional[int],
    *,
    label: str,
) -> str:
    idx = get_vault_index()
    c = idx.conn
    target_uid, denial = authorize_user_access(user_id)
    if target_uid is None:
        return "err: no user resolved — multi-user not configured?"
    if denial:
        return denial

    at_clean = (at or "").strip().lower()
    new_at: Optional[str]
    if at_clean in ("", "off", "disable", "disabled"):
        new_at = None
    else:
        new_at = _validate_time(at_clean)
        if new_at is None:
            return f"err: at must be HH:MM 24-hour (got '{at}'); 'off' to disable"

    existing = c.execute(
        f"SELECT {col}, brief_channel_id, brief_timezone FROM users WHERE id = ?",
        (target_uid,),
    ).fetchone()
    if existing is None:
        return f"err: no user with id={target_uid}"

    # Channel: explicit param wins; otherwise keep existing.
    # coerce_channel_id handles str→int losslessly for snowflakes that
    # exceed JSON float precision (the LLM should pass them as strings).
    coerced = coerce_channel_id(channel_id) if channel_id else None
    new_channel = coerced if coerced is not None else existing["brief_channel_id"]
    # When ENABLING a brief, a channel is required.
    if new_at is not None and not new_channel:
        return (
            f"err: channel_id required to enable {label} "
            "(no previous channel on this user)."
        )
    # v22: channel-user binding check. If the new channel is already
    # bound to a different user, refuse — that's the leak path we're
    # closing at the data layer.
    if new_channel is not None:
        bound = c.execute(
            "SELECT user_id FROM user_channels WHERE channel_id = ?",
            (new_channel,),
        ).fetchone()
        if bound is not None and int(bound["user_id"]) != int(target_uid):
            return (
                f"err: channel {new_channel} is already bound to user_id="
                f"{bound['user_id']}; cannot use for user_id={target_uid}'s "
                f"{label}. Resolve the binding first."
            )

    new_tz = (timezone or "").strip() or existing["brief_timezone"]

    now = datetime.now().isoformat(timespec="seconds")
    c.execute(
        f"UPDATE users SET {col} = ?, brief_channel_id = ?, "
        "  brief_timezone = ?, updated_at = ? WHERE id = ?",
        (new_at, new_channel, new_tz, now, target_uid),
    )
    # v22: register the channel→user binding alongside the column
    # update so background loops route this user's brief to this
    # channel. INSERT OR IGNORE — pre-check above ensures no
    # cross-user conflict, so this either creates a new binding or
    # no-ops on a re-set.
    if new_channel is not None:
        c.execute(
            "INSERT OR IGNORE INTO user_channels "
            "    (channel_id, user_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (new_channel, target_uid, now, now),
        )
    c.commit()
    maybe_reload_db_plugin()

    if new_at is None:
        return f"ok {label} disabled for user_id={target_uid}"
    tz_part = f" in {new_tz}" if new_tz else ""
    return (
        f"ok {label} scheduled at {new_at}{tz_part} → "
        f"channel {new_channel} (user_id={target_uid})"
    )


@mcp.tool()
def user_set_health_daily(
    at: str = "",
    channel_id: str = "",
    user_id: Optional[int] = None,
) -> str:
    """Set the time + channel for a user's DAILY health card.

    v23: moves the legacy ``NOTIFY_HEALTH_DAILY_AT`` env var into a
    per-user column so the owner can configure it via Iris too.
    ``at`` is HH:MM in the user's brief_timezone (or HOME_TZ). Send
    ``"off"`` to disable.

    The health card body comes from ``health_daily_summary(user_id)``
    so it's correctly scoped to the user's own weights + meals.
    """
    return _set_health_field(
        "health_daily_at", at, channel_id, user_id,
        label="daily health card",
    )


@mcp.tool()
def user_set_health_weekly(
    at: str = "",
    dow: int = 1,
    channel_id: str = "",
    user_id: Optional[int] = None,
) -> str:
    """Set the time + ISO weekday + channel for a user's WEEKLY health card.

    v23: moves the legacy ``NOTIFY_HEALTH_WEEKLY_AT`` /
    ``NOTIFY_HEALTH_WEEKLY_DOW`` env vars into per-user columns.

    Args:
        at: 'HH:MM' local time, or '' / 'off' to disable.
        dow: ISO weekday (1=Monday, 7=Sunday). Default 1 (Monday) for
            the typical "weekend recap on Monday morning" flow.
        channel_id: Discord channel snowflake (string).
        user_id: Target user.
    """
    if dow not in range(1, 8):
        return f"err: dow must be 1-7 (ISO weekday, 1=Monday); got {dow!r}"
    # Reuse the brief field helper but write to health_weekly_at +
    # health_weekly_dow + the health channel column.
    idx = get_vault_index()
    c = idx.conn
    target_uid, denial = authorize_user_access(user_id)
    if target_uid is None:
        return "err: no user resolved — multi-user not configured?"
    if denial:
        return denial
    at_clean = (at or "").strip().lower()
    new_at: Optional[str]
    if at_clean in ("", "off", "disable", "disabled"):
        new_at = None
    else:
        new_at = _validate_time(at_clean)
        if new_at is None:
            return f"err: at must be HH:MM (got '{at}'); 'off' to disable"

    existing = c.execute(
        "SELECT health_weekly_at, health_weekly_dow, health_channel_id, "
        " brief_channel_id FROM users WHERE id = ?",
        (target_uid,),
    ).fetchone()
    if existing is None:
        return f"err: no user with id={target_uid}"

    coerced = coerce_channel_id(channel_id) if channel_id else None
    # Effective channel: explicit > existing health > existing brief.
    new_channel = (
        coerced
        if coerced is not None
        else (existing["health_channel_id"] or existing["brief_channel_id"])
    )
    if new_at is not None and not new_channel:
        return (
            "err: channel_id required to enable weekly health card "
            "(no previous channel on this user)."
        )
    if new_channel is not None:
        bound = c.execute(
            "SELECT user_id FROM user_channels WHERE channel_id = ?",
            (new_channel,),
        ).fetchone()
        if bound is not None and int(bound["user_id"]) != int(target_uid):
            return (
                f"err: channel {new_channel} is already bound to "
                f"user_id={bound['user_id']}"
            )

    now = datetime.now().isoformat(timespec="seconds")
    c.execute(
        "UPDATE users SET health_weekly_at = ?, health_weekly_dow = ?, "
        "  health_channel_id = COALESCE(health_channel_id, ?), "
        "  updated_at = ? WHERE id = ?",
        (new_at, dow, new_channel, now, target_uid),
    )
    if new_channel is not None:
        c.execute(
            "INSERT OR IGNORE INTO user_channels "
            "  (channel_id, user_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (new_channel, target_uid, now, now),
        )
    c.commit()
    maybe_reload_db_plugin()
    if new_at is None:
        return f"ok weekly health card disabled for user_id={target_uid}"
    dow_name = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[dow - 1]
    return (
        f"ok weekly health card scheduled at {new_at} on {dow_name} → "
        f"channel {new_channel} (user_id={target_uid})"
    )


def _set_health_field(
    col: str,
    at: str,
    channel_id: str,
    user_id: Optional[int],
    *,
    label: str,
) -> str:
    """Sibling of ``_set_brief_field`` for the health columns. Writes
    to ``health_daily_at`` (or future per-user health column) + the
    user's ``health_channel_id``, registering the binding too."""
    idx = get_vault_index()
    c = idx.conn
    target_uid, denial = authorize_user_access(user_id)
    if target_uid is None:
        return "err: no user resolved — multi-user not configured?"
    if denial:
        return denial

    at_clean = (at or "").strip().lower()
    new_at: Optional[str]
    if at_clean in ("", "off", "disable", "disabled"):
        new_at = None
    else:
        new_at = _validate_time(at_clean)
        if new_at is None:
            return f"err: at must be HH:MM (got '{at}'); 'off' to disable"

    existing = c.execute(
        f"SELECT {col}, health_channel_id, brief_channel_id "
        "FROM users WHERE id = ?",
        (target_uid,),
    ).fetchone()
    if existing is None:
        return f"err: no user with id={target_uid}"

    coerced = coerce_channel_id(channel_id) if channel_id else None
    # Effective: explicit > existing health > existing brief.
    new_channel = (
        coerced
        if coerced is not None
        else (existing["health_channel_id"] or existing["brief_channel_id"])
    )
    if new_at is not None and not new_channel:
        return (
            f"err: channel_id required to enable {label} "
            "(no previous channel on this user)."
        )
    if new_channel is not None:
        bound = c.execute(
            "SELECT user_id FROM user_channels WHERE channel_id = ?",
            (new_channel,),
        ).fetchone()
        if bound is not None and int(bound["user_id"]) != int(target_uid):
            return (
                f"err: channel {new_channel} is already bound to "
                f"user_id={bound['user_id']}"
            )

    now = datetime.now().isoformat(timespec="seconds")
    c.execute(
        f"UPDATE users SET {col} = ?, "
        "  health_channel_id = COALESCE(health_channel_id, ?), "
        "  updated_at = ? WHERE id = ?",
        (new_at, new_channel, now, target_uid),
    )
    if new_channel is not None:
        c.execute(
            "INSERT OR IGNORE INTO user_channels "
            "  (channel_id, user_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (new_channel, target_uid, now, now),
        )
    c.commit()
    maybe_reload_db_plugin()
    if new_at is None:
        return f"ok {label} disabled for user_id={target_uid}"
    return (
        f"ok {label} scheduled at {new_at} → "
        f"channel {new_channel} (user_id={target_uid})"
    )


@mcp.tool()
def user_set_ping_channel(
    channel_id: str = "",
    user_id: Optional[int] = None,
) -> str:
    """Set the Discord channel where Iris sends a user's event /
    reminder / snooze-replay pings.

    Per-user override of the singleton ``IRIS_DISCORD_PING_CHANNEL``
    env var. ``channel_id=None`` clears the override so the user
    falls back to ``brief_channel_id`` (if set), then to the env var.

    Args:
        channel_id: Discord channel snowflake. Pass None or 0 to clear.
        user_id: Target user. Owner can set this for any user; other
            speakers can only set their own.
    """
    return _set_channel_field(
        "ping_channel_id", channel_id, user_id, label="ping channel",
    )


@mcp.tool()
def user_set_health_channel(
    channel_id: str = "",
    user_id: Optional[int] = None,
) -> str:
    """Set the Discord channel where Iris sends a user's daily /
    weekly health card.

    Per-user override of ``IRIS_DISCORD_HEALTH_CHANNEL``. None /0 →
    fall back to ``brief_channel_id`` → env var.

    Args:
        channel_id: Discord channel snowflake. Pass None or 0 to clear.
        user_id: Target user. Owner-or-self for the modify check.
    """
    return _set_channel_field(
        "health_channel_id", channel_id, user_id, label="health channel",
    )


def _set_channel_field(
    col: str,
    channel_id: str,
    user_id: Optional[int],
    *,
    label: str,
) -> str:
    idx = get_vault_index()
    c = idx.conn
    target_uid, denial = authorize_user_access(user_id)
    if target_uid is None:
        return "err: no user resolved — multi-user not configured?"
    if denial:
        return denial
    # Precision-safe coercion (handles 0/""/None → None; str → int via
    # int(s) which is lossless for any digit length).
    cid: Optional[int] = coerce_channel_id(channel_id)
    # v22: channel-user binding check. Before claiming a channel for
    # this user, refuse if it's already bound to a DIFFERENT user. This
    # closes the "owner-row points at mom's channel" leak path at the
    # write-time gate: it can never be saved in the first place.
    if cid is not None:
        bound = c.execute(
            "SELECT user_id FROM user_channels WHERE channel_id = ?",
            (cid,),
        ).fetchone()
        if bound is not None and int(bound["user_id"]) != int(target_uid):
            return (
                f"err: channel {cid} is already bound to user_id="
                f"{bound['user_id']}; cannot reassign to user_id="
                f"{target_uid}. Have the original owner clear it first "
                "(or the bot owner can manually edit the user_channels table)."
            )
    now = datetime.now().isoformat(timespec="seconds")
    cur = c.execute(
        f"UPDATE users SET {col} = ?, updated_at = ? WHERE id = ?",
        (cid, now, target_uid),
    )
    if cur.rowcount == 0:
        return f"err: no user with id={target_uid}"
    # v22: register the channel→user binding so background loops route
    # to it. INSERT OR IGNORE — the upstream check above already
    # confirmed no cross-user conflict, so this either creates a new
    # binding or no-ops on a same-user re-set.
    if cid is not None:
        c.execute(
            "INSERT OR IGNORE INTO user_channels "
            "    (channel_id, user_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (cid, target_uid, now, now),
        )
    c.commit()
    maybe_reload_db_plugin()
    if cid is None:
        return f"ok {label} cleared for user_id={target_uid} (will fall back to brief_channel_id / env)"
    return f"ok {label} set to {cid} for user_id={target_uid}"


@mcp.tool()
def user_brief_settings(user_id: Optional[int] = None) -> str:
    """Show the current scheduled-brief + channel settings for a user
    (or the speaker by default).

    Useful for confirming what Iris has on file after a "send me a
    morning brief at 6:30" / "send my health card to #fitness" request."""
    idx = get_vault_index()
    c = idx.conn
    target_uid, denial = authorize_user_access(user_id)
    if target_uid is None:
        return "err: no user resolved"
    if denial:
        return denial
    row = c.execute(
        "SELECT display_name, brief_morning_at, brief_evening_at, "
        " brief_channel_id, brief_timezone, "
        " ping_channel_id, health_channel_id "
        "FROM users WHERE id = ?",
        (target_uid,),
    ).fetchone()
    if row is None:
        return f"err: no user with id={target_uid}"
    parts = [f"User {row['display_name']!r} (id={target_uid}):"]
    parts.append(f"  morning brief : {row['brief_morning_at'] or 'off'}")
    parts.append(f"  evening wrap  : {row['brief_evening_at'] or 'off'}")
    parts.append(f"  brief channel : {row['brief_channel_id'] or '—'}")
    parts.append(f"  ping channel  : {row['ping_channel_id'] or '— (uses brief / env fallback)'}")
    parts.append(f"  health channel: {row['health_channel_id'] or '— (uses brief / env fallback)'}")
    parts.append(f"  timezone      : {row['brief_timezone'] or 'inherit HOME_TZ'}")
    try:
        pw_set = row["web_password_hash"] is not None
    except (KeyError, IndexError):
        pw_set = False
    parts.append(f"  web login     : {'configured' if pw_set else 'not set'}")
    return "\n".join(parts)


@mcp.tool()
def user_set_web_password(
    password: str,
    user_id: Optional[int] = None,
) -> str:
    """Set or change a user's password for the iris-web login.

    Phase 1 of the web-UI rollout (Roadmap §1.1). The password is
    bcrypt-hashed before storage; the plaintext never touches disk.

    Args:
        password: New password. Min 8 chars. Stored hashed.
        user_id: Target user. Owner-or-self gate via
            ``authorize_user_access`` — non-owners can only set their
            own password; owner can set anyone's (e.g., bootstrap a
            new family member's account).

    Returns ok/err. On success: 'ok password set for user_id=N'.
    """
    if not password or not password.strip():
        return "err: password required"
    if len(password) < 8:
        return "err: password must be at least 8 characters"
    if len(password) > 200:
        return "err: password too long (max 200 chars)"

    idx = get_vault_index()
    c = idx.conn
    target_uid, denial = authorize_user_access(user_id)
    if target_uid is None:
        return "err: no user resolved — multi-user not configured?"
    if denial:
        return denial

    # bcrypt is imported lazily so the MCP server doesn't require it
    # at startup if no one ever calls this tool.
    try:
        import bcrypt  # noqa: PLC0415
    except ImportError:
        return (
            "err: bcrypt not installed — pip install bcrypt or add to "
            "docker/requirements.txt for the iris-web service."
        )
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

    now = datetime.now().isoformat(timespec="seconds")
    cur = c.execute(
        "UPDATE users SET web_password_hash = ?, "
        "  web_password_set_at = ?, updated_at = ? "
        "WHERE id = ?",
        (hashed, now, now, target_uid),
    )
    if cur.rowcount == 0:
        return f"err: no user with id={target_uid}"
    c.commit()
    return f"ok password set for user_id={target_uid}"

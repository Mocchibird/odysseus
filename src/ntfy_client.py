"""Small ntfy notification helper shared by reminders and agent tools."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional
from urllib.parse import quote

import httpx


def _is_ntfy(integration: Dict[str, Any]) -> bool:
    """True for an enabled ntfy integration that has a base URL."""
    if not integration.get("enabled", True) or not integration.get("base_url"):
        return False
    preset = str(integration.get("preset") or "").lower()
    name = str(integration.get("name") or "").lower()
    return preset == "ntfy" or name == "ntfy"


def resolve_ntfy_integration(
    integrations: Iterable[Dict[str, Any]],
    *,
    topic: Optional[str] = None,
    integration_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Pick the ntfy connection whose token matches the target topic.

    Resolution order (first match wins):
      1. ``integration_id`` — an explicit per-user/connection choice.
      2. ``topic`` — the connection whose ``ntfy_topic`` field equals the
         topic (trimmed, case-insensitive). This is what makes a
         "one connection + token per topic" setup work: the admin adds a
         connection per topic, each tagged with its topic + scoped token,
         and the right token is used automatically.
      3. Fallback: the first enabled ntfy connection (legacy behaviour, so
         single-connection setups keep working unchanged).

    Only enabled ntfy connections with a base_url are considered.
    """
    ntfy = [i for i in integrations if _is_ntfy(i)]
    if not ntfy:
        return None

    if integration_id:
        for i in ntfy:
            if str(i.get("id") or "") == str(integration_id):
                return i

    want = str(topic or "").strip().lower()
    if want:
        for i in ntfy:
            if str(i.get("ntfy_topic") or "").strip().lower() == want:
                return i

    return ntfy[0]


def find_ntfy_integration(integrations: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the first enabled ntfy integration (back-compat wrapper)."""
    return resolve_ntfy_integration(integrations)


def _apply_auth(
    integration: Dict[str, Any],
    url: str,
    headers: Dict[str, str],
) -> tuple[str, Optional[httpx.Auth]]:
    """Apply the auth mode from a saved integration."""
    api_key = str(integration.get("api_key") or "")
    auth_type = str(integration.get("auth_type") or "none").lower()
    if not api_key:
        return url, None

    if auth_type == "bearer":
        headers["Authorization"] = f"Bearer {api_key}"
    elif auth_type == "header":
        header_name = str(integration.get("auth_header") or "Authorization")
        headers[header_name] = api_key
    elif auth_type == "query":
        param_name = str(integration.get("auth_param") or "api_key")
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{quote(param_name, safe='')}={quote(api_key, safe='')}"
    elif auth_type == "basic":
        user, sep, password = api_key.partition(":")
        if sep:
            return url, httpx.BasicAuth(user, password)

    return url, None


def _ascii_safe(value: str) -> bool:
    """httpx rejects any non-ASCII header value (encodes headers as ASCII),
    so the header publish path is only safe for pure-ASCII title/actions."""
    try:
        str(value).encode("ascii")
        return True
    except (UnicodeEncodeError, AttributeError):
        return False


def _actions_header(actions: list) -> str:
    """Format structured actions as the simple ntfy ``Actions`` header value."""
    parts = []
    for a in actions:
        bits = [f"action={a.get('action', 'http')}", f"label={a.get('label', '')}", f"url={a.get('url', '')}"]
        if a.get("method"):
            bits.append(f"method={a['method']}")
        if a.get("clear"):
            bits.append("clear=true")
        parts.append(", ".join(bits))
    return "; ".join(parts)


# ntfy's JSON publish endpoint wants numeric priorities.
_PRIORITY_NUM = {"max": 5, "urgent": 5, "high": 4, "default": 3, "low": 2, "min": 1}


async def send_ntfy_notification(
    integration: Dict[str, Any],
    topic: str,
    message: str,
    *,
    title: str = "Iris",
    priority: str = "high",
    tags: str = "bell",
    actions: Optional[Any] = None,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """Send a plain-text ntfy notification through a saved integration.

    ``actions`` is either a prebuilt ntfy ``Actions`` header string or a list
    of action dicts ({action,label,url,method,clear}) — see
    https://docs.ntfy.sh/publish/#action-buttons.

    HTTP header values must be ASCII (httpx refuses anything else), so when
    the title or action labels carry non-ASCII text (Korean reminders, accented
    titles) the notification is published through ntfy's JSON endpoint instead,
    which is full UTF-8. The message body is UTF-8 on both paths.
    """
    base_url = str(integration.get("base_url") or "").rstrip("/")
    clean_topic = str(topic or "").strip()
    clean_message = str(message or "").strip()
    if not base_url:
        return {"error": "ntfy integration has no base_url configured", "exit_code": 1}
    if not clean_topic:
        return {"error": "ntfy topic is not configured", "exit_code": 1}
    if not clean_message:
        return {"error": "message is required", "exit_code": 1}

    clean_title = str(title or "Iris")[:120]
    action_list = actions if isinstance(actions, list) else None
    action_text = (
        _actions_header(action_list) if action_list is not None
        else (str(actions) if actions else "")
    )
    needs_json = not _ascii_safe(clean_title) or (action_text and not _ascii_safe(action_text))

    headers: Dict[str, str] = {}
    if needs_json:
        # JSON publish: topic/title/actions ride in the UTF-8 body.
        url = base_url
        payload: Dict[str, Any] = {
            "topic": clean_topic,
            "message": clean_message,
            "title": clean_title,
            "priority": _PRIORITY_NUM.get(str(priority or "high").lower(), 4),
        }
        if tags:
            payload["tags"] = [t.strip() for t in str(tags).split(",") if t.strip()]
        if action_list is not None:
            payload["actions"] = action_list
        elif action_text and _ascii_safe(action_text):
            # A prebuilt header string can't be structured for the JSON body —
            # attach it as a header only when transport-safe; otherwise drop
            # the buttons rather than failing the whole push.
            headers["Actions"] = action_text
    else:
        url = f"{base_url}/{quote(clean_topic, safe='')}"
        headers = {
            "Title": clean_title,
            "Priority": str(priority or "high"),
        }
        if tags:
            headers["Tags"] = str(tags)
        if action_text:
            headers["Actions"] = action_text

    # Keep the pre-auth URL for the returned result: for auth_type="query",
    # _apply_auth appends the API key to the URL as a query param. That
    # augmented URL must go to httpx but must NOT be returned to the caller —
    # do_send_ping surfaces the result into the LLM tool-result stream and
    # stored transcripts, which would leak the key. Return publish_url instead.
    publish_url = url
    url, auth = _apply_auth(integration, url, headers)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if needs_json:
                import json as _json
                response = await client.post(
                    url, content=_json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers=headers, auth=auth,
                )
            else:
                response = await client.post(url, content=clean_message, headers=headers, auth=auth)
    except Exception as exc:
        return {"error": f"ntfy ping failed: {exc}", "exit_code": 1}

    if not response.is_success:
        return {
            "error": f"ntfy returned HTTP {response.status_code}: {response.text[:240]}",
            "exit_code": 1,
            "status_code": response.status_code,
        }

    return {
        "output": f"Sent ntfy ping to topic {clean_topic}.",
        "topic": clean_topic,
        "url": publish_url,
        "exit_code": 0,
    }


async def send_ntfy(
    title: str,
    message: str,
    *,
    owner: str = "",
    topic: Optional[str] = None,
    integration_id: Optional[str] = None,
    priority: str = "high",
    tags: str = "bell",
    actions: Optional[str] = None,
) -> Dict[str, Any]:
    """High-level, reusable ntfy push: resolve the target ntfy connection + topic
    from the saved integrations/settings, then send.

    Intended as the single entry point anything (scheduled tasks, reminders,
    tools) can call to push an ntfy notification.

    Robust for BACKGROUND contexts: it keys off the ntfy *integration* (a global
    connection list) plus the topic, and is deliberately NOT gated on the
    per-user ``reminder_channel`` setting — so it still fires from the task
    scheduler, where that per-user setting may not resolve for the task owner.

    Returns ``{"ntfy_sent": bool, "ntfy_error": str, "topic": str}``.
    """
    try:
        from src.integrations import load_integrations
        from src.settings import get_setting, get_user_setting
    except Exception as exc:  # pragma: no cover - import safety
        return {"ntfy_sent": False, "ntfy_error": f"ntfy setup unavailable: {exc}", "topic": ""}

    # Topic: explicit arg -> per-user pref -> global pref -> (later) the
    # connection's own configured topic -> conventional default "reminders".
    resolved_topic = str(topic or "").strip()
    if not resolved_topic:
        try:
            resolved_topic = str(
                get_user_setting(
                    "reminder_ntfy_topic", owner or "", get_setting("reminder_ntfy_topic", "")
                ) or ""
            ).strip()
        except Exception:
            resolved_topic = ""

    resolved_id = integration_id
    if not resolved_id:
        try:
            resolved_id = get_user_setting("reminder_ntfy_integration_id", owner or "", "") or None
        except Exception:
            resolved_id = None

    try:
        integrations = load_integrations()
    except Exception as exc:
        return {"ntfy_sent": False, "ntfy_error": f"could not load integrations: {exc}", "topic": resolved_topic}

    intg = resolve_ntfy_integration(integrations, topic=resolved_topic or None, integration_id=resolved_id)
    if not intg:
        return {"ntfy_sent": False, "ntfy_error": "No enabled ntfy integration", "topic": resolved_topic}

    # Prefer the explicitly-resolved topic; fall back to the connection's own
    # configured topic, then the conventional default.
    send_topic = resolved_topic or str(intg.get("ntfy_topic") or "").strip() or "reminders"
    result = await send_ntfy_notification(
        intg,
        send_topic,
        message,
        title=title or "Reminder",
        priority=priority,
        tags=tags,
        actions=actions,
    )
    ok = result.get("exit_code") == 0
    return {
        "ntfy_sent": ok,
        "ntfy_error": "" if ok else str(result.get("error") or "ntfy send failed"),
        "topic": send_topic,
    }

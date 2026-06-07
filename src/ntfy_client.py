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


async def send_ntfy_notification(
    integration: Dict[str, Any],
    topic: str,
    message: str,
    *,
    title: str = "Iris",
    priority: str = "high",
    tags: str = "bell",
    actions: Optional[str] = None,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """Send a plain-text ntfy notification through a saved integration.

    ``actions`` is an optional ntfy ``Actions`` header value (e.g. snooze/done
    http buttons) — see https://docs.ntfy.sh/publish/#action-buttons.
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

    headers: Dict[str, str] = {
        "Title": str(title or "Iris")[:120],
        "Priority": str(priority or "high"),
    }
    if tags:
        headers["Tags"] = str(tags)
    if actions:
        headers["Actions"] = str(actions)

    url = f"{base_url}/{quote(clean_topic, safe='')}"
    url, auth = _apply_auth(integration, url, headers)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
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
        "url": url,
        "exit_code": 0,
    }

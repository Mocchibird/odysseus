"""Small ntfy notification helper shared by reminders and agent tools."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional
from urllib.parse import quote

import httpx


def find_ntfy_integration(integrations: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the enabled ntfy integration, if one is configured."""
    for integration in integrations:
        if not integration.get("enabled", True) or not integration.get("base_url"):
            continue
        preset = str(integration.get("preset") or "").lower()
        name = str(integration.get("name") or "").lower()
        if preset == "ntfy" or name == "ntfy":
            return integration
    return None


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
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """Send a plain-text ntfy notification through a saved integration."""
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

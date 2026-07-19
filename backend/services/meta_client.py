"""Thin Meta Graph API client — sender profile lookup (Phase 5).

Best-effort: fetches a Messenger/Instagram sender's display name + avatar so an
unlinked DM shows "Maria G." instead of a raw PSID in the triage list. Guarded
by ``META_PAGE_ACCESS_TOKEN`` — with no token (dev / pre-setup) it returns
``None`` and the caller falls back to the platform id. Never raises into the
webhook path.

The send path (outbound Messenger/IG replies) is intentionally NOT here yet —
it lands with Meta App Review + the human_agent tag in a later phase.
"""

from __future__ import annotations

import logging

import httpx

from config import settings

log = logging.getLogger(__name__)

_GRAPH = "https://graph.facebook.com/v21.0"
_TIMEOUT = 5.0


def fetch_profile(external_id: str, *, channel: str) -> dict | None:
    """Return ``{"display_name": str, "avatar_url": str | None}`` for a
    Messenger PSID or Instagram-scoped id, or ``None`` if unavailable.

    Messenger exposes ``first_name``/``last_name``/``profile_pic``; Instagram
    exposes ``name``/``username``/``profile_pic``. We request a superset and
    take whatever comes back.
    """
    token = settings.META_PAGE_ACCESS_TOKEN
    if not token or not external_id:
        return None
    fields = "name,first_name,last_name,username,profile_pic"
    try:
        resp = httpx.get(
            f"{_GRAPH}/{external_id}",
            params={"fields": fields, "access_token": token},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("meta_client.fetch_profile failed for %s (%s): %s",
                    external_id, channel, exc)
        return None

    name = (
        data.get("name")
        or " ".join(
            p for p in (data.get("first_name"), data.get("last_name")) if p
        ).strip()
        or (f"@{data['username']}" if data.get("username") else None)
    )
    if not name:
        return None
    return {"display_name": name, "avatar_url": data.get("profile_pic")}

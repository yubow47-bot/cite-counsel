"""Discord webhook notification for feedback events.

Reads env DISCORD_FEEDBACK_WEBHOOK. If unset, degrades gracefully (no-op).
All errors are swallowed and logged as warnings — never raised.
"""

import logging
import os

import requests as _requests

from local_tools.utils import discord_session

logger = logging.getLogger(__name__)

_WEBHOOK_ENV = "DISCORD_FEEDBACK_WEBHOOK"


def notify(record: dict) -> bool:
    """POST a human-readable feedback message to the Discord webhook.

    Returns True if the webhook call succeeded, False if the webhook is
    unconfigured or the POST failed. Never raises.
    """
    webhook = os.environ.get(_WEBHOOK_ENV, "").strip()
    if not webhook:
        return False

    kind = record.get("kind", "rating")
    note = record.get("note", "")
    verdict = record.get("verdict", "")
    output = record.get("output", "")

    if kind == "message":
        text = f"**💬 Feedback Message**\n{note}"
    else:
        text = (
            f"**{'👍' if verdict == 'up' else '👎'} Rating ({verdict})**\n"
            f"```{output[:500]}```\n"
            f"Note: {note}" if note else f"**{'👍' if verdict == 'up' else '👎'} Rating ({verdict})**\n```{output[:500]}```"
        )

    try:
        resp = discord_session.post(webhook, json={"content": text}, timeout=5)
        resp.raise_for_status()
        return True
    except _requests.RequestException as exc:
        logger.warning("Discord webhook POST failed: %s", exc)
        return False

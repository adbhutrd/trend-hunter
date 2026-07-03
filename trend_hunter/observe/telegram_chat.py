"""Telegram chat-ID extraction.

``wire_telegram.sh`` used to inline this logic in bash.  Why a real module:

* Pure function → trivially unit-testable (see ``tests/test_telegram_chat.py``).
* Typed exceptions surface real API errors (no more "no messages" silently).
* One source of truth — bash and config-fault-checks share the same code path.

The bash wrapper still drives:

    * ``curl … getMe``  (validates the token, returns ``$BOT_NAME``)
    * ``curl … getUpdates`` (returns the raw JSON we parse below)
    * exit-code semantics:
        3 → API error (``TelegramAPIError``)
        4 → no usable private chat (``NoPrivateMessageError``)
"""

from __future__ import annotations

from typing import Any


# ── typed errors ──────────────────────────────────────────────────────────────
class TelegramAPIError(Exception):
    """Telegram returned ``ok=False`` — auth, rate limit, malformed payload, …"""


class NoPrivateMessageError(Exception):
    """getUpdates returned no private chat from a non-bot user.

    Sentinels: ``bash wire_telegram.sh`` reads ``NO_PRIVATE_USER_MSG`` on stderr
    and prompts the operator to DM their bot first.
    """


# ── public API ────────────────────────────────────────────────────────────────
def extract_chat_id(data: dict[str, Any], existing: str = "") -> int:
    """Extract a usable ``chat_id`` from a ``getUpdates`` JSON response.

    Resolution order:

    1. If ``existing`` (a previously-saved chat_id from ``.env``) appears in the
       update stream, return it.  Makes re-runs idempotent on the same user.
    2. Otherwise pick the **last** update whose ``chat.type == "private"`` AND
       whose ``message.from.is_bot`` is ``False`` (i.e., the user DM'd the bot).

    Raises:
        TelegramAPIError: ``ok`` field is not ``True`` — caller should treat
            it as a hard failure (token revoked, network blip with HTTP 5xx
            leaked through, etc.).
        NoPrivateMessageError: nothing usable found — caller should ask the
            user to DM their bot, then re-run.

    The function is intentionally side-effect-free: no I/O, no logging.
    Caller is responsible for rate-limit handling and dispatch.
    """
    if not data.get("ok"):
        raise TelegramAPIError("API error: " + (data.get("description") or "unknown"))

    updates: list[dict[str, Any]] = list(data.get("result") or [])
    existing_norm = str(existing).strip() if existing else ""

    # 1) Existing chat_id — forward scan so we tolerate it appearing anywhere
    #    (including inside earlier channel_posts).
    if existing_norm:
        for u in updates:
            msg = u.get("message") or u.get("channel_post") or {}
            chat = msg.get("chat") or {}
            if str(chat.get("id", "")) == existing_norm:
                return int(existing_norm)

    # 2) Latest private chat from a non-bot user — reverse scan.
    for u in reversed(updates):
        msg = u.get("message") or {}
        sender = msg.get("from") or {}
        if sender.get("is_bot"):
            continue
        chat = msg.get("chat") or {}
        if chat.get("type") == "private" and "id" in chat:
            return int(chat["id"])

    raise NoPrivateMessageError("NO_PRIVATE_USER_MSG")

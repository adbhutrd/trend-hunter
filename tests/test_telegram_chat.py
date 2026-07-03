"""Tests for ``trend_hunter.observe.telegram_chat`` — chat_id extraction.

Comprehensive matrix of branches in ``extract_chat_id``:

* API errors → ``TelegramAPIError``
* Empty updates / only channel posts / only bot messages → ``NoPrivateMessageError``
* Single private user message → its chat_id
* Multiple private user messages → LATEST (reversed iteration)
* Bot messages are skipped even if they're the most recent
* ``existing`` parameter takes precedence when present in stream
* ``existing`` mismatch falls through to the latest private non-bot
* chat_id type coercion: Telegram can emit int or str; output always int
"""
from __future__ import annotations

import pytest

from trend_hunter.observe.telegram_chat import (
    NoPrivateMessageError,
    TelegramAPIError,
    extract_chat_id,
)

# ── helpers ────────────────────────────────────────────────────────────────────


def _private_msg(chat_id: int | str, *, sender_is_bot: bool = False) -> dict:
    """Build a getUpdates entry — a private chat message from `sender_is_bot`."""
    return {
        "update_id": 1000,
        "message": {
            "message_id": 1,
            "from": {
                "id": 22,
                "is_bot": sender_is_bot,
                "first_name": "User" if not sender_is_bot else "Bot",
            },
            "chat": {"id": chat_id, "type": "private"},
            "text": "hello",
        },
    }


def _channel_post(chat_id: int) -> dict:
    return {
        "update_id": 2000,
        "channel_post": {
            "message_id": 2,
            "chat": {"id": chat_id, "type": "channel"},
            "text": "channel update",
        },
    }


# ── API error cases ───────────────────────────────────────────────────────────


def test_api_error_raises_exception():
    """`ok=False` from Telegram → ``TelegramAPIError`` carrying description."""
    with pytest.raises(TelegramAPIError, match="Unauthorized"):
        extract_chat_id({"ok": False, "description": "Unauthorized"})


def test_api_error_unknown_description_falls_back_gracefully():
    """Missing `description` should not crash — uses 'unknown' fallback."""
    with pytest.raises(TelegramAPIError, match="unknown"):
        extract_chat_id({"ok": False})


# ── fallback / no-message cases ────────────────────────────────────────────────


def test_empty_updates_raises_no_private():
    """``{"ok": True, "result": []}`` → ``NoPrivateMessageError``."""
    with pytest.raises(NoPrivateMessageError):
        extract_chat_id({"ok": True, "result": []})


def test_missing_result_field_raises_no_private():
    """Defensive: missing `result` field → still raises (treated as empty)."""
    with pytest.raises(NoPrivateMessageError):
        extract_chat_id({"ok": True})


def test_channel_post_only_raises_no_private():
    """Only channel_post updates are not private chat → raises."""
    updates = [_channel_post(1001), _channel_post(1002)]
    with pytest.raises(NoPrivateMessageError):
        extract_chat_id({"ok": True, "result": updates})


def test_only_bot_senders_raises_no_private():
    """All updates come from bots → no human DM tracked → raises."""
    updates = [
        _private_msg(1001, sender_is_bot=True),
        _private_msg(1002, sender_is_bot=True),
    ]
    with pytest.raises(NoPrivateMessageError):
        extract_chat_id({"ok": True, "result": updates})


# ── happy paths ───────────────────────────────────────────────────────────────


def test_single_private_message_returns_chat_id():
    """One private user msg → that chat_id."""
    data = {"ok": True, "result": [_private_msg(555)]}
    assert extract_chat_id(data) == 555


def test_multiple_messages_pick_latest_user_chat():
    """3 user messages → returns the LATEST (reversed iteration)."""
    data = {"ok": True, "result": [
        _private_msg(100),
        _private_msg(200),
        _private_msg(300),  # newest
    ]}
    assert extract_chat_id(data) == 300


def test_skips_bot_message_in_latest_slot():
    """Latest update is a bot; earlier update is a real user → picks user."""
    data = {"ok": True, "result": [
        _private_msg(777),                          # older, real user
        _private_msg(999, sender_is_bot=True),      # newer, bot
    ]}
    assert extract_chat_id(data) == 777


def test_skips_senderless_update_defensively():
    """Update with no `from` key → don't crash, treat as not-from-user."""
    updates = [
        {
            "update_id": 1,
            "message": {
                "message_id": 1,
                # no `from` key at all
                "chat": {"id": 555, "type": "private"},
                "text": "anonymous?",
            },
        },
        _private_msg(1234),  # real user
    ]
    assert extract_chat_id({"ok": True, "result": updates}) == 1234


# ── existing param (re-run idempotency) ────────────────────────────────────────


def test_existing_matches_channel_post_in_stream():
    """`existing` matches a `channel_post.chat.id` → returns existing.

    The forward-scan loop is what makes the script re-run-safe even if the
    user's chat appeared in an earlier channel_post before they DM'd the bot.
    """
    updates = [_channel_post(42), _private_msg(99)]
    assert extract_chat_id({"ok": True, "result": updates}, existing="42") == 42


def test_existing_matches_message_in_stream():
    """Common path: existing chat appears in a real user DM → use it."""
    updates = [_private_msg(42)]
    assert extract_chat_id({"ok": True, "result": updates}, existing="42") == 42


def test_existing_no_match_falls_through_to_latest_user():
    """`existing` not in stream → fall back to last private non-bot user."""
    updates = [_private_msg(100), _private_msg(200)]
    assert extract_chat_id({"ok": True, "result": updates}, existing="999") == 200


def test_existing_empty_string_treated_as_unset():
    """`existing=""` (default) is treated as 'no preference'."""
    updates = [_private_msg(100), _private_msg(200)]
    assert extract_chat_id({"ok": True, "result": updates}, existing="") == 200


# ── type coercion ──────────────────────────────────────────────────────────────


def test_chat_id_string_input_returns_int():
    """Telegram emits `id` as int; pass it through.  String fallback → int."""
    updates = [{"message": {
        "message_id": 1,
        "from": {"id": 22, "is_bot": False, "first_name": "u"},
        "chat": {"id": "8888", "type": "private"},  # string!
        "text": "hi",
    }}]
    assert extract_chat_id({"ok": True, "result": updates}) == 8888
    assert isinstance(extract_chat_id({"ok": True, "result": updates}), int)


def test_chat_id_int_input_returns_int():
    """Normal case: integer chat_id round-trips cleanly."""
    updates = [_private_msg(9001)]
    assert extract_chat_id({"ok": True, "result": updates}) == 9001

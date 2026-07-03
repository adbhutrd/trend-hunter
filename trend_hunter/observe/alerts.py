"""Alerts — push notifications when the feedback loop detects degradation.

Reads ``Settings.discord_webhook``, ``Settings.telegram_bot_token``, and
``Settings.telegram_chat_id``.  All are optional — if none are configured
the module is a silent no-op.

Usage::

    from trend_hunter.observe.alerts import maybe_alert_on_success_rate

    maybe_alert_on_success_rate(storage, "scaffold", days=1, threshold=0.5)
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

from trend_hunter.observe.run_ledger import success_rate

# ── HTTP client timeouts ──────────────────────────────────────────────────────
# Plain ``timeout=10`` only bounds the *read* phase; the OS default TCP
# connect timeout is ~75 s on Linux, so a hung socket would freeze the
# release of new alert emissions for the entire feedback loop.  A tuple
# ``(connect, read)`` is supported on ``urlopen`` from Python 3.10 onwards;
# we target 3.12 so we can lean on it.  Cap connect time hard since a
# healthy hostname reaches us in <2 s and any longer means trouble somewhere.
CONNECT_TIMEOUT_S = 5
READ_TIMEOUT_S = 10


# ── dispatcher ────────────────────────────────────────────────────────────────
def _send_discord(webhook: str, message: str) -> None:
    payload = json.dumps({"content": message, "username": "trend-hunter"}).encode()
    req = urllib.request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(
            req,
            timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S),
        )
    except Exception as exc:  # noqa: BLE001
        from trend_hunter.core.logging import get

        get().warning("discord alert failed: %s", exc)


def _send_telegram(token: str, chat_id: str, message: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(
            req,
            timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S),
        )
    except Exception as exc:  # noqa: BLE001
        from trend_hunter.core.logging import get

        get().warning("telegram alert failed: %s", exc)


# ── public helpers ─────────────────────────────────────────────────────────────
def notify(  # noqa: PLR0913
    message: str,
    *,
    discord_webhook: str | None = None,
    telegram_bot_token: str | None = None,
    telegram_chat_id: str | None = None,
) -> None:
    """Deliver *message* to every configured channel.

    Call with keyword args from your own ``Settings`` instance, or omit them
    all — the function will load ``get_settings()`` automatically.
    """
    if discord_webhook or telegram_bot_token:
        from trend_hunter.core.config import get_settings

        s = get_settings()
        wh = discord_webhook or s.discord_webhook
        tok = telegram_bot_token or s.telegram_bot_token
        cid = telegram_chat_id or s.telegram_chat_id

        if wh:
            _send_discord(wh, message)
        if tok and cid:
            _send_telegram(tok, cid, message)


def maybe_alert_on_success_rate(
    storage: Any,
    command: str,
    days: int = 1,
    threshold: float = 0.5,
) -> float:
    """Check *command*\\'s success rate.  If below *threshold*, push an alert.

    Returns the actual rate (so callers can log it).  No-op if all alert
    channels are unconfigured.
    """
    rate = success_rate(storage, command, days=days)
    if rate < threshold:
        msg = (
            f"⚠️  *trend-hunter alert* — ``{command}`` success rate "
            f"dropped to {rate * 100:.0f}% (threshold {threshold * 100:.0f}%)."
        )
        notify(msg)
    return rate


# ── loop-report integration ────────────────────────────────────────────────────
def alert_on_degradation(storage: Any) -> list[dict]:
    """Check every core command's 1-day success rate; alert + return offenders.

    Called at the end of ``loop-report`` so degradation surfaces even when
    no one is watching the dashboard.
    """
    from trend_hunter.observe.constants import CORE_COMMANDS

    offenders: list[dict] = []
    for cmd in CORE_COMMANDS:
        rate = success_rate(storage, cmd, days=1)
        if 0 < rate < 0.5:
            offenders.append({"command": cmd, "rate": rate})
            msg = (
                f"⚠️  *trend-hunter alert* — ``{cmd}`` success rate "
                f"dropped to {rate * 100:.0f}% in the last 24h."
            )
            notify(msg)
    return offenders

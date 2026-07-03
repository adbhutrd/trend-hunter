"""Mailer stub — Phase 1 dry-run only.

Phase 2 will swap this for `GmailSMTPMailer` using `aiosmtplib`
+ a Gmail App Password, with mandatory one-click unsubscribe
and Listmonk fallback when outbound >500 emails/day.

Keeping dry-run by default means we never accidentally send a real
email in testing, and we never ship a tool that emails without
explicit operator approval.
"""

from __future__ import annotations

from loguru import logger

from trend_hunter.core.types import Lead


class StubMailer:
    async def send(
        self,
        lead: Lead,
        template: str,
        dry_run: bool = True,
    ) -> dict[str, str]:
        msg = (
            f"[dry-run mail] to={lead.contact_email or lead.domain!r} "
            f"template={template!r} score={lead.score:.2f}"
        )
        if dry_run:
            logger.info(msg)
            return {"status": "dry_run", "to": lead.contact_email or lead.domain}
        # Phase 2: real Gmail SMTP / Listmonk goes here.
        raise NotImplementedError(
            "Real send is not wired in Phase 1. "
            "Implement GmailSMTPMailer in adapters/mailer_gmail.py, "
            "then point the Mailer port at it.",
        )

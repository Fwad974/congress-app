"""Minimal outbound email.

The app has no hard dependency on email. When SMTP_HOST is configured, messages
are sent via SMTP; otherwise ``send_email`` logs the message (including any link)
at INFO level so operators can still retrieve, e.g., a password-reset link in
development or a not-yet-wired deployment. This keeps flows that "send an email"
functional instead of silently dead.
"""
import logging
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def email_configured() -> bool:
    return bool(get_settings().SMTP_HOST)


def send_email(to: str, subject: str, body: str) -> bool:
    cfg = get_settings()
    if not cfg.SMTP_HOST:
        # No mail server — log so the content is still retrievable.
        logger.info("EMAIL (not sent, SMTP unconfigured) to=%s subject=%s\n%s",
                    to, subject, body)
        return False
    try:
        msg = EmailMessage()
        msg["From"] = cfg.SMTP_FROM or cfg.VAPID_SUBJECT.replace("mailto:", "") or "noreply@congress"
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT, timeout=15) as s:
            if cfg.SMTP_STARTTLS:
                s.starttls()
            if cfg.SMTP_USER:
                s.login(cfg.SMTP_USER, cfg.SMTP_PASSWORD)
            s.send_message(msg)
        return True
    except Exception as e:  # noqa: BLE001 - never let a mail failure break a request
        logger.warning("Email send failed to=%s: %s", to, e)
        return False

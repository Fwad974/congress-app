"""
Web Push delivery — send notifications to users' browsers even when the app
tab is closed, using the VAPID keys configured in settings.

This sits *on top of* the persisted notification feed: the feed is the source
of truth, web push is a best-effort delivery channel. If VAPID keys aren't
configured (or pywebpush isn't installed) every function here is a safe no-op
and the app still works via the in-app feed.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Iterable, List

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.notification_service import in_quiet_hours
from app.models.notification import PushSubscription, NotificationSettings
from app.schemas.notification import DEFAULT_PREFS

logger = logging.getLogger(__name__)
settings = get_settings()

# pywebpush is an optional dependency. If it's unavailable we degrade to the
# in-app feed instead of crashing the app or its tests.
try:  # pragma: no cover - exercised via monkeypatch in tests
    from pywebpush import webpush as _webpush, WebPushException
except Exception:  # noqa: BLE001 - any import problem disables push
    _webpush = None

    class WebPushException(Exception):
        def __init__(self, message="", response=None):
            super().__init__(message)
            self.response = response


def push_enabled() -> bool:
    """True when VAPID keys are configured and the sender is importable."""
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)


def _send_one(sub: PushSubscription, payload_json: str) -> bool:
    """Send to a single subscription.

    Returns False when the subscription is permanently gone (HTTP 404/410) and
    should be deleted; True otherwise (delivered or a transient failure worth
    keeping the subscription for).
    """
    if _webpush is None:  # pragma: no cover - dependency missing
        logger.warning("pywebpush not installed; skipping web push")
        return True
    try:
        _webpush(
            subscription_info={
                "endpoint": sub.endpoint,
                "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
            },
            data=payload_json,
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.VAPID_SUBJECT},
            ttl=settings.PUSH_TTL,
            # Per-send timeout so one unresponsive push endpoint can't stall the
            # whole serial fan-out (a full-congress emergency alert) for minutes.
            timeout=settings.PUSH_SEND_TIMEOUT,
        )
        return True
    except WebPushException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (404, 410):
            return False  # expired / unsubscribed — drop it
        logger.warning("web push failed (status=%s): %s", status, e)
        return True
    except Exception as e:  # noqa: BLE001 - never let delivery break a request
        logger.warning("web push error: %s", e)
        return True


def _deliverable_ids(db: Session, ids: List[int]) -> List[int]:
    """Drop users who turned notifications off or are in quiet hours right now.

    Only applied to non-emergency sends; emergency alerts skip this entirely.
    """
    now = datetime.now(timezone.utc)
    rows = db.query(NotificationSettings.user_id, NotificationSettings.prefs).filter(
        NotificationSettings.user_id.in_(ids)
    ).all()
    prefs_by = {r[0]: (r[1] or {}) for r in rows}
    keep = []
    for uid in ids:
        prefs = {**DEFAULT_PREFS, **prefs_by.get(uid, {})}
        if not prefs.get("enabled", True):
            continue
        if in_quiet_hours(prefs, now):
            continue
        keep.append(uid)
    return keep


def send_push_to_users(
    db: Session, user_ids: Iterable[int], payload: dict, emergency: bool = False,
) -> int:
    """Deliver `payload` to every push subscription owned by `user_ids`.

    Non-emergency sends skip users who disabled notifications or are in their
    quiet-hours window; emergency alerts bypass both. Prunes subscriptions the
    push service reports as gone. Returns the number of successful sends.
    """
    if not push_enabled():
        return 0
    ids = list(set(user_ids))
    if not ids:
        return 0
    if not emergency:
        ids = _deliverable_ids(db, ids)
        if not ids:
            return 0

    subs = db.query(PushSubscription).filter(
        PushSubscription.user_id.in_(ids)
    ).all()
    if not subs:
        return 0

    payload_json = json.dumps(payload)
    sent = 0
    stale: List[int] = []
    for sub in subs:
        if _send_one(sub, payload_json):
            sent += 1
        else:
            stale.append(sub.id)

    if stale:
        db.query(PushSubscription).filter(
            PushSubscription.id.in_(stale)
        ).delete(synchronize_session=False)
        db.commit()
    return sent


def deliver_push(user_ids: List[int], payload: dict, emergency: bool = False) -> None:
    """BackgroundTask entrypoint — owns its own DB session so it can run after
    the originating request's session has closed."""
    if not push_enabled() or not user_ids:
        return
    db = SessionLocal()
    try:
        send_push_to_users(db, user_ids, payload, emergency=emergency)
    finally:
        db.close()

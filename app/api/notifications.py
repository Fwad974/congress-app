"""
Notification preferences + upcoming-bookmarked-items API.

The client-side scheduler calls /upcoming on every page load to know what
reminders to set; /settings is the user's preference editor.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import asc, desc

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.config import get_settings
from app.core.push_service import push_enabled
from app.models.user import User
from app.models.notification import (
    NotificationSettings, UserNotification, PushSubscription,
)
from app.models.schedule import ScheduleItem, ScheduleBookmark
from app.schemas.notification import (
    NotificationPrefs, NotificationSettingsResponse,
    UpcomingItem, UpcomingResponse, DEFAULT_PREFS,
    FeedItem, FeedResponse,
    PushKeyResponse, PushSubscriptionIn, PushUnsubscribeIn,
)

router = APIRouter()

settings = get_settings()
FEED_LIMIT = 50  # most-recent notifications returned to the client


def _get_or_create(db: Session, user_id: int) -> NotificationSettings:
    row = db.query(NotificationSettings).filter(
        NotificationSettings.user_id == user_id
    ).first()
    if row:
        return row
    row = NotificationSettings(user_id=user_id, prefs=dict(DEFAULT_PREFS))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/settings", response_model=NotificationSettingsResponse)
def get_settings(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = _get_or_create(db, user.id)
    prefs = NotificationPrefs(**{**DEFAULT_PREFS, **(row.prefs or {})})
    return NotificationSettingsResponse(prefs=prefs, updated_at=row.updated_at)


@router.put("/settings", response_model=NotificationSettingsResponse)
def update_settings(
    prefs: NotificationPrefs,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = _get_or_create(db, user.id)
    # Re-assign so SQLAlchemy detects the JSON column change
    row.prefs = prefs.model_dump()
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return NotificationSettingsResponse(prefs=prefs, updated_at=row.updated_at)


@router.get("/upcoming", response_model=UpcomingResponse)
def upcoming(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Return the user's prefs + their bookmarked items that haven't ended yet.

    The client uses this to build setTimeout-based reminders. We only return
    items whose end_time is in the future, so the scheduler isn't burdened
    with already-finished sessions.
    """
    row = _get_or_create(db, user.id)
    prefs = NotificationPrefs(**{**DEFAULT_PREFS, **(row.prefs or {})})

    now = datetime.now(timezone.utc)
    bookmark_q = db.query(ScheduleBookmark.schedule_item_id).filter(
        ScheduleBookmark.user_id == user.id
    )
    items = (
        db.query(ScheduleItem)
        .filter(ScheduleItem.id.in_(bookmark_q))
        .filter(ScheduleItem.end_time > now)
        .order_by(asc(ScheduleItem.start_time))
        .all()
    )
    return UpcomingResponse(
        prefs=prefs,
        items=[UpcomingItem(
            id=i.id, title=i.title, type=i.type.value, location=i.location,
            start_time=i.start_time, end_time=i.end_time,
        ) for i in items],
        server_time=now,
    )


# ─── Delivered notification feed ─────────────────────────────────
@router.get("/feed", response_model=FeedResponse)
def feed(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The user's most-recent notifications plus their unread count.

    The client polls this and surfaces unread rows as toasts, then marks them
    read via /feed/read-all.
    """
    rows = (
        db.query(UserNotification)
        .filter(UserNotification.user_id == user.id)
        .order_by(desc(UserNotification.created_at))
        .limit(FEED_LIMIT)
        .all()
    )
    unread_count = (
        db.query(UserNotification)
        .filter(UserNotification.user_id == user.id,
                UserNotification.read_at.is_(None))
        .count()
    )
    return FeedResponse(
        items=[FeedItem(
            id=r.id, kind=r.kind, title=r.title, body=r.body,
            schedule_item_id=r.schedule_item_id,
            read=r.read_at is not None, created_at=r.created_at,
        ) for r in rows],
        unread_count=unread_count,
        server_time=datetime.now(timezone.utc),
    )


@router.post("/feed/read-all")
def mark_all_read(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    updated = (
        db.query(UserNotification)
        .filter(UserNotification.user_id == user.id,
                UserNotification.read_at.is_(None))
        .update({UserNotification.read_at: datetime.now(timezone.utc)},
                synchronize_session=False)
    )
    db.commit()
    return {"updated": updated}


@router.post("/feed/{notif_id}/read")
def mark_read(
    notif_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.query(UserNotification).filter(
        UserNotification.id == notif_id,
        UserNotification.user_id == user.id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Notification not found")
    if row.read_at is None:
        row.read_at = datetime.now(timezone.utc)
        db.commit()
    return {"message": "Marked read", "read": True}


# ─── Web Push subscriptions ──────────────────────────────────────
@router.get("/push/key", response_model=PushKeyResponse)
def push_key(user: User = Depends(get_current_user)):
    """VAPID public key + whether push is configured, for the client to
    decide whether to register a service worker and subscribe."""
    return PushKeyResponse(
        enabled=push_enabled(),
        public_key=settings.VAPID_PUBLIC_KEY or "",
    )


@router.post("/push/subscribe")
def push_subscribe(
    sub: PushSubscriptionIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Save (or re-point to this user) a browser push subscription."""
    existing = db.query(PushSubscription).filter(
        PushSubscription.endpoint == sub.endpoint
    ).first()
    if existing:
        existing.user_id = user.id
        existing.p256dh = sub.keys.p256dh
        existing.auth = sub.keys.auth
    else:
        db.add(PushSubscription(
            user_id=user.id, endpoint=sub.endpoint,
            p256dh=sub.keys.p256dh, auth=sub.keys.auth,
        ))
    db.commit()
    return {"message": "subscribed"}


@router.post("/push/unsubscribe")
def push_unsubscribe(
    body: PushUnsubscribeIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    deleted = db.query(PushSubscription).filter(
        PushSubscription.user_id == user.id,
        PushSubscription.endpoint == body.endpoint,
    ).delete(synchronize_session=False)
    db.commit()
    return {"message": "unsubscribed" if deleted else "not subscribed"}

"""
Notification fan-out — turn an admin change to a schedule item into per-user
feed entries for everyone who bookmarked that item.

We only *add* rows (and flush so callers can read counts); committing is left to
the caller so the notifications land in the same transaction as the schedule
mutation and its audit-log entry.
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.models.schedule import ScheduleBookmark, ScheduleItem
from app.models.notification import UserNotification


def notify_bookmarkers(
    db: Session,
    item: ScheduleItem,
    kind: str,
    body: str,
    exclude_user_id: Optional[int] = None,
) -> int:
    """Create a UserNotification for every user who bookmarked `item`.

    `exclude_user_id` skips one user — typically the admin who made the change,
    so they don't get notified about their own edit. Returns the number of
    notifications created.
    """
    rows = db.query(ScheduleBookmark.user_id).filter(
        ScheduleBookmark.schedule_item_id == item.id
    ).all()
    user_ids = {r[0] for r in rows}
    user_ids.discard(exclude_user_id)

    for uid in user_ids:
        db.add(UserNotification(
            user_id=uid,
            schedule_item_id=item.id,
            kind=kind,
            title=item.title,
            body=body,
        ))
    db.flush()
    return len(user_ids)

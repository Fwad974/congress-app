# Notifications

The app notifies attendees about the sessions they care about. There are two
independent mechanisms:

1. **Reminders** — client-side "starts in N minutes" alerts for *bookmarked*
   sessions, driven by each user's notification preferences.
2. **Schedule-change notifications** — when an admin **edits or cancels** a
   session, everyone who bookmarked it is told. Delivered through a persisted
   in-app feed and, optionally, real browser Web Push.

The in-app feed is always the **source of truth**. Web Push is a best-effort
delivery channel layered on top — if it isn't configured (or a push fails),
users still get the notification via the feed on their next visit.

---

## 1. Reminders (bookmarked sessions)

- The client (`app/static/js/notifications.js`) polls
  `GET /api/notifications/upcoming` on load, on focus, and every 5 minutes.
- The endpoint returns the user's preferences plus their bookmarked items whose
  `end_time` is still in the future.
- The client sets `setTimeout`s to fire a toast + (optional) Web Notification +
  sound at each configured **lead time** (e.g. 10 min before, at start).
- De-duplication across tabs/reloads uses `localStorage` keyed by
  `(item_id, lead_minutes, day)`.

Preferences live in `NotificationSettings.prefs` (a JSON column) and are edited
via `GET`/`PUT /api/notifications/settings`:

| Field        | Type            | Notes                                  |
|--------------|-----------------|----------------------------------------|
| `enabled`    | bool            | Master switch for reminders + sound    |
| `lead_times` | list[int]       | Minutes before start (0 = "at start")  |
| `sound`      | str             | `bell` \| `chime` \| `buzz` \| `off`   |
| `volume`     | float (0.0–1.0) | Reminder/notification sound volume     |

---

## 2. Schedule-change notifications

### Trigger

| Admin action on a **bookmarked** item | Result                                   |
|---------------------------------------|------------------------------------------|
| Any update (`PUT /api/schedule/{id}`) | `kind="updated"` notification per bookmarker |
| Delete (`DELETE /api/schedule/{id}`)  | `kind="cancelled"` notification per bookmarker |

- A no-op update (nothing actually changed) creates nothing.
- The acting admin is excluded, even if they bookmarked the item.
- New items notify nobody (no bookmarks yet) — by design.

The `body` is a human summary, e.g. *"This session has been rescheduled to
Feb 16, 14:30 and moved to Hall B."* or *"This session has been cancelled."*

### Data model

`app/models/notification.py`:

- **`UserNotification`** — one row per recipient. `title`/`body` are
  denormalized snapshots so the feed stays meaningful after the source item is
  deleted (`schedule_item_id` is then set NULL). `read_at` tracks read state.
- **`PushSubscription`** — one row per browser/device. `endpoint` is unique;
  re-subscribing upserts.

### Fan-out

`app/core/notification_service.notify_bookmarkers()` creates a `UserNotification`
for every bookmarker (minus the excluded admin) within the same transaction as
the schedule change + audit-log entry, and returns the recipient IDs. The
schedule endpoints then schedule a Web Push to those recipients via FastAPI
`BackgroundTasks` **after the commit**, so a rolled-back change never pushes.

### Feed API

| Method & path                              | Purpose                          |
|--------------------------------------------|----------------------------------|
| `GET  /api/notifications/feed`             | Recent notifications + unread count |
| `POST /api/notifications/feed/{id}/read`   | Mark one read                    |
| `POST /api/notifications/feed/read-all`    | Mark all read                    |

The client polls `/feed`, surfaces unread rows as a toast + (optional) Web
Notification + sound, then calls `/feed/read-all`. `localStorage` dedups across
tabs.

---

## 3. Web Push setup

Web Push uses the [VAPID](https://datatracker.ietf.org/doc/html/rfc8292)
protocol via [`pywebpush`](https://pypi.org/project/pywebpush/).

### Configuration

| Env var             | Required | Description                                   |
|---------------------|----------|-----------------------------------------------|
| `VAPID_PUBLIC_KEY`  | No       | Base64url application server key (sent to browser) |
| `VAPID_PRIVATE_KEY` | No       | Base64url private key — **keep secret**       |
| `VAPID_SUBJECT`     | No       | `mailto:` contact for push services           |
| `PUSH_TTL`          | No       | Seconds a push service retains an undelivered message (default 86400) |
| `VAPID_KEY_FILE`    | No       | Where the container persists auto-generated keys (default `/app/data/vapid.env`) |

When either key is blank, push is **disabled** and the app uses the feed only.

### Docker (automatic)

On first boot, `entrypoint.sh`:

1. If `VAPID_PUBLIC_KEY`/`VAPID_PRIVATE_KEY` are set in the environment, uses them.
2. Else loads a previously persisted pair from `$VAPID_KEY_FILE`.
3. Else generates a new pair (`gen_vapid_keys.py --bare`) and persists it.

The keys live on the `vapid_keys` volume (mounted at `/app/data`) so the public
key is **stable across restarts** — important, because a changed public key
invalidates every existing browser subscription.

### Local (manual)

```bash
python gen_vapid_keys.py          # prints VAPID_* lines
# paste them into .env, then restart
```

### Client flow

1. `notifications.js` calls `GET /api/notifications/push/key`. If push is enabled
   and the user has granted notification permission, it registers
   `/static/sw.js` and subscribes via the Push API using the public key.
2. The subscription is sent to `POST /api/notifications/push/subscribe`.
3. The service worker (`app/static/sw.js`) receives `push` events and shows a
   system notification; clicking it focuses/opens `/schedule`.
4. `POST /api/notifications/push/unsubscribe` removes a subscription.

Subscriptions the push service reports as gone (HTTP 404/410) are pruned
automatically when a send fails.

| Method & path                                | Purpose                       |
|----------------------------------------------|-------------------------------|
| `GET  /api/notifications/push/key`           | VAPID public key + enabled flag |
| `POST /api/notifications/push/subscribe`     | Save/upsert a subscription    |
| `POST /api/notifications/push/unsubscribe`   | Remove a subscription         |

---

## Files

| File                                  | Role                                   |
|---------------------------------------|----------------------------------------|
| `app/models/notification.py`          | `NotificationSettings`, `UserNotification`, `PushSubscription` |
| `app/schemas/notification.py`         | Request/response schemas               |
| `app/api/notifications.py`            | Settings, upcoming, feed, push endpoints |
| `app/core/notification_service.py`    | Bookmarker fan-out                     |
| `app/core/push_service.py`            | VAPID-signed Web Push delivery         |
| `app/api/schedule.py`                 | Triggers fan-out on update/delete      |
| `app/static/js/notifications.js`      | Client reminders, feed polling, push registration |
| `app/static/sw.js`                    | Service worker for push                |
| `gen_vapid_keys.py`                   | VAPID key generator                    |

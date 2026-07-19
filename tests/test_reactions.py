"""
Tests for live emoji reactions (validation + relay). No DB persistence.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from app.models.schedule import ScheduleItem, ScheduleType
from app.core.realtime import broadcaster, react_channel
from tests.conftest import auth_cookie


def _item(db):
    now = datetime.now(timezone.utc)
    item = ScheduleItem(title="S", type=ScheduleType.talk,
                        start_time=now + timedelta(hours=1),
                        end_time=now + timedelta(hours=2))
    db.add(item); db.commit(); db.refresh(item)
    return item


class TestReactions:
    def test_unauthenticated_blocked(self, client, db):
        item = _item(db)
        assert client.post(f"/api/sessions/{item.id}/reactions",
                          json={"counts": {"🔥": 1}}).status_code == 401
        assert client.get("/api/reactions/emojis").status_code == 401

    def test_emojis_list(self, client, attendee):
        r = client.get("/api/reactions/emojis", cookies=auth_cookie(attendee))
        assert r.status_code == 200
        assert "🔥" in r.json()["emojis"]

    def test_valid_reaction(self, client, attendee, db):
        item = _item(db)
        r = client.post(f"/api/sessions/{item.id}/reactions",
                        json={"counts": {"🔥": 2, "💡": 1}},
                        cookies=auth_cookie(attendee))
        assert r.status_code == 200
        assert r.json()["counts"] == {"🔥": 2, "💡": 1}

    def test_unknown_emoji_rejected(self, client, attendee, db):
        item = _item(db)
        r = client.post(f"/api/sessions/{item.id}/reactions",
                        json={"counts": {"🍕": 5}}, cookies=auth_cookie(attendee))
        assert r.status_code == 400

    def test_per_emoji_and_total_clamped(self, client, attendee, db):
        item = _item(db)
        r = client.post(f"/api/sessions/{item.id}/reactions",
                        json={"counts": {"🔥": 100, "💡": 100, "🤔": 100, "👏": 100}},
                        cookies=auth_cookie(attendee))
        assert r.status_code == 200
        counts = r.json()["counts"]
        assert all(v <= 20 for v in counts.values())   # per-emoji cap
        assert sum(counts.values()) <= 60              # total cap

    def test_missing_session(self, client, attendee):
        assert client.post("/api/sessions/99999/reactions",
                          json={"counts": {"🔥": 1}},
                          cookies=auth_cookie(attendee)).status_code == 404

    def test_burst_reaches_subscriber(self, client, attendee, db):
        item = _item(db)
        ch = react_channel(item.id)
        q = asyncio.Queue()
        broadcaster._local[ch].add(q)
        try:
            client.post(f"/api/sessions/{item.id}/reactions",
                        json={"counts": {"🔥": 3}}, cookies=auth_cookie(attendee))
            raw = q.get_nowait()   # publish happened during the request
        finally:
            broadcaster._local[ch].discard(q)
        import json
        msg = json.loads(raw)
        assert msg["type"] == "burst" and msg["counts"]["🔥"] == 3

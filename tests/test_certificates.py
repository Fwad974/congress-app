"""
Tests for session attendance check-in and certificates/CME.
"""
from datetime import datetime, timedelta, timezone

from app.models.schedule import ScheduleItem, ScheduleType
from app.models.attendance import SessionAttendance  # noqa: F401 - register table
from app.models.user import UserRole
from tests.conftest import auth_cookie, make_user


def _item(db, title="Keynote", speaker_id=None):
    now = datetime.now(timezone.utc)
    item = ScheduleItem(
        title=title, type=ScheduleType.talk, speaker_id=speaker_id,
        start_time=now + timedelta(hours=1), end_time=now + timedelta(hours=2),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _code_for(client, db, item, admin):
    # Admin fetching the QR generates the attend code lazily.
    client.get(f"/api/schedule/{item.id}/qr", cookies=auth_cookie(admin))
    db.refresh(item)
    return item.attend_code


class TestSessionQR:
    def test_admin_qr_generates_code_and_svg(self, client, db):
        admin = make_user(db, email="ca@test.com", role=UserRole.admin)
        item = _item(db)
        assert item.attend_code is None
        r = client.get(f"/api/schedule/{item.id}/qr", cookies=auth_cookie(admin))
        assert r.status_code == 200 and b"<svg" in r.content
        db.refresh(item)
        assert item.attend_code and len(item.attend_code) >= 8

    def test_attendee_cannot_get_session_qr(self, client, db):
        att = make_user(db, email="cu@test.com", role=UserRole.attendee)
        item = _item(db)
        assert client.get(f"/api/schedule/{item.id}/qr",
                         cookies=auth_cookie(att)).status_code == 403

    def test_qr_print_page_admin_only(self, client, db):
        admin = make_user(db, email="cp@test.com", role=UserRole.admin)
        att = make_user(db, email="cpa@test.com", role=UserRole.attendee)
        item = _item(db)
        r = client.get(f"/schedule/{item.id}/qr-print", cookies=auth_cookie(admin))
        assert r.status_code == 200 and "Session Check-in" in r.text
        assert client.get(f"/schedule/{item.id}/qr-print", cookies=auth_cookie(att),
                          follow_redirects=False).status_code == 302


class TestCheckin:
    def test_checkin_records_attendance(self, client, db):
        admin = make_user(db, email="cc@test.com", role=UserRole.admin)
        att = make_user(db, email="cca@test.com", role=UserRole.attendee)
        item = _item(db)
        code = _code_for(client, db, item, admin)
        r = client.post("/api/attendance/checkin", json={"code": code},
                        cookies=auth_cookie(att))
        assert r.status_code == 200
        b = r.json()
        assert b["session_title"] == "Keynote" and b["attended"] == 1 and b["already"] is False

    def test_checkin_idempotent(self, client, db):
        admin = make_user(db, email="ci@test.com", role=UserRole.admin)
        att = make_user(db, email="cia@test.com", role=UserRole.attendee)
        code = _code_for(client, db, _item(db), admin)
        client.post("/api/attendance/checkin", json={"code": code}, cookies=auth_cookie(att))
        r = client.post("/api/attendance/checkin", json={"code": code}, cookies=auth_cookie(att))
        assert r.json()["already"] is True and r.json()["attended"] == 1

    def test_bad_code_404(self, client, db):
        att = make_user(db, email="cb@test.com", role=UserRole.attendee)
        assert client.post("/api/attendance/checkin", json={"code": "NOPE9999"},
                          cookies=auth_cookie(att)).status_code == 404

    def test_attendance_mine(self, client, db):
        admin = make_user(db, email="cm@test.com", role=UserRole.admin)
        att = make_user(db, email="cma@test.com", role=UserRole.attendee)
        code = _code_for(client, db, _item(db, title="Workshop"), admin)
        client.post("/api/attendance/checkin", json={"code": code}, cookies=auth_cookie(att))
        rows = client.get("/api/attendance/mine", cookies=auth_cookie(att)).json()
        assert len(rows) == 1 and rows[0]["title"] == "Workshop"


class TestCertificates:
    def test_status_locked_by_default(self, client, db):
        att = make_user(db, email="cs@test.com", role=UserRole.attendee)
        d = client.get("/api/certificates/status", cookies=auth_cookie(att)).json()
        kinds = {c["kind"]: c for c in d["certificates"]}
        assert set(kinds) == {"attendance", "cme", "speaker"}
        assert not any(c["unlocked"] for c in d["certificates"])

    def test_attendance_cert_unlocks_at_goal(self, client, db):
        from app.core.config import get_settings
        goal = get_settings().CERT_MIN_SESSIONS
        admin = make_user(db, email="cg@test.com", role=UserRole.admin)
        att = make_user(db, email="cga@test.com", role=UserRole.attendee)
        for i in range(goal):
            code = _code_for(client, db, _item(db, title=f"S{i}"), admin)
            client.post("/api/attendance/checkin", json={"code": code},
                        cookies=auth_cookie(att))
        d = client.get("/api/certificates/status", cookies=auth_cookie(att)).json()
        kinds = {c["kind"]: c for c in d["certificates"]}
        assert kinds["attendance"]["unlocked"] is True
        assert d["attended_sessions"] == goal

    def test_speaker_cert_from_schedule(self, client, db):
        sp = make_user(db, email="csp@test.com", role=UserRole.speaker)
        _item(db, title="My talk", speaker_id=sp.id)
        d = client.get("/api/certificates/status", cookies=auth_cookie(sp)).json()
        kinds = {c["kind"]: c for c in d["certificates"]}
        assert kinds["speaker"]["unlocked"] is True

    def test_view_page_locked_redirects(self, client, db):
        att = make_user(db, email="cv@test.com", role=UserRole.attendee)
        r = client.get("/certificates/attendance/view", cookies=auth_cookie(att),
                       follow_redirects=False)
        assert r.status_code == 302

    def test_view_page_unlocked_renders(self, client, db):
        sp = make_user(db, email="cvs@test.com", role=UserRole.speaker,
                       full_name="Dr Speaker")
        _item(db, speaker_id=sp.id)
        r = client.get("/certificates/speaker/view", cookies=auth_cookie(sp))
        assert r.status_code == 200
        assert "Dr Speaker" in r.text and "Speaker Certificate" in r.text

    def test_certificates_page_dynamic(self, client, attendee):
        html = client.get("/certificates", cookies=auth_cookie(attendee)).text
        assert "Session check-in" in html and "certificates/status" in html

"""
Tests for session attendance check-in and certificates/CME.
"""
from datetime import datetime, timedelta, timezone

from app.models.schedule import ScheduleItem, ScheduleType
from app.models.attendance import SessionAttendance  # noqa: F401 - register table
from app.models.certificate import IssuedCertificate  # noqa: F401 - register table
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
        # "survey" appears while the feedback feature is enabled (default in tests).
        assert {"attendance", "cme", "speaker"} <= set(kinds)
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


class TestCertificatePDF:
    def _speaker(self, db, email="pdf@test.com", name="Dr PDF"):
        sp = make_user(db, email=email, role=UserRole.speaker, full_name=name)
        _item(db, title="PDF talk", speaker_id=sp.id)
        return sp

    def test_download_locked_403(self, client, db):
        att = make_user(db, email="pl@test.com", role=UserRole.attendee)
        r = client.get("/api/certificates/attendance/download", cookies=auth_cookie(att))
        assert r.status_code == 403

    def test_download_unknown_kind_404(self, client, db):
        att = make_user(db, email="pk@test.com", role=UserRole.attendee)
        assert client.get("/api/certificates/bogus/download",
                          cookies=auth_cookie(att)).status_code == 404

    def test_download_requires_auth(self, client, db):
        assert client.get("/api/certificates/attendance/download").status_code == 401

    def test_download_pdf_with_stable_serial(self, client, db):
        sp = self._speaker(db)
        r1 = client.get("/api/certificates/speaker/download", cookies=auth_cookie(sp))
        assert r1.status_code == 200
        assert r1.headers["content-type"].startswith("application/pdf")
        assert r1.content.startswith(b"%PDF")
        assert "attachment" in r1.headers["content-disposition"]
        rec = db.query(IssuedCertificate).filter_by(user_id=sp.id, kind="speaker").one()
        assert rec.serial.startswith("DSCC") and "-SPK-" in rec.serial
        # Second download reuses the same serial and issue date.
        client.get("/api/certificates/speaker/download", cookies=auth_cookie(sp))
        assert db.query(IssuedCertificate).filter_by(user_id=sp.id).count() == 1

    def test_view_page_shows_serial_and_matches_download(self, client, db):
        sp = self._speaker(db, email="pv@test.com", name="Dr View")
        html = client.get("/certificates/speaker/view", cookies=auth_cookie(sp)).text
        rec = db.query(IssuedCertificate).filter_by(user_id=sp.id, kind="speaker").one()
        assert rec.serial in html and "Scan to verify" in html and "<svg" in html
        # Download after view keeps the same record.
        client.get("/api/certificates/speaker/download", cookies=auth_cookie(sp))
        assert db.query(IssuedCertificate).filter_by(user_id=sp.id).count() == 1


class TestCertificateVerification:
    def _issued_serial(self, client, db, email="vf@test.com", name="Dr Verify"):
        sp = make_user(db, email=email, role=UserRole.speaker, full_name=name)
        _item(db, speaker_id=sp.id)
        client.get("/api/certificates/speaker/download", cookies=auth_cookie(sp))
        return db.query(IssuedCertificate).filter_by(user_id=sp.id).one().serial

    def test_verify_api_public_and_valid(self, client, db):
        serial = self._issued_serial(client, db)
        r = client.get(f"/api/certificates/verify/{serial}")  # no auth cookie
        assert r.status_code == 200
        b = r.json()
        assert b["valid"] is True and b["holder"] == "Dr Verify"
        assert b["kind"] == "speaker" and b["serial"] == serial

    def test_verify_api_unknown_404(self, client, db):
        assert client.get("/api/certificates/verify/DSCC2027-ATT-NOPE").status_code == 404

    def test_verify_page_valid(self, client, db):
        serial = self._issued_serial(client, db, email="vp@test.com", name="Dr Page")
        html = client.get(f"/verify/{serial}").text
        assert "Valid certificate" in html and "Dr Page" in html and serial in html

    def test_verify_page_not_found(self, client, db):
        html = client.get("/verify/DSCC2027-CME-FFFFFFFF").text
        assert "Not found" in html

    def test_verify_page_revoked(self, client, db):
        serial = self._issued_serial(client, db, email="vr@test.com", name="Dr Revoked")
        rec = db.query(IssuedCertificate).filter_by(serial=serial).one()
        rec.revoked = True
        db.commit()
        html = client.get(f"/verify/{serial}").text
        assert "Revoked" in html and "Dr Revoked" not in html
        assert client.get(f"/api/certificates/verify/{serial}").json()["valid"] is False


class TestAttendanceReport:
    def test_export_requires_auth(self, client, db):
        assert client.get("/api/attendance/report/export").status_code == 401

    def test_export_csv_contents(self, client, db):
        admin = make_user(db, email="re@test.com", role=UserRole.admin)
        att = make_user(db, email="rea@test.com", role=UserRole.attendee,
                        full_name="Report User")
        for title in ("Morning Keynote", "Gene Therapy Workshop"):
            code = _code_for(client, db, _item(db, title=title), admin)
            client.post("/api/attendance/checkin", json={"code": code},
                        cookies=auth_cookie(att))
        r = client.get("/api/attendance/report/export", cookies=auth_cookie(att))
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "attachment" in r.headers["content-disposition"]
        body = r.text
        assert "Report User" in body
        assert "Morning Keynote" in body and "Gene Therapy Workshop" in body
        assert "Total sessions attended,2" in body

    def test_export_includes_issued_serials(self, client, db):
        sp = make_user(db, email="res@test.com", role=UserRole.speaker)
        _item(db, speaker_id=sp.id)
        client.get("/api/certificates/speaker/download", cookies=auth_cookie(sp))
        serial = db.query(IssuedCertificate).filter_by(user_id=sp.id).one().serial
        body = client.get("/api/attendance/report/export", cookies=auth_cookie(sp)).text
        assert serial in body and f"/verify/{serial}" in body


class TestCertificateHardening:
    def _speaker(self, db, email, name="Dr H"):
        sp = make_user(db, email=email, role=UserRole.speaker, full_name=name)
        _item(db, speaker_id=sp.id)
        return sp

    def test_revoked_blocks_pdf_download(self, client, db):
        sp = self._speaker(db, "hv@test.com")
        client.get("/api/certificates/speaker/download", cookies=auth_cookie(sp))
        rec = db.query(IssuedCertificate).filter_by(user_id=sp.id).one()
        rec.revoked = True
        db.commit()
        r = client.get("/api/certificates/speaker/download", cookies=auth_cookie(sp))
        assert r.status_code == 403

    def test_revoked_blocks_view_page(self, client, db):
        sp = self._speaker(db, "hw@test.com")
        client.get("/api/certificates/speaker/download", cookies=auth_cookie(sp))
        db.query(IssuedCertificate).filter_by(user_id=sp.id).update({"revoked": True})
        db.commit()
        r = client.get("/certificates/speaker/view", cookies=auth_cookie(sp),
                       follow_redirects=False)
        assert r.status_code == 302

    def test_revoked_verify_json_hides_pii(self, client, db):
        sp = self._speaker(db, "hp@test.com", name="Secret Holder")
        client.get("/api/certificates/speaker/download", cookies=auth_cookie(sp))
        rec = db.query(IssuedCertificate).filter_by(user_id=sp.id).one()
        serial = rec.serial
        rec.revoked = True
        db.commit()
        b = client.get(f"/api/certificates/verify/{serial}").json()
        assert b["valid"] is False and b["status"] == "revoked"
        assert "holder" not in b and "Secret Holder" not in str(b)

    def test_serial_is_64bit(self, client, db):
        sp = self._speaker(db, "hs@test.com")
        client.get("/api/certificates/speaker/download", cookies=auth_cookie(sp))
        serial = db.query(IssuedCertificate).filter_by(user_id=sp.id).one().serial
        # DSCC2027-SPK-<16 hex chars>
        assert len(serial.rsplit("-", 1)[1]) == 16

    def test_csv_formula_injection_neutralized(self, client, db):
        admin = make_user(db, email="hc@test.com", role=UserRole.admin)
        att = make_user(db, email="hca@test.com", role=UserRole.attendee,
                        full_name="=cmd|calc", institution="+evil")
        code = _code_for(client, db, _item(db, title="=HYPERLINK(1)"), admin)
        client.post("/api/attendance/checkin", json={"code": code},
                    cookies=auth_cookie(att))
        body = client.get("/api/attendance/report/export",
                          cookies=auth_cookie(att)).text
        # Dangerous leading chars are prefixed with a quote; no raw formula cell.
        assert "'=cmd|calc" in body and "'+evil" in body
        assert "\n=HYPERLINK" not in body and ",=HYPERLINK" not in body

    def test_break_sessions_not_counted(self, client, db):
        from datetime import datetime, timezone, timedelta
        admin = make_user(db, email="hb@test.com", role=UserRole.admin)
        att = make_user(db, email="hba@test.com", role=UserRole.attendee)
        now = datetime.now(timezone.utc)
        brk = ScheduleItem(title="Coffee", type=ScheduleType.break_,
                           start_time=now, end_time=now + timedelta(minutes=30))
        db.add(brk); db.commit(); db.refresh(brk)
        code = _code_for(client, db, brk, admin)
        client.post("/api/attendance/checkin", json={"code": code},
                    cookies=auth_cookie(att))
        d = client.get("/api/certificates/status", cookies=auth_cookie(att)).json()
        assert d["attended_sessions"] == 0 and d["credits"] == 0

    def test_logged_out_checkin_carries_code_to_login(self, client, db):
        r = client.get("/certificates?checkin=ABC123", follow_redirects=False)
        assert r.status_code == 302 and "checkin=ABC123" in r.headers["location"]
        assert r.headers["location"].startswith("/login")


class TestAttendancePctThreshold:
    def test_pct_goal_scales_with_program(self, client, db, monkeypatch):
        from app.core.config import get_settings
        monkeypatch.setattr(get_settings(), "CERT_ATTENDANCE_PCT", 50)
        admin = make_user(db, email="pc@test.com", role=UserRole.admin)
        att = make_user(db, email="pca@test.com", role=UserRole.attendee)
        items = [_item(db, title=f"T{i}") for i in range(4)]
        # Breaks don't count toward the denominator.
        now = datetime.now(timezone.utc)
        db.add(ScheduleItem(title="Coffee", type=ScheduleType.break_,
                            start_time=now, end_time=now + timedelta(minutes=30)))
        db.commit()
        # 50% of 4 non-break sessions = goal of 2.
        d = client.get("/api/certificates/status", cookies=auth_cookie(att)).json()
        cert = {c["kind"]: c for c in d["certificates"]}["attendance"]
        assert cert["goal"] == 2 and cert["unlocked"] is False
        for item in items[:2]:
            code = _code_for(client, db, item, admin)
            client.post("/api/attendance/checkin", json={"code": code},
                        cookies=auth_cookie(att))
        d = client.get("/api/certificates/status", cookies=auth_cookie(att)).json()
        cert = {c["kind"]: c for c in d["certificates"]}["attendance"]
        assert cert["unlocked"] is True and cert["progress"] == 2

    def test_pct_zero_keeps_fixed_goal(self, client, db):
        from app.core.config import get_settings
        att = make_user(db, email="pz@test.com", role=UserRole.attendee)
        d = client.get("/api/certificates/status", cookies=auth_cookie(att)).json()
        cert = {c["kind"]: c for c in d["certificates"]}["attendance"]
        assert cert["goal"] == get_settings().CERT_MIN_SESSIONS

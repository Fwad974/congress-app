"""
Tests for Feedback & Ratings: inline session ratings, private speaker feedback,
the post-event survey (+ certificate unlock), the anonymized speaker digest, and
the organizer sentiment dashboard.
"""
from datetime import datetime, timedelta, timezone

from app.models.feedback import SessionRating, SurveyResponse  # noqa: F401
from app.models.schedule import ScheduleItem, ScheduleType
from app.models.user import UserRole
from tests.conftest import auth_cookie, make_user


def _session(db, speaker_id=None, title="Talk"):
    now = datetime.now(timezone.utc)
    it = ScheduleItem(title=title, type=ScheduleType.talk, speaker_id=speaker_id,
                      start_time=now + timedelta(hours=1), end_time=now + timedelta(hours=2))
    db.add(it); db.commit(); db.refresh(it)
    return it


# ─── Inline session ratings ──────────────────────────────────────
class TestRating:
    def test_rate_and_persist(self, client, db):
        att = make_user(db, email="r1@t.com", role=UserRole.attendee)
        sid = _session(db).id
        r = client.post(f"/api/sessions/{sid}/rating",
                        json={"stars": 5, "comment": "great"}, cookies=auth_cookie(att))
        assert r.status_code == 200 and r.json()["stars"] == 5
        got = client.get(f"/api/sessions/{sid}/rating", cookies=auth_cookie(att)).json()
        assert got["rated"] is True and got["stars"] == 5 and got["comment"] == "great"

    def test_rating_is_upsert(self, client, db):
        att = make_user(db, email="r2@t.com", role=UserRole.attendee)
        sid = _session(db).id
        client.post(f"/api/sessions/{sid}/rating", json={"stars": 2}, cookies=auth_cookie(att))
        client.post(f"/api/sessions/{sid}/rating", json={"stars": 4}, cookies=auth_cookie(att))
        assert db.query(SessionRating).filter_by(schedule_item_id=sid, user_id=att.id).count() == 1
        assert db.query(SessionRating).filter_by(schedule_item_id=sid).one().stars == 4

    def test_stars_out_of_range_rejected(self, client, db):
        att = make_user(db, email="r3@t.com", role=UserRole.attendee)
        sid = _session(db).id
        assert client.post(f"/api/sessions/{sid}/rating", json={"stars": 6},
                           cookies=auth_cookie(att)).status_code == 422

    def test_rate_unknown_session_404(self, client, db):
        att = make_user(db, email="r4@t.com", role=UserRole.attendee)
        assert client.post("/api/sessions/9999/rating", json={"stars": 3},
                           cookies=auth_cookie(att)).status_code == 404

    def test_my_ratings_list(self, client, db):
        att = make_user(db, email="r5@t.com", role=UserRole.attendee)
        s1, s2 = _session(db, title="A").id, _session(db, title="B").id
        client.post(f"/api/sessions/{s1}/rating", json={"stars": 5}, cookies=auth_cookie(att))
        client.post(f"/api/sessions/{s2}/rating", json={"stars": 3}, cookies=auth_cookie(att))
        rows = client.get("/api/feedback/mine", cookies=auth_cookie(att)).json()
        assert len(rows) == 2 and {r["title"] for r in rows} == {"A", "B"}


# ─── Private speaker feedback visibility ─────────────────────────
class TestSpeakerFeedbackPrivacy:
    def _rated(self, db, client, n):
        speaker = make_user(db, email=f"sf{n}s@t.com", role=UserRole.speaker)
        att = make_user(db, email=f"sf{n}a@t.com", role=UserRole.attendee)
        sid = _session(db, speaker_id=speaker.id).id
        client.post(f"/api/sessions/{sid}/rating",
                    json={"stars": 4, "comment": "public", "speaker_feedback": "SECRET"},
                    cookies=auth_cookie(att))
        return speaker, sid

    def test_organizer_sees_private_feedback(self, client, db):
        org = make_user(db, email="sf1o@t.com", role=UserRole.admin)
        _, sid = self._rated(db, client, 1)
        d = client.get(f"/api/sessions/{sid}/rating/summary", cookies=auth_cookie(org)).json()
        assert d["speaker_feedback"] == ["SECRET"] and d["comments"] == ["public"]

    def test_speaker_never_sees_private_feedback(self, client, db):
        speaker, sid = self._rated(db, client, 2)
        d = client.get(f"/api/sessions/{sid}/rating/summary", cookies=auth_cookie(speaker)).json()
        assert d["speaker_feedback"] is None and d["comments"] == ["public"]

    def test_other_attendee_cannot_see_summary(self, client, db):
        _, sid = self._rated(db, client, 3)
        stranger = make_user(db, email="sf3x@t.com", role=UserRole.attendee)
        assert client.get(f"/api/sessions/{sid}/rating/summary",
                          cookies=auth_cookie(stranger)).status_code == 403

    def test_speaker_digest_excludes_private(self, client, db):
        speaker, sid = self._rated(db, client, 4)
        d = client.get("/api/feedback/my-talks", cookies=auth_cookie(speaker)).json()
        assert d["total_ratings"] == 1 and d["sessions"][0]["comments"] == ["public"]
        assert d["sessions"][0]["speaker_feedback"] is None


# ─── Post-event survey + certificate unlock ──────────────────────
class TestSurvey:
    def test_submit_and_get(self, client, db):
        att = make_user(db, email="sv1@t.com", role=UserRole.attendee)
        r = client.post("/api/feedback/survey",
                        json={"overall": 5, "organization": 4, "venue": 5,
                              "would_recommend": True, "comment": "great"},
                        cookies=auth_cookie(att))
        assert r.status_code == 201 and r.json()["submitted"] is True
        got = client.get("/api/feedback/survey", cookies=auth_cookie(att)).json()
        assert got["submitted"] is True and got["overall"] == 5

    def test_survey_is_upsert(self, client, db):
        att = make_user(db, email="sv2@t.com", role=UserRole.attendee)
        client.post("/api/feedback/survey", json={"overall": 2}, cookies=auth_cookie(att))
        client.post("/api/feedback/survey", json={"overall": 5}, cookies=auth_cookie(att))
        assert db.query(SurveyResponse).filter_by(user_id=att.id).count() == 1
        assert db.query(SurveyResponse).one().overall == 5

    def test_overall_required(self, client, db):
        att = make_user(db, email="sv3@t.com", role=UserRole.attendee)
        assert client.post("/api/feedback/survey", json={"comment": "x"},
                           cookies=auth_cookie(att)).status_code == 422

    def test_survey_unlocks_certificate(self, client, db):
        att = make_user(db, email="sv4@t.com", role=UserRole.attendee)
        before = client.get("/api/certificates/status", cookies=auth_cookie(att)).json()
        survey = [c for c in before["certificates"] if c["kind"] == "survey"][0]
        assert survey["unlocked"] is False
        client.post("/api/feedback/survey", json={"overall": 4}, cookies=auth_cookie(att))
        after = client.get("/api/certificates/status", cookies=auth_cookie(att)).json()
        survey = [c for c in after["certificates"] if c["kind"] == "survey"][0]
        assert survey["unlocked"] is True

    def test_participation_certificate_downloadable(self, client, db):
        att = make_user(db, email="sv5@t.com", role=UserRole.attendee, full_name="Cert Person")
        client.post("/api/feedback/survey", json={"overall": 5}, cookies=auth_cookie(att))
        r = client.get("/api/certificates/survey/download", cookies=auth_cookie(att))
        assert r.status_code == 200 and r.content.startswith(b"%PDF")


# ─── Organizer sentiment dashboard ───────────────────────────────
class TestSentiment:
    def test_dashboard_aggregates(self, client, db):
        org = make_user(db, email="se1@t.com", role=UserRole.admin)
        a1 = make_user(db, email="se1a@t.com", role=UserRole.attendee)
        a2 = make_user(db, email="se1b@t.com", role=UserRole.attendee)
        sid = _session(db, title="Keynote").id
        client.post(f"/api/sessions/{sid}/rating", json={"stars": 5, "comment": "yes"},
                    cookies=auth_cookie(a1))
        client.post(f"/api/sessions/{sid}/rating", json={"stars": 3}, cookies=auth_cookie(a2))
        client.post("/api/feedback/survey", json={"overall": 4, "would_recommend": True},
                    cookies=auth_cookie(a1))
        d = client.get("/api/feedback/sentiment", cookies=auth_cookie(org)).json()
        assert d["total_ratings"] == 2 and d["overall_average"] == 4.0
        assert d["distribution"]["5"] == 1 and d["distribution"]["3"] == 1
        assert d["rated_sessions"] == 1 and d["top_sessions"][0]["title"] == "Keynote"
        assert d["survey"]["responses"] == 1 and d["survey"]["recommend_pct"] == 100.0
        assert "yes" in d["recent_comments"]

    def test_attendee_cannot_see_sentiment(self, client, db):
        att = make_user(db, email="se2@t.com", role=UserRole.attendee)
        assert client.get("/api/feedback/sentiment",
                          cookies=auth_cookie(att)).status_code == 403

    def test_survey_export_csv(self, client, db):
        org = make_user(db, email="se3@t.com", role=UserRole.admin)
        att = make_user(db, email="se3a@t.com", role=UserRole.attendee)
        client.post("/api/feedback/survey", json={"overall": 5, "comment": "loved it"},
                    cookies=auth_cookie(att))
        r = client.get("/api/feedback/survey/export", cookies=auth_cookie(org))
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
        assert "loved it" in r.text

    def test_attendee_cannot_export(self, client, db):
        att = make_user(db, email="se4@t.com", role=UserRole.attendee)
        assert client.get("/api/feedback/survey/export",
                          cookies=auth_cookie(att)).status_code == 403


# ─── Feature gating & page ───────────────────────────────────────
class TestFeedbackGating:
    def test_page_renders(self, client, db):
        att = make_user(db, email="fg1@t.com", role=UserRole.attendee)
        html = client.get("/feedback", cookies=auth_cookie(att)).text
        assert "My Feedback" in html and "/api/feedback/survey" in html

    def test_disabled_blocks_attendee(self, client, db):
        from app.core import feature_flags
        att = make_user(db, email="fg2@t.com", role=UserRole.attendee)
        sid = _session(db).id
        feature_flags.set_enabled(db, "feedback", False)
        try:
            assert client.post(f"/api/sessions/{sid}/rating", json={"stars": 5},
                               cookies=auth_cookie(att)).status_code == 403
            r = client.get("/feedback", cookies=auth_cookie(att), follow_redirects=False)
            assert r.status_code == 302
        finally:
            feature_flags.set_enabled(db, "feedback", True)

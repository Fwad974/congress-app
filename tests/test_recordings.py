"""
Tests for Session Recordings: speaker consent gating, organizer publication,
the post-event availability window, searchable transcripts (incl. WebVTT/SRT
parsing), slide-sync markers, and the page/flag gating.
"""
from datetime import datetime, timedelta, timezone

from app.core.transcripts import format_timestamp, parse_timestamp, parse_vtt, snippet
from app.models.audit_log import AuditLog
from app.models.notification import UserNotification
from app.models.recording import (  # noqa: F401 - registers models
    SessionRecording, SlideMarker, TranscriptSegment,
)
from app.models.schedule import ScheduleItem, ScheduleType
from app.models.user import UserRole
from tests.conftest import auth_cookie, make_user

VTT = """WEBVTT

00:00:00.000 --> 00:00:06.000
<v Dr Sam>Welcome to our work on liver organoids.

2
00:00:06.000 --> 00:00:12.500
We derived hepatocytes from iPSC lines using a CRISPR knock-in.

00:00:12.500 --> 00:00:19.000
Sam: The clinical trial starts next year.
"""


def _session(db, speaker_id=None, title="Organoid keynote"):
    now = datetime.now(timezone.utc)
    item = ScheduleItem(title=title, type=ScheduleType.talk, location="Hall 1",
                        speaker_id=speaker_id, start_time=now - timedelta(hours=2),
                        end_time=now - timedelta(hours=1), extra={})
    db.add(item); db.commit(); db.refresh(item)
    return item


def _recording(client, admin, item, **over):
    body = {"schedule_item_id": item.id,
            "video_url": "https://cdn.example.com/talk.mp4"}
    body.update(over)
    r = client.post("/api/recordings", json=body, cookies=auth_cookie(admin))
    assert r.status_code == 201, r.text
    return r.json()


def _live(client, admin, speaker, item):
    """A published, consented recording — the common precondition."""
    rec = _recording(client, admin, item)
    client.post(f"/api/recordings/{rec['id']}/consent", json={"decision": "granted"},
                cookies=auth_cookie(speaker))
    r = client.post(f"/api/recordings/{rec['id']}/publish", cookies=auth_cookie(admin))
    assert r.status_code == 200, r.text
    return r.json()


# ─── Transcript parsing ──────────────────────────────────────────
class TestVttParsing:
    def test_parses_cues_with_timestamps(self):
        segments = parse_vtt(VTT)
        assert len(segments) == 3
        assert segments[0]["start_seconds"] == 0 and segments[0]["end_seconds"] == 6
        assert segments[1]["start_seconds"] == 6

    def test_voice_tag_becomes_speaker(self):
        assert parse_vtt(VTT)[0]["speaker_label"] == "Dr Sam"

    def test_name_prefix_becomes_speaker(self):
        segment = parse_vtt(VTT)[2]
        assert segment["speaker_label"] == "Sam"
        assert segment["text"] == "The clinical trial starts next year."

    def test_srt_comma_timestamps(self):
        srt = "1\n00:00:03,500 --> 00:00:09,000\nHello congress.\n"
        segments = parse_vtt(srt)
        assert segments and segments[0]["start_seconds"] == 3

    def test_short_mm_ss_timestamps(self):
        segments = parse_vtt("01:30 --> 01:45\nA short cue.\n")
        assert segments[0]["start_seconds"] == 90

    def test_garbage_is_skipped_not_fatal(self):
        assert parse_vtt("WEBVTT\n\nNOTE just a note\n\nnot a cue at all") == []

    def test_empty_input(self):
        assert parse_vtt("") == []

    def test_timestamp_helpers(self):
        assert parse_timestamp("01:02:03.000") == 3723
        assert format_timestamp(3723) == "1:02:03"
        assert format_timestamp(75) == "1:15"

    def test_snippet_centres_on_match(self):
        text = "word " * 60 + "CRISPR editing " + "tail " * 60
        assert "CRISPR" in snippet(text, "CRISPR")


# ─── Consent gating (REC-01) ─────────────────────────────────────
class TestConsent:
    def test_new_recording_is_pending(self, client, db):
        admin = make_user(db, email="rc1@t.com", role=UserRole.admin)
        speaker = make_user(db, email="rs1@t.com", role=UserRole.speaker)
        rec = _recording(client, admin, _session(db, speaker.id))
        assert rec["consent_state"] == "pending"
        assert rec["status"] == "draft" and rec["available"] is False

    def test_publish_requires_consent(self, client, db):
        admin = make_user(db, email="rc2@t.com", role=UserRole.admin)
        speaker = make_user(db, email="rs2@t.com", role=UserRole.speaker)
        rec = _recording(client, admin, _session(db, speaker.id))
        r = client.post(f"/api/recordings/{rec['id']}/publish", cookies=auth_cookie(admin))
        assert r.status_code == 400 and "consent" in r.json()["detail"].lower()

    def test_speaker_grants_consent(self, client, db):
        admin = make_user(db, email="rc3@t.com", role=UserRole.admin)
        speaker = make_user(db, email="rs3@t.com", role=UserRole.speaker)
        rec = _recording(client, admin, _session(db, speaker.id))
        r = client.post(f"/api/recordings/{rec['id']}/consent",
                        json={"decision": "granted", "note": "cut the Q&A"},
                        cookies=auth_cookie(speaker))
        assert r.status_code == 200
        assert r.json()["consent_state"] == "granted"
        assert r.json()["consent_note"] == "cut the Q&A"

    def test_other_users_cannot_consent(self, client, db):
        admin = make_user(db, email="rc4@t.com", role=UserRole.admin)
        speaker = make_user(db, email="rs4@t.com", role=UserRole.speaker)
        other = make_user(db, email="ro4@t.com", role=UserRole.attendee)
        rec = _recording(client, admin, _session(db, speaker.id))
        r = client.post(f"/api/recordings/{rec['id']}/consent",
                        json={"decision": "granted"}, cookies=auth_cookie(other))
        assert r.status_code == 403

    def test_denying_consent_unpublishes(self, client, db):
        admin = make_user(db, email="rc5@t.com", role=UserRole.admin)
        speaker = make_user(db, email="rs5@t.com", role=UserRole.speaker)
        item = _session(db, speaker.id)
        rec = _live(client, admin, speaker, item)
        assert rec["available"] is True
        r = client.post(f"/api/recordings/{rec['id']}/consent",
                        json={"decision": "denied"}, cookies=auth_cookie(speaker))
        assert r.status_code == 200 and r.json()["available"] is False
        assert client.get("/api/recordings", cookies=auth_cookie(speaker)).json()[0][
            "available"] is False

    def test_consent_is_audit_logged(self, client, db):
        admin = make_user(db, email="rc6@t.com", role=UserRole.admin)
        speaker = make_user(db, email="rs6@t.com", role=UserRole.speaker)
        rec = _recording(client, admin, _session(db, speaker.id))
        client.post(f"/api/recordings/{rec['id']}/consent", json={"decision": "granted"},
                    cookies=auth_cookie(speaker))
        entries = db.query(AuditLog).filter(AuditLog.target_type == "recording").all()
        assert any("consent granted" in e.description for e in entries)

    def test_speaker_notified_when_recording_created(self, client, db):
        admin = make_user(db, email="rc7@t.com", role=UserRole.admin)
        speaker = make_user(db, email="rs7@t.com", role=UserRole.speaker)
        _recording(client, admin, _session(db, speaker.id))
        notes = db.query(UserNotification).filter_by(user_id=speaker.id).all()
        assert any("consent" in n.title.lower() for n in notes)

    def test_bad_decision_rejected(self, client, db):
        admin = make_user(db, email="rc8@t.com", role=UserRole.admin)
        speaker = make_user(db, email="rs8@t.com", role=UserRole.speaker)
        rec = _recording(client, admin, _session(db, speaker.id))
        assert client.post(f"/api/recordings/{rec['id']}/consent",
                           json={"decision": "maybe"},
                           cookies=auth_cookie(speaker)).status_code == 422


# ─── Availability window (REC-04) ────────────────────────────────
class TestAvailability:
    def test_publish_sets_30_day_window(self, client, db):
        admin = make_user(db, email="ra1@t.com", role=UserRole.admin)
        speaker = make_user(db, email="rb1@t.com", role=UserRole.speaker)
        rec = _live(client, admin, speaker, _session(db, speaker.id))
        assert rec["days_left"] in (29, 30)
        assert rec["available_until"] is not None

    def test_custom_window(self, client, db):
        admin = make_user(db, email="ra2@t.com", role=UserRole.admin)
        speaker = make_user(db, email="rb2@t.com", role=UserRole.speaker)
        item = _session(db, speaker.id)
        rec = _recording(client, admin, item)
        client.post(f"/api/recordings/{rec['id']}/consent", json={"decision": "granted"},
                    cookies=auth_cookie(speaker))
        r = client.post(f"/api/recordings/{rec['id']}/publish?days=7",
                        cookies=auth_cookie(admin))
        assert r.json()["days_left"] in (6, 7)

    def test_expired_recording_hidden_from_attendees(self, client, db):
        admin = make_user(db, email="ra3@t.com", role=UserRole.admin)
        speaker = make_user(db, email="rb3@t.com", role=UserRole.speaker)
        att = make_user(db, email="rz3@t.com", role=UserRole.attendee)
        rec = _live(client, admin, speaker, _session(db, speaker.id))
        row = db.query(SessionRecording).filter_by(id=rec["id"]).one()
        row.available_until = datetime.now(timezone.utc) - timedelta(days=1)
        db.commit()
        assert client.get("/api/recordings", cookies=auth_cookie(att)).json() == []
        assert client.get(f"/api/recordings/{rec['id']}",
                          cookies=auth_cookie(att)).status_code == 404

    def test_organizer_still_sees_expired_and_can_extend(self, client, db):
        admin = make_user(db, email="ra4@t.com", role=UserRole.admin)
        speaker = make_user(db, email="rb4@t.com", role=UserRole.speaker)
        rec = _live(client, admin, speaker, _session(db, speaker.id))
        row = db.query(SessionRecording).filter_by(id=rec["id"]).one()
        row.available_until = datetime.now(timezone.utc) - timedelta(days=1)
        db.commit()
        listed = client.get("/api/recordings?include_hidden=true",
                            cookies=auth_cookie(admin)).json()
        assert len(listed) == 1 and listed[0]["expired"] is True
        later = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
        r = client.put(f"/api/recordings/{rec['id']}",
                       json={"available_until": later}, cookies=auth_cookie(admin))
        assert r.status_code == 200 and r.json()["available"] is True

    def test_withhold_pulls_it_back(self, client, db):
        admin = make_user(db, email="ra5@t.com", role=UserRole.admin)
        speaker = make_user(db, email="rb5@t.com", role=UserRole.speaker)
        att = make_user(db, email="rz5@t.com", role=UserRole.attendee)
        rec = _live(client, admin, speaker, _session(db, speaker.id))
        client.post(f"/api/recordings/{rec['id']}/withhold", cookies=auth_cookie(admin))
        assert client.get("/api/recordings", cookies=auth_cookie(att)).json() == []

    def test_speaker_sees_own_unpublished_recording(self, client, db):
        admin = make_user(db, email="ra6@t.com", role=UserRole.admin)
        speaker = make_user(db, email="rb6@t.com", role=UserRole.speaker)
        _recording(client, admin, _session(db, speaker.id))
        listed = client.get("/api/recordings?mine=true",
                            cookies=auth_cookie(speaker)).json()
        assert len(listed) == 1 and listed[0]["can_consent"] is True


# ─── Transcript + slide sync ─────────────────────────────────────
class TestTranscriptAndSlides:
    def test_upload_vtt(self, client, db):
        admin = make_user(db, email="rt1@t.com", role=UserRole.admin)
        speaker = make_user(db, email="ru1@t.com", role=UserRole.speaker)
        rec = _live(client, admin, speaker, _session(db, speaker.id))
        r = client.post(f"/api/recordings/{rec['id']}/transcript", json={"vtt": VTT},
                        cookies=auth_cookie(admin))
        assert r.status_code == 200 and r.json()["segment_count"] == 3

    def test_upload_structured_segments(self, client, db):
        admin = make_user(db, email="rt2@t.com", role=UserRole.admin)
        speaker = make_user(db, email="ru2@t.com", role=UserRole.speaker)
        rec = _live(client, admin, speaker, _session(db, speaker.id))
        r = client.post(f"/api/recordings/{rec['id']}/transcript",
                        json={"segments": [{"start_seconds": 10, "text": "Hello"}]},
                        cookies=auth_cookie(admin))
        assert r.status_code == 200 and r.json()["transcript"][0]["start_seconds"] == 10

    def test_upload_replaces_previous(self, client, db):
        admin = make_user(db, email="rt3@t.com", role=UserRole.admin)
        speaker = make_user(db, email="ru3@t.com", role=UserRole.speaker)
        rec = _live(client, admin, speaker, _session(db, speaker.id))
        client.post(f"/api/recordings/{rec['id']}/transcript", json={"vtt": VTT},
                    cookies=auth_cookie(admin))
        client.post(f"/api/recordings/{rec['id']}/transcript",
                    json={"segments": [{"start_seconds": 0, "text": "Only line"}]},
                    cookies=auth_cookie(admin))
        assert db.query(TranscriptSegment).filter_by(recording_id=rec["id"]).count() == 1

    def test_empty_upload_rejected(self, client, db):
        admin = make_user(db, email="rt4@t.com", role=UserRole.admin)
        speaker = make_user(db, email="ru4@t.com", role=UserRole.speaker)
        rec = _live(client, admin, speaker, _session(db, speaker.id))
        assert client.post(f"/api/recordings/{rec['id']}/transcript", json={},
                           cookies=auth_cookie(admin)).status_code == 400

    def test_unparseable_vtt_rejected(self, client, db):
        admin = make_user(db, email="rt5@t.com", role=UserRole.admin)
        speaker = make_user(db, email="ru5@t.com", role=UserRole.speaker)
        rec = _live(client, admin, speaker, _session(db, speaker.id))
        r = client.post(f"/api/recordings/{rec['id']}/transcript",
                        json={"vtt": "no cues here"}, cookies=auth_cookie(admin))
        assert r.status_code == 400

    def test_attendee_cannot_upload(self, client, db):
        admin = make_user(db, email="rt6@t.com", role=UserRole.admin)
        speaker = make_user(db, email="ru6@t.com", role=UserRole.speaker)
        att = make_user(db, email="rv6@t.com", role=UserRole.attendee)
        rec = _live(client, admin, speaker, _session(db, speaker.id))
        assert client.post(f"/api/recordings/{rec['id']}/transcript", json={"vtt": VTT},
                           cookies=auth_cookie(att)).status_code == 403

    def test_slide_markers(self, client, db):
        admin = make_user(db, email="rt7@t.com", role=UserRole.admin)
        speaker = make_user(db, email="ru7@t.com", role=UserRole.speaker)
        rec = _live(client, admin, speaker, _session(db, speaker.id))
        r = client.post(f"/api/recordings/{rec['id']}/slides",
                        json={"slides": [{"at_seconds": 0, "slide_number": 1,
                                          "title": "Title"},
                                         {"at_seconds": 300, "slide_number": 2,
                                          "title": "Methods"}]},
                        cookies=auth_cookie(admin))
        assert r.status_code == 200 and r.json()["slide_count"] == 2
        assert r.json()["slides"][1]["at_seconds"] == 300

    def test_duplicate_slide_number_rejected(self, client, db):
        admin = make_user(db, email="rt8@t.com", role=UserRole.admin)
        speaker = make_user(db, email="ru8@t.com", role=UserRole.speaker)
        rec = _live(client, admin, speaker, _session(db, speaker.id))
        r = client.post(f"/api/recordings/{rec['id']}/slides",
                        json={"slides": [{"at_seconds": 0, "slide_number": 1},
                                         {"at_seconds": 9, "slide_number": 1}]},
                        cookies=auth_cookie(admin))
        assert r.status_code == 400


# ─── Search ──────────────────────────────────────────────────────
class TestSearch:
    def _with_transcript(self, client, db, admin, speaker, title="Talk"):
        rec = _live(client, admin, speaker, _session(db, speaker.id, title))
        client.post(f"/api/recordings/{rec['id']}/transcript", json={"vtt": VTT},
                    cookies=auth_cookie(admin))
        return rec

    def test_search_returns_timestamped_hits(self, client, db):
        admin = make_user(db, email="rq1@t.com", role=UserRole.admin)
        speaker = make_user(db, email="rw1@t.com", role=UserRole.speaker)
        att = make_user(db, email="rx1@t.com", role=UserRole.attendee)
        self._with_transcript(client, db, admin, speaker)
        r = client.get("/api/recordings/search?q=CRISPR", cookies=auth_cookie(att))
        assert r.status_code == 200 and r.json()["total"] == 1
        assert r.json()["hits"][0]["start_seconds"] == 6

    def test_search_is_case_insensitive(self, client, db):
        admin = make_user(db, email="rq2@t.com", role=UserRole.admin)
        speaker = make_user(db, email="rw2@t.com", role=UserRole.speaker)
        att = make_user(db, email="rx2@t.com", role=UserRole.attendee)
        self._with_transcript(client, db, admin, speaker)
        assert client.get("/api/recordings/search?q=organoids",
                          cookies=auth_cookie(att)).json()["total"] == 1

    def test_unpublished_transcripts_are_not_searchable(self, client, db):
        admin = make_user(db, email="rq3@t.com", role=UserRole.admin)
        speaker = make_user(db, email="rw3@t.com", role=UserRole.speaker)
        att = make_user(db, email="rx3@t.com", role=UserRole.attendee)
        rec = self._with_transcript(client, db, admin, speaker)
        client.post(f"/api/recordings/{rec['id']}/withhold", cookies=auth_cookie(admin))
        assert client.get("/api/recordings/search?q=CRISPR",
                          cookies=auth_cookie(att)).json()["total"] == 0
        # …but the organizer can still find it.
        assert client.get("/api/recordings/search?q=CRISPR",
                          cookies=auth_cookie(admin)).json()["total"] == 1

    def test_detail_filter_narrows_transcript(self, client, db):
        admin = make_user(db, email="rq4@t.com", role=UserRole.admin)
        speaker = make_user(db, email="rw4@t.com", role=UserRole.speaker)
        att = make_user(db, email="rx4@t.com", role=UserRole.attendee)
        rec = self._with_transcript(client, db, admin, speaker)
        d = client.get(f"/api/recordings/{rec['id']}?q=trial",
                       cookies=auth_cookie(att)).json()
        assert len(d["transcript"]) == 1 and "trial" in d["transcript"][0]["text"]

    def test_short_query_rejected(self, client, db):
        att = make_user(db, email="rx5@t.com", role=UserRole.attendee)
        assert client.get("/api/recordings/search?q=a",
                          cookies=auth_cookie(att)).status_code == 422


# ─── Lifecycle, counters, permissions ────────────────────────────
class TestLifecycle:
    def test_one_recording_per_session(self, client, db):
        admin = make_user(db, email="rl1@t.com", role=UserRole.admin)
        speaker = make_user(db, email="rm1@t.com", role=UserRole.speaker)
        item = _session(db, speaker.id)
        _recording(client, admin, item)
        r = client.post("/api/recordings", json={"schedule_item_id": item.id},
                        cookies=auth_cookie(admin))
        assert r.status_code == 400

    def test_attendee_cannot_create(self, client, db):
        att = make_user(db, email="rl2@t.com", role=UserRole.attendee)
        item = _session(db)
        assert client.post("/api/recordings", json={"schedule_item_id": item.id},
                           cookies=auth_cookie(att)).status_code == 403

    def test_unknown_session_404(self, client, db):
        admin = make_user(db, email="rl3@t.com", role=UserRole.admin)
        assert client.post("/api/recordings", json={"schedule_item_id": 9999},
                           cookies=auth_cookie(admin)).status_code == 404

    def test_publish_requires_video_link(self, client, db):
        admin = make_user(db, email="rl4@t.com", role=UserRole.admin)
        speaker = make_user(db, email="rm4@t.com", role=UserRole.speaker)
        rec = _recording(client, admin, _session(db, speaker.id), video_url=None)
        client.post(f"/api/recordings/{rec['id']}/consent", json={"decision": "granted"},
                    cookies=auth_cookie(speaker))
        r = client.post(f"/api/recordings/{rec['id']}/publish", cookies=auth_cookie(admin))
        assert r.status_code == 400 and "video" in r.json()["detail"].lower()

    def test_bad_video_url_rejected(self, client, db):
        admin = make_user(db, email="rl5@t.com", role=UserRole.admin)
        item = _session(db)
        assert client.post("/api/recordings",
                           json={"schedule_item_id": item.id, "video_url": "javascript:x"},
                           cookies=auth_cookie(admin)).status_code == 422

    def test_view_counter_ignores_organizer_preview(self, client, db):
        admin = make_user(db, email="rl6@t.com", role=UserRole.admin)
        speaker = make_user(db, email="rm6@t.com", role=UserRole.speaker)
        att = make_user(db, email="rn6@t.com", role=UserRole.attendee)
        rec = _live(client, admin, speaker, _session(db, speaker.id))
        client.post(f"/api/recordings/{rec['id']}/view", cookies=auth_cookie(admin))
        client.post(f"/api/recordings/{rec['id']}/view", cookies=auth_cookie(speaker))
        r = client.post(f"/api/recordings/{rec['id']}/view", cookies=auth_cookie(att))
        assert r.json()["views"] == 1

    def test_delete_removes_transcript_and_slides(self, client, db):
        admin = make_user(db, email="rl7@t.com", role=UserRole.admin)
        speaker = make_user(db, email="rm7@t.com", role=UserRole.speaker)
        rec = _live(client, admin, speaker, _session(db, speaker.id))
        client.post(f"/api/recordings/{rec['id']}/transcript", json={"vtt": VTT},
                    cookies=auth_cookie(admin))
        client.post(f"/api/recordings/{rec['id']}/slides",
                    json={"slides": [{"at_seconds": 0, "slide_number": 1}]},
                    cookies=auth_cookie(admin))
        assert client.delete(f"/api/recordings/{rec['id']}",
                             cookies=auth_cookie(admin)).status_code == 204
        assert db.query(TranscriptSegment).filter_by(recording_id=rec["id"]).count() == 0
        assert db.query(SlideMarker).filter_by(recording_id=rec["id"]).count() == 0


# ─── Page + feature flag ─────────────────────────────────────────
class TestGating:
    def test_page_renders(self, client, db):
        att = make_user(db, email="rg1@t.com", role=UserRole.attendee)
        r = client.get("/recordings", cookies=auth_cookie(att))
        assert r.status_code == 200 and "Recordings" in r.text

    def test_anonymous_redirected(self, client, db):
        r = client.get("/recordings", follow_redirects=False)
        assert r.status_code == 302 and "/login" in r.headers["location"]

    def test_flag_off_blocks_attendee(self, client, db):
        from app.core import feature_flags
        admin = make_user(db, email="rg2@t.com", role=UserRole.admin)
        att = make_user(db, email="rg3@t.com", role=UserRole.attendee)
        feature_flags.set_enabled(db, "recordings", False, admin.id)
        try:
            r = client.get("/recordings", cookies=auth_cookie(att), follow_redirects=False)
            assert r.status_code == 302
            assert client.get("/api/recordings",
                              cookies=auth_cookie(att)).status_code == 403
            # Admins keep preview access.
            assert client.get("/api/recordings",
                              cookies=auth_cookie(admin)).status_code == 200
        finally:
            feature_flags.set_enabled(db, "recordings", True, admin.id)

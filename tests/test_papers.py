"""
Tests for Abstracts & Paper submission + peer review.
"""
from app.models.paper import Paper, Review, PaperStatus
from app.models.notification import UserNotification
from app.models.schedule import ScheduleItem  # noqa: F401 - register table for FK resolution
from app.models.audit_log import AuditLog, AuditAction
from app.models.user import UserRole
from tests.conftest import auth_cookie, make_user

SUB = {"title": "On iPSCs", "authors": "Jane Doe, John Roe",
       "abstract": "We show…", "category": "iPSC"}


def _submit(client, author, **over):
    body = dict(SUB); body.update(over)
    return client.post("/api/papers", json=body, cookies=auth_cookie(author))


class TestSubmission:
    def test_submit_and_mine(self, client, attendee):
        r = _submit(client, attendee)
        assert r.status_code == 201
        b = r.json()
        assert b["status"] == "submitted" and b["is_mine"] is True
        mine = client.get("/api/papers/mine", cookies=auth_cookie(attendee)).json()
        assert mine["total"] == 1

    def test_bad_url_rejected(self, client, attendee):
        assert _submit(client, attendee, file_url="ftp://x").status_code == 422

    def test_empty_title_rejected(self, client, attendee):
        assert _submit(client, attendee, title="  ").status_code == 422

    def test_non_chair_cannot_list_all(self, client, attendee):
        assert client.get("/api/papers", cookies=auth_cookie(attendee)).status_code == 403

    def test_chair_lists_all(self, client, attendee, db):
        chair = make_user(db, email="chair@test.com", role=UserRole.review_chair)
        _submit(client, attendee)
        r = client.get("/api/papers", cookies=auth_cookie(chair)).json()
        assert r["total"] == 1 and r["can_manage"] is True


class TestReviewerAssignment:
    def _setup(self, client, db):
        chair = make_user(db, email="c@test.com", role=UserRole.review_chair)
        author = make_user(db, email="au@test.com", role=UserRole.attendee, institution="MIT")
        pid = _submit(client, author).json()["id"]
        return chair, author, pid

    def test_reviewers_list_marks_coi_and_excludes_author(self, client, db):
        chair, author, pid = self._setup(client, db)
        make_user(db, email="rev_same@test.com", role=UserRole.reviewer, institution="MIT")
        make_user(db, email="rev_diff@test.com", role=UserRole.reviewer, institution="Yale")
        revs = client.get(f"/api/papers/reviewers?paper_id={pid}",
                          cookies=auth_cookie(chair)).json()
        by_email = {r["email"]: r for r in revs}
        assert by_email["rev_same@test.com"]["coi"] is True
        assert by_email["rev_diff@test.com"]["coi"] is False
        assert "au@test.com" not in by_email   # author never a reviewer

    def test_coi_blocks_without_override(self, client, db):
        chair, author, pid = self._setup(client, db)
        rev = make_user(db, email="rs@test.com", role=UserRole.reviewer, institution="MIT")
        r = client.post(f"/api/papers/{pid}/assign", json={"reviewer_ids": [rev.id]},
                        cookies=auth_cookie(chair))
        assert r.status_code == 400
        r2 = client.post(f"/api/papers/{pid}/assign",
                         json={"reviewer_ids": [rev.id], "override_coi": True},
                         cookies=auth_cookie(chair))
        assert r2.status_code == 200 and r2.json()["status"] == "under_review"

    def test_assign_creates_review_and_notifies(self, client, db):
        chair, author, pid = self._setup(client, db)
        rev = make_user(db, email="rv@test.com", role=UserRole.reviewer, institution="Yale")
        client.post(f"/api/papers/{pid}/assign", json={"reviewer_ids": [rev.id]},
                    cookies=auth_cookie(chair))
        assert db.query(Review).filter(Review.paper_id == pid,
                                       Review.reviewer_id == rev.id).count() == 1
        assert db.query(UserNotification).filter(
            UserNotification.user_id == rev.id, UserNotification.kind == "paper").count() == 1


class TestReviewing:
    def _assigned(self, client, db):
        chair = make_user(db, email="c2@test.com", role=UserRole.review_chair)
        author = make_user(db, email="a2@test.com", role=UserRole.attendee)
        rev = make_user(db, email="r2@test.com", role=UserRole.reviewer)
        pid = _submit(client, author).json()["id"]
        client.post(f"/api/papers/{pid}/assign", json={"reviewer_ids": [rev.id]},
                    cookies=auth_cookie(chair))
        return chair, author, rev, pid

    def test_assigned_reviewer_submits(self, client, db):
        chair, author, rev, pid = self._assigned(client, db)
        r = client.put(f"/api/papers/{pid}/review",
                       json={"score": 4, "comments": "Solid", "submitted": True},
                       cookies=auth_cookie(rev))
        assert r.status_code == 200
        assert r.json()["my_review"]["score"] == 4

    def test_unassigned_cannot_review(self, client, db):
        chair, author, rev, pid = self._assigned(client, db)
        other = make_user(db, email="r3@test.com", role=UserRole.reviewer)
        assert client.put(f"/api/papers/{pid}/review", json={"score": 3},
                         cookies=auth_cookie(other)).status_code == 403

    def test_author_sees_reviews_only_after_decision(self, client, db):
        chair, author, rev, pid = self._assigned(client, db)
        client.put(f"/api/papers/{pid}/review",
                   json={"score": 2, "comments": "Needs work", "submitted": True},
                   cookies=auth_cookie(rev))
        # Before decision: author sees no reviews.
        before = client.get(f"/api/papers/{pid}", cookies=auth_cookie(author)).json()
        assert before["reviews"] == []
        # Decide revision → author now sees anonymized review.
        client.post(f"/api/papers/{pid}/decision",
                    json={"decision": "revision", "comment": "Please revise"},
                    cookies=auth_cookie(chair))
        after = client.get(f"/api/papers/{pid}", cookies=auth_cookie(author)).json()
        assert len(after["reviews"]) == 1
        assert after["reviews"][0]["reviewer_label"] == "Reviewer 1"
        assert after["reviews"][0]["comments"] == "Needs work"

    def test_reviewer_does_not_see_author_name(self, client, db):
        chair, author, rev, pid = self._assigned(client, db)
        seen = client.get(f"/api/papers/{pid}", cookies=auth_cookie(rev)).json()
        assert seen["author_name"] is None   # blind
        chair_view = client.get(f"/api/papers/{pid}", cookies=auth_cookie(chair)).json()
        assert chair_view["author_name"] is not None


class TestDecisionsAndRevision:
    def _paper(self, client, db):
        chair = make_user(db, email="c3@test.com", role=UserRole.review_chair)
        author = make_user(db, email="a3@test.com", role=UserRole.attendee)
        pid = _submit(client, author).json()["id"]
        return chair, author, pid

    def test_decision_notifies_author_and_logs(self, client, db):
        chair, author, pid = self._paper(client, db)
        r = client.post(f"/api/papers/{pid}/decision", json={"decision": "accept"},
                        cookies=auth_cookie(chair))
        assert r.status_code == 200 and r.json()["status"] == "accepted"
        assert db.query(UserNotification).filter(
            UserNotification.user_id == author.id, UserNotification.kind == "paper").count() == 1
        assert db.query(AuditLog).filter(
            AuditLog.action == AuditAction.paper_decision).count() >= 1

    def test_resubmit_bumps_round(self, client, db):
        chair, author, pid = self._paper(client, db)
        client.post(f"/api/papers/{pid}/decision", json={"decision": "revision"},
                    cookies=auth_cookie(chair))
        r = client.put(f"/api/papers/{pid}",
                       json={"abstract": "Revised abstract", "author_response": "Fixed"},
                       cookies=auth_cookie(author))
        assert r.status_code == 200
        b = r.json()
        assert b["round"] == 2 and b["status"] == "under_review"

    def test_max_revision_rounds(self, client, db):
        chair, author, pid = self._paper(client, db)
        client.post(f"/api/papers/{pid}/decision", json={"decision": "revision"},
                    cookies=auth_cookie(chair))
        client.put(f"/api/papers/{pid}", json={"author_response": "r1"},
                   cookies=auth_cookie(author))   # round → 2
        # A second revision request is refused (max 2 rounds).
        r = client.post(f"/api/papers/{pid}/decision", json={"decision": "revision"},
                        cookies=auth_cookie(chair))
        assert r.status_code == 400

    def test_author_edits_submitted_draft(self, client, db):
        chair, author, pid = self._paper(client, db)   # status submitted
        r = client.put(f"/api/papers/{pid}",
                       json={"title": "Edited title", "abstract": "Edited"},
                       cookies=auth_cookie(author))
        assert r.status_code == 200
        b = r.json()
        assert b["title"] == "Edited title" and b["round"] == 1  # no round bump on a draft edit

    def test_withdraw(self, client, db):
        chair, author, pid = self._paper(client, db)
        r = client.post(f"/api/papers/{pid}/withdraw", cookies=auth_cookie(author))
        assert r.status_code == 200 and r.json()["status"] == "withdrawn"

    def test_terminal_guards(self, client, db):
        chair, author, pid = self._paper(client, db)
        client.post(f"/api/papers/{pid}/withdraw", cookies=auth_cookie(author))
        # A withdrawn paper can't be re-withdrawn, assigned, or decided.
        assert client.post(f"/api/papers/{pid}/withdraw",
                          cookies=auth_cookie(author)).status_code == 400
        rev = make_user(db, email="rw@test.com", role=UserRole.reviewer)
        assert client.post(f"/api/papers/{pid}/assign", json={"reviewer_ids": [rev.id]},
                          cookies=auth_cookie(chair)).status_code == 400
        assert client.post(f"/api/papers/{pid}/decision", json={"decision": "accept"},
                          cookies=auth_cookie(chair)).status_code == 400

    def test_stranger_cannot_view(self, client, db):
        chair, author, pid = self._paper(client, db)
        stranger = make_user(db, email="s@test.com", role=UserRole.attendee)
        assert client.get(f"/api/papers/{pid}",
                         cookies=auth_cookie(stranger)).status_code == 403


class TestFileUpload:
    def _paper(self, client, db):
        chair = make_user(db, email="cf@test.com", role=UserRole.review_chair)
        author = make_user(db, email="af@test.com", role=UserRole.attendee)
        pid = _submit(client, author).json()["id"]
        return chair, author, pid

    def _upload(self, client, pid, author, name="m.pdf", data=b"%PDF-1.4 hi", ct="application/pdf"):
        return client.post(f"/api/papers/{pid}/file",
                           files={"file": (name, data, ct)}, cookies=auth_cookie(author))

    def test_upload_pdf_and_flags(self, client, db):
        chair, author, pid = self._paper(client, db)
        r = self._upload(client, pid, author)
        assert r.status_code == 200
        b = r.json()
        assert b["has_file"] is True and b["file_name"] == "m.pdf"

    def test_author_reviewer_chair_can_download(self, client, db):
        chair, author, pid = self._paper(client, db)
        rev = make_user(db, email="rf@test.com", role=UserRole.reviewer)
        self._upload(client, pid, author, data=b"%PDF-1.4 body")
        client.post(f"/api/papers/{pid}/assign", json={"reviewer_ids": [rev.id]},
                    cookies=auth_cookie(chair))
        for u in (author, rev, chair):
            d = client.get(f"/api/papers/{pid}/file", cookies=auth_cookie(u))
            assert d.status_code == 200 and d.content == b"%PDF-1.4 body"

    def test_stranger_cannot_download(self, client, db):
        chair, author, pid = self._paper(client, db)
        self._upload(client, pid, author)
        stranger = make_user(db, email="sf@test.com", role=UserRole.attendee)
        assert client.get(f"/api/papers/{pid}/file",
                         cookies=auth_cookie(stranger)).status_code == 403

    def test_non_author_cannot_upload(self, client, db):
        chair, author, pid = self._paper(client, db)
        other = make_user(db, email="of@test.com", role=UserRole.attendee)
        assert self._upload(client, pid, other).status_code == 403

    def test_bad_extension_rejected(self, client, db):
        chair, author, pid = self._paper(client, db)
        r = self._upload(client, pid, author, name="virus.exe",
                         data=b"MZ", ct="application/octet-stream")
        assert r.status_code == 422

    def test_docx_allowed(self, client, db):
        chair, author, pid = self._paper(client, db)
        r = self._upload(client, pid, author, name="paper.docx", data=b"PK\x03\x04doc",
                         ct="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        assert r.status_code == 200 and r.json()["file_name"] == "paper.docx"

    def test_oversize_rejected(self, client, db):
        from app.core.config import get_settings
        chair, author, pid = self._paper(client, db)
        big = b"x" * (get_settings().MAX_UPLOAD_MB * 1024 * 1024 + 1)
        assert self._upload(client, pid, author, data=big).status_code == 413

    def test_delete_file(self, client, db):
        chair, author, pid = self._paper(client, db)
        self._upload(client, pid, author)
        r = client.delete(f"/api/papers/{pid}/file", cookies=auth_cookie(author))
        assert r.status_code == 200 and r.json()["has_file"] is False
        assert client.get(f"/api/papers/{pid}/file",
                         cookies=auth_cookie(author)).status_code == 404

    def test_cannot_upload_after_decision(self, client, db):
        chair, author, pid = self._paper(client, db)
        client.post(f"/api/papers/{pid}/decision", json={"decision": "accept"},
                    cookies=auth_cookie(chair))
        assert self._upload(client, pid, author).status_code == 400


class TestPapersPage:
    def test_requires_login(self, client):
        assert client.get("/papers", follow_redirects=False).status_code == 302

    def test_renders(self, client, attendee):
        r = client.get("/papers", cookies=auth_cookie(attendee))
        assert r.status_code == 200 and "Submit" in r.text

    def test_manage_tab_only_for_chair(self, client, db):
        chair = make_user(db, email="pc@test.com", role=UserRole.review_chair)
        att = make_user(db, email="pa@test.com", role=UserRole.attendee)
        assert 'data-t="manage"' in client.get("/papers", cookies=auth_cookie(chair)).text
        assert 'data-t="manage"' not in client.get("/papers", cookies=auth_cookie(att)).text

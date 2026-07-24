"""
Tests for the Sponsor & Exhibitor Portal: directory + tiers, virtual booths,
opt-in lead capture (DATA-06), leads/PII access, analytics, logo upload, and
feature-flag gating.
"""
import io

from app.models.sponsor import Sponsor, SponsorLead, SponsorVisit  # noqa: F401
from app.models.audit_log import AuditLog, AuditAction
from app.models.user import UserRole
from app.core import feature_flags
from tests.conftest import auth_cookie, make_user


def _png():
    # Minimal 1x1 PNG.
    return (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
            b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


def _sp(client, org, **over):
    body = {"name": "Acme Bio", "tier": "gold"}
    body.update(over)
    r = client.post("/api/sponsors", json=body, cookies=auth_cookie(org))
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestDirectory:
    def test_attendee_sees_active_only(self, client, db):
        org = make_user(db, email="o1@t.com", role=UserRole.admin)
        att = make_user(db, email="a1@t.com", role=UserRole.attendee)
        _sp(client, org, name="Visible", tier="gold")
        _sp(client, org, name="Hidden", tier="gold", is_active=False)
        d = client.get("/api/sponsors", cookies=auth_cookie(att)).json()
        assert d["total"] == 1 and d["sponsors"][0]["name"] == "Visible"
        assert d["can_manage"] is False

    def test_organizer_can_include_inactive(self, client, db):
        org = make_user(db, email="o2@t.com", role=UserRole.admin)
        _sp(client, org, name="Visible")
        _sp(client, org, name="Hidden", is_active=False)
        d = client.get("/api/sponsors?include_inactive=true", cookies=auth_cookie(org)).json()
        assert d["total"] == 2 and d["can_manage"] is True

    def test_tier_ordering(self, client, db):
        org = make_user(db, email="o3@t.com", role=UserRole.admin)
        _sp(client, org, name="S", tier="silver")
        _sp(client, org, name="P", tier="platinum")
        _sp(client, org, name="G", tier="gold")
        d = client.get("/api/sponsors", cookies=auth_cookie(org)).json()
        assert [s["tier"] for s in d["sponsors"]] == ["platinum", "gold", "silver"]


class TestCreateValidation:
    def test_attendee_cannot_create(self, client, db):
        att = make_user(db, email="c1@t.com", role=UserRole.attendee)
        assert client.post("/api/sponsors", json={"name": "X", "tier": "gold"},
                           cookies=auth_cookie(att)).status_code == 403

    def test_bad_tier_rejected(self, client, db):
        org = make_user(db, email="c2@t.com", role=UserRole.admin)
        assert client.post("/api/sponsors", json={"name": "X", "tier": "diamond"},
                           cookies=auth_cookie(org)).status_code == 422

    def test_empty_name_rejected(self, client, db):
        org = make_user(db, email="c3@t.com", role=UserRole.admin)
        assert client.post("/api/sponsors", json={"name": "  ", "tier": "gold"},
                           cookies=auth_cookie(org)).status_code == 422

    def test_bad_url_rejected(self, client, db):
        org = make_user(db, email="c4@t.com", role=UserRole.admin)
        assert client.post("/api/sponsors",
                           json={"name": "X", "tier": "gold", "website_url": "ftp://x"},
                           cookies=auth_cookie(org)).status_code == 422

    def test_create_logs_audit(self, client, db):
        org = make_user(db, email="c5@t.com", role=UserRole.admin)
        _sp(client, org, name="Logged Co")
        log = db.query(AuditLog).filter(AuditAction.sponsor_create == AuditLog.action).first()
        assert log is not None and "Logged Co" in log.description


class TestBoothAndVisits:
    def test_booth_detail_and_visit_recorded(self, client, db):
        org = make_user(db, email="b1@t.com", role=UserRole.admin)
        att = make_user(db, email="b1a@t.com", role=UserRole.attendee)
        sid = _sp(client, org, about="Great booth", team=[{"name": "Dr X", "title": "CTO"}])
        d = client.get(f"/api/sponsors/{sid}", cookies=auth_cookie(att)).json()
        assert d["about"] == "Great booth" and len(d["team"]) == 1
        assert db.query(SponsorVisit).filter_by(sponsor_id=sid, user_id=att.id).one().visits == 1

    def test_repeat_visit_increments(self, client, db):
        org = make_user(db, email="b2@t.com", role=UserRole.admin)
        att = make_user(db, email="b2a@t.com", role=UserRole.attendee)
        sid = _sp(client, org)
        for _ in range(3):
            client.get(f"/api/sponsors/{sid}", cookies=auth_cookie(att))
        row = db.query(SponsorVisit).filter_by(sponsor_id=sid, user_id=att.id).one()
        assert row.visits == 3

    def test_organizer_view_does_not_record_visit(self, client, db):
        org = make_user(db, email="b3@t.com", role=UserRole.admin)
        sid = _sp(client, org)
        client.get(f"/api/sponsors/{sid}", cookies=auth_cookie(org))
        assert db.query(SponsorVisit).filter_by(sponsor_id=sid).count() == 0

    def test_inactive_booth_404_for_attendee(self, client, db):
        org = make_user(db, email="b4@t.com", role=UserRole.admin)
        att = make_user(db, email="b4a@t.com", role=UserRole.attendee)
        sid = _sp(client, org, is_active=False)
        assert client.get(f"/api/sponsors/{sid}", cookies=auth_cookie(att)).status_code == 404
        assert client.get(f"/api/sponsors/{sid}", cookies=auth_cookie(org)).status_code == 200


class TestLeadCapture:
    def test_consent_required(self, client, db):
        org = make_user(db, email="l1@t.com", role=UserRole.admin)
        att = make_user(db, email="l1a@t.com", role=UserRole.attendee)
        sid = _sp(client, org)
        r = client.post(f"/api/sponsors/{sid}/lead", json={"consent": False},
                        cookies=auth_cookie(att))
        assert r.status_code == 400
        assert db.query(SponsorLead).count() == 0

    def test_lead_with_consent(self, client, db):
        org = make_user(db, email="l2@t.com", role=UserRole.admin)
        att = make_user(db, email="l2a@t.com", role=UserRole.attendee)
        sid = _sp(client, org)
        r = client.post(f"/api/sponsors/{sid}/lead",
                        json={"consent": True, "message": "Demo please"},
                        cookies=auth_cookie(att))
        assert r.status_code == 201 and r.json()["lead_submitted"] is True
        lead = db.query(SponsorLead).one()
        assert lead.consent is True and lead.message == "Demo please"

    def test_lead_idempotent_updates_message(self, client, db):
        org = make_user(db, email="l3@t.com", role=UserRole.admin)
        att = make_user(db, email="l3a@t.com", role=UserRole.attendee)
        sid = _sp(client, org)
        client.post(f"/api/sponsors/{sid}/lead", json={"consent": True, "message": "one"},
                    cookies=auth_cookie(att))
        client.post(f"/api/sponsors/{sid}/lead", json={"consent": True, "message": "two"},
                    cookies=auth_cookie(att))
        rows = db.query(SponsorLead).filter_by(sponsor_id=sid).all()
        assert len(rows) == 1 and rows[0].message == "two"

    def test_lead_on_inactive_404(self, client, db):
        org = make_user(db, email="l4@t.com", role=UserRole.admin)
        att = make_user(db, email="l4a@t.com", role=UserRole.attendee)
        sid = _sp(client, org, is_active=False)
        assert client.post(f"/api/sponsors/{sid}/lead", json={"consent": True},
                           cookies=auth_cookie(att)).status_code == 404


class TestLeadsAccess:
    def test_organizer_sees_pii_and_audit_logged(self, client, db):
        org = make_user(db, email="p1@t.com", role=UserRole.admin)
        att = make_user(db, email="p1a@t.com", role=UserRole.attendee,
                        full_name="Bob Lead", institution="MIT")
        sid = _sp(client, org)
        client.post(f"/api/sponsors/{sid}/lead", json={"consent": True, "message": "hi"},
                    cookies=auth_cookie(att))
        rows = client.get(f"/api/sponsors/{sid}/leads", cookies=auth_cookie(org)).json()
        assert len(rows) == 1
        assert rows[0]["name"] == "Bob Lead" and rows[0]["email"] == "p1a@t.com"
        assert rows[0]["institution"] == "MIT"
        log = db.query(AuditLog).filter(AuditLog.action == AuditAction.export_data).first()
        assert log is not None

    def test_attendee_cannot_see_leads(self, client, db):
        org = make_user(db, email="p2@t.com", role=UserRole.admin)
        att = make_user(db, email="p2a@t.com", role=UserRole.attendee)
        sid = _sp(client, org)
        assert client.get(f"/api/sponsors/{sid}/leads",
                          cookies=auth_cookie(att)).status_code == 403

    def test_csv_export(self, client, db):
        org = make_user(db, email="p3@t.com", role=UserRole.admin)
        att = make_user(db, email="p3a@t.com", role=UserRole.attendee, full_name="Carol")
        sid = _sp(client, org)
        client.post(f"/api/sponsors/{sid}/lead", json={"consent": True},
                    cookies=auth_cookie(att))
        r = client.get(f"/api/sponsors/{sid}/leads/export", cookies=auth_cookie(org))
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
        assert "Carol" in r.text and "Total leads,1" in r.text

    def test_csv_formula_injection_neutralized(self, client, db):
        org = make_user(db, email="p4@t.com", role=UserRole.admin)
        att = make_user(db, email="p4a@t.com", role=UserRole.attendee, full_name="=cmd()")
        sid = _sp(client, org)
        client.post(f"/api/sponsors/{sid}/lead", json={"consent": True},
                    cookies=auth_cookie(att))
        body = client.get(f"/api/sponsors/{sid}/leads/export", cookies=auth_cookie(org)).text
        assert "'=cmd()" in body and "\n=cmd()" not in body


class TestAnalytics:
    def test_analytics_numbers(self, client, db):
        org = make_user(db, email="an@t.com", role=UserRole.admin)
        a1 = make_user(db, email="an1@t.com", role=UserRole.attendee)
        a2 = make_user(db, email="an2@t.com", role=UserRole.attendee)
        sid = _sp(client, org)
        client.get(f"/api/sponsors/{sid}", cookies=auth_cookie(a1))
        client.get(f"/api/sponsors/{sid}", cookies=auth_cookie(a1))  # repeat
        client.get(f"/api/sponsors/{sid}", cookies=auth_cookie(a2))
        client.post(f"/api/sponsors/{sid}/lead", json={"consent": True},
                    cookies=auth_cookie(a1))
        d = client.get("/api/sponsors/analytics", cookies=auth_cookie(org)).json()
        s = d["sponsors"][0]
        assert s["unique_visitors"] == 2 and s["total_visits"] == 3 and s["leads"] == 1
        assert s["conversion_rate"] == 0.5
        assert d["totals"]["unique_visitors"] == 2

    def test_attendee_cannot_see_analytics(self, client, db):
        att = make_user(db, email="an3@t.com", role=UserRole.attendee)
        assert client.get("/api/sponsors/analytics",
                          cookies=auth_cookie(att)).status_code == 403


class TestUpdateDelete:
    def test_update_fields_and_team(self, client, db):
        org = make_user(db, email="u1@t.com", role=UserRole.admin)
        sid = _sp(client, org, tier="silver")
        r = client.put(f"/api/sponsors/{sid}",
                       json={"tier": "platinum", "team": [{"name": "New Person"}]},
                       cookies=auth_cookie(org))
        assert r.status_code == 200
        s = db.query(Sponsor).get(sid)
        assert s.tier == "platinum" and s.team[0]["name"] == "New Person"

    def test_attendee_cannot_update(self, client, db):
        org = make_user(db, email="u2@t.com", role=UserRole.admin)
        att = make_user(db, email="u2a@t.com", role=UserRole.attendee)
        sid = _sp(client, org)
        assert client.put(f"/api/sponsors/{sid}", json={"tier": "bronze"},
                          cookies=auth_cookie(att)).status_code == 403

    def test_delete_cascades(self, client, db):
        org = make_user(db, email="u3@t.com", role=UserRole.admin)
        att = make_user(db, email="u3a@t.com", role=UserRole.attendee)
        sid = _sp(client, org)
        client.get(f"/api/sponsors/{sid}", cookies=auth_cookie(att))
        client.post(f"/api/sponsors/{sid}/lead", json={"consent": True},
                    cookies=auth_cookie(att))
        assert client.delete(f"/api/sponsors/{sid}", cookies=auth_cookie(org)).status_code == 204
        assert db.query(Sponsor).count() == 0
        assert db.query(SponsorLead).count() == 0 and db.query(SponsorVisit).count() == 0


class TestLogo:
    def test_upload_and_serve(self, client, db):
        org = make_user(db, email="lg@t.com", role=UserRole.admin)
        att = make_user(db, email="lga@t.com", role=UserRole.attendee)
        sid = _sp(client, org)
        r = client.post(f"/api/sponsors/{sid}/logo",
                        files={"file": ("logo.png", _png(), "image/png")},
                        cookies=auth_cookie(org))
        assert r.status_code == 200 and r.json()["has_logo"] is True
        img = client.get(f"/api/sponsors/{sid}/logo", cookies=auth_cookie(att))
        assert img.status_code == 200 and img.headers["content-type"] == "image/png"

    def test_bad_extension_rejected(self, client, db):
        org = make_user(db, email="lg2@t.com", role=UserRole.admin)
        sid = _sp(client, org)
        r = client.post(f"/api/sponsors/{sid}/logo",
                        files={"file": ("logo.svg", b"<svg/>", "image/svg+xml")},
                        cookies=auth_cookie(org))
        assert r.status_code == 422

    def test_attendee_cannot_upload(self, client, db):
        org = make_user(db, email="lg3@t.com", role=UserRole.admin)
        att = make_user(db, email="lg3a@t.com", role=UserRole.attendee)
        sid = _sp(client, org)
        r = client.post(f"/api/sponsors/{sid}/logo",
                        files={"file": ("logo.png", _png(), "image/png")},
                        cookies=auth_cookie(att))
        assert r.status_code == 403


class TestFeatureGating:
    def test_disabled_blocks_attendee_allows_admin(self, client, db):
        org = make_user(db, email="fg@t.com", role=UserRole.admin)
        att = make_user(db, email="fga@t.com", role=UserRole.attendee)
        _sp(client, org)
        feature_flags.set_enabled(db, "sponsors", False)
        try:
            assert client.get("/api/sponsors", cookies=auth_cookie(att)).status_code == 403
            assert client.get("/api/sponsors", cookies=auth_cookie(org)).status_code == 200
            # Page redirects non-admins away.
            r = client.get("/sponsors", cookies=auth_cookie(att), follow_redirects=False)
            assert r.status_code == 302
        finally:
            feature_flags.set_enabled(db, "sponsors", True)

    def test_page_renders_for_organizer(self, client, db):
        org = make_user(db, email="fg2@t.com", role=UserRole.admin)
        html = client.get("/sponsors", cookies=auth_cookie(org)).text
        assert "Sponsors" in html and "const IS_ORG = true" in html

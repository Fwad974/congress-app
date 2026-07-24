"""
Tests for Venue & Dubai Info: admin-managed settings with Dubai defaults,
room CRUD + floor plan, live session overlay, turn-by-turn routing, the WiFi
QR, and EN/AR content fields.
"""
from datetime import datetime, timedelta, timezone

from app.api.venue import wifi_qr_payload
from app.models.venue import VenueRoom, VenueSetting  # noqa: F401 - register
from app.models.schedule import ScheduleItem, ScheduleType
from app.models.audit_log import AuditLog, AuditAction
from app.models.user import UserRole
from tests.conftest import auth_cookie, make_user


def _room(client, admin, **over):
    body = {"name": "Hall A", "floor": 1, "x": 5, "y": 5, "w": 30, "h": 20}
    body.update(over)
    r = client.post("/api/venue/rooms", json=body, cookies=auth_cookie(admin))
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestDefaults:
    def test_defaults_served_until_admin_saves(self, client, db):
        att = make_user(db, email="vd1@t.com", role=UserRole.attendee)
        d = client.get("/api/venue", cookies=auth_cookie(att)).json()
        assert d["wifi"]["ssid"].startswith("DSCC-")
        assert len(d["transport"]) >= 3 and len(d["emergency"]) >= 3
        assert any(p["category"] == "pharmacy" for p in d["places"])
        assert d["can_manage"] is False

    def test_defaults_include_arabic(self, client, db):
        att = make_user(db, email="vd2@t.com", role=UserRole.attendee)
        d = client.get("/api/venue", cookies=auth_cookie(att)).json()
        assert d["general"]["venue_name_ar"]
        assert any(e.get("label_ar") for e in d["emergency"])


class TestSettings:
    def test_admin_partial_update(self, client, db):
        admin = make_user(db, email="vs1@t.com", role=UserRole.admin)
        r = client.put("/api/venue/settings",
                       json={"wifi": {"ssid": "New-Net", "password": "pw12345",
                                      "security": "WPA"}},
                       cookies=auth_cookie(admin))
        assert r.status_code == 200
        d = r.json()
        assert d["wifi"]["ssid"] == "New-Net"
        # Untouched sections still serve defaults.
        assert len(d["transport"]) >= 3

    def test_attendee_cannot_update(self, client, db):
        att = make_user(db, email="vs2@t.com", role=UserRole.attendee)
        assert client.put("/api/venue/settings", json={"wifi": {"ssid": "X"}},
                          cookies=auth_cookie(att)).status_code == 403

    def test_empty_update_400(self, client, db):
        admin = make_user(db, email="vs3@t.com", role=UserRole.admin)
        assert client.put("/api/venue/settings", json={},
                          cookies=auth_cookie(admin)).status_code == 400

    def test_update_audit_logged(self, client, db):
        admin = make_user(db, email="vs4@t.com", role=UserRole.admin)
        client.put("/api/venue/settings", json={"emergency": [
            {"kind": "general", "label": "Police", "phone": "999"}]},
            cookies=auth_cookie(admin))
        log = db.query(AuditLog).filter(
            AuditLog.action == AuditAction.settings_change,
            AuditLog.description.like("%venue info%")).first()
        assert log is not None and "emergency" in log.description


class TestRooms:
    def test_admin_room_crud(self, client, db):
        admin = make_user(db, email="vr1@t.com", role=UserRole.admin)
        rid = _room(client, admin, name="Hall B", name_ar="القاعة ب")
        d = client.get("/api/venue", cookies=auth_cookie(admin)).json()
        room = [r for r in d["rooms"] if r["id"] == rid][0]
        assert room["name"] == "Hall B" and room["name_ar"] == "القاعة ب"
        r = client.put(f"/api/venue/rooms/{rid}", json={"floor": 3, "x": 50},
                       cookies=auth_cookie(admin))
        assert r.status_code == 200 and r.json()["floor"] == 3
        assert client.delete(f"/api/venue/rooms/{rid}",
                             cookies=auth_cookie(admin)).status_code == 204
        assert db.query(VenueRoom).count() == 0

    def test_attendee_cannot_manage_rooms(self, client, db):
        admin = make_user(db, email="vr2@t.com", role=UserRole.admin)
        att = make_user(db, email="vr2a@t.com", role=UserRole.attendee)
        rid = _room(client, admin)
        assert client.post("/api/venue/rooms", json={"name": "X"},
                           cookies=auth_cookie(att)).status_code == 403
        assert client.put(f"/api/venue/rooms/{rid}", json={"floor": 2},
                          cookies=auth_cookie(att)).status_code == 403
        assert client.delete(f"/api/venue/rooms/{rid}",
                             cookies=auth_cookie(att)).status_code == 403

    def test_bad_kind_rejected(self, client, db):
        admin = make_user(db, email="vr3@t.com", role=UserRole.admin)
        assert client.post("/api/venue/rooms", json={"name": "X", "kind": "spaceship"},
                           cookies=auth_cookie(admin)).status_code == 422

    def test_coordinates_clamped(self, client, db):
        admin = make_user(db, email="vr4@t.com", role=UserRole.admin)
        rid = _room(client, admin, x=500, y=-10, w=1)
        room = db.query(VenueRoom).get(rid)
        assert room.x == 100 and room.y == 0 and room.w == 2

    def test_floors_list(self, client, db):
        admin = make_user(db, email="vr5@t.com", role=UserRole.admin)
        _room(client, admin, name="A", floor=1)
        _room(client, admin, name="B", floor=2)
        d = client.get("/api/venue", cookies=auth_cookie(admin)).json()
        assert d["floors"] == [1, 2]


class TestSessionOverlay:
    def test_now_and_next(self, client, db):
        admin = make_user(db, email="vo1@t.com", role=UserRole.admin)
        att = make_user(db, email="vo1a@t.com", role=UserRole.attendee)
        _room(client, admin, name="Hall A")
        now = datetime.now(timezone.utc)
        db.add(ScheduleItem(title="Current", type=ScheduleType.talk,
                            location="hall a",   # case-insensitive match
                            start_time=now - timedelta(minutes=10),
                            end_time=now + timedelta(minutes=50)))
        db.add(ScheduleItem(title="Upcoming", type=ScheduleType.talk,
                            location="Hall A",
                            start_time=now + timedelta(hours=2),
                            end_time=now + timedelta(hours=3)))
        db.commit()
        d = client.get("/api/venue", cookies=auth_cookie(att)).json()
        room = [r for r in d["rooms"] if r["name"] == "Hall A"][0]
        assert room["now"]["title"] == "Current"
        assert room["next"]["title"] == "Upcoming"

    def test_no_session_means_null(self, client, db):
        admin = make_user(db, email="vo2@t.com", role=UserRole.admin)
        _room(client, admin, name="Quiet Room")
        d = client.get("/api/venue", cookies=auth_cookie(admin)).json()
        room = d["rooms"][0]
        assert room["now"] is None and room["next"] is None


class TestRoute:
    def test_same_floor_route(self, client, db):
        admin = make_user(db, email="vt1@t.com", role=UserRole.admin)
        att = make_user(db, email="vt1a@t.com", role=UserRole.attendee)
        a = _room(client, admin, name="A", x=0, y=10, w=10, h=10)
        b = _room(client, admin, name="B", x=60, y=10, w=10, h=10)
        d = client.get(f"/api/venue/route?from_room={a}&to_room={b}",
                       cookies=auth_cookie(att)).json()
        assert d["distance_m"] == 60
        assert "Exit A" in d["steps"][0]["en"]
        assert "right" in d["steps"][1]["en"]
        assert "B will be" in d["steps"][-1]["en"]
        assert all(s["ar"] for s in d["steps"])   # Arabic present on every step

    def test_cross_floor_adds_stairs_step(self, client, db):
        admin = make_user(db, email="vt2@t.com", role=UserRole.admin)
        a = _room(client, admin, name="A", floor=1)
        b = _room(client, admin, name="B", floor=3, x=50)
        d = client.get(f"/api/venue/route?from_room={a}&to_room={b}",
                       cookies=auth_cookie(admin)).json()
        assert any("stairs or elevator up to floor 3" in s["en"] for s in d["steps"])
        assert d["distance_m"] >= 30   # includes 15 m per floor crossed

    def test_hint_appended(self, client, db):
        admin = make_user(db, email="vt3@t.com", role=UserRole.admin)
        a = _room(client, admin, name="A")
        b = _room(client, admin, name="B", x=60, directions_hint="next to the cafe")
        d = client.get(f"/api/venue/route?from_room={a}&to_room={b}",
                       cookies=auth_cookie(admin)).json()
        assert "next to the cafe" in d["steps"][-1]["en"]

    def test_same_room(self, client, db):
        admin = make_user(db, email="vt4@t.com", role=UserRole.admin)
        a = _room(client, admin, name="A")
        d = client.get(f"/api/venue/route?from_room={a}&to_room={a}",
                       cookies=auth_cookie(admin)).json()
        assert d["distance_m"] == 0 and len(d["steps"]) == 1

    def test_unknown_room_404(self, client, db):
        admin = make_user(db, email="vt5@t.com", role=UserRole.admin)
        a = _room(client, admin, name="A")
        assert client.get(f"/api/venue/route?from_room={a}&to_room=999",
                          cookies=auth_cookie(admin)).status_code == 404


class TestWifiQR:
    def test_qr_svg(self, client, db):
        att = make_user(db, email="vq1@t.com", role=UserRole.attendee)
        r = client.get("/api/venue/wifi-qr", cookies=auth_cookie(att))
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/svg")

    def test_payload_escaping(self):
        p = wifi_qr_payload({"ssid": "A;B,C:D", "password": 'x"y\\z', "security": "WPA"})
        assert p == 'WIFI:T:WPA;S:A\\;B\\,C\\:D;P:x\\"y\\\\z;;'

    def test_open_network_payload(self):
        assert wifi_qr_payload({"ssid": "Free", "security": "nopass"}) == \
            "WIFI:T:nopass;S:Free;;"


class TestMapLinks:
    def test_default_venue_map_url_is_google_maps(self, client, db):
        att = make_user(db, email="vm1@t.com", role=UserRole.attendee)
        d = client.get("/api/venue", cookies=auth_cookie(att)).json()
        assert "maps.google.com" in d["general"]["map_url"]

    def test_place_map_url_roundtrip(self, client, db):
        admin = make_user(db, email="vm2@t.com", role=UserRole.admin)
        r = client.put("/api/venue/settings", json={"places": [
            {"category": "hotel", "name": "Test Hotel",
             "map_url": "https://maps.google.com/?q=Test+Hotel"}]},
            cookies=auth_cookie(admin))
        assert r.status_code == 200
        assert r.json()["places"][0]["map_url"] == "https://maps.google.com/?q=Test+Hotel"

    def test_page_has_venue_map_button_and_fallback(self, client, db):
        att = make_user(db, email="vm3@t.com", role=UserRole.attendee)
        html = client.get("/venue", cookies=auth_cookie(att)).text
        assert "venueMapBtn" in html          # venue "Open in Google Maps" card
        assert "function gmaps" in html       # auto-generated Maps search fallback


class TestGatingAndPage:
    def test_page_renders_with_lang_toggle(self, client, db):
        att = make_user(db, email="vp1@t.com", role=UserRole.attendee)
        html = client.get("/venue", cookies=auth_cookie(att)).text
        assert "langBtn" in html and "عربي" in html
        assert "vn-manage" not in html   # no organizer tools for attendees

    def test_admin_page_has_manage(self, client, db):
        admin = make_user(db, email="vp2@t.com", role=UserRole.admin)
        html = client.get("/venue", cookies=auth_cookie(admin)).text
        assert "vn-manage" in html

    def test_flag_off_blocks_attendee(self, client, db):
        from app.core import feature_flags
        att = make_user(db, email="vp3@t.com", role=UserRole.attendee)
        admin = make_user(db, email="vp3a@t.com", role=UserRole.admin)
        feature_flags.set_enabled(db, "venue", False)
        try:
            assert client.get("/api/venue", cookies=auth_cookie(att)).status_code == 403
            assert client.get("/api/venue", cookies=auth_cookie(admin)).status_code == 200
            assert client.get("/venue", cookies=auth_cookie(att),
                              follow_redirects=False).status_code == 302
        finally:
            feature_flags.set_enabled(db, "venue", True)

"""
Venue & Dubai Info API.

- **Attendees/speakers** get the whole package in one call (`GET /api/venue`):
  the schematic floor plan (rooms + live current/next session overlay), WiFi
  (with a one-tap-connect QR), transport, emergency contacts, and nearby
  places — every text field with an optional Arabic counterpart for the EN/AR
  toggle.
- **Organizers** (admin / super_admin) manage everything: room CRUD (position
  on the floor plan grid) and the content sections (`PUT /api/venue/settings`).
  Nothing is hardcoded — code-level Dubai defaults are served only until an
  admin saves their own version. All edits are audit-logged.
- **Turn-by-turn navigation** (`GET /api/venue/route`) generates walking steps
  between two rooms from their grid coordinates (1 unit ≈ 1 m): exit, floor
  changes, heading, and which side the destination is on — in both languages.
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.core.audit_service import log_action
from app.models.user import User, UserRole
from app.models.audit_log import AuditAction
from app.models.schedule import ScheduleItem
from app.models.venue import VenueRoom, VenueSetting
from app.schemas.venue import (
    SETTING_KEYS, RoomCreate, RoomUpdate, RoomOut, SessionBrief,
    VenueResponse, SettingsUpdate, RouteStep, RouteResponse,
)

router = APIRouter()

ORGANIZER_ROLES = {UserRole.admin, UserRole.super_admin}


def _is_organizer(user: User) -> bool:
    return user.role in ORGANIZER_ROLES


def _require_organizer(user: User) -> None:
    if not _is_organizer(user):
        raise HTTPException(status_code=403, detail="Organizers only")


# ─── Defaults (served until an admin saves their own) ────────────
def _defaults() -> Dict:
    s = get_settings()
    return {
        "general": {
            "venue_name": s.CONGRESS_VENUE,
            "venue_name_ar": "مركز دبي التجاري العالمي",
            "address": "Sheikh Zayed Road, Trade Centre 2, Dubai, UAE",
            "address_ar": "شارع الشيخ زايد، المركز التجاري الثانية، دبي، الإمارات",
            "map_url": "https://maps.google.com/?q=Dubai+World+Trade+Centre",
        },
        "wifi": {
            "ssid": f"DSCC-{s.CONGRESS_YEAR}",
            "password": "stemcells2027",
            "security": "WPA",  # WPA | WEP | nopass
            "note": "Free for all attendees throughout the venue.",
            "note_ar": "مجاني لجميع الحضور في جميع أنحاء المكان.",
        },
        "transport": [
            {"mode": "metro", "title": "Dubai Metro — Red Line",
             "title_ar": "مترو دبي — الخط الأحمر",
             "details": "Alight at World Trade Centre station; the venue is a "
                        "5-minute covered walk from the exit.",
             "details_ar": "انزل في محطة المركز التجاري العالمي؛ يبعد المكان "
                           "خمس دقائق سيرًا عبر الممر المغطى.",
             "link": ""},
            {"mode": "taxi", "title": "Taxi / Careem / Uber",
             "title_ar": "تاكسي / كريم / أوبر",
             "details": "Drop-off at Gate 4. RTA taxi booking: +971 4 208 0808.",
             "details_ar": "التوقف عند البوابة ٤. حجز تاكسي هيئة الطرق: +97142080808",
             "link": ""},
            {"mode": "parking", "title": "Parking",
             "title_ar": "مواقف السيارات",
             "details": "Multi-storey car park at Gate 5 (paid). Overflow "
                        "parking signposted from Sheikh Zayed Road exit 43.",
             "details_ar": "موقف متعدد الطوابق عند البوابة ٥ (مدفوع).",
             "link": ""},
        ],
        "emergency": [
            {"kind": "security", "label": "Venue security (24h)",
             "label_ar": "أمن المكان (٢٤ ساعة)", "phone": "+971 4 306 4000"},
            {"kind": "medical", "label": "First-aid room — Hall 3 concourse",
             "label_ar": "غرفة الإسعافات الأولية — قاعة ٣", "phone": "+971 4 306 4040"},
            {"kind": "general", "label": "Police", "label_ar": "الشرطة", "phone": "999"},
            {"kind": "medical", "label": "Ambulance", "label_ar": "الإسعاف", "phone": "998"},
            {"kind": "general", "label": "Fire", "label_ar": "الدفاع المدني", "phone": "997"},
        ],
        "places": [
            {"category": "hotel", "name": "Ibis One Central",
             "name_ar": "فندق إيبيس ون سنترال",
             "details": "Budget hotel connected to the venue concourse.",
             "details_ar": "فندق اقتصادي متصل بممر المكان.",
             "distance": "2 min walk", "map_url": ""},
            {"category": "hotel", "name": "Novotel World Trade Centre",
             "name_ar": "فندق نوفوتيل المركز التجاري",
             "details": "4-star, on the venue grounds.",
             "details_ar": "أربع نجوم، داخل حرم المكان.",
             "distance": "3 min walk", "map_url": ""},
            {"category": "restaurant", "name": "The Kitchens (food court)",
             "name_ar": "ذا كيتشنز (ساحة مطاعم)",
             "details": "Food court in the concourse — halal, vegetarian and "
                        "vegan options.",
             "details_ar": "ساحة مطاعم في الممر — خيارات حلال ونباتية.",
             "distance": "In the venue", "map_url": ""},
            {"category": "pharmacy", "name": "BinSina Pharmacy",
             "name_ar": "صيدلية بن سينا",
             "details": "Open 08:00–23:00, One Central Mall.",
             "details_ar": "مفتوحة من ٨ صباحًا حتى ١١ مساءً، ون سنترال مول.",
             "distance": "5 min walk", "map_url": ""},
            {"category": "atm", "name": "Emirates NBD ATM",
             "name_ar": "صراف بنك الإمارات دبي الوطني",
             "details": "Concourse level, next to Hall 4 entrance.",
             "details_ar": "طابق الممر، بجوار مدخل القاعة ٤.",
             "distance": "In the venue", "map_url": ""},
        ],
    }


def _get_setting(db: Session, key: str):
    row = db.query(VenueSetting).filter(VenueSetting.key == key).first()
    return row.value if row else _defaults()[key]


# ─── Session overlay (current / next per room) ───────────────────
def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _brief(it: ScheduleItem) -> SessionBrief:
    return SessionBrief(id=it.id, title=it.title,
                        start_time=it.start_time, end_time=it.end_time)


def _room_out(room: VenueRoom, by_location: Dict[str, List[ScheduleItem]],
              now: datetime) -> RoomOut:
    current = nxt = None
    for it in by_location.get(room.name.strip().lower(), []):
        start, end = _as_utc(it.start_time), _as_utc(it.end_time)
        if start and end and start <= now <= end:
            current = current or _brief(it)
        elif start and start > now and (nxt is None
                                        or start < _as_utc(nxt.start_time)):
            nxt = _brief(it)
    return RoomOut(
        id=room.id, name=room.name, name_ar=room.name_ar, kind=room.kind,
        floor=room.floor, x=room.x, y=room.y, w=room.w, h=room.h,
        directions_hint=room.directions_hint,
        directions_hint_ar=room.directions_hint_ar,
        now=current, next=nxt)


def _rooms_with_sessions(db: Session) -> List[RoomOut]:
    rooms = db.query(VenueRoom).order_by(VenueRoom.floor, VenueRoom.name).all()
    if not rooms:
        return []
    wanted = {r.name.strip().lower() for r in rooms}
    by_location: Dict[str, List[ScheduleItem]] = {}
    for it in db.query(ScheduleItem).filter(ScheduleItem.location.isnot(None)).all():
        key = (it.location or "").strip().lower()
        if key in wanted:
            by_location.setdefault(key, []).append(it)
    now = datetime.now(timezone.utc)
    return [_room_out(r, by_location, now) for r in rooms]


# ─── The whole venue package ─────────────────────────────────────
@router.get("", response_model=VenueResponse)
@router.get("/", response_model=VenueResponse)
def venue_info(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rooms = _rooms_with_sessions(db)
    return VenueResponse(
        general=_get_setting(db, "general"),
        wifi=_get_setting(db, "wifi"),
        transport=_get_setting(db, "transport"),
        emergency=_get_setting(db, "emergency"),
        places=_get_setting(db, "places"),
        rooms=rooms,
        floors=sorted({r.floor for r in rooms}),
        can_manage=_is_organizer(user),
    )


# ─── WiFi one-tap connect QR ─────────────────────────────────────
def _wifi_escape(v: str) -> str:
    out = ""
    for ch in v or "":
        out += ("\\" + ch) if ch in ('\\', ';', ',', ':', '"') else ch
    return out


def wifi_qr_payload(wifi: Dict) -> str:
    sec = (wifi.get("security") or "WPA").upper()
    if sec not in ("WPA", "WEP", "NOPASS"):
        sec = "WPA"
    ssid = _wifi_escape(wifi.get("ssid") or "")
    if sec == "NOPASS":
        return f"WIFI:T:nopass;S:{ssid};;"
    return f"WIFI:T:{sec};S:{ssid};P:{_wifi_escape(wifi.get('password') or '')};;"


@router.get("/wifi-qr")
def wifi_qr(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """SVG QR in the standard WIFI: format — scanning joins the network."""
    wifi = _get_setting(db, "wifi")
    if not wifi.get("ssid"):
        raise HTTPException(status_code=404, detail="WiFi is not configured")
    from app.core.qr import qr_svg_document
    return Response(content=qr_svg_document(wifi_qr_payload(wifi), scale=6),
                    media_type="image/svg+xml")


# ─── Turn-by-turn navigation ─────────────────────────────────────
def _center(r: VenueRoom):
    return r.x + r.w / 2, r.y + r.h / 2


_DIR = {  # (en, ar) phrases for the primary walking direction on the map grid
    "right": ("to the right", "إلى اليمين"),
    "left": ("to the left", "إلى اليسار"),
    "forward": ("straight ahead", "إلى الأمام"),
    "back": ("back the way you came", "بالاتجاه المعاكس"),
}
_SIDE = {"right": ("on your right", "على يمينك"),
         "left": ("on your left", "على يسارك"),
         "ahead": ("straight ahead", "أمامك مباشرة")}


@router.get("/route", response_model=RouteResponse)
def route(from_room: int = Query(...), to_room: int = Query(...),
          db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    a = db.query(VenueRoom).filter(VenueRoom.id == from_room).first()
    b = db.query(VenueRoom).filter(VenueRoom.id == to_room).first()
    if not a or not b:
        raise HTTPException(status_code=404, detail="Room not found")

    steps: List[RouteStep] = []
    a_name, b_name = a.name, b.name
    a_name_ar, b_name_ar = a.name_ar or a.name, b.name_ar or b.name

    if a.id == b.id:
        return RouteResponse(from_room=a_name, to_room=b_name, distance_m=0,
                             steps=[RouteStep(en=f"You are already at {b_name}.",
                                              ar=f"أنت بالفعل في {b_name_ar}.")])

    steps.append(RouteStep(en=f"Exit {a_name} into the corridor.",
                           ar=f"اخرج من {a_name_ar} إلى الممر."))

    floors_crossed = abs(b.floor - a.floor)
    if floors_crossed:
        up = b.floor > a.floor
        steps.append(RouteStep(
            en=f"Take the stairs or elevator {'up' if up else 'down'} "
               f"to floor {b.floor}.",
            ar=f"استخدم الدرج أو المصعد {'صعودًا' if up else 'نزولًا'} "
               f"إلى الطابق {b.floor}."))

    ax, ay = _center(a)
    bx, by = _center(b)
    dx, dy = bx - ax, by - ay
    # Grid units ≈ metres; add a rough 15 m per floor change.
    distance = round((dx * dx + dy * dy) ** 0.5) + floors_crossed * 15

    primary = ("right" if dx > 0 else "left") if abs(dx) >= abs(dy) else \
              ("forward" if dy < 0 else "back")
    walk = max(round((dx * dx + dy * dy) ** 0.5), 1)
    steps.append(RouteStep(
        en=f"Head {_DIR[primary][0]} along the corridor for about {walk} m.",
        ar=f"اتجه {_DIR[primary][1]} عبر الممر لمسافة {walk} مترًا تقريبًا."))

    # Which side the destination lands on, relative to the walking direction.
    # Screen grid: x grows right, y grows down; a walker heading f=(fx,fy) has
    # their right hand along r=(-fy,fx). side_val = residual · r.
    if primary == "right":       # f=(1,0),  r=(0,1)
        side_val = dy
    elif primary == "left":      # f=(-1,0), r=(0,-1)
        side_val = -dy
    elif primary == "forward":   # f=(0,-1), r=(1,0)
        side_val = dx
    else:                        # back: f=(0,1), r=(-1,0)
        side_val = -dx
    side = "ahead" if abs(side_val) < 3 else ("right" if side_val > 0 else "left")
    arrive_en = f"{b_name} will be {_SIDE[side][0]}."
    arrive_ar = f"ستجد {b_name_ar} {_SIDE[side][1]}."
    if b.directions_hint:
        arrive_en += f" ({b.directions_hint})"
    if b.directions_hint_ar or b.directions_hint:
        arrive_ar += f" ({b.directions_hint_ar or b.directions_hint})"
    steps.append(RouteStep(en=arrive_en, ar=arrive_ar))

    return RouteResponse(from_room=a_name, to_room=b_name,
                         distance_m=distance, steps=steps)


# ─── Organizer: settings + room CRUD ─────────────────────────────
@router.put("/settings", response_model=VenueResponse)
def update_settings(req: SettingsUpdate, request: Request,
                    db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    _require_organizer(user)
    changed = []
    for key in SETTING_KEYS:
        val = getattr(req, key)
        if val is None:
            continue
        row = db.query(VenueSetting).filter(VenueSetting.key == key).first()
        if not row:
            row = VenueSetting(key=key, value=val)
            db.add(row)
        else:
            row.value = val
        row.updated_by = user.id
        row.updated_at = datetime.now(timezone.utc)
        changed.append(key)
    if not changed:
        raise HTTPException(status_code=400, detail="Nothing to update")
    log_action(db, user, AuditAction.settings_change,
               f"Updated venue info: {', '.join(changed)}",
               request=request, target_type="venue")
    db.commit()
    return venue_info(db, user)


@router.post("/rooms", response_model=RoomOut, status_code=201)
def create_room(req: RoomCreate, request: Request, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    _require_organizer(user)
    room = VenueRoom(**req.model_dump())
    db.add(room)
    db.commit()
    db.refresh(room)
    log_action(db, user, AuditAction.settings_change,
               f"Added venue room '{room.name}' (floor {room.floor})",
               request=request, target_type="venue_room", target_id=room.id)
    return _room_out(room, {}, datetime.now(timezone.utc))


@router.put("/rooms/{room_id}", response_model=RoomOut)
def update_room(room_id: int, req: RoomUpdate, request: Request,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    _require_organizer(user)
    room = db.query(VenueRoom).filter(VenueRoom.id == room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    for f, val in req.model_dump(exclude_unset=True).items():
        if f == "name":
            val = (val or "").strip()
            if not val:
                continue
        setattr(room, f, val)
    room.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(room)
    log_action(db, user, AuditAction.settings_change,
               f"Updated venue room '{room.name}'",
               request=request, target_type="venue_room", target_id=room.id)
    return _room_out(room, {}, datetime.now(timezone.utc))


@router.delete("/rooms/{room_id}", status_code=204)
def delete_room(room_id: int, request: Request, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    _require_organizer(user)
    room = db.query(VenueRoom).filter(VenueRoom.id == room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    name = room.name
    db.delete(room)
    db.commit()
    log_action(db, user, AuditAction.settings_change,
               f"Deleted venue room '{name}'",
               request=request, target_type="venue_room", target_id=room_id)
    return None

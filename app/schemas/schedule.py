"""
Schedule Schemas — Request/Response models for congress schedule items.

Type-specific fields (speaker, capacity, etc.) live in the `extra` dict and
are validated against EXTRA_SPEC: unknown keys are dropped, types are coerced,
empty values are removed.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, field_validator, model_validator


VALID_TYPES = ["keynote", "talk", "panel", "workshop", "poster",
               "break", "networking", "other"]


# Per-type allowed extra-field spec. Value is the coerced kind:
#   "str"      -> trimmed string, dropped if empty
#   "int"      -> int >= 0, dropped if missing/invalid
#   "list_str" -> list of trimmed non-empty strings (accepts a list or a
#                 newline-separated string)
EXTRA_SPEC: Dict[str, Dict[str, str]] = {
    "keynote":    {"speaker": "str", "affiliation": "str", "abstract": "str"},
    "talk":       {"speaker": "str", "affiliation": "str", "abstract": "str"},
    "panel":      {"moderator": "str", "panelists": "list_str"},
    "workshop":   {"instructor": "str", "capacity": "int", "prerequisites": "str"},
    "poster":     {"presenter": "str", "poster_number": "str", "abstract": "str"},
    "break":      {},
    "networking": {},
    "other":      {},
}


def _coerce_extra(type_: str, raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Filter & coerce an extra dict against the spec for `type_`.

    - Unknown keys are dropped.
    - Strings are stripped; empties dropped.
    - Ints are parsed; non-ints dropped.
    - list_str accepts a list or a newline-separated string; trims each entry,
      drops empties, drops the field if the resulting list is empty.
    """
    if not raw:
        return {}
    spec = EXTRA_SPEC.get(type_, {})
    out: Dict[str, Any] = {}
    for key, kind in spec.items():
        if key not in raw or raw[key] is None:
            continue
        v = raw[key]
        if kind == "str":
            s = str(v).strip()
            if s:
                out[key] = s
        elif kind == "int":
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if n >= 0:
                out[key] = n
        elif kind == "list_str":
            if isinstance(v, str):
                items = [p.strip() for p in v.splitlines()]
            elif isinstance(v, list):
                items = [str(p).strip() for p in v]
            else:
                continue
            items = [p for p in items if p]
            if items:
                out[key] = items
    return out


class ScheduleItemBase(BaseModel):
    title: str
    description: Optional[str] = None
    type: str = "talk"
    location: Optional[str] = None
    start_time: datetime
    end_time: datetime
    extra: Dict[str, Any] = {}

    @field_validator("title")
    @classmethod
    def title_required(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("Title is required")
        if len(v) > 255:
            raise ValueError("Title is too long")
        return v

    @field_validator("type")
    @classmethod
    def valid_type(cls, v):
        if v not in VALID_TYPES:
            raise ValueError(f"Invalid type: {v}")
        return v

    @model_validator(mode="after")
    def end_after_start(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        # Filter extras to allowed keys for the chosen type
        self.extra = _coerce_extra(self.type, self.extra)
        return self


class ScheduleItemCreate(ScheduleItemBase):
    pass


class ScheduleItemUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    type: Optional[str] = None
    location: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    extra: Optional[Dict[str, Any]] = None

    @field_validator("type")
    @classmethod
    def valid_type(cls, v):
        if v is None:
            return v
        if v not in VALID_TYPES:
            raise ValueError(f"Invalid type: {v}")
        return v


class ScheduleItemResponse(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    type: str
    location: Optional[str] = None
    start_time: datetime
    end_time: datetime
    extra: Dict[str, Any] = {}
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ScheduleListResponse(BaseModel):
    items: List[ScheduleItemResponse]
    total: int

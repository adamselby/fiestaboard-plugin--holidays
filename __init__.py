"""Holidays plugin for FiestaBoard.
"""

# ---------------------------------------------------------------------------
# Shared core (generated into each plugin from src/_shared_core.py; keep in sync)
# Pure standard library: no network, no third-party packages.
# ---------------------------------------------------------------------------

from __future__ import annotations

import logging
import random
import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.plugins.base import PluginBase, PluginResult, TriggerResult

logger = logging.getLogger(__name__)

def wrap(text: str, width: int) -> list[str]:
    """Greedy word wrap; '|' forces a line break; long words are hard-cut."""
    lines: list[str] = []
    for chunk in str(text).split("|"):
        current = ""
        for word in chunk.split():
            word = word[:width]
            if not current:
                current = word
            elif len(current) + 1 + len(word) <= width:
                current += " " + word
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


def info_lines(rows: list[str], width: int, height: int) -> list[str]:
    """Plain centered rows; on short boards drop blank spacer rows first."""
    if height < len(rows):
        rows = [r for r in rows if r] or rows
    rows = [r[:width].center(width) for r in rows[:height]]
    return (rows + [""] * height)[:height]


# ---------------------------------------------------------------------------
# Date-plugin helpers (from src/_shared.py)
# ---------------------------------------------------------------------------

DEFAULT_TZ = "America/Chicago"
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
COLORS = {
    "red": "{63}",
    "orange": "{64}",
    "yellow": "{65}",
    "green": "{66}",
    "blue": "{67}",
    "violet": "{68}",
    "purple": "{68}",
    "white": "{69}",
}
RAINBOW = ["red", "orange", "yellow", "green", "blue", "violet"]
CONFETTI_TILES = 22  # widest single board; a Note uses the first 15
_YMD_RE = re.compile(r"^\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s*$")


def parse_ymd(raw: Any) -> date:
    """Parse YYYY-MM-DD (slashes allowed). Raises ValueError."""
    m = _YMD_RE.match(str(raw or ""))
    if not m:
        raise ValueError(f"'{raw}' is not YYYY-MM-DD")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def fmt_date(d: date) -> str:
    return f"{MONTHS[d.month - 1]} {d.day}"


def parse_palette(raw: Any) -> list[str]:
    """'red, white, blue' → tile markers. Unknown names are ignored."""
    names = [p.strip().lower() for p in str(raw or "").replace("/", ",").split(",")]
    return [COLORS[n] for n in names if n in COLORS]


def random_confetti(tiles: int, palette: list[str] | None = None, rng: random.Random | None = None) -> str:
    """Random color tiles; never the same color twice in a row when avoidable."""
    rng = rng or random
    pal = palette or [COLORS[c] for c in RAINBOW]
    out: list[str] = []
    for _ in range(tiles):
        choices = [c for c in pal if not out or c != out[-1]] or pal
        out.append(rng.choice(choices))
    return "".join(out)


def days_phrase(days: int) -> str:
    if days == 0:
        return "TODAY"
    if days == 1:
        return "TOMORROW"
    return f"IN {days} DAYS"


def celebration_lines(message: str, width: int, height: int, palette: list[str] | None = None) -> list[str]:
    """Message centered, with confetti rows above (and below when there's room)."""
    body = wrap(message, width)[:height]
    top = [random_confetti(width, palette)] if height >= len(body) + 1 else []
    bottom = [random_confetti(width, palette)] if height > 3 and height >= len(body) + len(top) + 1 else []
    free = height - len(top) - len(bottom) - len(body)
    middle = [""] * max(free // 2, 0) + [line.center(width) for line in body]
    middle += [""] * (height - len(top) - len(bottom) - len(middle))
    return (top + middle + bottom + [""] * height)[:height]


def flanked(text: str, width: int, palette: list[str] | None = None) -> str:
    """Message centered with confetti filling both sides."""
    text = text[: max(width - 2, 0)]
    spare = width - len(text) - 2
    left = spare // 2
    return random_confetti(left, palette) + " " + text + " " + random_confetti(spare - left, palette)


def announce_settings(default_priority: int, what: str) -> dict[str, Any]:
    """Settings-schema fragment for the takeover ("announce") feature."""
    return {
        "announce": {
            "type": "boolean",
            "title": "Take Over the Board",
            "description": f"Interrupt your normal pages to show {what}, then hand the board back.",
            "default": False,
        },
        "announce_mode": {
            "type": "string",
            "title": "Takeover Style",
            "description": "Once a day for a set length, or the first few minutes of every hour.",
            "enum": ["daily", "hourly"],
            "enumNames": ["Once a day", "Start of every hour"],
            "default": "daily",
        },
        "announce_hour": {
            "type": "integer",
            "title": "Start Hour",
            "description": "0-23, local time. Once a day: when it starts. Every hour: the first hour it runs.",
            "default": 8,
            "minimum": 0,
            "maximum": 23,
        },
        "announce_end_hour": {
            "type": "integer",
            "title": "End Hour (every-hour style)",
            "description": "0-24. The takeover doesn't start in this hour or later.",
            "default": 22,
            "minimum": 1,
            "maximum": 24,
        },
        "announce_minutes": {
            "type": "integer",
            "title": "Takeover Length (minutes)",
            "description": "Once a day: up to 1440. Every hour: capped at 60.",
            "default": 30,
            "minimum": 1,
            "maximum": 1440,
        },
        "takeover_priority": {
            "type": "integer",
            "title": "Takeover Priority",
            "description": "When two takeovers overlap, the higher number wins.",
            "default": default_priority,
            "minimum": 1,
            "maximum": 99,
        },
    }


class Takeover:
    """Mixin (deliberately NOT a PluginBase subclass, so the loader finds exactly
    one plugin class per module) providing timezone handling and trigger logic.

    Subclasses set ACTIVE_FLAG (the data key that must be "true" to take over)
    and DEFAULT_PRIORITY.
    """

    ACTIVE_FLAG = "active"
    DEFAULT_PRIORITY = 50

    def _tz(self) -> ZoneInfo:
        name = self.config.get("timezone") or DEFAULT_TZ
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo(DEFAULT_TZ)

    def _now(self) -> datetime:
        return datetime.now(self._tz())

    def _validate_common(self, config: dict[str, Any]) -> list[str]:
        errors = list(self._validate_refresh_seconds(config))
        tz = config.get("timezone") or DEFAULT_TZ
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            errors.append(f"Invalid timezone: {tz}")
        if config.get("announce_mode", "daily") not in ("daily", "hourly"):
            errors.append("Takeover style must be 'daily' or 'hourly'")
        if config.get("announce_mode") == "hourly" and int(config.get("announce_end_hour", 22)) <= int(
            config.get("announce_hour", 8)
        ):
            errors.append("End hour must be after the start hour")
        return errors

    def announce_window(self, now: datetime) -> tuple[datetime, str] | None:
        """(window end, stable window key) if a takeover window is open now."""
        cfg = self.config
        if not cfg.get("announce"):
            return None
        start_hour = int(cfg.get("announce_hour", 8))
        minutes = int(cfg.get("announce_minutes", 30))
        if cfg.get("announce_mode") == "hourly":
            end_hour = int(cfg.get("announce_end_hour", 22))
            if not (start_hour <= now.hour < end_hour):
                return None
            start = now.replace(minute=0, second=0, microsecond=0)
            end = start + timedelta(minutes=min(minutes, 60))
            key = now.strftime("%Y-%m-%d-%H")
        else:
            start = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
            end = start + timedelta(minutes=minutes)
            key = now.strftime("%Y-%m-%d")
        if start <= now < end:
            return end, key
        return None

    def check_triggers(self) -> list[TriggerResult]:
        now = self._now()
        window = self.announce_window(now)
        if window is None:
            return []
        # Use the cached fetch (refreshed every refresh_seconds). Recomputing
        # here would reshuffle confetti on every engine tick and re-flap the board.
        result = self.get_data()
        if not result.available or not result.data or result.data.get(self.ACTIVE_FLAG) != "true":
            return []
        end, key = window
        return [
            TriggerResult(
                triggered=True,
                # One id per window: re-returning it each tick just refreshes it,
                # and a manual page change suppresses it for the rest of the window.
                trigger_id=f"{self.plugin_id}_{key}",
                priority=int(self.config.get("takeover_priority", self.DEFAULT_PRIORITY)),
                duration_seconds=max(int((end - now).total_seconds()), 30),
                data=result.data,
                formatted_lines=result.formatted_lines,
                message=result.data.get("message") or None,
            )
        ]

    def _size(self) -> tuple[int, int]:
        board = self.board
        return (board.width if board else 22), (board.height if board else 6)

    def get_formatted_display(self) -> list[str] | None:
        result = self.fetch_data()
        if not result.available or not result.data:
            return None
        return result.formatted_lines


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------

_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_MONTHS_LC = [m.lower() for m in MONTHS]
_ORDINALS = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5, "first": 1, "second": 2, "third": 3,
             "fourth": 4, "fifth": 5, "last": -1}
_OFFSET_RE = re.compile(r"^(.*\S)\s*([+-])\s*(\d{1,3})$")
_MMDD_RE = re.compile(r"^(\d{1,2})[-/](\d{1,2})$")
_NTH_RE = re.compile(r"^(\w+)\s+([a-z]{3})[a-z]*\s+([a-z]{3})[a-z]*$")


def easter(year: int) -> date:
    """Western (Gregorian) Easter Sunday — anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return date(year, month, day + 1)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date | None:
    """n-th (1-5) or last (-1) weekday of a month; None if it doesn't exist."""
    if n == -1:
        last = (date(year + (month == 12), month % 12 + 1, 1)) - timedelta(days=1)
        return last - timedelta(days=(last.weekday() - weekday) % 7)
    first = date(year, month, 1)
    d = first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    return d if d.month == month else None


def parse_rule(raw: Any):
    """Parse a date rule into a function year -> date | None. Raises ValueError."""
    text = " ".join(str(raw or "").strip().lower().split())
    if not text:
        raise ValueError("date is empty")
    offset = 0
    m = _OFFSET_RE.match(text)
    if m and not _MMDD_RE.match(text):
        text, offset = m.group(1).strip(), int(m.group(2) + m.group(3))

    if text == "easter":
        base = easter
    elif (mm := _MMDD_RE.match(text)) is not None:
        month, day = int(mm.group(1)), int(mm.group(2))
        date(2000, month, day)  # validates (leap year so 02-29 passes)

        def base(year: int, month=month, day=day) -> date | None:
            try:
                return date(year, month, day)
            except ValueError:  # 02-29 in a non-leap year: skip that year
                return None
    elif (nm := _NTH_RE.match(text)) is not None:
        n = _ORDINALS.get(nm.group(1))
        if n is None or nm.group(2) not in _WEEKDAYS or nm.group(3) not in _MONTHS_LC:
            raise ValueError(f"'{raw}' — try e.g. '4th thu nov' or 'last mon may'")
        wd, month = _WEEKDAYS.index(nm.group(2)), _MONTHS_LC.index(nm.group(3)) + 1

        def base(year: int, wd=wd, month=month, n=n) -> date | None:
            return nth_weekday(year, month, wd, n)
    else:
        raise ValueError(f"'{raw}' isn't a date rule (examples: 12-25, 4th thu nov, last mon may, easter-47)")

    def rule(year: int) -> date | None:
        d = base(year)
        return d + timedelta(days=offset) if d else None

    return rule


# Preloaded defaults: fun US-centric holidays. Users can edit or delete any of
# these (and add their own) in the plugin settings.
DEFAULT_HOLIDAYS: list[dict[str, str]] = [
    {"name": "New Year's Day", "date": "01-01", "message": "HAPPY NEW YEAR!", "colors": "yellow, white"},
    {"name": "Valentine's Day", "date": "02-14", "message": "HAPPY VALENTINE'S DAY!", "colors": "red, violet, white"},
    {"name": "Leap Day", "date": "02-29", "message": "HAPPY LEAP DAY!", "colors": "green, blue"},
    {"name": "Mardi Gras", "date": "easter-47", "message": "HAPPY MARDI GRAS!", "colors": "violet, green, yellow"},
    {"name": "St. Patrick's Day", "date": "03-17", "message": "HAPPY ST. PATRICK'S DAY!", "colors": "green, white"},
    {"name": "April Fools' Day", "date": "04-01", "message": "HAPPY APRIL FOOLS' DAY!", "colors": ""},
    {"name": "Easter", "date": "easter", "message": "HAPPY EASTER!", "colors": "violet, yellow, green, blue"},
    {"name": "Earth Day", "date": "04-22", "message": "HAPPY EARTH DAY!", "colors": "green, blue"},
    {"name": "May the 4th", "date": "05-04", "message": "MAY THE 4TH BE WITH YOU", "colors": "blue, green, red"},
    {"name": "Cinco de Mayo", "date": "05-05", "message": "HAPPY CINCO DE MAYO!", "colors": "green, white, red"},
    {"name": "Mother's Day", "date": "2nd sun may", "message": "HAPPY MOTHER'S DAY!", "colors": "violet, red, white"},
    {"name": "Father's Day", "date": "3rd sun jun", "message": "HAPPY FATHER'S DAY!", "colors": "blue, green"},
    {"name": "Juneteenth", "date": "06-19", "message": "HAPPY JUNETEENTH!", "colors": "red, green, yellow"},
    {"name": "Independence Day", "date": "07-04", "message": "HAPPY 4TH OF JULY!", "colors": "red, white, blue"},
    {"name": "Ice Cream Day", "date": "3rd sun jul", "message": "HAPPY NATIONAL ICE CREAM DAY!", "colors": "white, violet, yellow"},
    {"name": "Dog Day", "date": "08-26", "message": "HAPPY NATIONAL DOG DAY!", "colors": "orange, yellow"},
    {"name": "Labor Day", "date": "1st mon sep", "message": "HAPPY LABOR DAY!", "colors": "red, white, blue"},
    {"name": "Coffee Day", "date": "09-29", "message": "HAPPY NATIONAL COFFEE DAY!", "colors": "orange, white"},
    {"name": "Cat Day", "date": "10-29", "message": "HAPPY NATIONAL CAT DAY!", "colors": "orange, violet"},
    {"name": "Halloween", "date": "10-31", "message": "HAPPY HALLOWEEN!", "colors": "orange, violet, green"},
    {"name": "Thanksgiving", "date": "4th thu nov", "message": "HAPPY THANKSGIVING!", "colors": "orange, red, yellow"},
    {"name": "Christmas Eve", "date": "12-24", "message": "MERRY CHRISTMAS EVE!", "colors": "red, green, white"},
    {"name": "Christmas", "date": "12-25", "message": "MERRY CHRISTMAS!", "colors": "red, green, white"},
    {"name": "New Year's Eve", "date": "12-31", "message": "HAPPY NEW YEAR'S EVE!", "colors": "yellow, white, violet"},
]


class HolidaysPlugin(Takeover, PluginBase):
    """Fun holidays from local date rules."""

    ACTIVE_FLAG = "is_holiday"
    DEFAULT_PRIORITY = 40

    @property
    def plugin_id(self) -> str:
        return "holidays"

    def _entries(self) -> list[dict[str, Any]]:
        raw = self.config.get("holidays")
        # Missing key → preloaded defaults. An explicitly emptied list stays empty.
        return DEFAULT_HOLIDAYS if raw is None else raw

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors = self._validate_common(config)
        entries = config.get("holidays")
        if entries is None:
            return errors
        if not isinstance(entries, list):
            return errors + ["Holidays must be a list"]
        for i, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                errors.append(f"Row {i}: expected a name and a date")
                continue
            name = str(entry.get("name") or "").strip()
            if not name:
                errors.append(f"Row {i}: name is required")
            try:
                parse_rule(entry.get("date"))
            except ValueError as e:
                errors.append(f"Row {i} ({name or 'unnamed'}): {e}")
        return errors

    def _holidays(self) -> list[tuple[str, str, list[str], Any]]:
        out = []
        for entry in self._entries():
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            try:
                rule = parse_rule(entry.get("date"))
            except ValueError as e:
                logger.warning("holidays: skipping %s: %s", name, e)
                continue
            message = str(entry.get("message") or "").strip().upper() or f"HAPPY {name.upper()}!"
            out.append((name.upper(), message, parse_palette(entry.get("colors")), rule))
        return out

    @staticmethod
    def _next(rule, after: date) -> date | None:
        """First occurrence on or after ``after`` (looks up to 8 years ahead for leap days)."""
        for year in range(after.year, after.year + 9):
            d = rule(year)
            if d and d >= after:
                return d
        return None

    def compute(self, today: date) -> dict[str, Any]:
        holidays = self._holidays()
        lookahead = int(self.config.get("lookahead_days") or 30)
        todays = [h for h in holidays if self._next(h[3], today) == today]
        upcoming = sorted(
            ((d - today).days, h[0], d)
            for h in holidays
            if (d := self._next(h[3], today + timedelta(days=1)))
        )
        palette = todays[0][2] if todays else []
        data: dict[str, Any] = {
            "is_holiday": "true" if todays else "false",
            "name": todays[0][0] if todays else "",
            "names": " & ".join(h[0] for h in todays),
            "message": todays[0][1] if todays else "",
            "count": str(len(todays)),
            "confetti": random_confetti(CONFETTI_TILES, palette),
            "today": [{"name": h[0], "message": h[1]} for h in todays],
            "upcoming": [
                {"name": name, "date": fmt_date(d), "days": str(days)}
                for days, name, d in upcoming
                if days <= lookahead
            ],
            "next_name": "",
            "next_date": "",
            "next_days": "",
            "next_formatted": "",
        }
        if upcoming:
            days, name, d = upcoming[0]
            data.update(next_name=name, next_date=fmt_date(d), next_days=str(days),
                        next_formatted=f"{name} {days_phrase(days)}")
        self._palette = palette
        return data

    def fetch_data(self) -> PluginResult:
        try:
            if not self._holidays():
                return PluginResult(available=False, error="No holidays configured")
            data = self.compute(self._now().date())
            return PluginResult(available=True, data=data, formatted_lines=self._format_display(data))
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("Error computing holidays")
            return PluginResult(available=False, error=str(e))

    def _format_display(self, data: dict[str, Any]) -> list[str]:
        width, height = self._size()
        if data.get("is_holiday") == "true":
            label = data["name"] + "!"
            return celebration_lines(label, width, height, getattr(self, "_palette", None))
        when = days_phrase(int(data["next_days"])) if data.get("next_days") else ""
        rows = ["NEXT HOLIDAY", "", data.get("next_name", ""), data.get("next_date", ""), when, ""]
        if height <= 3:
            rows = ["NEXT HOLIDAY", data.get("next_name", ""), when]
        return info_lines(rows, width, height)


Plugin = HolidaysPlugin

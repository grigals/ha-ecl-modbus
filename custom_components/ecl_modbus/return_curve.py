"""Return limit curve: make the fixed return limit follow the outdoor temperature.

The ECL 120 has one fixed return temperature limit (register 21210). The ECL 210/310
can make the limit follow the outdoor temperature. This module emulates that: after
each poll it works out a limit from a curve of 2 to 12 points and writes it to 21210.

Fail-safe: the controller keeps the last value written. If Home Assistant stops, the
ECL simply goes on limiting at that value.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import datetime, timedelta
from typing import Any

from pymodbus.exceptions import ModbusException

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import (
    CONF_RETURN_CURVE_AVERAGE_HOURS,
    CONF_RETURN_CURVE_MAX,
    CONF_RETURN_CURVE_MIN,
    CONF_RETURN_CURVE_OUTDOOR_ENTITY,
    CONF_RETURN_CURVE_POINTS,
    DEFAULT_RETURN_CURVE_AVERAGE_HOURS,
    DEFAULT_RETURN_CURVE_MAX,
    DEFAULT_RETURN_CURVE_MIN,
    DEFAULT_RETURN_CURVE_POINTS,
    DOMAIN,
)
from .modbus import EclModbusCoordinator, EclModbusHub
from .registers import ALL_REGISTERS

_LOGGER = logging.getLogger(__name__)

_BY_KEY = {reg.key: reg for reg in ALL_REGISTERS}
RETURN_LIMIT_REGISTER = _BY_KEY["heat_return_temperature_reference"]  # 21210
OUTDOOR_REGISTER = _BY_KEY["outside_temperature"]  # 21102

MIN_POINTS = 2
MAX_POINTS = 12
WRITE_THRESHOLD = 1.0  # K: smaller changes are not written
WRITE_INTERVAL = timedelta(minutes=30)  # at most one write per interval
# 21210 is a float with step 0.1. A value read back within this is "the same".
MATCH_TOLERANCE = 0.05
BUCKET_SECONDS = 600  # the outdoor average is kept in 10-minute buckets

ISSUE_PAUSED = "return_curve_paused"
ISSUE_NOT_APPLIED = "return_curve_not_applied"

STATUS_OFF = "off"
STATUS_ACTIVE = "active"
STATUS_VERIFYING = "verifying"
STATUS_PAUSED = "paused"
STATUS_OUTDOOR_UNAVAILABLE = "outdoor_unavailable"
STATUS_LIMIT_UNAVAILABLE = "return_limit_unavailable"
STATUS_INVALID_CURVE = "invalid_curve"

Point = tuple[float, float]


# -----------------------------------------------------------------------------
# Pure helpers
# -----------------------------------------------------------------------------


def parse_points(text: str) -> list[Point]:
    """Parse "outdoor:limit" pairs separated by commas, e.g. "-20:60, 4:37".

    "->" and "=" are accepted instead of ":". Returns the points sorted by outdoor
    temperature. Raises ValueError when the text is not 2 to 12 distinct points.
    """
    points: list[Point] = []
    for part in re.split(r"[,;\n]+", text or ""):
        part = part.strip()
        if not part:
            continue
        match = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(?:->|:|=)\s*(-?\d+(?:\.\d+)?)", part)
        if match is None:
            raise ValueError(f"Not an outdoor:limit pair: {part!r}")
        points.append((float(match[1]), float(match[2])))

    if not MIN_POINTS <= len(points) <= MAX_POINTS:
        raise ValueError(f"Give {MIN_POINTS} to {MAX_POINTS} points, not {len(points)}")
    points.sort()
    if len({x for x, _ in points}) != len(points):
        raise ValueError("Each outdoor temperature may appear only once")
    return points


def format_points(points: list[Point]) -> str:
    """Format points the way parse_points reads them."""
    return ", ".join(f"{_num(x)}:{_num(y)}" for x, y in points)


def _num(value: float) -> str:
    return f"{value:g}"


def interpolate(points: list[Point], outdoor: float) -> float:
    """Linear interpolation between the points, flat beyond the end points."""
    if outdoor <= points[0][0]:
        return points[0][1]
    if outdoor >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= outdoor <= x1:
            return y0 + (y1 - y0) * (outdoor - x0) / (x1 - x0)
    raise AssertionError("unreachable")  # pragma: no cover


def active_segment(points: list[Point], outdoor: float) -> str:
    """Describe which part of the curve applies, e.g. "-4 to 0 °C"."""
    if outdoor <= points[0][0]:
        return f"below {_num(points[0][0])} °C"
    if outdoor >= points[-1][0]:
        return f"above {_num(points[-1][0])} °C"
    for (x0, _), (x1, _) in zip(points, points[1:]):
        if x0 <= outdoor <= x1:
            return f"{_num(x0)} to {_num(x1)} °C"
    raise AssertionError("unreachable")  # pragma: no cover


def clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def calculate_limit(
    points: list[Point], outdoor: float, min_value: float, max_value: float
) -> float:
    """Return the limit to write for an (averaged) outdoor temperature."""
    return round(clamp(interpolate(points, outdoor), min_value, max_value), 1)


def should_write(
    target: float,
    current: float,
    last_write: datetime | None,
    now: datetime,
) -> bool:
    """Write only a change of at least WRITE_THRESHOLD, at most once per WRITE_INTERVAL."""
    if abs(target - current) < WRITE_THRESHOLD - 1e-9:
        return False
    return last_write is None or now - last_write >= WRITE_INTERVAL


def changed_externally(current: float, expected: float | None) -> bool:
    """True when the controller holds another value than the curve last left there."""
    return expected is not None and abs(current - expected) > MATCH_TOLERANCE


class OutdoorAverage:
    """Mean outdoor temperature over a sliding window, kept in 10-minute buckets.

    A window of 0 hours means no averaging: the latest value is used.
    """

    def __init__(self, window_hours: float) -> None:
        self.window_seconds = max(0.0, float(window_hours)) * 3600
        self._buckets: list[list[float]] = []  # [bucket start (unix s), sum, count]
        self._latest: float | None = None

    def add(self, now: datetime, value: float) -> None:
        self._latest = value
        start = now.timestamp() // BUCKET_SECONDS * BUCKET_SECONDS
        if self._buckets and self._buckets[-1][0] == start:
            self._buckets[-1][1] += value
            self._buckets[-1][2] += 1
        else:
            self._buckets.append([start, value, 1])
        self._prune(now)

    def _prune(self, now: datetime) -> None:
        cutoff = now.timestamp() - max(self.window_seconds, BUCKET_SECONDS)
        self._buckets = [b for b in self._buckets if b[0] + BUCKET_SECONDS > cutoff]

    def mean(self, now: datetime) -> float | None:
        if self.window_seconds == 0:
            return self._latest
        self._prune(now)
        count = sum(b[2] for b in self._buckets)
        if not count:
            return None
        return sum(b[1] for b in self._buckets) / count

    def as_list(self) -> list[list[float]]:
        return [list(b) for b in self._buckets]

    def restore(self, buckets: Any) -> None:
        try:
            self._buckets = [
                [float(start), float(total), int(count)]
                for start, total, count in buckets
                if int(count) > 0
            ]
        except (TypeError, ValueError):
            self._buckets = []


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# -----------------------------------------------------------------------------
# Controller
# -----------------------------------------------------------------------------


class ReturnCurveController:
    """Works out and writes the return limit after each poll."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: EclModbusCoordinator,
        hub: EclModbusHub,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.hub = hub

        options = entry.options
        self.points: list[Point] | None
        try:
            self.points = parse_points(
                options.get(CONF_RETURN_CURVE_POINTS, DEFAULT_RETURN_CURVE_POINTS)
            )
        except ValueError as exc:
            _LOGGER.error("ECL Modbus: return limit curve is invalid: %s", exc)
            self.points = None
        self.min_value = float(options.get(CONF_RETURN_CURVE_MIN, DEFAULT_RETURN_CURVE_MIN))
        self.max_value = float(options.get(CONF_RETURN_CURVE_MAX, DEFAULT_RETURN_CURVE_MAX))
        self.outdoor_entity: str | None = options.get(CONF_RETURN_CURVE_OUTDOOR_ENTITY) or None
        self.average = OutdoorAverage(
            options.get(CONF_RETURN_CURVE_AVERAGE_HOURS, DEFAULT_RETURN_CURVE_AVERAGE_HOURS)
        )

        self.enabled = False
        self.paused = False
        self.status = STATUS_OFF
        self.outdoor_now: float | None = None
        self.outdoor_average: float | None = None
        self.target: float | None = None
        self.current_limit: float | None = None  # what 21210 held at the last poll
        self.segment: str | None = None
        self.last_write: datetime | None = None
        self.last_result: str | None = None

        # The value the curve last left in 21210; anything else is an outside change.
        self._expected: float | None = None
        # A written value waiting for the read-back of a poll started after the write.
        self._pending: float | None = None
        self._pending_after_poll = 0

        self._lock = asyncio.Lock()
        self._hold_writes = False
        self._listeners: list[CALLBACK_TYPE] = []

    # --- Wiring --------------------------------------------------------------

    @callback
    def async_add_listener(self, update: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call `update` whenever the curve state changes. Returns the remover."""
        self._listeners.append(update)
        return lambda: self._listeners.remove(update)

    @callback
    def _notify(self) -> None:
        for update in list(self._listeners):
            update()

    @callback
    def async_start(self) -> CALLBACK_TYPE:
        """Evaluate after every poll. Returns the function that stops it."""
        remove = self.coordinator.async_add_listener(self._handle_coordinator_update)
        if self.enabled:
            # The first poll ran before the restored state was known.
            self.entry.async_create_task(
                self.hass, self.coordinator.async_request_refresh(), "ecl_return_curve_refresh"
            )
        return remove

    @callback
    def _handle_coordinator_update(self) -> None:
        self.entry.async_create_task(
            self.hass, self.async_evaluate(), "ecl_return_curve_evaluate"
        )

    @callback
    def _set_polled(self) -> None:
        """Poll 21210 and the ECL outdoor sensor while on, even if their entities are off."""
        extra = self.coordinator.extra_registers
        extra.clear()
        if self.enabled:
            extra[RETURN_LIMIT_REGISTER.key] = RETURN_LIMIT_REGISTER
            if self.outdoor_entity is None:
                extra[OUTDOOR_REGISTER.key] = OUTDOOR_REGISTER

    # --- State kept across restarts -------------------------------------------

    def as_restore_data(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "paused": self.paused,
            "expected": self._expected,
            "pending": self._pending,
            "last_write": self.last_write.isoformat() if self.last_write else None,
            "last_result": self.last_result,
            "outdoor_buckets": self.average.as_list(),
        }

    @callback
    def async_restore(self, data: dict[str, Any]) -> None:
        self.enabled = bool(data.get("enabled"))
        self.paused = self.enabled and bool(data.get("paused"))
        self._expected = _finite(data.get("expected"))
        self._pending = _finite(data.get("pending"))
        self._pending_after_poll = 0  # any later poll can confirm it
        last_write = data.get("last_write")
        self.last_write = dt_util.parse_datetime(last_write) if last_write else None
        self.last_result = data.get("last_result")
        self.average.restore(data.get("outdoor_buckets") or [])
        self.status = STATUS_PAUSED if self.paused else (
            STATUS_VERIFYING if self.enabled else STATUS_OFF
        )
        self._set_polled()

    # --- Switch -----------------------------------------------------------------

    async def async_set_enabled(self, enabled: bool, *, refresh: bool = True) -> None:
        """Turn the curve on or off. Turning it on again also resumes after a pause.

        refresh=False is for the options flow: the entry reloads with the new curve
        right after, so this instance must not write with the old one meanwhile.
        """
        async with self._lock:
            if not refresh:
                self._hold_writes = True
            self.enabled = enabled
            self.paused = False
            # Start from whatever the controller holds now.
            self._expected = None
            self._pending = None
            self.status = STATUS_VERIFYING if enabled else STATUS_OFF
            self._delete_issue(ISSUE_PAUSED)
            self._delete_issue(ISSUE_NOT_APPLIED)
            self._set_polled()
        self._notify()
        if enabled and refresh:
            await self.coordinator.async_request_refresh()

    # --- Evaluation ---------------------------------------------------------------

    async def async_evaluate(self) -> None:
        async with self._lock:
            await self._async_evaluate(dt_util.utcnow())
        self._notify()

    async def _async_evaluate(self, now: datetime) -> None:
        coordinator = self.coordinator
        data = (coordinator.data or {}) if coordinator.last_update_success else {}

        self.outdoor_now = self._read_outdoor(data)
        if self.outdoor_now is not None:
            self.average.add(now, self.outdoor_now)
        self.outdoor_average = self.average.mean(now)

        if self.points is not None and self.outdoor_average is not None:
            self.target = calculate_limit(
                self.points, self.outdoor_average, self.min_value, self.max_value
            )
            self.segment = active_segment(self.points, self.outdoor_average)
        else:
            self.target = None
            self.segment = None

        self.current_limit = _finite(data.get(RETURN_LIMIT_REGISTER.key))

        if not self.enabled:
            self.status = STATUS_OFF
            return
        if self.points is None:
            self.status = STATUS_INVALID_CURVE
            return
        if self.paused:
            self.status = STATUS_PAUSED
            return

        current = self.current_limit
        if current is None:
            self.status = STATUS_LIMIT_UNAVAILABLE
            return

        if self._pending is not None:
            if coordinator.data_poll <= self._pending_after_poll:
                self.status = STATUS_VERIFYING  # this poll may predate the write
                return
            self._verify(current)
        elif self._expected is None:
            self._expected = current
        elif changed_externally(current, self._expected):
            self._pause(current)
            return

        if self.outdoor_now is None or self.target is None:
            self.status = STATUS_OUTDOOR_UNAVAILABLE
            return

        self.status = STATUS_ACTIVE
        if not self._hold_writes and should_write(self.target, current, self.last_write, now):
            await self._async_write(now, self.target, current)

    def _read_outdoor(self, data: dict[str, Any]) -> float | None:
        if self.outdoor_entity is None:
            return _finite(data.get(OUTDOOR_REGISTER.key))

        state = self.hass.states.get(self.outdoor_entity)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        value = _finite(state.state)
        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        if value is not None and unit in (UnitOfTemperature.FAHRENHEIT, UnitOfTemperature.KELVIN):
            value = TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS)
        return value

    async def _async_write(self, now: datetime, target: float, current: float) -> None:
        self.last_write = now  # also limits retries after a failed write
        try:
            await self.hass.async_add_executor_job(
                self.hub.write_float, RETURN_LIMIT_REGISTER.address, target
            )
        except (ModbusException, OSError) as exc:
            self.last_result = f"write failed: {exc}"
            _LOGGER.warning(
                "ECL Modbus: could not write return limit %.1f °C: %s", target, exc
            )
            self._create_issue(ISSUE_NOT_APPLIED, target=target, read_back="no response")
            return

        _LOGGER.info(
            "ECL Modbus: return limit curve wrote %.1f °C (was %.1f °C, outdoor average %.1f °C)",
            target,
            current,
            self.outdoor_average,
        )
        self._pending = target
        self._pending_after_poll = self.coordinator.poll_count
        self.last_result = "waiting for read-back"
        self.status = STATUS_VERIFYING
        self.entry.async_create_task(
            self.hass, self.coordinator.async_request_refresh(), "ecl_return_curve_readback"
        )

    def _verify(self, current: float) -> None:
        pending, self._pending = self._pending, None
        if abs(current - pending) <= MATCH_TOLERANCE:
            self.last_result = "ok"
            self._expected = pending
            self._delete_issue(ISSUE_NOT_APPLIED)
            return

        # Accept what the controller holds, so this is not taken for an outside change.
        self._expected = current
        self.last_result = f"not applied: read back {current:.1f} °C"
        _LOGGER.warning(
            "ECL Modbus: return limit %.1f °C was written, but the controller reads back "
            "%.1f °C",
            pending,
            current,
        )
        self._create_issue(ISSUE_NOT_APPLIED, target=pending, read_back=f"{current:.1f} °C")

    def _pause(self, current: float) -> None:
        self.paused = True
        self.status = STATUS_PAUSED
        _LOGGER.warning(
            "ECL Modbus: the return limit was changed outside Home Assistant "
            "(%.1f °C, expected %.1f °C). The return limit curve is paused",
            current,
            self._expected,
        )
        self._create_issue(ISSUE_PAUSED, expected=self._expected, current=current)

    # --- Repairs ----------------------------------------------------------------

    def _issue_id(self, issue: str) -> str:
        return f"{issue}_{self.entry.entry_id}"

    def _create_issue(self, issue: str, **values: Any) -> None:
        placeholders = {
            key: f"{value:.1f} °C" if isinstance(value, float) else str(value)
            for key, value in values.items()
        }
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id(issue),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=issue,
            translation_placeholders={"name": self.entry.title, **placeholders},
        )

    def _delete_issue(self, issue: str) -> None:
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(issue))

    # --- Entity attributes ---------------------------------------------------------

    @property
    def attributes(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "outdoor_source": self.outdoor_entity or f"ECL register {OUTDOOR_REGISTER.address}",
            "outdoor_current": _round(self.outdoor_now),
            "outdoor_average": _round(self.outdoor_average),
            "average_hours": self.average.window_seconds / 3600,
            "active_segment": self.segment,
            "curve": format_points(self.points) if self.points else None,
            "min_limit": self.min_value,
            "max_limit": self.max_value,
            "last_write": self.last_write.isoformat() if self.last_write else None,
            "last_write_result": self.last_result,
        }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def remove_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Delete this entry's repair issues (used when the entry is removed)."""
    for issue in (ISSUE_PAUSED, ISSUE_NOT_APPLIED):
        ir.async_delete_issue(hass, DOMAIN, f"{issue}_{entry_id}")

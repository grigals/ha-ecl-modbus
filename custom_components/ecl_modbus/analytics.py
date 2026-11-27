"""Derived values: limiter reduction, flow/return difference, and daily return compliance.

ReturnCompliance is a plain accumulator, so it is easy to test. ReturnComplianceTracker
feeds it after every poll and keeps it across restarts.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .modbus import EclModbusCoordinator
from .registers import ALL_REGISTERS

_BY_KEY = {reg.key: reg for reg in ALL_REGISTERS}
FLOW = _BY_KEY["heat_flow_temperature"]  # 21100
RETURN = _BY_KEY["return_temperature"]  # 21104
RETURN_LIMIT = _BY_KEY["heat_return_temperature_reference"]  # 21210
CURVE_REFERENCE = _BY_KEY["heat_weather_comp_reference"]  # 21206
FLOW_REFERENCE = _BY_KEY["heat_flow_reference"]  # 21200

# A gap longer than this (an outage, a restart) is not counted as measured time.
MAX_SAMPLE_GAP = timedelta(minutes=10)

_STORE_VERSION = 1


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def difference(data: dict[str, Any] | None, minuend: str, subtrahend: str) -> float | None:
    """data[minuend] - data[subtrahend], rounded to 0.1, or None if either is missing."""
    if not data:
        return None
    a, b = finite(data.get(minuend)), finite(data.get(subtrahend))
    return None if a is None or b is None else round(a - b, 1)


@dataclass
class DayTotals:
    """How the return compared with its limit over one day."""

    day: str  # ISO date, local time
    measured_s: float = 0.0
    above_s: float = 0.0
    excess_kh: float = 0.0  # sum of (return - limit) over the time above, in K·h

    @property
    def percent_above(self) -> float | None:
        if self.measured_s <= 0:
            return None
        return round(100 * self.above_s / self.measured_s, 1)


@dataclass
class ReturnCompliance:
    """Accumulates, per local day, the time the return was above its limit and by how much."""

    today: DayTotals | None = None
    yesterday: DayTotals | None = None
    last_sample: datetime | None = field(default=None, repr=False)

    def add(self, now: datetime, return_temp: float | None, limit: float | None) -> None:
        """Count the time since the last sample with the values read now."""
        day = dt_util.as_local(now).date()
        self._roll_over(day)

        previous, self.last_sample = self.last_sample, now
        if return_temp is None or limit is None or previous is None:
            return
        elapsed = now - previous
        if elapsed <= timedelta(0) or elapsed > MAX_SAMPLE_GAP:
            return

        seconds = elapsed.total_seconds()
        self.today.measured_s += seconds
        if return_temp > limit:
            self.today.above_s += seconds
            self.today.excess_kh += (return_temp - limit) * seconds / 3600

    def _roll_over(self, day: date) -> None:
        if self.today is None:
            self.today = DayTotals(day.isoformat())
            return
        if self.today.day == day.isoformat():
            return
        previous_day = date.fromisoformat(self.today.day)
        self.yesterday = self.today if day - previous_day == timedelta(days=1) else None
        self.today = DayTotals(day.isoformat())

    def as_dict(self) -> dict[str, Any]:
        return {
            "today": asdict(self.today) if self.today else None,
            "yesterday": asdict(self.yesterday) if self.yesterday else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ReturnCompliance:
        result = cls()
        if not data:
            return result
        try:
            if data.get("today"):
                result.today = DayTotals(**data["today"])
            if data.get("yesterday"):
                result.yesterday = DayTotals(**data["yesterday"])
        except TypeError:
            return cls()
        return result


class ReturnComplianceTracker:
    """Feeds ReturnCompliance after every poll and stores it across restarts."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, coordinator: EclModbusCoordinator
    ) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.compliance = ReturnCompliance()
        self._store: Store[dict[str, Any]] = Store(
            hass, _STORE_VERSION, f"{DOMAIN}.{entry.entry_id}.return_compliance"
        )
        self._listeners: list[CALLBACK_TYPE] = []

    async def async_load(self) -> None:
        self.compliance = ReturnCompliance.from_dict(await self._store.async_load())

    @callback
    def async_start(self) -> CALLBACK_TYPE:
        """Start counting after every poll. Returns the function that stops it."""
        return self.coordinator.async_add_listener(self._handle_coordinator_update)

    async def async_save(self) -> None:
        """Keep what was counted up to now (on unload; a reload loads it straight back)."""
        await self._store.async_save(self.compliance.as_dict())

    @callback
    def async_add_listener(self, update: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(update)
        return lambda: self._listeners.remove(update)

    @callback
    def _handle_coordinator_update(self) -> None:
        coordinator = self.coordinator
        data = coordinator.data if coordinator.last_update_success else None
        data = data or {}
        self.compliance.add(
            dt_util.utcnow(),
            finite(data.get(RETURN.key)),
            finite(data.get(RETURN_LIMIT.key)),
        )
        self._store.async_delay_save(self.compliance.as_dict, 60)
        for update in list(self._listeners):
            update()

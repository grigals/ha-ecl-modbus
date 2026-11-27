"""Tests of the derived sensors: limiter reduction, flow/return difference, compliance."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ecl_modbus.analytics import (
    DayTotals,
    ReturnCompliance,
    difference,
)
from custom_components.ecl_modbus.const import DOMAIN
from custom_components.ecl_modbus.registers import ALL_REGISTERS

from .conftest import float_words

BY_KEY = {reg.key: reg for reg in ALL_REGISTERS}
# Noon UTC is the same local day in the test time zone (US/Pacific).
NOON = datetime(2026, 1, 15, 20, 0, tzinfo=timezone.utc)


# --- Pure logic ------------------------------------------------------------------


def test_difference() -> None:
    data = {"a": 54.44, "b": 50.0, "nan": float("nan")}
    assert difference(data, "a", "b") == 4.4
    assert difference(data, "b", "a") == -4.4
    assert difference(data, "a", "missing") is None
    assert difference(data, "a", "nan") is None
    assert difference(None, "a", "b") is None


def test_compliance_counts_time_and_excess() -> None:
    compliance = ReturnCompliance()
    compliance.add(NOON, 46.0, 44.0)  # first sample only starts the clock
    assert compliance.today.measured_s == 0

    compliance.add(NOON + timedelta(minutes=6), 46.0, 44.0)  # 2 K above for 6 min
    compliance.add(NOON + timedelta(minutes=12), 43.0, 44.0)  # below for 6 min
    today = compliance.today
    assert today.measured_s == 720
    assert today.above_s == 360
    assert today.percent_above == 50.0
    assert today.excess_kh == pytest.approx(0.2)  # 2 K * 0.1 h


def test_compliance_skips_gaps_and_missing_values() -> None:
    compliance = ReturnCompliance()
    compliance.add(NOON, 46.0, 44.0)
    compliance.add(NOON + timedelta(minutes=30), 46.0, 44.0)  # outage: not counted
    compliance.add(NOON + timedelta(minutes=31), None, 44.0)  # no return value
    compliance.add(NOON + timedelta(minutes=32), 46.0, 44.0)
    assert compliance.today.measured_s == 60


def test_compliance_rolls_over_at_local_midnight() -> None:
    compliance = ReturnCompliance()
    compliance.add(NOON, 46.0, 44.0)
    compliance.add(NOON + timedelta(minutes=5), 46.0, 44.0)
    first_day = compliance.today.day

    compliance.add(NOON + timedelta(days=1), 46.0, 44.0)
    assert compliance.yesterday.day == first_day
    assert compliance.yesterday.percent_above == 100.0
    assert compliance.today.measured_s == 0

    # Skipping a whole day leaves no "yesterday".
    compliance.add(NOON + timedelta(days=3), 46.0, 44.0)
    assert compliance.yesterday is None


def test_compliance_round_trip() -> None:
    compliance = ReturnCompliance(
        today=DayTotals("2026-01-15", 600, 300, 0.5), yesterday=DayTotals("2026-01-14", 10, 0, 0)
    )
    restored = ReturnCompliance.from_dict(compliance.as_dict())
    assert restored.today == compliance.today
    assert restored.yesterday == compliance.yesterday
    assert ReturnCompliance.from_dict({"today": {"bogus": 1}}).today is None


# --- Against a fake controller ----------------------------------------------------


def _set(fake_client, **values: float) -> None:
    for key, value in values.items():
        fake_client.set_words(BY_KEY[key].address, float_words(value))


def _state(hass: HomeAssistant, entry: MockConfigEntry, key: str):
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}")
    assert entity_id, key
    return hass.states.get(entity_id)


async def _poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


async def test_derived_sensors(hass: HomeAssistant, fake_client, freezer) -> None:
    _set(
        fake_client,
        heat_weather_comp_reference=54.4,
        heat_flow_reference=50.4,
        heat_flow_temperature=51.8,
        return_temperature=46.0,
        heat_return_temperature_reference=44.0,
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ECL Modbus",
        data={"name": "ECL Modbus", "transport": "tcp", "host": "10.0.0.5", "tcp_port": 502, "slave_id": 5},
        version=1,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _poll(hass, entry)

    # 21206 and 21210 have disabled entities, but the sensors have them polled.
    assert float(_state(hass, entry, "limiter_supply_reduction").state) == 4.0
    assert float(_state(hass, entry, "flow_return_difference").state) == 5.8

    # 2 K above the limit for 10 minutes, then below for 10 minutes.
    for _ in range(2):
        freezer.tick(timedelta(minutes=5))
        await _poll(hass, entry)
    _set(fake_client, return_temperature=43.0)
    for _ in range(2):
        freezer.tick(timedelta(minutes=5))
        await _poll(hass, entry)

    above = _state(hass, entry, "return_above_limit_today")
    assert float(above.state) == 50.0
    assert above.attributes["hours_above_today"] == pytest.approx(0.17, abs=0.01)
    excess = _state(hass, entry, "return_excess_today")
    assert float(excess.state) == pytest.approx(0.33, abs=0.01)  # 2 K * 1/6 h
    assert excess.attributes["unit_of_measurement"] == "K·h"

    # The day's totals survive a reload.
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert float(_state(hass, entry, "return_above_limit_today").state) == 50.0
    assert await hass.config_entries.async_unload(entry.entry_id)

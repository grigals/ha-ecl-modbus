"""Tests of the return limit curve: the maths, and the write logic against a fake ECL."""

from __future__ import annotations

import math
import struct
from datetime import datetime, timedelta, timezone

from xml.etree import ElementTree

import pytest
from homeassistant.components.image import async_get_image
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ecl_modbus.const import DOMAIN
from custom_components.ecl_modbus.curve_chart import preview_table, render_svg
from custom_components.ecl_modbus.return_curve import (
    OutdoorAverage,
    active_segment,
    calculate_limit,
    changed_externally,
    clamp,
    format_points,
    interpolate,
    parse_points,
    should_write,
)

from .conftest import float_words

# Example curve: Rigas Siltums zone 1, minus 5 K.
EXAMPLE = "-20->60, -14->59, -10->54, -4->47, 0->42, 4->37"
POINTS = parse_points(EXAMPLE)

RETURN_LIMIT = 21210
OUTDOOR = 21102
NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


# --- Curve maths ---------------------------------------------------------------


def test_parse_example_curve() -> None:
    assert POINTS == [(-20, 60), (-14, 59), (-10, 54), (-4, 47), (0, 42), (4, 37)]
    assert format_points(POINTS) == "-20:60, -14:59, -10:54, -4:47, 0:42, 4:37"
    assert parse_points(format_points(POINTS)) == POINTS


def test_parse_sorts_and_accepts_separators() -> None:
    assert parse_points("4=37; -20 : 60.5\n0->42") == [(-20, 60.5), (0, 42), (4, 37)]


@pytest.mark.parametrize(
    "text",
    ["", "-20:60", ",".join(f"{x}:40" for x in range(13)), "0:40, 0:45", "0:40, cold:60"],
    ids=["empty", "one", "thirteen", "duplicate", "garbage"],
)
def test_parse_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        parse_points(text)


@pytest.mark.parametrize(
    ("outdoor", "expected"),
    [
        (-20, 60),  # end points
        (4, 37),
        (-14, 59),  # inner points
        (0, 42),
        (-17, 59.5),  # between points
        (-12, 56.5),
        (-7, 50.5),
        (-2, 44.5),
        (2, 39.5),
        (-30, 60),  # flat beyond the end points
        (15, 37),
    ],
)
def test_interpolation(outdoor: float, expected: float) -> None:
    assert interpolate(POINTS, outdoor) == pytest.approx(expected)


# A 16-row table (outdoor -> return limit, every 2 K) that is flat at both ends.
TABLE = {
    10: 42, 8: 42, 6: 42, 4: 42, 2: 45, 0: 47, -2: 50, -4: 52,
    -6: 55, -8: 57, -10: 59, -12: 61, -14: 64, -16: 65, -18: 65, -20: 65,
}


def test_long_table_fits_with_12_points() -> None:
    """The flat ends come free, so the 11 rows from -16 to 4 reproduce the table."""
    points = parse_points(", ".join(f"{x}:{TABLE[x]}" for x in range(-16, 5, 2)))
    assert len(points) == 11
    for outdoor, limit in TABLE.items():
        assert interpolate(points, outdoor) == limit


def test_active_segment() -> None:
    assert active_segment(POINTS, -25) == "below -20 °C"
    assert active_segment(POINTS, -7) == "-10 to -4 °C"
    assert active_segment(POINTS, 10) == "above 4 °C"


def test_clamping() -> None:
    assert clamp(70, 40, 55) == 55
    assert clamp(30, 40, 55) == 40
    assert clamp(45, 40, 55) == 45
    assert calculate_limit(POINTS, -20, 40, 55) == 55
    assert calculate_limit(POINTS, 4, 40, 55) == 40
    # Rounded to the register's 0.1 step: -4.5 °C gives 47.583...
    assert calculate_limit(POINTS, -4.5, 5, 150) == 47.6


@pytest.mark.parametrize(
    ("target", "current", "last_write", "expected"),
    [
        (47.0, 47.9, None, False),  # under 1 K
        (47.0, 48.0, None, True),  # exactly 1 K
        (47.0, 46.0, None, True),  # downwards too
        (47.0, 50.0, NOW - timedelta(minutes=29), False),  # too soon
        (47.0, 50.0, NOW - timedelta(minutes=30), True),
    ],
)
def test_write_threshold(target, current, last_write, expected: bool) -> None:
    assert should_write(target, current, last_write, NOW) is expected


def test_external_change_detection() -> None:
    assert not changed_externally(47.0, None)  # nothing written yet
    assert not changed_externally(47.0000001, 47.0)  # float round trip
    assert changed_externally(45.0, 47.0)
    assert changed_externally(47.1, 47.0)


def test_outdoor_average() -> None:
    average = OutdoorAverage(24)
    assert average.mean(NOW) is None
    average.add(NOW - timedelta(hours=30), -20.0)  # outside the window
    average.add(NOW - timedelta(hours=10), -4.0)
    average.add(NOW - timedelta(hours=1), 0.0)
    average.add(NOW, 1.0)
    assert average.mean(NOW) == pytest.approx(-1.0)

    restored = OutdoorAverage(24)
    restored.restore(average.as_list())
    assert restored.mean(NOW) == pytest.approx(-1.0)
    assert average.mean(NOW + timedelta(hours=25)) is None


def test_no_averaging_uses_latest_value() -> None:
    average = OutdoorAverage(0)
    average.add(NOW, -4.0)
    average.add(NOW, 2.0)
    assert average.mean(NOW) == 2.0


# --- Against a fake controller ---------------------------------------------------


def _entry(hass: HomeAssistant, **options) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ECL Modbus",
        data={"name": "ECL Modbus", "transport": "tcp", "host": "10.0.0.5", "tcp_port": 502, "slave_id": 5},
        options={
            "return_curve_points": EXAMPLE,
            "return_curve_average_hours": 0,
            "return_curve_min": 30,
            "return_curve_max": 65,
            **options,
        },
        version=1,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    return entry


def _switch_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    return er.async_get(hass).async_get_entity_id(
        "switch", DOMAIN, f"{entry.entry_id}_return_limit_curve"
    )


def _sensor(hass: HomeAssistant, entry: MockConfigEntry):
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_return_limit_calculated"
    )
    return hass.states.get(entity_id)


def _curve_writes(fake_client) -> list[float]:
    return [
        struct.unpack(">f", struct.pack(">HH", *words))[0]
        for client in fake_client.instances
        for address, words in client.writes
        if address == RETURN_LIMIT
    ]


def _set_ecl(fake_client, *, outdoor: float | None = None, limit: float | None = None) -> None:
    if outdoor is not None:
        fake_client.set_words(OUTDOOR, float_words(outdoor))
    if limit is not None:
        fake_client.set_words(RETURN_LIMIT, float_words(limit))


async def _poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, *, on: bool) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    if on:
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": _switch_id(hass, entry)}, blocking=True
        )
        await hass.async_block_till_done()


def _issues(hass: HomeAssistant) -> set[str]:
    return {issue_id for (domain, issue_id) in ir.async_get(hass).issues if domain == DOMAIN}


async def test_off_by_default(hass: HomeAssistant, fake_client) -> None:
    _set_ecl(fake_client, outdoor=-4.0, limit=60.0)
    entry = _entry(hass)
    await _setup(hass, entry, on=False)
    await _poll(hass, entry)

    assert hass.states.get(_switch_id(hass, entry)).state == "off"
    assert _curve_writes(fake_client) == []
    # While off, the curve doesn't have 21210 polled (the compliance sensors may).
    assert entry.runtime_data.coordinator.extra_registers_of("return_curve") == []
    # The calculated value is shown anyway.
    state = _sensor(hass, entry)
    assert float(state.state) == 47.0
    assert state.attributes["status"] == "off"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_writes_and_verifies(hass: HomeAssistant, fake_client) -> None:
    _set_ecl(fake_client, outdoor=-4.0, limit=60.0)
    entry = _entry(hass)
    await _setup(hass, entry, on=True)

    assert _curve_writes(fake_client) == [47.0]
    # The read-back may already have run.
    assert _sensor(hass, entry).attributes["last_write_result"] in ("waiting for read-back", "ok")

    await _poll(hass, entry)
    attrs = _sensor(hass, entry).attributes
    assert attrs["last_write_result"] == "ok"
    assert attrs["status"] == "active"
    assert attrs["outdoor_average"] == -4.0
    assert attrs["active_segment"] == "-10 to -4 °C"
    assert attrs["last_write"] is not None
    assert _issues(hass) == set()
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_write_threshold_and_interval(hass: HomeAssistant, fake_client, freezer) -> None:
    _set_ecl(fake_client, outdoor=-4.0, limit=60.0)
    entry = _entry(hass)
    await _setup(hass, entry, on=True)
    await _poll(hass, entry)
    assert _curve_writes(fake_client) == [47.0]

    # Under 1 K: no write, however long it waits.
    _set_ecl(fake_client, outdoor=-4.5)
    freezer.tick(timedelta(hours=1))
    await _poll(hass, entry)
    assert float(_sensor(hass, entry).state) == 47.6
    assert _curve_writes(fake_client) == [47.0]

    _set_ecl(fake_client, outdoor=-10.0)
    await _poll(hass, entry)
    assert _curve_writes(fake_client) == [47.0, 54.0]
    await _poll(hass, entry)  # read-back

    # 1 K or more, but within 30 minutes of the last write: wait.
    _set_ecl(fake_client, outdoor=4.0)
    freezer.tick(timedelta(minutes=29))
    await _poll(hass, entry)
    assert _curve_writes(fake_client) == [47.0, 54.0]

    freezer.tick(timedelta(minutes=1))
    await _poll(hass, entry)
    assert _curve_writes(fake_client) == [47.0, 54.0, 37.0]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_clamped_to_min_and_max(hass: HomeAssistant, fake_client) -> None:
    _set_ecl(fake_client, outdoor=-25.0, limit=40.0)
    entry = _entry(hass, return_curve_min=40, return_curve_max=55)
    await _setup(hass, entry, on=True)
    assert _curve_writes(fake_client) == [55.0]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_pauses_on_external_change(hass: HomeAssistant, fake_client, freezer) -> None:
    _set_ecl(fake_client, outdoor=-4.0, limit=60.0)
    entry = _entry(hass)
    await _setup(hass, entry, on=True)
    await _poll(hass, entry)
    assert _curve_writes(fake_client) == [47.0]

    # Someone sets 45 °C in ECL Go.
    _set_ecl(fake_client, limit=45.0, outdoor=-10.0)
    await _poll(hass, entry)
    freezer.tick(timedelta(hours=2))
    await _poll(hass, entry)

    assert _curve_writes(fake_client) == [47.0]  # not overwritten
    state = hass.states.get(_switch_id(hass, entry))
    assert state.state == "on"
    assert state.attributes["paused"] is True
    assert _sensor(hass, entry).attributes["status"] == "paused"
    assert _issues(hass) == {f"return_curve_paused_{entry.entry_id}"}

    # Off and on again resumes from the value the controller holds now.
    for service in ("turn_off", "turn_on"):
        await hass.services.async_call(
            "switch", service, {"entity_id": _switch_id(hass, entry)}, blocking=True
        )
        await hass.async_block_till_done()
    assert _issues(hass) == set()
    assert _curve_writes(fake_client) == [47.0, 54.0]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_value_that_does_not_stick_raises_issue(hass: HomeAssistant, fake_client) -> None:
    _set_ecl(fake_client, outdoor=-4.0, limit=60.0)
    fake_client.write_ignored = True
    entry = _entry(hass)
    await _setup(hass, entry, on=True)
    await _poll(hass, entry)

    attrs = _sensor(hass, entry).attributes
    assert attrs["last_write_result"] == "not applied: read back 60.0 °C"
    assert _issues(hass) == {f"return_curve_not_applied_{entry.entry_id}"}
    # Not mistaken for an outside change.
    assert attrs["status"] == "active"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_failed_write_raises_issue(hass: HomeAssistant, fake_client) -> None:
    _set_ecl(fake_client, outdoor=-4.0, limit=60.0)
    fake_client.write_error = True
    entry = _entry(hass)
    await _setup(hass, entry, on=True)

    assert _sensor(hass, entry).attributes["last_write_result"].startswith("write failed")
    assert _issues(hass) == {f"return_curve_not_applied_{entry.entry_id}"}
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_no_write_without_outdoor_value(hass: HomeAssistant, fake_client) -> None:
    _set_ecl(fake_client, outdoor=math.nan, limit=60.0)
    entry = _entry(hass)
    await _setup(hass, entry, on=True)

    assert _curve_writes(fake_client) == []
    assert _sensor(hass, entry).attributes["status"] == "outdoor_unavailable"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_home_assistant_outdoor_sensor(hass: HomeAssistant, fake_client) -> None:
    _set_ecl(fake_client, outdoor=-20.0, limit=60.0)
    hass.states.async_set("sensor.outdoor", "0", {"unit_of_measurement": "°C"})
    entry = _entry(hass, return_curve_outdoor_entity="sensor.outdoor")
    await _setup(hass, entry, on=True)
    assert _curve_writes(fake_client) == [42.0]
    # The ECL outdoor register isn't needed for the curve then.
    assert [
        reg.key for reg in entry.runtime_data.coordinator.extra_registers_of("return_curve")
    ] == ["heat_return_temperature_reference"]

    hass.states.async_set("sensor.outdoor", "unavailable")
    await _poll(hass, entry)
    await _poll(hass, entry)
    assert _sensor(hass, entry).attributes["status"] == "outdoor_unavailable"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_state_survives_reload(hass: HomeAssistant, fake_client) -> None:
    _set_ecl(fake_client, outdoor=-4.0, limit=60.0)
    entry = _entry(hass)
    await _setup(hass, entry, on=True)
    await _poll(hass, entry)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    curve = entry.runtime_data.return_curve
    assert curve.enabled
    assert curve.last_result == "ok"
    assert hass.states.get(_switch_id(hass, entry)).state == "on"

    # An outside change while Home Assistant was down is still noticed.
    _set_ecl(fake_client, limit=50.0)
    await _poll(hass, entry)
    assert curve.paused
    assert await hass.config_entries.async_unload(entry.entry_id)


# --- Options flow ---------------------------------------------------------------


async def test_options_flow(hass: HomeAssistant, fake_client) -> None:
    entry = _entry(hass)
    hass.config_entries.async_update_entry(entry, options={})  # a fresh install
    await _setup(hass, entry, on=False)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"register_groups": [], "scan_interval": 30, "configure_return_curve": True},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "return_curve"

    user_input = {
        "return_curve_points": "0:42",
        "return_curve_average_hours": 24,
        "return_curve_min": 30,
        "return_curve_max": 65,
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input)
    assert result["errors"] == {"return_curve_points": "invalid_points"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**user_input, "return_curve_points": EXAMPLE, "return_curve_min": 70}
    )
    assert result["errors"] == {"return_curve_max": "min_not_below_max"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**user_input, "return_curve_points": EXAMPLE, "return_curve_outdoor_entity": "sensor.out"},
    )
    assert result["step_id"] == "return_curve_preview"
    placeholders = result["description_placeholders"]
    assert "| 4 °C | 37 °C |" in placeholders["table"]
    assert placeholders["outdoor"] == "sensor.out"
    assert placeholders["averaging"] == "24 h average"

    # Back to edit keeps what was entered.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"enable_return_curve": True, "edit_return_curve": True}
    )
    assert result["step_id"] == "return_curve"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**user_input, "return_curve_points": EXAMPLE, "return_curve_outdoor_entity": "sensor.out"},
    )
    assert entry.runtime_data.return_curve.enabled is False
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"enable_return_curve": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options == {
        "scan_interval": 30,
        "return_curve_points": "-20:60, -14:59, -10:54, -4:47, 0:42, 4:37",
        "return_curve_average_hours": 24.0,
        "return_curve_min": 30.0,
        "return_curve_max": 65.0,
        "return_curve_outdoor_entity": "sensor.out",
    }
    # The reload applies the new curve, and the tick turned it on.
    curve = entry.runtime_data.return_curve
    assert curve.outdoor_entity == "sensor.out"
    assert curve.enabled
    assert hass.states.get(_switch_id(hass, entry)).state == "on"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_preview_can_leave_curve_off(hass: HomeAssistant, fake_client) -> None:
    entry = _entry(hass)
    await _setup(hass, entry, on=True)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"register_groups": [], "scan_interval": 30, "configure_return_curve": True},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "return_curve_points": EXAMPLE,
            "return_curve_average_hours": 0,
            "return_curve_min": 30,
            "return_curve_max": 65,
        },
    )
    assert result["description_placeholders"]["averaging"] == "no averaging"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"enable_return_curve": False}
    )
    await hass.async_block_till_done()
    assert not entry.runtime_data.return_curve.enabled
    assert hass.states.get(_switch_id(hass, entry)).state == "off"
    assert await hass.config_entries.async_unload(entry.entry_id)


# --- Chart and preview -------------------------------------------------------------


def test_preview_table_marks_clamped_values() -> None:
    table = preview_table(POINTS, 40, 55)
    lines = table.splitlines()
    assert lines[2] == "| 8 °C | 40 °C * |"  # 4 K beyond the warm end, flat and clamped
    assert "| -10 °C | 54 °C |" in lines
    assert "| -24 °C | 55 °C * |" in lines
    assert lines[-1] == r"\* cut to the 40–55 °C range"
    assert "*" not in preview_table(POINTS, 5, 150)


def test_svg_chart_is_valid() -> None:
    svg = render_svg(POINTS, 40, 55, outdoor=-4.2, target=47.2, current=50.0, status="active")
    root = ElementTree.fromstring(svg)
    tags = [el.tag.split("}")[1] for el in root.iter()]
    assert tags.count("polyline") == 1
    assert tags.count("circle") == len(POINTS) + 2  # points, target, current
    assert "ECL holds 50.0 °C" in svg
    # Without an operating point it still draws the curve.
    ElementTree.fromstring(render_svg(POINTS, 5, 150))


async def test_chart_image_entity(hass: HomeAssistant, fake_client) -> None:
    _set_ecl(fake_client, outdoor=-4.0, limit=60.0)
    entry = _entry(hass)
    await _setup(hass, entry, on=True)
    await _poll(hass, entry)

    entity_id = er.async_get(hass).async_get_entity_id(
        "image", DOMAIN, f"{entry.entry_id}_return_limit_curve_chart"
    )
    first = hass.states.get(entity_id).state
    image = await async_get_image(hass, entity_id)
    assert image.content_type == "image/svg+xml"
    assert b"ECL holds 47.0" in image.content

    # A new operating point gives a new picture.
    _set_ecl(fake_client, outdoor=-10.0)
    await _poll(hass, entry)
    assert hass.states.get(entity_id).state != first
    assert b"Target 54" in (await async_get_image(hass, entity_id)).content
    assert await hass.config_entries.async_unload(entry.entry_id)

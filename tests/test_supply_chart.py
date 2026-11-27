"""Tests of the supply (heat) curve chart."""

from __future__ import annotations

from xml.etree import ElementTree

from homeassistant.components.image import async_get_image
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ecl_modbus.const import DOMAIN
from custom_components.ecl_modbus.curve_chart import _line_at, render_supply_svg
from custom_components.ecl_modbus.registers import ALL_REGISTERS

from .conftest import float_words

BY_KEY = {reg.key: reg for reg in ALL_REGISTERS}
# A heat curve as the Danfoss portal shows it.
HEAT_CURVE = [(-30.0, 85.0), (-15.0, 69.0), (-5.0, 58.0), (0.0, 52.0), (5.0, 43.0), (15.0, 31.0)]


def test_heat_curve_continues_its_end_slopes() -> None:
    # Beyond -30 it keeps rising at the -30..-15 slope, beyond 15 it keeps falling.
    assert _line_at(HEAT_CURVE, -35, extrapolate=True) == 85 + 16 / 15 * 5
    assert _line_at(HEAT_CURVE, 20, extrapolate=True) == 31 - 12 / 10 * 5
    # The return limit curve stays flat instead.
    assert _line_at(HEAT_CURVE, -35, extrapolate=False) == 85


def test_supply_svg_is_cut_to_min_and_max() -> None:
    svg = render_supply_svg(
        HEAT_CURVE, 30, 88, outdoor=-0.9, curve_reference=52.9, flow_reference=50.0, flow=49.6
    )
    root = ElementTree.fromstring(svg)
    ns = "{http://www.w3.org/2000/svg}"
    (line,) = root.iter(f"{ns}polyline")
    ys = [float(pair.split(",")[1]) for pair in line.get("points").split()]
    # The line bends where it meets the limits: a flat run at each end.
    assert ys[0] == ys[1]
    assert ys[-1] == ys[-2]
    assert "Curve 52.9 °C at -0.9 °C outdoor" in svg
    assert "Reference 50.0 °C" in svg
    assert "Flow 49.6 °C" in svg
    assert len(list(root.iter(f"{ns}circle"))) == len(HEAT_CURVE) + 3


async def test_supply_chart_entity(hass: HomeAssistant, fake_client) -> None:
    for (outdoor, supply), key in zip(
        HEAT_CURVE,
        [
            "circuit1_supply_temp_at_minus30",
            "circuit1_supply_temp_at_minus15",
            "circuit1_supply_temp_at_minus5",
            "circuit1_supply_temp_at_0",
            "circuit1_supply_temp_at_5",
            "circuit1_supply_temp_at_15",
        ],
    ):
        fake_client.set_words(BY_KEY[key].address, float_words(supply))
    for key, value in {
        "circuit1_supply_temp_min": 30.0,
        "circuit1_supply_temp_max": 95.0,
        "outside_temperature": -0.9,
        "heat_weather_comp_reference": 52.9,
        "heat_flow_reference": 50.0,
        "heat_flow_temperature": 49.6,
    }.items():
        fake_client.set_words(BY_KEY[key].address, float_words(value))

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

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("image", DOMAIN, f"{entry.entry_id}_supply_curve_chart")
    # The curve point entities are disabled, but the chart has them polled.
    point = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_circuit1_supply_temp_at_minus15"
    )
    assert registry.async_get(point).disabled
    assert hass.states.get(entity_id).state not in ("unknown", "unavailable")

    image = await async_get_image(hass, entity_id)
    assert image.content_type == "image/svg+xml"
    assert b"Curve 52.9" in image.content
    assert b"Supply" in image.content

    # Disabling the chart stops polling the registers only it needed.
    registry.async_update_entity(entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    client = fake_client.instances[-1]
    client.reads.clear()
    await entry.runtime_data.coordinator.async_refresh()
    assert BY_KEY["circuit1_supply_temp_at_minus15"].address not in client.reads
    assert await hass.config_entries.async_unload(entry.entry_id)

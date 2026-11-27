"""Image platform for ECL Modbus: charts of the return limit curve and the heat curve."""

from __future__ import annotations

import math

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import EclConfigEntry
from .curve_chart import render_supply_svg, render_svg
from .entity import EclReturnCurveEntity, device_info
from .modbus import EclModbusCoordinator
from .registers import ALL_REGISTERS

_BY_KEY = {reg.key: reg for reg in ALL_REGISTERS}

# The ECL heat curve: supply temperature at six fixed outdoor temperatures.
SUPPLY_CURVE_POINTS = {
    -30.0: "circuit1_supply_temp_at_minus30",
    -15.0: "circuit1_supply_temp_at_minus15",
    -5.0: "circuit1_supply_temp_at_minus5",
    0.0: "circuit1_supply_temp_at_0",
    5.0: "circuit1_supply_temp_at_5",
    15.0: "circuit1_supply_temp_at_15",
}
SUPPLY_MIN = "circuit1_supply_temp_min"
SUPPLY_MAX = "circuit1_supply_temp_max"
OUTDOOR = "outside_temperature"
CURVE_REFERENCE = "heat_weather_comp_reference"
FLOW_REFERENCE = "heat_flow_reference"
FLOW = "heat_flow_temperature"
# Polled for the chart even while their own entities are disabled. The outdoor, flow
# reference and flow markers use their entities (on by default) and are left out when
# the user has turned those off.
SUPPLY_CHART_REGISTERS = [
    _BY_KEY[key]
    for key in (*SUPPLY_CURVE_POINTS.values(), SUPPLY_MIN, SUPPLY_MAX, CURVE_REFERENCE)
]
_OWNER = "supply_curve_chart"


class _SvgImage(ImageEntity):
    """An image entity whose picture is an SVG string; only a new picture is a new state."""

    _attr_content_type = "image/svg+xml"
    _attr_icon = "mdi:chart-line"
    _svg: str | None = None

    def _set_svg(self, svg: str | None) -> bool:
        if svg == self._svg:
            return False
        self._svg = svg
        self._attr_image_last_updated = dt_util.utcnow()
        return True

    @property
    def available(self) -> bool:
        return self._svg is not None

    async def async_image(self) -> bytes | None:
        return self._svg.encode() if self._svg is not None else None


class EclReturnCurveImage(EclReturnCurveEntity, _SvgImage):
    """The return limit curve, its min/max limits, and where the controller is on it."""

    def __init__(self, hass: HomeAssistant, curve, entry_id: str, entry_title: str) -> None:
        EclReturnCurveEntity.__init__(
            self, curve, entry_id, entry_title, "return_limit_curve_chart", "Return limit curve chart"
        )
        ImageEntity.__init__(self, hass)
        self._update_svg()

    @callback
    def _handle_curve_update(self) -> None:
        if self._update_svg():
            self.async_write_ha_state()

    def _update_svg(self) -> bool:
        curve = self._curve
        svg = None
        if curve.points is not None:
            svg = render_svg(
                curve.points,
                curve.min_value,
                curve.max_value,
                outdoor=curve.outdoor_average,
                target=curve.target,
                current=curve.current_limit,
                status=curve.status,
            )
        return self._set_svg(svg)


class EclSupplyCurveImage(CoordinatorEntity[EclModbusCoordinator], _SvgImage):
    """The ECL heat curve (read from the controller) and the current supply temperatures.

    While enabled, the registers it needs are polled even if their own entities are
    disabled.
    """

    def __init__(
        self, hass: HomeAssistant, coordinator: EclModbusCoordinator, entry_id: str, entry_title: str
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self._attr_name = f"{entry_title} Supply curve chart"
        self._attr_unique_id = f"{entry_id}_supply_curve_chart"
        self._attr_device_info = device_info(entry_id, entry_title)
        self._update_svg()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.coordinator.set_extra_registers(_OWNER, SUPPLY_CHART_REGISTERS)
        self.async_on_remove(lambda: self.coordinator.set_extra_registers(_OWNER, []))
        # Registers whose entities are disabled are read by the refresh that
        # follows setup.
        self._update_svg()

    @property
    def available(self) -> bool:
        return self._svg is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._update_svg():
            self.async_write_ha_state()

    def _value(self, key: str) -> float | None:
        data = self.coordinator.data if self.coordinator.last_update_success else None
        try:
            value = float(data[key])
        except (KeyError, TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    def _update_svg(self) -> bool:
        points = [(x, self._value(key)) for x, key in SUPPLY_CURVE_POINTS.items()]
        min_value, max_value = self._value(SUPPLY_MIN), self._value(SUPPLY_MAX)
        if any(y is None for _, y in points) or min_value is None or max_value is None:
            # Keep the last picture through a failed poll rather than going blank.
            return False
        svg = render_supply_svg(
            points,
            min_value,
            max_value,
            outdoor=self._value(OUTDOOR),
            curve_reference=self._value(CURVE_REFERENCE),
            flow_reference=self._value(FLOW_REFERENCE),
            flow=self._value(FLOW),
        )
        return self._set_svg(svg)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EclConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = entry.runtime_data
    async_add_entities(
        [
            EclReturnCurveImage(hass, data.return_curve, entry.entry_id, data.title),
            EclSupplyCurveImage(hass, data.coordinator, entry.entry_id, data.title),
        ]
    )

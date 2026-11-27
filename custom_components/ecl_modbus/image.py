"""Image platform for ECL Modbus: a chart of the return limit curve."""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import EclConfigEntry
from .curve_chart import render_svg
from .entity import EclReturnCurveEntity


class EclReturnCurveImage(EclReturnCurveEntity, ImageEntity):
    """The curve, its min/max limits, and where the controller is on it now."""

    _attr_content_type = "image/svg+xml"
    _attr_icon = "mdi:chart-line"

    def __init__(self, hass: HomeAssistant, curve, entry_id: str, entry_title: str) -> None:
        EclReturnCurveEntity.__init__(
            self, curve, entry_id, entry_title, "return_limit_curve_chart", "Return limit curve chart"
        )
        ImageEntity.__init__(self, hass)
        self._svg: str | None = None
        self._update_svg()

    @callback
    def _handle_curve_update(self) -> None:
        # Only a new picture changes the entity's state (its last-updated time).
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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EclConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = entry.runtime_data
    async_add_entities(
        [EclReturnCurveImage(hass, data.return_curve, entry.entry_id, data.title)]
    )

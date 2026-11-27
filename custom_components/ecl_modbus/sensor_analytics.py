"""Sensors derived from several registers: limiter reduction, flow/return, return compliance."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE
from homeassistant.core import callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .analytics import (
    CURVE_REFERENCE,
    FLOW,
    FLOW_REFERENCE,
    RETURN,
    RETURN_LIMIT,
    ReturnComplianceTracker,
    difference,
)
from .entity import device_info
from .modbus import EclModbusCoordinator
from .registers import RegisterDef

UNIT_KELVIN = "K"
UNIT_KELVIN_HOURS = "K·h"


class _PollsRegisters(Entity):
    """While enabled, has the coordinator poll the registers it needs.

    Only registers whose entities are off by default are polled this way. One the
    user turned off on purpose stays unpolled, and the sensor is then unknown.
    """

    coordinator: EclModbusCoordinator
    _needs: list[RegisterDef]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        owner = self.unique_id
        self.coordinator.set_extra_registers(
            owner, [reg for reg in self._needs if not reg.enabled_default]
        )
        self.async_on_remove(lambda: self.coordinator.set_extra_registers(owner, []))


class EclDifferenceSensor(_PollsRegisters, CoordinatorEntity[EclModbusCoordinator], SensorEntity):
    """One register minus another, in K."""

    _attr_native_unit_of_measurement = UNIT_KELVIN
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: EclModbusCoordinator,
        entry_id: str,
        entry_title: str,
        *,
        key: str,
        name: str,
        icon: str,
        minuend: RegisterDef,
        subtrahend: RegisterDef,
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        self._attr_name = f"{entry_title} {name}"
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_icon = icon
        self._attr_device_info = device_info(entry_id, entry_title)
        self._minuend = minuend
        self._subtrahend = subtrahend
        self._needs = [minuend, subtrahend]
        self._attr_extra_state_attributes = {
            "ecl_modbus_registers": f"{minuend.address} - {subtrahend.address}"
        }

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data if self.coordinator.last_update_success else None
        return difference(data, self._minuend.key, self._subtrahend.key)


def limiter_reduction_sensor(coordinator, entry_id: str, title: str) -> EclDifferenceSensor:
    """How far the limiters pull the supply reference below what the curve asks for."""
    return EclDifferenceSensor(
        coordinator,
        entry_id,
        title,
        key="limiter_supply_reduction",
        name="Limiter supply reduction",
        icon="mdi:arrow-collapse-down",
        minuend=CURVE_REFERENCE,
        subtrahend=FLOW_REFERENCE,
    )


def flow_return_difference_sensor(coordinator, entry_id: str, title: str) -> EclDifferenceSensor:
    """Flow minus return temperature: the cooling the building achieves."""
    return EclDifferenceSensor(
        coordinator,
        entry_id,
        title,
        key="flow_return_difference",
        name="Flow return difference",
        icon="mdi:thermometer-chevron-down",
        minuend=FLOW,
        subtrahend=RETURN,
    )


class EclReturnComplianceSensor(_PollsRegisters, SensorEntity):
    """Today's share of time, or K·h, with the return above its limit (21104 vs 21210)."""

    _attr_should_poll = False
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        tracker: ReturnComplianceTracker,
        entry_id: str,
        entry_title: str,
        *,
        excess: bool,
    ) -> None:
        self._tracker = tracker
        self.coordinator = tracker.coordinator
        self._excess = excess
        self._needs = [RETURN, RETURN_LIMIT]
        self._attr_device_info = device_info(entry_id, entry_title)
        if excess:
            self._attr_name = f"{entry_title} Return excess today"
            self._attr_unique_id = f"{entry_id}_return_excess_today"
            self._attr_native_unit_of_measurement = UNIT_KELVIN_HOURS
            # Restarts from 0 each day; Home Assistant treats the drop as a new cycle.
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
            self._attr_icon = "mdi:thermometer-alert"
        else:
            self._attr_name = f"{entry_title} Return above limit today"
            self._attr_unique_id = f"{entry_id}_return_above_limit_today"
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_icon = "mdi:timer-alert-outline"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._tracker.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        today = self._tracker.compliance.today
        if today is None or today.measured_s <= 0:
            return None
        if self._excess:
            return round(today.excess_kh, 2)
        return today.percent_above

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        compliance = self._tracker.compliance
        today, yesterday = compliance.today, compliance.yesterday
        attrs: dict[str, Any] = {}
        if today is not None:
            attrs["measured_hours_today"] = round(today.measured_s / 3600, 2)
            attrs["hours_above_today"] = round(today.above_s / 3600, 2)
        if yesterday is not None:
            attrs["yesterday_percent_above"] = yesterday.percent_above
            attrs["yesterday_excess_kh"] = round(yesterday.excess_kh, 2)
        return attrs

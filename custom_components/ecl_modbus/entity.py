"""Shared base entity for all ECL Modbus platforms."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .modbus import EclModbusCoordinator
from .registers import RegisterDef, group_category

if TYPE_CHECKING:
    from .return_curve import ReturnCurveController


def device_info(entry_id: str, entry_title: str) -> DeviceInfo:
    """Return the one device that all entities of a config entry belong to."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        name=entry_title,
        manufacturer="Danfoss",
        model="ECL 120/220",
    )


class EclModbusEntity(CoordinatorEntity[EclModbusCoordinator]):
    """One entity backed by one register.

    The unique_id and the legacy-style name below must stay exactly as they are:
    Home Assistant links an entity's history and statistics through them.
    """

    def __init__(
        self,
        coordinator: EclModbusCoordinator,
        entry_id: str,
        entry_title: str,
        reg: RegisterDef,
    ) -> None:
        super().__init__(coordinator)
        self._reg = reg

        self._attr_name = f"{entry_title} {reg.name}"
        self._attr_unique_id = f"{entry_id}_{reg.key}"
        self._attr_icon = reg.icon
        self._attr_entity_registry_enabled_default = reg.enabled_default

        category = group_category(reg)
        self._attr_entity_category = EntityCategory(category) if category else None

        self._attr_device_info = device_info(entry_id, entry_title)

        self._attr_extra_state_attributes = {
            "ecl_modbus_register": reg.address,
            "ecl_modbus_type": reg.reg_type.value,
            "ecl_modbus_writable": reg.writable,
        }

    @property
    def _raw_value(self):
        """Return the last raw value read for this register, if any."""
        data = self.coordinator.data
        return data.get(self._reg.key) if data else None


class EclReturnCurveEntity(Entity):
    """An entity of the return limit curve, updated whenever the curve's state changes."""

    _attr_should_poll = False

    def __init__(
        self,
        curve: ReturnCurveController,
        entry_id: str,
        entry_title: str,
        key: str,
        name: str,
    ) -> None:
        self._curve = curve
        self._attr_name = f"{entry_title} {name}"
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = device_info(entry_id, entry_title)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._curve.async_add_listener(self._handle_curve_update))

    @callback
    def _handle_curve_update(self) -> None:
        self.async_write_ha_state()

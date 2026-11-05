"""Shared base entity for all ECL Modbus platforms."""

from __future__ import annotations

from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .modbus import EclModbusCoordinator
from .registers import RegisterDef, group_category


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

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=entry_title,
            manufacturer="Danfoss",
            model="ECL 120/220",
        )

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

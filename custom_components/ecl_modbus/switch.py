"""Switch platform for ECL Modbus: writable 0/1 registers."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pymodbus.exceptions import ModbusException

from . import EclConfigEntry
from .entity import EclModbusEntity
from .registers import ALL_REGISTERS, RegisterDef


class EclModbusRegisterSwitch(EclModbusEntity, SwitchEntity):
    """Switch for one writable 0/1 register."""

    def __init__(self, coordinator, entry_id: str, entry_title: str, hub, reg: RegisterDef) -> None:
        super().__init__(coordinator, entry_id, entry_title, reg)
        self._hub = hub

    @property
    def is_on(self) -> bool | None:
        raw = self._raw_value
        return None if raw is None else bool(raw)

    async def _async_write(self, value: int) -> None:
        try:
            await self.hass.async_add_executor_job(
                self._hub.write_int16, self._reg.address, value
            )
        except (ModbusException, OSError) as exc:
            raise HomeAssistantError(f"Could not write {self._reg.name}: {exc}") from exc

        if self.coordinator.data is not None:
            self.coordinator.data[self._reg.key] = value
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_write(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_write(0)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EclConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = entry.runtime_data
    async_add_entities(
        EclModbusRegisterSwitch(data.coordinator, entry.entry_id, data.title, data.hub, reg)
        for reg in ALL_REGISTERS
        if reg.platform == "switch"
    )

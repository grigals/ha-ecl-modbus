"""Select platform for ECL Modbus: writable token/enum registers.

Registers like 0=Auto, 1=Comfort, 2=Saving are chosen from a dropdown.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pymodbus.exceptions import ModbusException

from . import EclConfigEntry
from .entity import EclModbusEntity
from .registers import ALL_REGISTERS, RegisterDef


class EclModbusRegisterSelect(EclModbusEntity, SelectEntity):
    """Select entity for one token/enum register."""

    def __init__(self, coordinator, entry_id: str, entry_title: str, hub, reg: RegisterDef) -> None:
        super().__init__(coordinator, entry_id, entry_title, reg)
        self._hub = hub

        self._options_map: dict[int, str] = dict(reg.value_map or {})
        self._reverse_map: dict[str, int] = {
            label: code for code, label in self._options_map.items()
        }
        self._attr_options = [self._options_map[k] for k in sorted(self._options_map)]

    @property
    def current_option(self) -> str | None:
        """Return current option label from coordinator cache."""
        raw = self._raw_value
        if raw is None:
            return None

        try:
            code = int(raw)
        except (TypeError, ValueError):
            return None

        return self._options_map.get(code)

    async def async_select_option(self, option: str) -> None:
        """Write selected option to Modbus and update local cache."""
        code = self._reverse_map[option]

        try:
            await self.hass.async_add_executor_job(
                self._hub.write_int16, self._reg.address, code
            )
        except (ModbusException, OSError) as exc:
            raise HomeAssistantError(f"Could not write {self._reg.name}: {exc}") from exc

        # Update coordinator cache immediately (store RAW like reader does)
        if self.coordinator.data is not None:
            self.coordinator.data[self._reg.key] = code

        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EclConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Select entities for token/enum registers."""
    data = entry.runtime_data
    async_add_entities(
        EclModbusRegisterSelect(data.coordinator, entry.entry_id, data.title, data.hub, reg)
        for reg in ALL_REGISTERS
        if reg.platform == "select"
    )

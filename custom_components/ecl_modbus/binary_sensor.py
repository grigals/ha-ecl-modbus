"""Binary sensor platform for ECL Modbus: read-only 0/1 registers."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EclConfigEntry
from .entity import EclModbusEntity
from .registers import ALL_REGISTERS, RegisterDef


class EclModbusRegisterBinarySensor(EclModbusEntity, BinarySensorEntity):
    """Binary sensor exposing one read-only 0/1 register."""

    def __init__(self, coordinator, entry_id: str, entry_title: str, reg: RegisterDef) -> None:
        super().__init__(coordinator, entry_id, entry_title, reg)
        self._attr_device_class = reg.device_class

    @property
    def is_on(self) -> bool | None:
        raw = self._raw_value
        return None if raw is None else bool(raw)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EclConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = entry.runtime_data
    async_add_entities(
        EclModbusRegisterBinarySensor(data.coordinator, entry.entry_id, data.title, reg)
        for reg in ALL_REGISTERS
        if reg.platform == "binary_sensor"
    )

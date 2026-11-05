"""Sensor platform for ECL Modbus: read-only registers."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EclConfigEntry
from .entity import EclModbusEntity
from .registers import ALL_REGISTERS, RegisterDef, RegisterType


def _decimals_from_step(step: float | None) -> int | None:
    """Return number of decimals implied by step (e.g. 0.1 -> 1, 0.01 -> 2)."""
    if step is None:
        return None
    try:
        d = Decimal(str(step)).normalize()
    except (InvalidOperation, ValueError):
        return None
    exp = d.as_tuple().exponent
    return abs(exp) if exp < 0 else 0


class EclModbusRegisterSensor(EclModbusEntity, SensorEntity):
    """Generic sensor exposing one read-only register."""

    def __init__(self, coordinator, entry_id: str, entry_title: str, reg: RegisterDef) -> None:
        super().__init__(coordinator, entry_id, entry_title, reg)

        self._attr_native_unit_of_measurement = reg.unit
        self._attr_device_class = reg.device_class
        # If we show mapped text, don't let HA treat it as a measurement/statistics sensor
        self._attr_state_class = None if reg.value_map else reg.state_class

    @property
    def native_value(self) -> Any:
        raw = self._raw_value
        if raw is None:
            return None

        # Strings
        if self._reg.reg_type in (RegisterType.STRING16, RegisterType.STRING32):
            return raw

        # Enum/token mapping (show text instead of number)
        value_map = self._reg.value_map
        if value_map:
            try:
                code = int(float(raw))
            except (TypeError, ValueError):
                return None
            return value_map.get(code, str(code))

        scale = float(self._reg.scale or 1.0)

        # Integer-ish types: keep integers as integers (avoid 1234.0)
        if self._reg.reg_type in (RegisterType.INT16, RegisterType.UINT32):
            try:
                n = int(raw)
            except (TypeError, ValueError):
                try:
                    n = int(float(raw))
                except (TypeError, ValueError):
                    return None

            if scale == 1.0:
                return n

            # If scaling is used for an int-type, return scaled value with sensible rounding
            value = n * scale
            decimals = _decimals_from_step(self._reg.step)
            if decimals is not None:
                return round(value, decimals)
            return round(value, 6)

        # Float types (and any other numeric we treat as float)
        try:
            value = float(raw) * scale
        except (TypeError, ValueError):
            return None

        # Pretty rounding for common types
        if self._reg.device_class == "temperature":
            return round(value, 1)
        if self._reg.unit == "%":
            return round(value, 1)

        # Avoid ugly float representation (e.g. 30.100000381...)
        decimals = _decimals_from_step(self._reg.step)
        if decimals is not None:
            return round(value, decimals)

        return round(value, 6)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EclConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = entry.runtime_data
    async_add_entities(
        EclModbusRegisterSensor(data.coordinator, entry.entry_id, data.title, reg)
        for reg in ALL_REGISTERS
        if reg.platform == "sensor"
    )

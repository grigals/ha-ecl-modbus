"""Number platform for ECL Modbus: writable numeric registers."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pymodbus.exceptions import ModbusException

from . import EclConfigEntry
from .entity import EclModbusEntity
from .registers import ALL_REGISTERS, RegisterDef, RegisterType


def _coerce_step(value: float, step: float | None) -> float:
    if not step or step <= 0:
        return value
    snapped = round(value / step) * step
    return float(round(snapped, 6))


def _clamp(value: float, min_v: float | None, max_v: float | None) -> float:
    if min_v is not None and value < min_v:
        return min_v
    if max_v is not None and value > max_v:
        return max_v
    return value


class EclModbusRegisterNumber(EclModbusEntity, NumberEntity):
    """Number entity for one writable register."""

    _attr_mode = "box"

    def __init__(self, coordinator, entry_id: str, entry_title: str, hub, reg: RegisterDef) -> None:
        super().__init__(coordinator, entry_id, entry_title, reg)
        self._hub = hub

        self._attr_native_unit_of_measurement = reg.unit
        self._attr_device_class = reg.device_class

        self._attr_native_min_value = float(reg.min_value)
        self._attr_native_max_value = float(reg.max_value)
        self._attr_native_step = float(reg.step)

        self._attr_extra_state_attributes = {
            **self._attr_extra_state_attributes,
            "ecl_modbus_scale": reg.scale,
        }

    @property
    def native_value(self) -> float | None:
        raw = self._raw_value
        if raw is None:
            return None

        try:
            val = float(raw)
        except (TypeError, ValueError):
            return None

        value = val * float(self._reg.scale or 1.0)

        # Number of decimals derived from step (e.g. 0.1 -> 1, 0.01 -> 2)
        decimals = max(0, len(str(self._reg.step).split(".")[-1]))
        return round(value, decimals)

    async def async_set_native_value(self, value: float) -> None:
        # Enforce HA-side min/max/step
        value = _clamp(
            _coerce_step(float(value), self._attr_native_step),
            self._attr_native_min_value,
            self._attr_native_max_value,
        )

        # Undo scaling for raw write
        scale = float(self._reg.scale or 1.0)
        raw_to_write = value / scale

        try:
            await self.hass.async_add_executor_job(self._write_register_value, raw_to_write)
        except (ModbusException, OSError) as exc:
            raise HomeAssistantError(f"Could not write {self._reg.name}: {exc}") from exc

        # Update coordinator cache immediately if possible
        if self.coordinator.data is not None:
            self.coordinator.data[self._reg.key] = raw_to_write
        self.async_write_ha_state()

        # Read back the value the controller actually stored.
        await self.coordinator.async_request_refresh()

    def _write_register_value(self, raw_value: float) -> None:
        """Blocking write (runs in executor)."""
        if self._reg.reg_type == RegisterType.FLOAT:
            self._hub.write_float(self._reg.address, float(raw_value))
        elif self._reg.reg_type == RegisterType.INT16:
            self._hub.write_int16(self._reg.address, int(round(raw_value)))
        elif self._reg.reg_type == RegisterType.UINT32:
            self._hub.write_uint32(self._reg.address, int(round(raw_value)))
        else:
            raise ValueError(f"Unsupported writable type: {self._reg.reg_type}")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EclConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = entry.runtime_data
    async_add_entities(
        EclModbusRegisterNumber(data.coordinator, entry.entry_id, data.title, data.hub, reg)
        for reg in ALL_REGISTERS
        if reg.platform == "number"
    )

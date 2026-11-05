"""Shared Modbus hub + coordinator for ECL Modbus.

This module contains:
- EclModbusHub: handles connection + low-level read/write (serial RTU or TCP)
- EclModbusCoordinator: polls enabled registers on a global interval

Keeping this separate avoids platform import order issues (sensor/number).
"""

from __future__ import annotations

import logging
import struct
import threading
from datetime import timedelta
from typing import Any

from pymodbus.client import ModbusSerialClient, ModbusTcpClient
from pymodbus.exceptions import ModbusException, ModbusIOException

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DEFAULT_TCP_PORT,
    TRANSPORT_TCP,
)
from .entity_state import async_enabled_registers
from .registers import RegisterDef, RegisterType

_LOGGER = logging.getLogger(__name__)


class EclCommunicationError(Exception):
    """The controller or gateway could not be reached."""


class EclModbusHub:
    """Handle Modbus communication with the ECL controller (Serial RTU or TCP)."""

    def __init__(
        self,
        *,
        transport: str,
        slave_id: int,
        # Serial RTU:
        port: str | None = None,
        baudrate: int | None = None,
        # TCP:
        host: str | None = None,
        tcp_port: int | None = None,
    ) -> None:
        self._transport = transport
        self._slave_id = slave_id

        self._port = port
        self._baudrate = baudrate

        self._host = host
        self._tcp_port = tcp_port

        self._client: ModbusSerialClient | ModbusTcpClient | None = None
        self._lock = threading.Lock()
        self._rejected_addresses: set[int] = set()

    def _ensure_client(self) -> None:
        """Create/connect the underlying Modbus client if needed."""
        if self._client is not None and self._client.connected:
            return

        # Close previous client if present
        self._close_client()

        # Create correct client type based on transport
        if self._transport == TRANSPORT_TCP:
            if not self._host:
                raise ModbusIOException("TCP transport selected but host is missing")

            port = int(self._tcp_port or DEFAULT_TCP_PORT)
            _LOGGER.info("ECL Modbus: creating ModbusTcpClient to %s:%s", self._host, port)

            self._client = ModbusTcpClient(
                host=str(self._host),
                port=port,
                timeout=2,
            )

            if not self._client.connect():
                raise ModbusIOException(f"Could not connect to {self._host}:{port}")

            return

        # Default: Serial RTU
        if not self._port:
            raise ModbusIOException("Serial transport selected but port is missing")

        baudrate = int(self._baudrate or 38400)
        _LOGGER.info(
            "ECL Modbus: creating ModbusSerialClient on %s @ %s baud",
            self._port,
            baudrate,
        )

        self._client = ModbusSerialClient(
            port=str(self._port),
            baudrate=baudrate,
            parity="E",
            stopbits=1,
            bytesize=8,
            timeout=2,
        )

        if not self._client.connect():
            raise ModbusIOException(f"Could not connect to {self._port}")

    def close(self) -> None:
        """Close the connection. It is reopened on the next read or write."""
        with self._lock:
            self._close_client()

    def _close_client(self) -> None:
        """Close the connection. The caller must hold the lock."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
        self._client = None

    def _read_registers(self, reg_address_manual: int, count: int) -> list[int] | None:
        """Read holding registers (manual addresses are 1-based in Danfoss docs).

        Returns None if the controller rejects this register, so the other registers
        can still be read. Raises EclCommunicationError if the controller can't be
        reached, after reconnecting and trying once more.
        """
        result = None
        with self._lock:
            for attempt in (1, 2):
                error: Exception
                try:
                    self._ensure_client()
                    result = self._client.read_holding_registers(
                        address=reg_address_manual - 1,
                        count=count,
                        device_id=self._slave_id,
                    )
                except (ModbusException, OSError) as exc:
                    error = exc
                else:
                    if not isinstance(result, ModbusIOException):
                        break
                    error = result

                self._close_client()
                if attempt == 2:
                    raise EclCommunicationError(
                        f"No response reading register {reg_address_manual}: {error}"
                    ) from error

        if result.isError():
            # Warn once: this repeats on every poll until the entity is disabled.
            if reg_address_manual not in self._rejected_addresses:
                self._rejected_addresses.add(reg_address_manual)
                _LOGGER.warning(
                    "ECL Modbus: controller rejected register %s: %s. "
                    "Disable this entity if your controller doesn't have it",
                    reg_address_manual,
                    result,
                )
            return None

        return list(result.registers) or None

    def read_float(self, reg_address_manual: int) -> float | None:
        regs = self._read_registers(reg_address_manual, count=2)
        if regs is None:
            return None
        raw = (regs[0] << 16) | regs[1]
        return struct.unpack(">f", raw.to_bytes(4, byteorder="big"))[0]

    def read_int16(self, reg_address_manual: int, signed: bool = True) -> int | None:
        regs = self._read_registers(reg_address_manual, count=1)
        if regs is None:
            return None
        raw = regs[0]
        if signed and raw > 0x7FFF:
            raw -= 0x10000
        return raw

    def read_uint32(self, reg_address_manual: int) -> int | None:
        """Read an unsigned 32-bit integer from two holding registers (big-endian)."""
        regs = self._read_registers(reg_address_manual, count=2)
        if regs is None:
            return None

        return (regs[0] << 16) | regs[1]

    def read_string(self, reg_address_manual: int, reg_count: int) -> str | None:
        regs = self._read_registers(reg_address_manual, count=reg_count)
        if regs is None:
            return None
        raw_bytes = bytearray()
        for reg in regs:
            raw_bytes.extend(reg.to_bytes(2, byteorder="big"))
        text = raw_bytes.decode("ascii", errors="ignore").strip("\x00").strip()
        return text or None

    # -----------------
    # Write operations
    # -----------------

    def _write(self, reg_address_manual: int, values: list[int]) -> None:
        """Write one or more holding registers (manual addr is 1-based)."""
        with self._lock:
            try:
                self._ensure_client()
                address = reg_address_manual - 1
                if len(values) == 1:
                    result = self._client.write_register(
                        address=address,
                        value=values[0],
                        device_id=self._slave_id,
                    )
                else:
                    result = self._client.write_registers(
                        address=address,
                        values=values,
                        device_id=self._slave_id,
                    )
            except (ModbusException, OSError):
                self._close_client()
                raise

            if result.isError():
                self._close_client()
                raise ModbusIOException(
                    f"Write failed at register {reg_address_manual}: {result}"
                )

    def write_int16(self, reg_address_manual: int, value: int) -> None:
        """Write a single 16-bit holding register."""
        self._write(reg_address_manual, [int(value) & 0xFFFF])

    def write_float(self, reg_address_manual: int, value: float) -> None:
        """Write a 32-bit float into two holding registers (big-endian)."""
        b = struct.pack(">f", float(value))
        self._write(
            reg_address_manual,
            [int.from_bytes(b[0:2], "big"), int.from_bytes(b[2:4], "big")],
        )

    def write_uint32(self, reg_address_manual: int, value: int) -> None:
        """Write an unsigned 32-bit integer into two holding registers (big-endian)."""
        v = int(value)
        if v < 0 or v > 0xFFFFFFFF:
            raise ValueError("UINT32 value out of range (0 .. 4294967295)")
        self._write(reg_address_manual, [(v >> 16) & 0xFFFF, v & 0xFFFF])


class EclModbusCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll the enabled registers at a single global interval."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        hub: EclModbusHub,
        scan_interval_sec: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="ECL Modbus Coordinator",
            update_interval=timedelta(seconds=scan_interval_sec),
        )
        self.hub = hub

    def _read_register(self, reg: RegisterDef) -> Any:
        if reg.reg_type == RegisterType.FLOAT:
            return self.hub.read_float(reg.address)
        if reg.reg_type == RegisterType.INT16:
            return self.hub.read_int16(reg.address, signed=reg.signed)
        if reg.reg_type == RegisterType.UINT32:
            return self.hub.read_uint32(reg.address)
        if reg.reg_type == RegisterType.STRING16:
            return self.hub.read_string(reg.address, reg_count=8)
        if reg.reg_type == RegisterType.STRING32:
            return self.hub.read_string(reg.address, reg_count=16)
        return None

    async def _async_update_data(self) -> dict[str, Any]:
        # Read from the entity registry on every poll, so disabling an entity
        # stops its polling straight away.
        registers = async_enabled_registers(self.hass, self.config_entry)

        def _read_all() -> dict[str, Any]:
            return {reg.key: self._read_register(reg) for reg in registers}

        try:
            return await self.hass.async_add_executor_job(_read_all)
        except EclCommunicationError as exc:
            # Stop at the first failure: otherwise every register waits for its own
            # timeout. Entities show as unavailable until the connection is back.
            raise UpdateFailed(f"ECL Modbus update failed: {exc}") from exc

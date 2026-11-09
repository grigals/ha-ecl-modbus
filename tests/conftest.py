"""Shared fixtures: real Home Assistant, with a fake Modbus client."""

from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import patch

import struct

import pytest
from pymodbus.exceptions import ConnectionException, ModbusIOException


def float_words(value: float) -> list[int]:
    """Encode a float as the two big-endian words the controller sends."""
    raw = struct.pack(">f", value)
    return [int.from_bytes(raw[0:2], "big"), int.from_bytes(raw[2:4], "big")]


def string_words(text: str, reg_count: int) -> list[int]:
    """Encode ASCII text as NUL-padded big-endian words."""
    raw = text.encode("ascii").ljust(reg_count * 2, b"\x00")
    return [int.from_bytes(raw[i : i + 2], "big") for i in range(0, len(raw), 2)]


class FakeModbusClient:
    """Stands in for ModbusTcpClient/ModbusSerialClient and records all traffic.

    Class attributes control the simulated controller for the whole test:
    - values: 1-based address -> 16-bit word (unset words read as 1)
    - rejected_addresses: reads starting here return a Modbus error response
    - io_errors: the next N reads return a ModbusIOException (no response)
    - write_error / write_raises: make writes fail with an error result / exception
    """

    instances: list["FakeModbusClient"] = []
    reachable = True
    rejected_addresses: set[int] = set()
    values: dict[int, int] = {}
    io_errors = 0
    write_error = False
    write_raises = False

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.connected = False
        self.closed = 0
        self.reads: list[int] = []
        self.device_ids: set[int] = set()
        self.writes: list[tuple[int, list[int]]] = []
        FakeModbusClient.instances.append(self)

    @classmethod
    def set_words(cls, address: int, words: list[int]) -> None:
        for offset, word in enumerate(words):
            cls.values[address + offset] = word

    def connect(self) -> bool:
        self.connected = FakeModbusClient.reachable
        return self.connected

    def close(self) -> None:
        self.closed += 1
        self.connected = False

    def read_holding_registers(self, address: int, count: int, device_id: int):
        if not FakeModbusClient.reachable:
            raise ConnectionException("gateway unreachable")
        self.device_ids.add(device_id)
        if FakeModbusClient.io_errors > 0:
            FakeModbusClient.io_errors -= 1
            return ModbusIOException("no response")
        self.reads.append(address + 1)
        if address + 1 in FakeModbusClient.rejected_addresses:
            return SimpleNamespace(isError=lambda: True, registers=[])
        words = [FakeModbusClient.values.get(address + 1 + i, 1) for i in range(count)]
        return SimpleNamespace(isError=lambda: False, registers=words)

    def _store(self, address: int, words: list[int], device_id: int):
        """Like the controller: a successful write is read back by the next poll."""
        if not FakeModbusClient.reachable:
            raise ConnectionException("gateway unreachable")
        if FakeModbusClient.write_raises:
            raise ConnectionException("write timed out")
        self.device_ids.add(device_id)
        self.writes.append((address + 1, words))
        if FakeModbusClient.write_error:
            return SimpleNamespace(isError=lambda: True)
        FakeModbusClient.set_words(address + 1, words)
        return SimpleNamespace(isError=lambda: False)

    def write_register(self, address: int, value: int, device_id: int):
        return self._store(address, [value], device_id)

    def write_registers(self, address: int, values: list[int], device_id: int):
        return self._store(address, values, device_id)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations) -> None:
    """Let Home Assistant load the integration from custom_components."""


@pytest.fixture
def fake_client() -> Generator[type[FakeModbusClient]]:
    """Replace both Modbus clients with the fake."""
    FakeModbusClient.instances = []
    FakeModbusClient.reachable = True
    FakeModbusClient.rejected_addresses = set()
    FakeModbusClient.values = {}
    FakeModbusClient.io_errors = 0
    FakeModbusClient.write_error = False
    FakeModbusClient.write_raises = False
    with (
        patch("custom_components.ecl_modbus.modbus.ModbusTcpClient", FakeModbusClient),
        patch("custom_components.ecl_modbus.modbus.ModbusSerialClient", FakeModbusClient),
    ):
        yield FakeModbusClient

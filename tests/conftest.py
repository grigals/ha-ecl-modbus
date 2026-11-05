"""Shared fixtures: real Home Assistant, with a fake Modbus client."""

from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pymodbus.exceptions import ConnectionException


class FakeModbusClient:
    """Stands in for ModbusTcpClient/ModbusSerialClient and records all traffic."""

    instances: list["FakeModbusClient"] = []
    reachable = True
    rejected_addresses: set[int] = set()

    def __init__(self, **kwargs) -> None:
        self.connected = False
        self.reads: list[int] = []
        self.writes: list[tuple[int, list[int]]] = []
        FakeModbusClient.instances.append(self)

    def connect(self) -> bool:
        self.connected = FakeModbusClient.reachable
        return self.connected

    def close(self) -> None:
        self.connected = False

    def read_holding_registers(self, address: int, count: int, device_id: int):
        if not FakeModbusClient.reachable:
            raise ConnectionException("gateway unreachable")
        self.reads.append(address + 1)
        if address + 1 in FakeModbusClient.rejected_addresses:
            return SimpleNamespace(isError=lambda: True, registers=[])
        return SimpleNamespace(isError=lambda: False, registers=[1] * count)

    def write_register(self, address: int, value: int, device_id: int):
        self.writes.append((address + 1, [value]))
        return SimpleNamespace(isError=lambda: False)

    def write_registers(self, address: int, values: list[int], device_id: int):
        self.writes.append((address + 1, values))
        return SimpleNamespace(isError=lambda: False)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations) -> None:
    """Let Home Assistant load the integration from custom_components."""


@pytest.fixture
def fake_client() -> Generator[type[FakeModbusClient]]:
    """Replace both Modbus clients with the fake."""
    FakeModbusClient.instances = []
    FakeModbusClient.reachable = True
    FakeModbusClient.rejected_addresses = set()
    with (
        patch("custom_components.ecl_modbus.modbus.ModbusTcpClient", FakeModbusClient),
        patch("custom_components.ecl_modbus.modbus.ModbusSerialClient", FakeModbusClient),
    ):
        yield FakeModbusClient

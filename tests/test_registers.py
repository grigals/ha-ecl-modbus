"""Consistency checks for the register table, manifest and translations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.ecl_modbus.const import PLATFORMS
from custom_components.ecl_modbus.entity_state import LEGACY_REGISTER_KEYS
from custom_components.ecl_modbus.registers import (
    ALL_REGISTERS,
    REGISTER_GROUPS,
    RegisterType,
    group_category,
)

COMPONENT = Path(__file__).parents[1] / "custom_components" / "ecl_modbus"
WORDS = {
    RegisterType.FLOAT: 2,
    RegisterType.INT16: 1,
    RegisterType.UINT32: 2,
    RegisterType.STRING16: 8,
    RegisterType.STRING32: 16,
}
IDS = [reg.key for reg in ALL_REGISTERS]


def test_keys_are_unique() -> None:
    assert len(IDS) == len(set(IDS))


def test_names_are_unique() -> None:
    names = [reg.name for reg in ALL_REGISTERS]
    assert len(names) == len(set(names))


def test_registers_do_not_overlap() -> None:
    """Two registers must not read the same words (a typo in an address)."""
    owner: dict[int, str] = {}
    for reg in ALL_REGISTERS:
        for word in range(reg.address, reg.address + WORDS[reg.reg_type]):
            assert word not in owner, f"{reg.key} overlaps {owner[word]} at {word}"
            owner[word] = reg.key


def test_legacy_keys_still_exist() -> None:
    """Removing a legacy register would break migrating old installs."""
    assert LEGACY_REGISTER_KEYS <= set(IDS)


def test_every_platform_is_forwarded() -> None:
    assert {reg.platform for reg in ALL_REGISTERS} <= set(PLATFORMS)


@pytest.mark.parametrize("reg", ALL_REGISTERS, ids=IDS)
def test_register_definition(reg) -> None:
    assert reg.group in REGISTER_GROUPS
    assert reg.address > 0
    assert reg.scale != 0
    group_category(reg)  # must not raise

    if reg.device_class is not None and reg.platform == "sensor":
        SensorDeviceClass(reg.device_class)
    if reg.state_class is not None:
        SensorStateClass(reg.state_class)

    if reg.platform == "number":
        assert reg.reg_type in (RegisterType.FLOAT, RegisterType.INT16, RegisterType.UINT32)
        assert None not in (reg.min_value, reg.max_value, reg.step)
        assert reg.min_value < reg.max_value
        assert reg.step > 0
        if reg.reg_type == RegisterType.INT16 and not reg.signed:
            assert reg.min_value >= 0
            assert reg.max_value / reg.scale <= 0xFFFF
        if reg.reg_type == RegisterType.UINT32:
            assert reg.min_value >= 0

    if reg.platform == "select":
        assert reg.value_map
        assert reg.reg_type == RegisterType.INT16
        labels = list(reg.value_map.values())
        assert len(labels) == len(set(labels))

    if reg.binary:
        assert reg.reg_type == RegisterType.INT16
        assert not reg.value_map

    if reg.reg_type in (RegisterType.STRING16, RegisterType.STRING32):
        assert reg.platform == "sensor"
        assert reg.state_class is None, "text can't have a state class"

    if reg.value_map and reg.platform == "sensor":
        assert reg.reg_type == RegisterType.INT16


def test_translations_cover_groups() -> None:
    strings = json.loads((COMPONENT / "translations" / "en.json").read_text("utf-8"))
    assert set(strings["selector"]["register_groups"]["options"]) == set(REGISTER_GROUPS)


def test_manifest() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text("utf-8"))
    assert manifest["domain"] == "ecl_modbus"
    assert manifest["config_flow"] is True
    assert any(req.startswith("pymodbus") for req in manifest["requirements"])
    assert manifest["version"]

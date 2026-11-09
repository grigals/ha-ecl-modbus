"""End-to-end tests of entity states, writes, transports and the entry lifecycle."""

from __future__ import annotations

import math
from datetime import timedelta

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ecl_modbus.const import DOMAIN
from custom_components.ecl_modbus.registers import ALL_REGISTERS

from .conftest import float_words, string_words

TCP_DATA = {
    "name": "ECL Modbus",
    "transport": "tcp",
    "host": "10.0.0.5",
    "tcp_port": 502,
    "slave_id": 5,
}
SERIAL_DATA = {
    "name": "Boiler room",
    "transport": "serial",
    "port": "/dev/ttyUSB0",
    "baudrate": 38400,
    "slave_id": 5,
}
BY_KEY = {reg.key: reg for reg in ALL_REGISTERS}


def _entry(hass: HomeAssistant, data: dict = TCP_DATA, **kwargs) -> MockConfigEntry:
    kwargs.setdefault("version", 1)
    kwargs.setdefault("minor_version", 2)
    entry = MockConfigEntry(domain=DOMAIN, title=data["name"], data=data, **kwargs)
    entry.add_to_hass(hass)
    return entry


def _entity_id(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        BY_KEY[key].platform, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert entity_id, key
    return entity_id


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, *enable: str) -> None:
    """Set up the entry, with the given non-default registers enabled too."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    if not enable:
        return
    registry = er.async_get(hass)
    for key in enable:
        registry.async_update_entity(_entity_id(hass, entry, key), disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


def _state(hass: HomeAssistant, entry: MockConfigEntry, key: str):
    state = hass.states.get(_entity_id(hass, entry, key))
    assert state is not None, key
    return state


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


# --- Sensor values -----------------------------------------------------------


async def test_sensor_values(hass: HomeAssistant, fake_client) -> None:
    fake_client.set_words(BY_KEY["s1_temperature"].address, float_words(21.4567))
    fake_client.set_words(BY_KEY["outside_temperature"].address, float_words(-7.04))
    fake_client.values[BY_KEY["operation_status"].address] = 1
    fake_client.values[BY_KEY["heat_pump_state"].address] = 0
    fake_client.values[BY_KEY["motor_speed"].address] = 0xFFFF
    fake_client.set_words(BY_KEY["modbus_messages_ok"].address, [1, 4464])  # 70000
    fake_client.set_words(BY_KEY["firmware_version"].address, string_words("1.12", 8))
    fake_client.set_words(
        BY_KEY["ethernet_mac_address"].address, string_words("00:11:22:33:44:55", 16)
    )

    entry = _entry(hass)
    await _setup(
        hass,
        entry,
        "s1_temperature",
        "outside_temperature",
        "motor_speed",
        "modbus_messages_ok",
        "firmware_version",
        "ethernet_mac_address",
    )

    s1 = _state(hass, entry, "s1_temperature")
    assert s1.state == "21.5"
    assert s1.attributes["unit_of_measurement"] == "°C"
    assert s1.attributes["device_class"] == "temperature"
    assert s1.attributes["state_class"] == "measurement"
    assert s1.attributes["ecl_modbus_register"] == 4000
    assert s1.attributes["ecl_modbus_type"] == "float"
    assert s1.attributes["ecl_modbus_writable"] is False

    assert _state(hass, entry, "outside_temperature").state == "-7.0"
    assert _state(hass, entry, "operation_status").state == "Saving"
    heat_pump = _state(hass, entry, "heat_pump_state")
    assert heat_pump.state == "Off"
    assert "state_class" not in heat_pump.attributes  # mapped text has no statistics
    assert _state(hass, entry, "motor_speed").state == "65535"  # unsigned
    assert _state(hass, entry, "modbus_messages_ok").state == "70000"
    assert _state(hass, entry, "firmware_version").state == "1.12"
    assert _state(hass, entry, "ethernet_mac_address").state == "00:11:22:33:44:55"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unknown_mapped_code_is_shown_as_number(
    hass: HomeAssistant, fake_client
) -> None:
    fake_client.values[BY_KEY["operation_status"].address] = 9
    entry = _entry(hass)
    await _setup(hass, entry)
    assert _state(hass, entry, "operation_status").state == "9"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_values_update_on_the_polling_interval(
    hass: HomeAssistant, fake_client
) -> None:
    address = BY_KEY["heat_flow_temperature"].address
    fake_client.set_words(address, float_words(40.0))
    entry = _entry(hass, options={"scan_interval": 30})
    await _setup(hass, entry)
    assert _state(hass, entry, "heat_flow_temperature").state == "40.0"

    fake_client.set_words(address, float_words(55.5))
    # Not yet: the interval hasn't passed.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=20))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _state(hass, entry, "heat_flow_temperature").state == "40.0"

    # The coordinator adds up to 1 s of jitter, and polls in a background task.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=35))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _state(hass, entry, "heat_flow_temperature").state == "55.5"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_nan_temperature_does_not_break_the_entity(
    hass: HomeAssistant, fake_client, caplog
) -> None:
    """A disconnected sensor can read as NaN; the entity must not crash."""
    fake_client.set_words(BY_KEY["heat_flow_temperature"].address, float_words(math.nan))
    entry = _entry(hass)
    await _setup(hass, entry)
    assert _state(hass, entry, "heat_flow_temperature").state == "unknown"

    # And the same when it happens later, on an entity that already has a value.
    address = BY_KEY["return_temperature"].address
    fake_client.set_words(address, float_words(math.inf))
    await _refresh(hass, entry)
    assert _state(hass, entry, "return_temperature").state == "unknown"
    fake_client.set_words(address, float_words(31.0))
    await _refresh(hass, entry)
    assert _state(hass, entry, "return_temperature").state == "31.0"

    assert "Error adding entity" not in caplog.text
    assert "non-finite" not in caplog.text
    assert await hass.config_entries.async_unload(entry.entry_id)


# --- Binary sensors and switches ---------------------------------------------


async def test_binary_sensor_and_switch_states(hass: HomeAssistant, fake_client) -> None:
    fake_client.values[BY_KEY["alarm_sensor_missing"].address] = 1
    fake_client.values[BY_KEY["alarm_reference_not_reached"].address] = 0
    fake_client.values[BY_KEY["summer_cut_out_enable"].address] = 0
    entry = _entry(hass)
    await _setup(hass, entry, "summer_cut_out_enable")

    assert _state(hass, entry, "alarm_sensor_missing").state == "on"
    assert _state(hass, entry, "alarm_reference_not_reached").state == "off"
    assert _state(hass, entry, "summer_cut_out_enable").state == "off"

    fake_client.values[BY_KEY["alarm_reference_not_reached"].address] = 1
    await _refresh(hass, entry)
    assert _state(hass, entry, "alarm_reference_not_reached").state == "on"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_switch_on_and_off(hass: HomeAssistant, fake_client) -> None:
    entry = _entry(hass)
    await _setup(hass, entry, "bypass_enable")
    entity_id = _entity_id(hass, entry, "bypass_enable")
    address = BY_KEY["bypass_enable"].address

    await hass.services.async_call("switch", "turn_off", {"entity_id": entity_id}, blocking=True)
    assert hass.states.get(entity_id).state == "off"
    await hass.services.async_call("switch", "turn_on", {"entity_id": entity_id}, blocking=True)
    assert hass.states.get(entity_id).state == "on"

    writes = fake_client.instances[-1].writes
    assert writes[-2:] == [(address, [0]), (address, [1])]
    assert await hass.config_entries.async_unload(entry.entry_id)


# --- Select ------------------------------------------------------------------


async def test_select_options_and_state(hass: HomeAssistant, fake_client) -> None:
    fake_client.values[BY_KEY["operation_mode"].address] = 3
    entry = _entry(hass)
    await _setup(hass, entry)

    state = _state(hass, entry, "operation_mode")
    assert state.state == "Frost protection"
    assert state.attributes["options"] == [
        "Automatic",
        "Comfort",
        "Saving",
        "Frost protection",
    ]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_select_rejects_unknown_option(hass: HomeAssistant, fake_client) -> None:
    entry = _entry(hass)
    await _setup(hass, entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": _entity_id(hass, entry, "operation_mode"), "option": "Party"},
            blocking=True,
        )
    assert fake_client.instances[-1].writes == []
    assert await hass.config_entries.async_unload(entry.entry_id)


# --- Numbers -----------------------------------------------------------------


async def _set_number(hass: HomeAssistant, entry, key: str, value: float) -> None:
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _entity_id(hass, entry, key), "value": value},
        blocking=True,
    )


async def test_number_limits_and_state(hass: HomeAssistant, fake_client) -> None:
    fake_client.set_words(BY_KEY["circuit1_supply_temp_min"].address, float_words(10.0))
    entry = _entry(hass)
    await _setup(hass, entry, "circuit1_supply_temp_min")

    state = _state(hass, entry, "circuit1_supply_temp_min")
    assert float(state.state) == 10.0
    assert state.attributes["min"] == 5
    assert state.attributes["max"] == 150
    assert state.attributes["step"] == 1
    assert state.attributes["mode"] == "box"
    assert state.attributes["ecl_modbus_writable"] is True

    with pytest.raises(ServiceValidationError):
        await _set_number(hass, entry, "circuit1_supply_temp_min", 151)
    with pytest.raises(ServiceValidationError):
        await _set_number(hass, entry, "circuit1_supply_temp_min", 4)
    assert fake_client.instances[-1].writes == []
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_number_writes_each_type(hass: HomeAssistant, fake_client) -> None:
    entry = _entry(hass)
    await _setup(hass, entry, "circuit1_pid_xp_low", "boost_period", "circuit1_pid_deadband")
    client = fake_client.instances[-1]

    # INT16, single register.
    await _set_number(hass, entry, "circuit1_pid_xp_low", 37)
    assert client.writes[-1] == (BY_KEY["circuit1_pid_xp_low"].address, [37])

    # UINT32, two registers, high word first.
    await _set_number(hass, entry, "boost_period", 86400)
    assert client.writes[-1] == (BY_KEY["boost_period"].address, [1, 20864])

    # FLOAT with scale 2: the UI shows twice the stored value.
    await _set_number(hass, entry, "circuit1_pid_deadband", 10)
    assert client.writes[-1] == (BY_KEY["circuit1_pid_deadband"].address, float_words(5.0))
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_number_snaps_to_step(hass: HomeAssistant, fake_client) -> None:
    """boost_period has a 60 s step, which Home Assistant itself doesn't enforce."""
    entry = _entry(hass)
    await _setup(hass, entry, "boost_period")
    await _set_number(hass, entry, "boost_period", 100)
    assert fake_client.instances[-1].writes[-1] == (BY_KEY["boost_period"].address, [0, 120])
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_number_scaled_read(hass: HomeAssistant, fake_client) -> None:
    fake_client.set_words(BY_KEY["circuit1_pid_deadband"].address, float_words(3.0))
    entry = _entry(hass)
    await _setup(hass, entry, "circuit1_pid_deadband")
    state = _state(hass, entry, "circuit1_pid_deadband")
    assert float(state.state) == 6.0
    assert state.attributes["ecl_modbus_scale"] == 2.0
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_write_reads_back_controller_value(hass: HomeAssistant, fake_client) -> None:
    """After a write the entity follows what the controller actually stored."""
    address = BY_KEY["circuit1_supply_temp_min"].address
    entry = _entry(hass)
    await _setup(hass, entry, "circuit1_supply_temp_min")

    await _set_number(hass, entry, "circuit1_supply_temp_min", 30)
    assert float(_state(hass, entry, "circuit1_supply_temp_min").state) == 30.0

    # The controller stored something else (its own limits): the next poll shows it.
    fake_client.set_words(address, float_words(25.0))
    await _refresh(hass, entry)
    assert float(_state(hass, entry, "circuit1_supply_temp_min").state) == 25.0
    assert await hass.config_entries.async_unload(entry.entry_id)


# --- Write failures ----------------------------------------------------------


@pytest.mark.parametrize("failure", ["write_error", "write_raises", "unreachable"])
@pytest.mark.parametrize(
    ("key", "service", "data"),
    [
        ("operation_mode", "select.select_option", {"option": "Comfort"}),
        ("summer_cut_out_enable", "switch.turn_on", {}),
        ("circuit1_supply_temp_min", "number.set_value", {"value": 20}),
    ],
    ids=["select", "switch", "number"],
)
async def test_failed_write_raises_and_keeps_state(
    hass: HomeAssistant, fake_client, failure: str, key: str, service: str, data: dict
) -> None:
    fake_client.values[BY_KEY["operation_mode"].address] = 0
    fake_client.values[BY_KEY["summer_cut_out_enable"].address] = 0
    fake_client.set_words(BY_KEY["circuit1_supply_temp_min"].address, float_words(10.0))
    entry = _entry(hass)
    await _setup(hass, entry, "summer_cut_out_enable", "circuit1_supply_temp_min")
    entity_id = _entity_id(hass, entry, key)
    before = hass.states.get(entity_id).state

    if failure == "unreachable":
        fake_client.reachable = False
        entry.runtime_data.hub.close()
    else:
        setattr(fake_client, failure, True)

    domain, name = service.split(".")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            domain, name, {"entity_id": entity_id, **data}, blocking=True
        )
    assert hass.states.get(entity_id).state == before

    # Writes work again once the controller is back.
    fake_client.reachable = True
    fake_client.write_error = fake_client.write_raises = False
    await hass.services.async_call(domain, name, {"entity_id": entity_id, **data}, blocking=True)
    assert hass.states.get(entity_id).state != before
    assert await hass.config_entries.async_unload(entry.entry_id)


# --- Device, naming and categories -------------------------------------------


async def test_device_and_entity_metadata(hass: HomeAssistant, fake_client) -> None:
    entry = _entry(hass)
    await _setup(hass, entry)
    registry = er.async_get(hass)

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert device is not None
    assert device.name == "ECL Modbus"
    assert device.manufacturer == "Danfoss"

    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert len(entities) == len(ALL_REGISTERS)
    assert {e.device_id for e in entities} == {device.id}

    def category(key: str):
        return registry.async_get(_entity_id(hass, entry, key)).entity_category

    assert category("s1_temperature") is None
    assert category("circuit1_supply_temp_min") is EntityCategory.CONFIG
    assert category("firmware_version") is EntityCategory.DIAGNOSTIC
    assert (
        hass.states.get(_entity_id(hass, entry, "heat_flow_temperature")).name
        == f"ECL Modbus {BY_KEY['heat_flow_temperature'].name}"
    )
    assert await hass.config_entries.async_unload(entry.entry_id)


# --- Transports and config flow ----------------------------------------------


async def test_serial_setup(hass: HomeAssistant, fake_client) -> None:
    entry = _entry(hass, {**SERIAL_DATA, "slave_id": 9, "baudrate": 19200})
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    client = fake_client.instances[0]
    assert client.kwargs["port"] == "/dev/ttyUSB0"
    assert client.kwargs["baudrate"] == 19200
    assert client.kwargs["parity"] == "E"
    assert client.device_ids == {9}
    assert hass.states.get(_entity_id(hass, entry, "return_temperature")).name.startswith(
        "Boiler room "
    )
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_serial_config_flow(hass: HomeAssistant, fake_client) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Boiler room", "transport": "serial"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "serial"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"port": "/dev/ttyUSB0"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Boiler room"
    assert result["data"] == SERIAL_DATA
    await hass.async_block_till_done()

    entry = result["result"]
    assert entry.unique_id == "/dev/ttyUSB0:5"
    assert (entry.version, entry.minor_version) == (1, 2)
    assert entry.state is ConfigEntryState.LOADED

    # The same serial controller can't be added twice.
    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    again = await hass.config_entries.flow.async_configure(
        again["flow_id"], {"name": "Dup", "transport": "serial"}
    )
    again = await hass.config_entries.flow.async_configure(
        again["flow_id"], {"port": "/dev/ttyUSB0", "slave_id": 5}
    )
    assert again["type"] is FlowResultType.ABORT
    assert again["reason"] == "already_configured"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_tcp_config_flow_defaults(hass: HomeAssistant, fake_client) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"transport": "tcp"}
    )
    assert result["step_id"] == "tcp"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "10.0.0.5"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == TCP_DATA
    assert result["result"].unique_id == "10.0.0.5:502:5"
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(result["result"].entry_id)


# --- Options and lifecycle ---------------------------------------------------


@pytest.mark.parametrize(
    ("stored", "expected"),
    [(None, 30), (60, 60), (1, 5), (99999, 3600), ("bad", 30)],
)
async def test_scan_interval(hass: HomeAssistant, fake_client, stored, expected: int) -> None:
    options = {} if stored is None else {"scan_interval": stored}
    entry = _entry(hass, options=options)
    await _setup(hass, entry)
    assert entry.runtime_data.coordinator.update_interval == timedelta(seconds=expected)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_options_change_reloads_with_new_interval(
    hass: HomeAssistant, fake_client
) -> None:
    entry = _entry(hass)
    await _setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"register_groups": [], "scan_interval": 120}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.coordinator.update_interval == timedelta(seconds=120)
    # The old connection is closed when the entry reloads.
    assert fake_client.instances[0].closed
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_options_flow_clamps_interval(hass: HomeAssistant, fake_client) -> None:
    entry = _entry(hass)
    await _setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"register_groups": [], "scan_interval": 1}
    )
    assert entry.options["scan_interval"] == 5
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_enabling_a_group_polls_it_after_reload(
    hass: HomeAssistant, fake_client
) -> None:
    entry = _entry(hass)
    await _setup(hass, entry)
    groups = (await hass.config_entries.options.async_init(entry.entry_id))
    result = await hass.config_entries.options.async_configure(
        groups["flow_id"], {"register_groups": ["device"], "scan_interval": 30}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    # Home Assistant reloads ~30 s after entities are enabled.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=35))
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    polled = set(fake_client.instances[-1].reads)
    assert BY_KEY["firmware_version"].address in polled
    assert _state(hass, entry, "firmware_version").state not in ("unknown", "unavailable")
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unload_closes_connection(hass: HomeAssistant, fake_client) -> None:
    entry = _entry(hass)
    await _setup(hass, entry)
    client = fake_client.instances[0]
    assert client.connected

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not client.connected
    assert hass.states.get(_entity_id(hass, entry, "return_temperature")).state == "unavailable"


async def test_setup_retry_closes_connection_and_recovers(
    hass: HomeAssistant, fake_client
) -> None:
    fake_client.io_errors = 2  # first refresh fails, nothing answers
    entry = _entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert all(not c.connected for c in fake_client.instances)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_future_version_is_not_migrated(hass: HomeAssistant, fake_client) -> None:
    entry = _entry(hass, version=2, minor_version=1)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.MIGRATION_ERROR
    assert fake_client.instances == []


async def test_two_controllers_are_independent(hass: HomeAssistant, fake_client) -> None:
    """Each entry has its own connection, slave ID, device and entities."""
    first = _entry(hass, TCP_DATA)
    second = _entry(hass, {**TCP_DATA, "name": "Second", "slave_id": 6})
    fake_client.set_words(BY_KEY["heat_flow_temperature"].address, float_words(44.0))
    await _setup(hass, first)  # sets up every entry of the integration
    assert second.state is ConfigEntryState.LOADED

    assert first.runtime_data.hub is not second.runtime_data.hub
    assert {c.device_ids.pop() for c in fake_client.instances if c.device_ids} == {5, 6}
    assert _entity_id(hass, first, "heat_flow_temperature") != _entity_id(
        hass, second, "heat_flow_temperature"
    )

    # Losing one controller doesn't affect the other.
    assert await hass.config_entries.async_unload(first.entry_id)
    await _refresh(hass, second)
    assert _state(hass, second, "heat_flow_temperature").state == "44.0"
    assert await hass.config_entries.async_unload(second.entry_id)

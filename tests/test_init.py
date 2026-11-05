"""Tests for migration, enabling, groups, polling and writes."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ecl_modbus.const import DOMAIN
from custom_components.ecl_modbus.entity_state import LEGACY_REGISTER_KEYS
from custom_components.ecl_modbus.registers import ALL_REGISTERS, registers_in_group

TCP_DATA = {
    "name": "ECL Modbus",
    "transport": "tcp",
    "host": "10.0.0.5",
    "tcp_port": 502,
    "slave_id": 5,
}
BY_KEY = {reg.key: reg for reg in ALL_REGISTERS}


def _old_entry(hass: HomeAssistant, options: dict) -> MockConfigEntry:
    """Create a config entry as version 2.3.0 stored it."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ECL Modbus",
        data=TCP_DATA,
        options=options,
        version=1,
        minor_version=1,
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)
    return entry


def _new_entry(hass: HomeAssistant, **data) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ECL Modbus",
        data={**TCP_DATA, **data},
        version=1,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    return entry


def _registered(
    registry: er.EntityRegistry, entry: MockConfigEntry, key: str, entity_id: str
) -> er.RegistryEntry:
    """Register an entity the way 2.3.0 did, under a custom entity_id."""
    domain, object_id = entity_id.split(".")
    return registry.async_get_or_create(
        domain,
        DOMAIN,
        f"{entry.entry_id}_{key}",
        config_entry=entry,
        suggested_object_id=object_id,
    )


def _entry_for(registry: er.EntityRegistry, entry: MockConfigEntry, key: str):
    entity_id = registry.async_get_entity_id(
        BY_KEY[key].platform, DOMAIN, f"{entry.entry_id}_{key}"
    )
    return registry.async_get(entity_id) if entity_id else None


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_migration_keeps_existing_entities(
    hass: HomeAssistant, fake_client
) -> None:
    """Existing entities stay the same entity, and the selection is carried over."""
    registry = er.async_get(hass)
    entry = _old_entry(
        hass,
        {
            "enable_s1_temperature": True,
            "enable_valve_position": True,
            "enable_operation_mode": True,
            "enable_circuit1_supply_temp_min": True,
            "enable_outside_temperature": False,
            "enable_heat_pump_state": False,
            "enable_s2_temperature": True,
            "scan_interval": 60,
        },
    )

    # Enabled, with entity_ids the user may have renamed.
    kept = {
        "s1_temperature": _registered(registry, entry, "s1_temperature", "sensor.my_flow"),
        "valve_position": _registered(registry, entry, "valve_position", "sensor.my_valve"),
        "operation_mode": _registered(registry, entry, "operation_mode", "select.my_mode"),
        "circuit1_supply_temp_min": _registered(
            registry, entry, "circuit1_supply_temp_min", "number.my_min"
        ),
    }
    # Disabled by the user by hand, while its option was on.
    by_hand = _registered(registry, entry, "s2_temperature", "sensor.my_s2")
    registry.async_update_entity(
        by_hand.entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )
    # Unticked after it had been enabled, so it has history but is not provided.
    unticked = _registered(registry, entry, "outside_temperature", "sensor.my_outside")

    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert (entry.version, entry.minor_version) == (1, 2)
    assert entry.options["scan_interval"] == 60

    for key, before in kept.items():
        after = _entry_for(registry, entry, key)
        assert after.id == before.id
        assert after.entity_id == before.entity_id
        assert after.unique_id == before.unique_id
        assert not after.disabled
        assert hass.states.get(after.entity_id) is not None

    assert _entry_for(registry, entry, "s2_temperature").disabled_by is (
        er.RegistryEntryDisabler.USER
    )

    # Unticked before: still there with the same id, but off and able to be re-enabled.
    after = _entry_for(registry, entry, "outside_temperature")
    assert after.id == unticked.id
    assert after.entity_id == unticked.entity_id
    assert after.disabled

    # Never enabled before, and now on by default: its "off" choice is respected.
    assert _entry_for(registry, entry, "heat_pump_state").disabled

    # Only what was enabled is polled, plus new registers that are on by default.
    polled = set(fake_client.instances[0].reads)
    assert BY_KEY["s1_temperature"].address in polled
    assert BY_KEY["outside_temperature"].address not in polled
    assert BY_KEY["heat_pump_state"].address not in polled
    assert BY_KEY["s2_temperature"].address not in polled

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_migration_of_never_saved_options(hass: HomeAssistant, fake_client) -> None:
    """Without saved options, 2.3.0 had only S3 and S4 enabled."""
    registry = er.async_get(hass)
    entry = _old_entry(hass, {})

    await _setup(hass, entry)

    enabled_legacy = {
        key
        for key in LEGACY_REGISTER_KEYS
        if not _entry_for(registry, entry, key).disabled
    }
    assert enabled_legacy == {"s3_temperature", "s4_temperature"}
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_new_entry_uses_defaults(hass: HomeAssistant, fake_client) -> None:
    """A new install polls only the default registers, already on the first refresh."""
    entry = _new_entry(hass)
    await _setup(hass, entry)

    expected = {reg.address for reg in ALL_REGISTERS if reg.enabled_default}
    assert set(fake_client.instances[0].reads) == expected
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_every_register_can_be_set_up(hass: HomeAssistant, fake_client, caplog) -> None:
    """With everything enabled, every register becomes a working entity."""
    registry = er.async_get(hass)
    entry = _new_entry(hass)
    await _setup(hass, entry)

    for reg in ALL_REGISTERS:
        reg_entry = _entry_for(registry, entry, reg.key)
        assert reg_entry is not None, reg.key
        if reg_entry.disabled:
            registry.async_update_entity(reg_entry.entity_id, disabled_by=None)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    for reg in ALL_REGISTERS:
        state = hass.states.get(_entry_for(registry, entry, reg.key).entity_id)
        assert state is not None, reg.key
        assert state.state not in ("unknown", "unavailable"), reg.key
    assert "Error adding entity" not in caplog.text
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_disabling_stops_polling_without_reload(
    hass: HomeAssistant, fake_client
) -> None:
    registry = er.async_get(hass)
    entry = _new_entry(hass)
    await _setup(hass, entry)

    reg_entry = _entry_for(registry, entry, "return_temperature")
    registry.async_update_entity(
        reg_entry.entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()

    client = fake_client.instances[0]
    client.reads.clear()
    await entry.runtime_data.coordinator.async_refresh()

    assert BY_KEY["return_temperature"].address not in client.reads
    assert BY_KEY["heat_flow_temperature"].address in client.reads
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_group_options(hass: HomeAssistant, fake_client) -> None:
    """Only groups whose tick changed are touched."""
    registry = er.async_get(hass)
    entry = _new_entry(hass)
    await _setup(hass, entry)

    # "status" is partly enabled, so it starts unticked and must stay as it is.
    partly = {
        reg.key: _entry_for(registry, entry, reg.key).disabled_by
        for reg in registers_in_group("status")
    }

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"register_groups": ["pid"], "scan_interval": 45},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["scan_interval"] == 45

    for reg in registers_in_group("pid"):
        assert not _entry_for(registry, entry, reg.key).disabled
    for reg in registers_in_group("status"):
        assert _entry_for(registry, entry, reg.key).disabled_by == partly[reg.key]

    # Unticking a fully enabled group disables it.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "pid" in result["data_schema"]({})["register_groups"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"register_groups": [], "scan_interval": 45},
    )
    await hass.async_block_till_done()
    for reg in registers_in_group("pid"):
        assert _entry_for(registry, entry, reg.key).disabled_by is (
            er.RegistryEntryDisabler.USER
        )

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unreachable_at_startup_retries(hass: HomeAssistant, fake_client) -> None:
    fake_client.reachable = False
    entry = _new_entry(hass)
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_lost_connection_stops_poll_and_recovers(
    hass: HomeAssistant, fake_client
) -> None:
    registry = er.async_get(hass)
    entry = _new_entry(hass)
    await _setup(hass, entry)
    coordinator = entry.runtime_data.coordinator
    entity_id = _entry_for(registry, entry, "return_temperature").entity_id

    fake_client.reachable = False
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.last_update_success is False
    assert hass.states.get(entity_id).state == "unavailable"

    fake_client.reachable = True
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state != "unavailable"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_rejected_register_does_not_stop_others(
    hass: HomeAssistant, fake_client
) -> None:
    registry = er.async_get(hass)
    fake_client.rejected_addresses = {BY_KEY["return_temperature"].address}
    entry = _new_entry(hass)
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert (
        hass.states.get(_entry_for(registry, entry, "return_temperature").entity_id).state
        == "unknown"
    )
    assert (
        hass.states.get(_entry_for(registry, entry, "heat_flow_temperature").entity_id).state
        != "unavailable"
    )
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_writes(hass: HomeAssistant, fake_client) -> None:
    """Select, switch and number all write through the hub."""
    registry = er.async_get(hass)
    entry = _new_entry(hass)
    await _setup(hass, entry)

    for key in ("circuit1_supply_temp_min", "summer_cut_out_enable"):
        registry.async_update_entity(
            _entry_for(registry, entry, key).entity_id, disabled_by=None
        )
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    client = fake_client.instances[-1]

    await hass.services.async_call(
        "select",
        "select_option",
        {
            "entity_id": _entry_for(registry, entry, "operation_mode").entity_id,
            "option": "Saving",
        },
        blocking=True,
    )
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": _entry_for(registry, entry, "summer_cut_out_enable").entity_id},
        blocking=True,
    )
    await hass.services.async_call(
        "number",
        "set_value",
        {
            "entity_id": _entry_for(registry, entry, "circuit1_supply_temp_min").entity_id,
            "value": 20,
        },
        blocking=True,
    )

    assert (21000, [2]) in client.writes
    assert (21603, [1]) in client.writes
    assert (21202, [0x41A0, 0x0000]) in client.writes
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_two_controllers(hass: HomeAssistant, fake_client) -> None:
    """Two controllers can be added through the flow; the same one twice can't."""

    async def add(host: str, slave_id: int):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"name": f"ECL {host} {slave_id}", "transport": "tcp"}
        )
        return await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": host, "tcp_port": 502, "slave_id": slave_id}
        )

    assert (await add("10.0.0.5", 5))["type"] is FlowResultType.CREATE_ENTRY
    assert (await add("10.0.0.6", 5))["type"] is FlowResultType.CREATE_ENTRY
    assert (await add("10.0.0.5", 6))["type"] is FlowResultType.CREATE_ENTRY
    assert (await add("10.0.0.5", 5))["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()

    for entry in hass.config_entries.async_entries(DOMAIN):
        assert entry.state is ConfigEntryState.LOADED
        assert await hass.config_entries.async_unload(entry.entry_id)

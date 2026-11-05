"""Which registers are enabled, and migration from the old per-register options.

Home Assistant's entity registry is the only source of truth for enabled/disabled.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import DEFAULT_NAME, DOMAIN
from .registers import ALL_REGISTERS, RegisterDef, option_key, registers_in_group

# Registers that existed before the entity registry took over. Only these can have
# an old "enable_<key>" option, so only these are touched by the migration.
LEGACY_REGISTER_KEYS = frozenset(
    {
        "s1_temperature",
        "s2_temperature",
        "s3_temperature",
        "s4_temperature",
        "s5_temperature",
        "s6_temperature",
        "outside_temperature",
        "ethernet_ip_address",
        "ethernet_mac_address",
        "product_serial_number",
        "valve_position",
        "heat_pump_state",
        "heat_flow_reference",
        "heat_weather_comp_reference",
        "heat_return_temperature_reference",
        "operation_mode",
        "operation_status",
        "circuit1_supply_temp_min",
        "circuit1_supply_temp_max",
        "circuit1_weather_compensation_slope",
        "circuit1_supply_temp_at_minus30",
        "circuit1_supply_temp_at_minus15",
        "circuit1_supply_temp_at_minus5",
        "circuit1_supply_temp_at_0",
        "circuit1_supply_temp_at_5",
        "circuit1_supply_temp_at_15",
        "circuit1_pid_xp_low",
        "circuit1_pid_xp_high",
        "circuit1_pid_tn",
        "circuit1_pid_deadband",
        "heat_room_comfort_relative_reference",
        "heat_room_saving_relative_reference",
        "boost_active",
        "boost_remaining_time",
        "boost_period",
        "boost_added_temperature",
    }
)

# What the old options defaulted to when a checkbox had never been saved.
_LEGACY_DEFAULT_ENABLED = frozenset({"s3_temperature", "s4_temperature"})


def entity_unique_id(entry: ConfigEntry, reg: RegisterDef) -> str:
    """Return the unique_id of a register's entity.

    Keep in sync with EclModbusEntity: changing it would orphan existing history.
    """
    return f"{entry.entry_id}_{reg.key}"


def _registry_entry(
    registry: er.EntityRegistry, entry: ConfigEntry, reg: RegisterDef
) -> er.RegistryEntry | None:
    entity_id = registry.async_get_entity_id(
        reg.platform, DOMAIN, entity_unique_id(entry, reg)
    )
    return registry.async_get(entity_id) if entity_id else None


@callback
def async_is_enabled(hass: HomeAssistant, entry: ConfigEntry, reg: RegisterDef) -> bool:
    """Return whether a register's entity is enabled (not yet registered = its default)."""
    reg_entry = _registry_entry(er.async_get(hass), entry, reg)
    return reg.enabled_default if reg_entry is None else not reg_entry.disabled


@callback
def async_enabled_registers(hass: HomeAssistant, entry: ConfigEntry) -> list[RegisterDef]:
    """Return the registers that should be polled for this entry."""
    return [reg for reg in ALL_REGISTERS if async_is_enabled(hass, entry, reg)]


@callback
def async_enabled_groups(hass: HomeAssistant, entry: ConfigEntry) -> list[str]:
    """Return the groups whose registers are all enabled."""
    groups: dict[str, bool] = {}
    for reg in ALL_REGISTERS:
        groups[reg.group] = groups.get(reg.group, True) and async_is_enabled(
            hass, entry, reg
        )
    return [group for group, all_enabled in groups.items() if all_enabled]


@callback
def async_set_group_enabled(
    hass: HomeAssistant, entry: ConfigEntry, group: str, enabled: bool
) -> None:
    """Enable or disable every registered entity in a group.

    Enabling makes Home Assistant reload the integration about 30 seconds later,
    which adds the entities and starts polling them.
    """
    registry = er.async_get(hass)
    for reg in registers_in_group(group):
        reg_entry = _registry_entry(registry, entry, reg)
        if reg_entry is None or reg_entry.disabled == (not enabled):
            continue
        registry.async_update_entity(
            reg_entry.entity_id,
            disabled_by=None if enabled else er.RegistryEntryDisabler.USER,
        )


@callback
def async_migrate_legacy_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Carry the old checkbox choices over to the entity registry.

    Existing entities keep their entity_id, name, area and history. Only the disabled
    state changes, and only for a register that was unticked. Entities the user has
    disabled by hand are left alone. The old options stay in the entry so that going
    back to an older version restores the same selection.
    """
    registry = er.async_get(hass)
    title = entry.data.get(CONF_NAME, DEFAULT_NAME)

    for reg in ALL_REGISTERS:
        if reg.key not in LEGACY_REGISTER_KEYS:
            continue

        was_enabled = bool(
            entry.options.get(option_key(reg.key), reg.key in _LEGACY_DEFAULT_ENABLED)
        )
        reg_entry = _registry_entry(registry, entry, reg)

        if reg_entry is not None:
            if not was_enabled and not reg_entry.disabled:
                registry.async_update_entity(
                    reg_entry.entity_id,
                    disabled_by=er.RegistryEntryDisabler.INTEGRATION,
                )
            continue

        # Never registered: reserve it now, so a changed default can't flip the choice.
        registry.async_get_or_create(
            reg.platform,
            DOMAIN,
            entity_unique_id(entry, reg),
            config_entry=entry,
            suggested_object_id=f"{title} {reg.name}",
            disabled_by=None if was_enabled else er.RegistryEntryDisabler.INTEGRATION,
        )

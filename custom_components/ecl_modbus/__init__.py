"""Home Assistant entry points for the ECL Modbus integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_BAUDRATE,
    CONF_HOST,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE_ID,
    CONF_TCP_PORT,
    CONF_TRANSPORT,
    DEFAULT_BAUDRATE,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SLAVE_ID,
    DEFAULT_TCP_PORT,
    DEFAULT_TRANSPORT,
    PLATFORMS,
    TRANSPORT_TCP,
)
from .entity_state import async_migrate_legacy_options
from .modbus import EclModbusCoordinator, EclModbusHub


@dataclass
class EclRuntimeData:
    """Objects shared by the platforms of one config entry."""

    hub: EclModbusHub
    coordinator: EclModbusCoordinator
    title: str


type EclConfigEntry = ConfigEntry[EclRuntimeData]


def _clamp_scan_interval(value: Any) -> int:
    try:
        sec = int(value)
    except (TypeError, ValueError):
        sec = DEFAULT_SCAN_INTERVAL
    return max(5, min(3600, sec))


def _create_hub(entry: ConfigEntry) -> EclModbusHub:
    transport = entry.data.get(CONF_TRANSPORT, DEFAULT_TRANSPORT)
    slave_id = entry.data.get(CONF_SLAVE_ID, DEFAULT_SLAVE_ID)

    if transport == TRANSPORT_TCP:
        return EclModbusHub(
            transport=transport,
            slave_id=slave_id,
            host=entry.data.get(CONF_HOST),
            tcp_port=entry.data.get(CONF_TCP_PORT, DEFAULT_TCP_PORT),
        )
    return EclModbusHub(
        transport=transport,
        slave_id=slave_id,
        port=entry.data[CONF_PORT],
        baudrate=entry.data.get(CONF_BAUDRATE, DEFAULT_BAUDRATE),
    )


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate an old config entry.

    1.1 -> 1.2: register selection moved from options to the entity registry.
    """
    if entry.version > 1:
        return False

    if entry.minor_version < 2:
        async_migrate_legacy_options(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=2)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: EclConfigEntry) -> bool:
    """Create hub and coordinator, then forward to the platforms."""
    hub = _create_hub(entry)
    coordinator = EclModbusCoordinator(
        hass, entry, hub, _clamp_scan_interval(entry.options.get(CONF_SCAN_INTERVAL))
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        # Release the connection, so the retry can reopen a serial port.
        await hass.async_add_executor_job(hub.close)
        raise

    entry.runtime_data = EclRuntimeData(
        hub=hub,
        coordinator=coordinator,
        title=entry.data.get(CONF_NAME, DEFAULT_NAME),
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EclConfigEntry) -> bool:
    """Unload the platforms, then close the connection."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await hass.async_add_executor_job(entry.runtime_data.hub.close)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: EclConfigEntry) -> None:
    """Reload when the options change."""
    await hass.config_entries.async_reload(entry.entry_id)

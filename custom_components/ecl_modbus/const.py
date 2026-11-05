"""
Constants for the ECL Modbus integration.

Keep ONLY constants and config keys here.
No runtime logic.
"""

from __future__ import annotations

DOMAIN = "ecl_modbus"

# Platforms we expose
PLATFORMS: list[str] = ["sensor", "binary_sensor", "number", "select", "switch"]

# Config entry (setup)
CONF_BAUDRATE = "baudrate"
CONF_SLAVE_ID = "slave_id"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_REGISTER_GROUPS = "register_groups"

DEFAULT_NAME = "ECL Modbus"
DEFAULT_BAUDRATE = 38400
DEFAULT_SLAVE_ID = 5
DEFAULT_SCAN_INTERVAL = 30

# Transport (serial vs tcp)
CONF_TRANSPORT = "transport"
CONF_HOST = "host"
CONF_TCP_PORT = "tcp_port"

TRANSPORT_SERIAL = "serial"
TRANSPORT_TCP = "tcp"

DEFAULT_TRANSPORT = TRANSPORT_SERIAL
DEFAULT_TCP_PORT = 502

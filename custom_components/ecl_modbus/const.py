"""
Constants for the ECL Modbus integration.

Keep ONLY constants and config keys here.
No runtime logic.
"""

from __future__ import annotations

DOMAIN = "ecl_modbus"

# Platforms we expose
PLATFORMS: list[str] = ["sensor", "binary_sensor", "number", "select", "switch", "image"]

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

# Return limit curve (opt-in): makes register 21210 follow the outdoor temperature.
CONF_CONFIGURE_RETURN_CURVE = "configure_return_curve"  # form-only, not stored
CONF_RETURN_CURVE_POINTS = "return_curve_points"
CONF_RETURN_CURVE_OUTDOOR_ENTITY = "return_curve_outdoor_entity"
CONF_RETURN_CURVE_AVERAGE_HOURS = "return_curve_average_hours"
CONF_RETURN_CURVE_MIN = "return_curve_min"
CONF_RETURN_CURVE_MAX = "return_curve_max"

DEFAULT_RETURN_CURVE_POINTS = "-15:55, 10:40"
DEFAULT_RETURN_CURVE_AVERAGE_HOURS = 24.0
DEFAULT_RETURN_CURVE_MIN = 25.0
DEFAULT_RETURN_CURVE_MAX = 65.0

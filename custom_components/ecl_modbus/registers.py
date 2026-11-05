from __future__ import annotations

"""Register definitions for the ECL Modbus integration.

Goal:
- Keep ALL register metadata in one place (address, type, unit, scaling, etc.)
- Make it easy to add new registers by adding a single entry to a list
- Support both read-only (R) and read/write (RW) registers

Notes:
- ECL Modbus "manual addresses" in Danfoss documentation are typically 1-based
  (PNU-style addresses).
- pymodbus read_holding_registers() uses 0-based addressing, so the integration
  will internally read (address - 1).
"""

from dataclasses import dataclass
from enum import Enum
from typing import Final, Mapping


class RegisterType(str, Enum):
    """How a register value should be decoded."""
    FLOAT = "float"          # 2 x 16-bit registers -> IEEE754 float (big-endian)
    INT16 = "int16"          # 1 x 16-bit register -> signed/unsigned int
    UINT32 = "uint32"        # 2 x 16-bit registers -> unsigned 32-bit int (big-endian)
    STRING16 = "string16"    # 16 chars (typically 8 registers)
    STRING32 = "string32"    # 32 chars (typically 16 registers)


@dataclass(frozen=True, slots=True)
class RegisterDef:
    """Definition of one Modbus register/point.

    A register can be read-only (R) or read/write (RW).
    RW registers can later be exposed as Number or Select entities in Home Assistant.
    """

    # Identity
    key: str                 # Stable internal key (also used for unique_id suffix)
    name: str                # Human-friendly name shown in Home Assistant
    address: int             # Manual register address from Danfoss documentation
    reg_type: RegisterType   # How the value is decoded

    # Presentation metadata (Home Assistant)
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = "measurement"
    icon: str | None = None

    # Value handling
    scale: float = 1.0       # Apply after decoding (e.g. int16 * 0.1)
    signed: bool = True      # Only relevant for INT16

    # Write support
    writable: bool = False   # True if the register supports writing (RW)

    # Optional limits for writable numeric registers
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None

    # Optional mapping for mode/token registers
    # Example: {1: "Comfort", 2: "Night", 3: "Frost"}
    value_map: Mapping[int, str] | None = None

    # Grouping and defaults
    group: str = "status"          # Register group, see REGISTER_GROUPS
    enabled_default: bool = False  # Whether a new entity starts enabled
    binary: bool = False           # 0/1 register: binary_sensor, or switch if writable

    @property
    def platform(self) -> str:
        """Return the Home Assistant platform this register is exposed on.

        Do not change this for an existing register: the entity would be recreated
        under another domain and lose its history.
        """
        if self.binary:
            return "switch" if self.writable else "binary_sensor"
        if self.writable:
            return "select" if self.value_map else "number"
        return "sensor"


# -----------------------------------------------------------------------------
# Core temperature sensors (S1–S6)
# -----------------------------------------------------------------------------
REG_SENSORS: Final[list[RegisterDef]] = [
    RegisterDef(
        key="s1_temperature",
        group="temperatures",
        name="S1 temperature",
        address=4000,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
        state_class="measurement",
    ),
    RegisterDef(
        key="s2_temperature",
        group="temperatures",
        name="S2 temperature",
        address=4010,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
        state_class="measurement",
    ),
    RegisterDef(
        key="s3_temperature",
        group="temperatures",
        name="S3 temperature",
        address=4020,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
        state_class="measurement",
    ),
    RegisterDef(
        key="s4_temperature",
        group="temperatures",
        name="S4 temperature",
        address=4030,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
        state_class="measurement",
    ),
    RegisterDef(
        key="s5_temperature",
        group="temperatures",
        name="S5 temperature",
        address=4040,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
        state_class="measurement",
    ),
    RegisterDef(
        key="s6_temperature",
        group="temperatures",
        name="S6 temperature",
        address=4050,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
        state_class="measurement",
    ),
    RegisterDef(
        key="outside_temperature",
        group="temperatures",
        enabled_default=True,
        name="Outside temperature",
        address=21102,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
        state_class="measurement",
    ),
]

# -----------------------------------------------------------------------------
# Extra diagnostics / info
# -----------------------------------------------------------------------------
REG_DIAGNOSTICS: Final[list[RegisterDef]] = [
    RegisterDef(
        key="ethernet_ip_address",
        group="device",
        name="Ethernet IP address",
        address=2100,
        reg_type=RegisterType.STRING16,
        state_class=None,
        icon="mdi:ip-network",
    ),
    RegisterDef(
        key="ethernet_mac_address",
        group="device",
        name="Ethernet MAC address",
        address=2110,
        reg_type=RegisterType.STRING32,
        state_class=None,
        icon="mdi:lan",
    ),
    RegisterDef(
        key="product_serial_number",
        group="device",
        name="Product serial number",
        address=1,
        reg_type=RegisterType.STRING16,
        state_class=None,
        icon="mdi:barcode",
    ),
]

# -----------------------------------------------------------------------------
# Extra calculated / control-related sensors
# -----------------------------------------------------------------------------
REG_EXTRAS: Final[list[RegisterDef]] = [
    RegisterDef(
        key="valve_position",
        group="status",
        enabled_default=True,
        name="Valve position",
        address=21700,
        reg_type=RegisterType.FLOAT,
        unit="%",
        device_class=None,
        state_class="measurement",
        icon="mdi:valve",
    ),
    RegisterDef(
        key="heat_pump_state",
        group="status",
        enabled_default=True,
        name="Heat pump state",
        address=21702,
        reg_type=RegisterType.INT16,
        value_map={
            0: "Off",
            1: "On",
        },
        icon="mdi:pump",
    ),
    RegisterDef(
        key="heat_flow_reference",
        group="setpoints",
        enabled_default=True,
        name="Heat flow temperature reference",
        address=21200,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
        state_class="measurement",
    ),
    RegisterDef(
        key="heat_weather_comp_reference",
        group="setpoints",
        name="Heat weather compensated reference",
        address=21206,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
        state_class="measurement",
    ),
    RegisterDef(
        key="heat_return_temperature_reference",
        group="setpoints",
        name="Heat return temperature reference",
        address=21210,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
        state_class="measurement",
        icon="mdi:thermometer-water",
        writable=True,
        min_value=5,
        max_value=150,
        step=0.1,
    ),
    RegisterDef(
        key="operation_mode",
        group="status",
        enabled_default=True,
        name="Operation mode",
        address=21000,
        reg_type=RegisterType.INT16,
        signed=False,
        writable=True,
        value_map={
            0: "Automatic",
            1: "Comfort",
            2: "Saving",
            3: "Frost protection",
        },
    ),
    RegisterDef(
        key="operation_status",
        group="status",
        enabled_default=True,
        name="Operation status",
        address=21001,
        reg_type=RegisterType.INT16,
        signed=False,
        writable=False,
        value_map={
            0: "Comfort",
            1: "Saving",
            2: "Frost protection",
        },
    ),
]

# -----------------------------------------------------------------------------
# Circuit 1 – Supply temperature (flow temperature / heating curve)
# DK: Fremløbstemperatur for kreds 1
# -----------------------------------------------------------------------------
REG_CIRCUIT1_SUPPLY: Final[list[RegisterDef]] = [
    # DK: Min. fremløbstemperatur for kreds 1
    RegisterDef(
        key="circuit1_supply_temp_min",
        group="setpoints",
        name="Circuit 1 minimum supply temperature",
        address=21202,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
        writable=True,
        min_value=5,
        max_value=150,
        step=1.0,
    ),
    # DK: Max. fremløbstemperatur for kreds 1
    RegisterDef(
        key="circuit1_supply_temp_max",
        group="setpoints",
        name="Circuit 1 maximum supply temperature",
        address=21204,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
        writable=True,
        min_value=5,
        max_value=150,
        step=1.0,
    ),
    # DK: Hældning på varmekurven for kreds 1
    RegisterDef(
        key="circuit1_weather_compensation_slope",
        group="weather_curve",
        name="Circuit 1 weather compensation slope",
        address=21456,
        reg_type=RegisterType.FLOAT,
        unit=None,
        device_class=None,
        state_class=None,
        writable=True,
        min_value=0.0,
        max_value=4.0,
        step=0.1,
        icon="mdi:chart-line",
    ),
    # DK: Fremløbstemperatur ved -30°C for kreds 1
    RegisterDef(
        key="circuit1_supply_temp_at_minus30",
        group="weather_curve",
        name="Circuit 1 supply temperature at -30 °C",
        address=21232,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
        writable=False,
    ),
    # DK: Fremløbstemperatur ved -15°C for kreds 1
    RegisterDef(
        key="circuit1_supply_temp_at_minus15",
        group="weather_curve",
        name="Circuit 1 supply temperature at -15 °C",
        address=21234,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
        writable=False,
    ),
    # DK: Fremløbstemperatur ved -5°C for kreds 1
    RegisterDef(
        key="circuit1_supply_temp_at_minus5",
        group="weather_curve",
        name="Circuit 1 supply temperature at -5 °C",
        address=21236,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
        writable=False,
    ),
    # DK: Fremløbstemperatur ved 0°C for kreds 1
    RegisterDef(
        key="circuit1_supply_temp_at_0",
        group="weather_curve",
        name="Circuit 1 supply temperature at 0 °C",
        address=21238,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
        writable=False,
    ),
    # DK: Fremløbstemperatur ved 5°C for kreds 1
    RegisterDef(
        key="circuit1_supply_temp_at_5",
        group="weather_curve",
        name="Circuit 1 supply temperature at 5 °C",
        address=21240,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
        writable=False,
    ),
    # DK: Fremløbstemperatur ved 15°C for kreds 1
    RegisterDef(
        key="circuit1_supply_temp_at_15",
        group="weather_curve",
        name="Circuit 1 supply temperature at 15 °C",
        address=21242,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
        writable=False,
    ),
]


# -----------------------------------------------------------------------------
# Circuit 1 – PID tuning / regulation parameters
# DK: Reguleringsparametre for kreds 1
# -----------------------------------------------------------------------------
REG_CIRCUIT1_PID_TUNING: Final[list[RegisterDef]] = [
    RegisterDef(
        key="circuit1_pid_xp_low",
        group="pid",
        name="Circuit 1 PID Xp (low)",
        address=21446,
        reg_type=RegisterType.INT16,   # UInt16 in docs -> use INT16 with signed=False
        signed=False,
        unit="K",
        writable=True,
        min_value=0,
        max_value=250,
        step=1,
        icon="mdi:tune",
    ),
    RegisterDef(
        key="circuit1_pid_xp_high",
        group="pid",
        name="Circuit 1 PID Xp (high)",
        address=21447,
        reg_type=RegisterType.INT16,   # UInt16 in docs -> use INT16 with signed=False
        signed=False,
        unit="K",
        writable=True,
        min_value=0,
        max_value=250,
        step=1,
        icon="mdi:tune",
    ),
    RegisterDef(
        key="circuit1_pid_tn",
        group="pid",
        name="Circuit 1 PID Tn (adaptation time)",
        address=21448,
        reg_type=RegisterType.INT16,   # UInt16 in docs -> use INT16 with signed=False
        signed=False,
        unit="s",
        device_class="duration",
        writable=True,
        min_value=0,
        max_value=999,
        step=1,
        icon="mdi:timer-outline",
    ),
    RegisterDef(
        key="circuit1_pid_deadband",
        group="pid",
        name="Circuit 1 PID deadband (Nz)",
        address=21458,
        reg_type=RegisterType.FLOAT,
        unit="K",
        writable=True,
        min_value=0.0,
        max_value=20.0,
        step=1.0,
        scale=2.0,  # scale=2.0 due to 0.5 K resolution in ECL (Modbus value * 2 = UI value)
        icon="mdi:arrow-expand-horizontal",
    ),
]


# -----------------------------------------------------------------------------
# Comfort / Saving / Boost (room limiter + boost)
# -----------------------------------------------------------------------------
REG_COMFORT_SAVING_BOOST: Final[list[RegisterDef]] = [
    # DK: Komforttemperatur references
    RegisterDef(
        key="heat_room_comfort_relative_reference",
        group="setpoints",
        name="Heat room comfort relative reference",
        address=21212,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
        writable=True,
        min_value=0.5,
        max_value=5.0,
        step=0.5,
        icon="mdi:home-thermometer",
    ),
    # DK: Sparetemperatur references
    RegisterDef(
        key="heat_room_saving_relative_reference",
        group="setpoints",
        name="Heat room saving relative reference",
        address=21214,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
        writable=True,
        min_value=0.5,
        max_value=5.0,
        step=0.5,
        icon="mdi:home-thermometer-outline",
    ),

    # Boost
    RegisterDef(
        key="boost_active",
        group="boost",
        name="Boost active",
        address=21003,
        reg_type=RegisterType.INT16,   # UInt16 in docs -> use INT16 with signed=False
        signed=False,
        writable=True,
        value_map={
            0: "Off",
            1: "On",
        },
        icon="mdi:rocket-launch",
    ),
    RegisterDef(
        key="boost_remaining_time",
        group="boost",
        name="Boost remaining time",
        address=21004,
        reg_type=RegisterType.FLOAT,
        unit="s",
        writable=False,
        icon="mdi:timer-outline",
    ),
    RegisterDef(
        key="boost_period",
        group="boost",
        name="Boost period",
        address=21438,
        reg_type=RegisterType.UINT32,
        unit="s",
        writable=True,
        min_value=0,
        max_value=86400,
        step=60,
        icon="mdi:timer-cog-outline",
    ),
    RegisterDef(
        key="boost_added_temperature",
        group="boost",
        name="Boost added temperature",
        address=21440,
        reg_type=RegisterType.FLOAT,
        unit="K",
        writable=True,
        min_value=0.0,
        max_value=25.0,
        step=1.0,
        icon="mdi:thermometer-plus",
    ),
]


# -----------------------------------------------------------------------------
# ECL120 application A130: temperatures by function
# -----------------------------------------------------------------------------
REG_A130_TEMPERATURES: Final[list[RegisterDef]] = [
    RegisterDef(
        key="heat_flow_temperature",
        group="temperatures",
        enabled_default=True,
        name="Heat flow temperature",
        address=21100,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
    ),
    RegisterDef(
        key="return_temperature",
        group="temperatures",
        enabled_default=True,
        name="Return temperature",
        address=21104,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
    ),
    RegisterDef(
        key="room_temperature",
        group="temperatures",
        name="Room temperature",
        address=21106,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
    ),
    RegisterDef(
        key="supply_temperature",
        group="temperatures",
        name="Supply temperature",
        address=21108,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
    ),
    RegisterDef(
        key="dhw_temperature",
        group="temperatures",
        name="DHW temperature",
        address=21110,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
    ),
]

# -----------------------------------------------------------------------------
# ECL120 application A130: status and alarms
# -----------------------------------------------------------------------------
REG_A130_STATUS: Final[list[RegisterDef]] = [
    RegisterDef(
        key="heat_demand_status",
        group="status",
        name="Heat demand",
        address=21002,
        reg_type=RegisterType.INT16,
        signed=False,
        binary=True,
        state_class=None,
    ),
    RegisterDef(
        key="bypass_active",
        group="status",
        name="Bypass active",
        address=21007,
        reg_type=RegisterType.INT16,
        signed=False,
        binary=True,
        state_class=None,
    ),
    RegisterDef(
        key="alarm_sensor_missing",
        group="status",
        enabled_default=True,
        name="Alarm heat flow sensor missing",
        address=21300,
        reg_type=RegisterType.INT16,
        signed=False,
        binary=True,
        state_class=None,
        device_class="problem",
    ),
    RegisterDef(
        key="alarm_reference_not_reached",
        group="status",
        enabled_default=True,
        name="Alarm reference not reached",
        address=21301,
        reg_type=RegisterType.INT16,
        signed=False,
        binary=True,
        state_class=None,
        device_class="problem",
    ),
]

_SENSOR_STATUS_MAP: Final[Mapping[int, str]] = {
    0: "OK",
    1: "Invalid",
    2: "Open circuit",
    3: "Short circuit",
}


def _sensor_status(number: int, address: int) -> RegisterDef:
    return RegisterDef(
        key=f"s{number}_status",
        group="sensor_health",
        name=f"S{number} status",
        address=address,
        reg_type=RegisterType.INT16,
        signed=False,
        state_class=None,
        value_map=_SENSOR_STATUS_MAP,
        icon="mdi:thermometer-alert",
    )


# S1 status is at 4009, S2 at 4019, and so on.
REG_SENSOR_HEALTH: Final[list[RegisterDef]] = [
    _sensor_status(number, 3999 + number * 10) for number in range(1, 7)
]


def _setpoint(
    key: str,
    name: str,
    address: int,
    min_value: float,
    max_value: float,
    step: float,
    *,
    group: str = "setpoints",
    unit: str | None = "°C",
) -> RegisterDef:
    """Build a writable float register."""
    return RegisterDef(
        key=key,
        group=group,
        name=name,
        address=address,
        reg_type=RegisterType.FLOAT,
        unit=unit,
        device_class="temperature" if unit == "°C" else None,
        writable=True,
        min_value=min_value,
        max_value=max_value,
        step=step,
    )


def _duration(
    key: str, name: str, address: int, max_value: int, *, group: str = "exercise"
) -> RegisterDef:
    """Build a writable UInt32 duration register, in seconds."""
    return RegisterDef(
        key=key,
        group=group,
        name=name,
        address=address,
        reg_type=RegisterType.UINT32,
        unit="s",
        device_class="duration",
        writable=True,
        min_value=0,
        max_value=max_value,
        step=1,
        icon="mdi:timer-outline",
    )


# -----------------------------------------------------------------------------
# ECL120 application A130: more setpoints
# -----------------------------------------------------------------------------
REG_A130_SETPOINTS: Final[list[RegisterDef]] = [
    _setpoint("heat_summer_cut_out_reference", "Heat summer cut-out temperature", 21208, 0, 50, 1),
    _setpoint("heat_room_comfort_reference", "Heat room comfort reference", 21216, 5, 40, 0.5),
    _setpoint("heat_room_saving_reference", "Heat room saving reference", 21218, 5, 40, 0.5),
    _setpoint("frost_protection_flow_reference", "Frost protection heat flow reference", 21220, 5, 40, 1),
    _setpoint("bypass_temperature_reference", "Bypass temperature reference", 21226, 5, 150, 1),
    _setpoint("constant_base_reference", "Constant base reference", 21244, -200, 200, 0.5),
    _setpoint("constant_base_slope", "Constant base slope", 21246, 0, 5, 0.1, unit=None),
    _setpoint("supply_offset_reference", "Supply offset reference", 21248, -45, 0, 0.5, unit="K"),
    _setpoint("pump_heat_temperature", "Pump heat temperature", 21250, 5, 40, 1),
    _setpoint("frost_protection_outside_reference", "Frost protection outside temperature", 21252, -10, 20, 1),
    _duration("reference_ramp_period", "Reference ramp period", 21442, 5940, group="setpoints"),
    # The spec gives no limits for this one, so it is read-only.
    RegisterDef(
        key="auto_reduced_saving_limit",
        group="setpoints",
        name="Auto reduced saving temperature limit",
        address=21444,
        reg_type=RegisterType.FLOAT,
        unit="°C",
        device_class="temperature",
    ),
]

# -----------------------------------------------------------------------------
# ECL120 application A130: return and room limiters
# -----------------------------------------------------------------------------
REG_A130_LIMITERS: Final[list[RegisterDef]] = [
    _setpoint("return_limiter_min_influence", "Return limiter min influence", 21402, 0, 10, 0.1, group="limiters", unit=None),
    _setpoint("return_limiter_max_influence", "Return limiter max influence", 21404, -10, 0, 0.1, group="limiters", unit=None),
    _setpoint("return_limiter_adaption_time", "Return limiter adaption time", 21406, 0, 50, 1, group="limiters", unit="s"),
    _setpoint("room_limiter_min_influence", "Room limiter min influence", 21408, 0, 10, 0.1, group="limiters", unit=None),
    _setpoint("room_limiter_max_influence", "Room limiter max influence", 21410, -10, 0, 0.1, group="limiters", unit=None),
    _setpoint("room_limiter_adaption_time", "Room limiter adaption time", 21412, 0, 50, 1, group="limiters", unit="s"),
]

# -----------------------------------------------------------------------------
# ECL120 application A130: pump and valve exercise
# -----------------------------------------------------------------------------
REG_A130_EXERCISE: Final[list[RegisterDef]] = [
    _duration("pump_after_run_time", "Heat pump after-run time", 21400, 18000),
    _duration("pump_exercise_time_on", "Heat pump exercise time on", 21426, 18000),
    _duration("pump_exercise_idle_time", "Heat pump exercise idle time", 21428, 864000),
    _duration("pump_exercise_trigger_time", "Heat pump exercise trigger time", 21430, 864000),
    _duration("valve_exercise_open_time", "Heat valve exercise open time", 21434, 86400),
    _duration("valve_exercise_close_time", "Heat valve exercise close time", 21436, 864000),
]


def _feature(key: str, name: str, address: int) -> RegisterDef:
    """Build a writable on/off feature register."""
    return RegisterDef(
        key=key,
        group="features",
        name=name,
        address=address,
        reg_type=RegisterType.INT16,
        signed=False,
        binary=True,
        writable=True,
        state_class=None,
    )


# -----------------------------------------------------------------------------
# ECL120 application A130: feature enables
# -----------------------------------------------------------------------------
REG_A130_FEATURES: Final[list[RegisterDef]] = [
    _feature("room_limiter_enable", "Room limiter", 21600),
    _feature("return_limiter_enable", "Return limiter", 21601),
    _feature("total_stop_enable", "Total stop", 21602),
    _feature("summer_cut_out_enable", "Summer cut-out", 21603),
    _feature("reference_ramp_enable", "Reference ramp", 21604),
    _feature("auto_reduced_saving_enable", "Auto reduced saving", 21605),
    _feature("frost_protection_enable", "Frost protection", 21606),
    _feature("pump_exercise_enable", "Pump exercise", 21608),
    _feature("valve_exercise_enable", "Valve exercise", 21609),
    _feature("bypass_enable", "Bypass", 21612),
    RegisterDef(
        key="external_override_mode",
        group="features",
        name="External override",
        address=21610,
        reg_type=RegisterType.INT16,
        signed=False,
        writable=True,
        state_class=None,
        value_map={
            0: "Off",
            1: "Saving when open",
            2: "Comfort when open",
            3: "Comfort when closed",
            4: "Saving when closed",
        },
    ),
]

# -----------------------------------------------------------------------------
# Device information and bus health
# -----------------------------------------------------------------------------
REG_DEVICE: Final[list[RegisterDef]] = [
    RegisterDef(
        key="product_model_name",
        group="device",
        name="Product model name",
        address=11,
        reg_type=RegisterType.STRING16,
        state_class=None,
    ),
    RegisterDef(
        key="firmware_version",
        group="device",
        name="Firmware version",
        address=21,
        reg_type=RegisterType.STRING16,
        state_class=None,
    ),
    RegisterDef(
        key="software_version",
        group="device",
        name="Software version",
        address=30,
        reg_type=RegisterType.STRING16,
        state_class=None,
    ),
    RegisterDef(
        key="software_application_name",
        group="device",
        name="Software application name",
        address=40,
        reg_type=RegisterType.STRING16,
        state_class=None,
    ),
    RegisterDef(
        key="software_code_number",
        group="device",
        name="Software code number",
        address=60,
        reg_type=RegisterType.STRING16,
        state_class=None,
    ),
    RegisterDef(
        key="modbus_messages_ok",
        group="device",
        name="Modbus messages OK",
        address=500,
        reg_type=RegisterType.UINT32,
        state_class="total_increasing",
    ),
    RegisterDef(
        key="modbus_messages_nok",
        group="device",
        name="Modbus messages not OK",
        address=502,
        reg_type=RegisterType.UINT32,
        state_class="total_increasing",
    ),
    RegisterDef(
        key="total_runtime",
        group="device",
        name="Total runtime",
        address=510,
        reg_type=RegisterType.UINT32,
        unit="s",
        device_class="duration",
        state_class="total_increasing",
    ),
    RegisterDef(
        key="application_runtime",
        group="device",
        name="Application runtime",
        address=512,
        reg_type=RegisterType.UINT32,
        unit="s",
        device_class="duration",
        state_class="total_increasing",
    ),
    RegisterDef(
        key="emergency_mode",
        group="device",
        name="Emergency mode",
        address=550,
        reg_type=RegisterType.INT16,
        signed=False,
        binary=True,
        state_class=None,
        device_class="problem",
    ),
    RegisterDef(
        key="emergency_mode_running_time",
        group="device",
        name="Emergency mode running time",
        address=551,
        reg_type=RegisterType.UINT32,
        unit="s",
        device_class="duration",
    ),
    # Mechanical valve configuration: shown, but deliberately not writable.
    RegisterDef(
        key="motor_speed",
        group="device",
        name="Motor speed",
        address=21449,
        reg_type=RegisterType.INT16,
        signed=False,
        unit="s/mm",
    ),
    RegisterDef(
        key="valve_spindle",
        group="device",
        name="Valve spindle length",
        address=21450,
        reg_type=RegisterType.FLOAT,
        unit="m",
    ),
]

# -----------------------------------------------------------------------------
# Single list of all registers we expose.
# Which ones are enabled is decided by the Home Assistant entity registry.
# -----------------------------------------------------------------------------

ALL_REGISTERS: Final[list[RegisterDef]] = [
    *REG_SENSORS,
    *REG_DIAGNOSTICS,
    *REG_EXTRAS,
    *REG_CIRCUIT1_SUPPLY,
    *REG_CIRCUIT1_PID_TUNING,
    *REG_COMFORT_SAVING_BOOST,
    *REG_A130_TEMPERATURES,
    *REG_A130_STATUS,
    *REG_SENSOR_HEALTH,
    *REG_A130_SETPOINTS,
    *REG_A130_LIMITERS,
    *REG_A130_EXERCISE,
    *REG_A130_FEATURES,
    *REG_DEVICE,
]

# Group key -> Home Assistant entity category for the group's registers:
# "config" applies to writable registers (read-only ones get no category),
# "diagnostic" applies to all, None leaves them as primary entities.
REGISTER_GROUPS: Final[Mapping[str, str | None]] = {
    "temperatures": None,
    "status": None,
    "boost": None,
    "setpoints": "config",
    "weather_curve": "config",
    "pid": "config",
    "limiters": "config",
    "exercise": "config",
    "features": "config",
    "sensor_health": "diagnostic",
    "device": "diagnostic",
}


def group_category(reg: RegisterDef) -> str | None:
    """Return the entity category for a register, based on its group."""
    category = REGISTER_GROUPS[reg.group]
    if category == "config" and not reg.writable:
        return None
    return category


def registers_in_group(group: str) -> list[RegisterDef]:
    """Return all registers in a group."""
    return [reg for reg in ALL_REGISTERS if reg.group == group]


def option_key(reg_key: str) -> str:
    """Return the old options key that enabled a register.

    Only used to migrate existing installs to the entity registry.
    """
    return f"enable_{reg_key}"

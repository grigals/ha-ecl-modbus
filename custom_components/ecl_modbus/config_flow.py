"""Config flow (UI setup) and options flow for the ECL Modbus integration.

Individual registers are enabled and disabled as normal Home Assistant entities.
The options flow only offers a shortcut for whole groups, and the polling interval.
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    CONF_BAUDRATE,
    CONF_CONFIGURE_RETURN_CURVE,
    CONF_HOST,
    CONF_REGISTER_GROUPS,
    CONF_RETURN_CURVE_AVERAGE_HOURS,
    CONF_RETURN_CURVE_MAX,
    CONF_RETURN_CURVE_MIN,
    CONF_RETURN_CURVE_OUTDOOR_ENTITY,
    CONF_RETURN_CURVE_POINTS,
    DEFAULT_RETURN_CURVE_AVERAGE_HOURS,
    DEFAULT_RETURN_CURVE_MAX,
    DEFAULT_RETURN_CURVE_MIN,
    DEFAULT_RETURN_CURVE_POINTS,
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
    DOMAIN,
    TRANSPORT_SERIAL,
    TRANSPORT_TCP,
)
from .entity_state import async_enabled_groups, async_set_group_enabled
from .registers import REGISTER_GROUPS
from .curve_chart import preview_table
from .return_curve import OUTDOOR_REGISTER, format_points, parse_points

CONF_ENABLE = "enable_return_curve"  # form-only
CONF_EDIT = "edit_return_curve"  # form-only


def _temperature_selector() -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=5, max=150, step=0.5, unit_of_measurement="°C", mode=NumberSelectorMode.BOX
        )
    )


class EclModbusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for setting up ECL Modbus."""

    VERSION = 1
    MINOR_VERSION = 2

    def __init__(self) -> None:
        self._base: dict = {}

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        """First step when a user adds the integration."""
        if user_input is not None:
            self._base = user_input
            transport = user_input.get(CONF_TRANSPORT, DEFAULT_TRANSPORT)

            if transport == TRANSPORT_TCP:
                return await self.async_step_tcp()

            return await self.async_step_serial()

        data_schema = vol.Schema(
            {
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_TRANSPORT, default=DEFAULT_TRANSPORT): vol.In(
                    [TRANSPORT_SERIAL, TRANSPORT_TCP]
                ),
            }
        )

        return self.async_show_form(step_id="user", data_schema=data_schema)

    async def _async_create(self, user_input: dict, connection: str) -> ConfigFlowResult:
        """Create the entry. One entry per controller: connection + slave ID."""
        data = {**self._base, **user_input}

        await self.async_set_unique_id(f"{connection}:{data[CONF_SLAVE_ID]}")
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=data.get(CONF_NAME, DEFAULT_NAME),
            data=data,
        )

    async def async_step_serial(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Serial (RTU) settings."""
        if user_input is not None:
            return await self._async_create(user_input, user_input[CONF_PORT])

        data_schema = vol.Schema(
            {
                vol.Required(CONF_PORT): str,  # e.g. /dev/ttyUSB0
                vol.Optional(CONF_BAUDRATE, default=DEFAULT_BAUDRATE): int,
                vol.Optional(CONF_SLAVE_ID, default=DEFAULT_SLAVE_ID): int,
            }
        )

        return self.async_show_form(step_id="serial", data_schema=data_schema)

    async def async_step_tcp(self, user_input: dict | None = None) -> ConfigFlowResult:
        """TCP settings."""
        if user_input is not None:
            return await self._async_create(
                user_input, f"{user_input[CONF_HOST]}:{user_input[CONF_TCP_PORT]}"
            )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,  # e.g. 10.10.100.50
                vol.Optional(CONF_TCP_PORT, default=DEFAULT_TCP_PORT): int,
                vol.Optional(CONF_SLAVE_ID, default=DEFAULT_SLAVE_ID): int,
            }
        )

        return self.async_show_form(step_id="tcp", data_schema=data_schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "EclModbusOptionsFlow":
        """Return the options flow handler for an existing entry."""
        return EclModbusOptionsFlow()


class EclModbusOptionsFlow(OptionsFlow):
    """Options: register groups, polling interval, and the return limit curve."""

    def __init__(self) -> None:
        self._options: dict = {}

    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Show the options form."""
        entry = self.config_entry
        options = dict(entry.options)
        # A group is ticked when all of its registers are enabled. Only a group whose
        # tick changes is touched, so hand-picked selections inside a group are kept.
        enabled_groups = async_enabled_groups(self.hass, entry)

        if user_input is not None:
            selected = set(user_input[CONF_REGISTER_GROUPS])
            for group in selected.difference(enabled_groups):
                async_set_group_enabled(self.hass, entry, group, True)
            for group in set(enabled_groups).difference(selected):
                async_set_group_enabled(self.hass, entry, group, False)

            options[CONF_SCAN_INTERVAL] = user_input[CONF_SCAN_INTERVAL]
            if user_input.get(CONF_CONFIGURE_RETURN_CURVE):
                self._options = options
                return await self.async_step_return_curve()
            return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_REGISTER_GROUPS, default=enabled_groups
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=list(REGISTER_GROUPS),
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                            translation_key=CONF_REGISTER_GROUPS,
                        )
                    ),
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): vol.All(int, vol.Clamp(min=5, max=3600)),
                    vol.Optional(CONF_CONFIGURE_RETURN_CURVE, default=False): bool,
                }
            ),
        )

    async def async_step_return_curve(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Curve points, outdoor source, averaging and limits of the return limit curve."""
        options = self._options
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                points = parse_points(user_input[CONF_RETURN_CURVE_POINTS])
            except ValueError:
                errors[CONF_RETURN_CURVE_POINTS] = "invalid_points"
            if user_input[CONF_RETURN_CURVE_MIN] >= user_input[CONF_RETURN_CURVE_MAX]:
                errors[CONF_RETURN_CURVE_MAX] = "min_not_below_max"

            if not errors:
                options[CONF_RETURN_CURVE_POINTS] = format_points(points)
                for key in (
                    CONF_RETURN_CURVE_AVERAGE_HOURS,
                    CONF_RETURN_CURVE_MIN,
                    CONF_RETURN_CURVE_MAX,
                ):
                    options[key] = float(user_input[key])
                # No entity chosen: use the ECL's own outdoor sensor.
                options.pop(CONF_RETURN_CURVE_OUTDOOR_ENTITY, None)
                if user_input.get(CONF_RETURN_CURVE_OUTDOOR_ENTITY):
                    options[CONF_RETURN_CURVE_OUTDOOR_ENTITY] = user_input[
                        CONF_RETURN_CURVE_OUTDOOR_ENTITY
                    ]
                return await self.async_step_return_curve_preview()

        current = {**options, **(user_input or {})}
        return self.async_show_form(
            step_id="return_curve",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_RETURN_CURVE_POINTS,
                        default=current.get(
                            CONF_RETURN_CURVE_POINTS, DEFAULT_RETURN_CURVE_POINTS
                        ),
                    ): TextSelector(),
                    vol.Optional(
                        CONF_RETURN_CURVE_OUTDOOR_ENTITY,
                        description={
                            "suggested_value": current.get(CONF_RETURN_CURVE_OUTDOOR_ENTITY)
                        },
                    ): EntitySelector(
                        EntitySelectorConfig(domain="sensor", device_class="temperature")
                    ),
                    vol.Required(
                        CONF_RETURN_CURVE_AVERAGE_HOURS,
                        default=current.get(
                            CONF_RETURN_CURVE_AVERAGE_HOURS, DEFAULT_RETURN_CURVE_AVERAGE_HOURS
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=168, step=0.5, unit_of_measurement="h",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_RETURN_CURVE_MIN,
                        default=current.get(CONF_RETURN_CURVE_MIN, DEFAULT_RETURN_CURVE_MIN),
                    ): _temperature_selector(),
                    vol.Required(
                        CONF_RETURN_CURVE_MAX,
                        default=current.get(CONF_RETURN_CURVE_MAX, DEFAULT_RETURN_CURVE_MAX),
                    ): _temperature_selector(),
                }
            ),
        )

    async def async_step_return_curve_preview(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Show the curve as a table before saving, and offer to turn it on."""
        options = self._options
        curve = getattr(getattr(self.config_entry, "runtime_data", None), "return_curve", None)

        if user_input is not None:
            if user_input.get(CONF_EDIT):
                return await self.async_step_return_curve()
            if curve is not None and user_input[CONF_ENABLE] != curve.enabled:
                # Applied by the reload that saving the options starts.
                await curve.async_set_enabled(user_input[CONF_ENABLE], refresh=False)
            return self.async_create_entry(title="", data=options)

        points = parse_points(options[CONF_RETURN_CURVE_POINTS])
        hours = options[CONF_RETURN_CURVE_AVERAGE_HOURS]
        return self.async_show_form(
            step_id="return_curve_preview",
            description_placeholders={
                "table": preview_table(
                    points, options[CONF_RETURN_CURVE_MIN], options[CONF_RETURN_CURVE_MAX]
                ),
                "outdoor": options.get(CONF_RETURN_CURVE_OUTDOOR_ENTITY)
                or f"ECL outdoor sensor (register {OUTDOOR_REGISTER.address})",
                "averaging": f"{hours:g} h average" if hours else "no averaging",
            },
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ENABLE, default=True): bool,
                    vol.Optional(CONF_EDIT, default=False): bool,
                }
            ),
        )

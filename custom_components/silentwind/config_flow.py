"""Config flow: find the controller (BLE advertisement or Classic inquiry), pair, read its parameters."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .bluez import BluezError, async_discover_controllers
from .client import SilentwindClient, SilentwindConnectionError, SilentwindError
from .const import (
    CONF_APP_ACCESS_MINUTES,
    CONF_CHANNEL,
    CONF_HAS_SOLAR,
    CONF_KEEP_CONNECTED,
    CONF_PIN,
    CONF_SCAN_INTERVAL,
    DEFAULT_APP_ACCESS_MINUTES,
    DEFAULT_CHANNEL,
    DEFAULT_KEEP_CONNECTED,
    DEFAULT_PIN,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .protocol import FrameError
from .transport import create_transport

_LOGGER = logging.getLogger(__name__)
MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")


class SilentwindConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    def __init__(self) -> None:
        self._address: str | None = None
        self._found: dict[str, str] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """The module also advertises over BLE; only used to offer it, the link is Classic."""
        address = discovery_info.address.upper()
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()
        self._address = address
        self._found = {address: discovery_info.name}
        self.context["title_placeholders"] = {"name": discovery_info.name or address}
        return await self.async_step_device()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Search for Classic controllers, then show the device form."""
        try:
            self._found = await async_discover_controllers()
        except BluezError as err:
            _LOGGER.warning("Bluetooth Classic search failed: %s", err)
            self._found = {}
        return await self.async_step_device()

    async def async_step_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper().replace("-", ":")
            if not MAC_RE.match(address):
                errors[CONF_ADDRESS] = "invalid_address"
            else:
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()
                data = {
                    CONF_ADDRESS: address,
                    CONF_CHANNEL: int(user_input[CONF_CHANNEL]),
                    CONF_PIN: str(user_input[CONF_PIN]).strip(),
                }
                transport = create_transport(self.hass, address, data[CONF_CHANNEL], data[CONF_PIN])
                client = SilentwindClient(transport)
                try:
                    await client.connect()
                    params = await client.read_params()
                    await client.read_live()
                except SilentwindConnectionError as err:
                    errors["base"] = "cannot_connect"
                    placeholders["error"] = f"\n\n{err}"
                except (SilentwindError, FrameError) as err:
                    errors["base"] = "no_response"
                    placeholders["error"] = f"\n\n{err}"
                except Exception as err:
                    _LOGGER.exception("Unexpected error while testing %s", address)
                    errors["base"] = "cannot_connect"
                    placeholders["error"] = f"\n\n{type(err).__name__}: {err}"
                else:
                    # remember the RFCOMM channel that worked
                    data[CONF_CHANNEL] = getattr(transport, "channel", data[CONF_CHANNEL])
                    scale = params.scale
                    return self.async_create_entry(
                        title=f"Silentwind Windgenerator {scale.system_voltage} V / {scale.rated_power} W",
                        data=data,
                    )
                finally:
                    await client.disconnect()

        defaults = user_input or {}
        current = self._async_current_ids()
        devices = [
            SelectOptionDict(value=addr, label=f"{name} ({addr})")
            for addr, name in self._found.items()
            if addr not in current
        ]
        default_address = defaults.get(CONF_ADDRESS) or self._address or (devices[0]["value"] if devices else None)
        schema = vol.Schema({
            vol.Required(CONF_ADDRESS, default=default_address or vol.UNDEFINED): SelectSelector(
                SelectSelectorConfig(options=devices, custom_value=True, mode=SelectSelectorMode.DROPDOWN)
            ),
            vol.Required(CONF_PIN, default=defaults.get(CONF_PIN, DEFAULT_PIN)): str,
            vol.Required(CONF_CHANNEL, default=defaults.get(CONF_CHANNEL, DEFAULT_CHANNEL)): vol.All(
                vol.Coerce(int), vol.Range(0, 30)
            ),
        })
        return self.async_show_form(
            step_id="device", data_schema=schema, errors=errors, description_placeholders=placeholders
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SilentwindOptionsFlow()


class SilentwindOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        opts = self.config_entry.options
        schema = vol.Schema({
            vol.Required(CONF_SCAN_INTERVAL, default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)): vol.All(
                vol.Coerce(int), vol.Range(5, 3600)
            ),
            vol.Required(CONF_KEEP_CONNECTED, default=opts.get(CONF_KEEP_CONNECTED, DEFAULT_KEEP_CONNECTED)): bool,
            vol.Required(CONF_HAS_SOLAR, default=opts.get(CONF_HAS_SOLAR, False)): bool,
            vol.Required(
                CONF_APP_ACCESS_MINUTES, default=opts.get(CONF_APP_ACCESS_MINUTES, DEFAULT_APP_ACCESS_MINUTES)
            ): vol.All(vol.Coerce(int), vol.Range(1, 240)),
        })
        return self.async_show_form(step_id="init", data_schema=schema)

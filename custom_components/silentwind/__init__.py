"""Silentwind Windgenerator: Win-Power wind-solar charge controller via Bluetooth."""

from __future__ import annotations

from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant

from .client import SilentwindClient
from .const import CONF_CHANNEL, CONF_PIN, DEFAULT_CHANNEL, DEFAULT_PIN
from .coordinator import SilentwindConfigEntry, SilentwindCoordinator
from .transport import create_transport

PLATFORMS = [Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SELECT, Platform.SENSOR, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: SilentwindConfigEntry) -> bool:
    transport = create_transport(
        hass,
        entry.data[CONF_ADDRESS],
        entry.data.get(CONF_CHANNEL, DEFAULT_CHANNEL),
        entry.data.get(CONF_PIN, DEFAULT_PIN),
    )
    coordinator = SilentwindCoordinator(hass, entry, SilentwindClient(transport))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload_on_options))
    return True


async def _reload_on_options(hass: HomeAssistant, entry: SilentwindConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SilentwindConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.disconnect()
    return unloaded

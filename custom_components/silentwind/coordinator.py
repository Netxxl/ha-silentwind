"""Polling coordinator: one Bluetooth session per poll (or kept open)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import SilentwindClient, SilentwindError, SilentwindTimeout
from .const import (
    CONF_KEEP_CONNECTED,
    CONF_SCAN_INTERVAL,
    DEFAULT_KEEP_CONNECTED,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PARAMS_EVERY,
)
from .protocol import FrameError, LiveData, Params

_LOGGER = logging.getLogger(__name__)
T = TypeVar("T")
# upper bound for connect + requests, so one hung call cannot stop all later polls
SESSION_TIMEOUT = 120


@dataclass
class SilentwindData:
    live: LiveData
    params: Params


type SilentwindConfigEntry = ConfigEntry[SilentwindCoordinator]


def entry_option(entry: ConfigEntry, key: str, default=None):
    """Options override the values from setup."""
    return entry.options.get(key, entry.data.get(key, default))


class SilentwindCoordinator(DataUpdateCoordinator[SilentwindData]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: SilentwindClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry_option(entry, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client
        self._keep_connected: bool = entry_option(entry, CONF_KEEP_CONNECTED, DEFAULT_KEEP_CONNECTED)
        self._lock = asyncio.Lock()
        self._polls = 0

    async def execute(self, func: Callable[[SilentwindClient], Awaitable[T]]) -> T:
        """Run func inside a Bluetooth session.

        Without keep_connected the link is dropped afterwards so the
        manufacturer app can still connect between polls.
        """
        async with self._lock:
            try:
                async with asyncio.timeout(SESSION_TIMEOUT):
                    await self.client.connect()
                    return await func(self.client)
            except TimeoutError as err:
                await self.client.disconnect()
                raise SilentwindTimeout(f"Bluetooth session hung for {SESSION_TIMEOUT} s") from err
            except BaseException:
                await self.client.disconnect()
                raise
            finally:
                if not self._keep_connected and self.client.is_connected:
                    await self.client.disconnect()

    async def _async_update_data(self) -> SilentwindData:
        async def poll(client: SilentwindClient) -> SilentwindData:
            if client.params is None or self._polls % PARAMS_EVERY == 0:
                await client.read_params()
            live = await client.read_live()
            return SilentwindData(live, client.params)

        try:
            data = await self.execute(poll)
        except (SilentwindError, FrameError) as err:
            raise UpdateFailed(str(err)) from err
        self._polls += 1
        return data

    async def async_change_params(
        self, change: Callable[[Params], None], verify: Callable[[Params], bool]
    ) -> None:
        """Apply change to a fresh copy of the parameter block, send it and verify."""

        async def write(client: SilentwindClient) -> SilentwindData:
            params = (await client.read_params()).copy()
            change(params)
            new = await client.write_params(params)
            if not verify(new):
                raise HomeAssistantError("Silentwind: controller did not accept the change")
            return SilentwindData(await client.read_live(), new)

        try:
            data = await self.execute(write)
        except ValueError as err:
            raise HomeAssistantError(f"Silentwind: {err}") from err
        except (SilentwindError, FrameError) as err:
            raise HomeAssistantError(f"Silentwind: {err}") from err
        self.async_set_updated_data(data)

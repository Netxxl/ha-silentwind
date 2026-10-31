"""Manual brake and load outputs, switched like the app does it."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SilentwindConfigEntry, SilentwindData
from .entity import SilentwindEntity
from .protocol import LOAD_OFF, LOAD_ON, P_LOAD1_MODE, P_LOAD2_MODE, Params


@dataclass(frozen=True, kw_only=True)
class SilentwindSwitchDescription(SwitchEntityDescription):
    is_on_fn: Callable[[SilentwindData], bool]
    set_fn: Callable[[Params, bool], None]
    verify_fn: Callable[[Params, bool], bool]


def _load(key: str, offset: int, live_attr: str) -> SilentwindSwitchDescription:
    return SilentwindSwitchDescription(
        key=key,
        translation_key=key,
        is_on_fn=lambda d: getattr(d.live, live_attr),
        set_fn=lambda p, on: p.set_raw(offset, LOAD_ON if on else LOAD_OFF),
        verify_fn=lambda p, on: p[offset] == (LOAD_ON if on else LOAD_OFF),
    )


SWITCHES = (
    SilentwindSwitchDescription(
        key="brake",
        translation_key="brake",
        is_on_fn=lambda d: d.live.manual_brake,
        set_fn=lambda p, on: p.set_manual_brake(on),
        verify_fn=lambda p, on: p.manual_brake == on,
    ),
    _load("load1", P_LOAD1_MODE, "load1_on"),
    _load("load2", P_LOAD2_MODE, "load2_on"),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: SilentwindConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(SilentwindSwitch(entry.runtime_data, d) for d in SWITCHES)


class SilentwindSwitch(SilentwindEntity, SwitchEntity):
    entity_description: SilentwindSwitchDescription

    @property
    def is_on(self) -> bool:
        return self.entity_description.is_on_fn(self.coordinator.data)

    async def _set(self, on: bool) -> None:
        d = self.entity_description
        await self.coordinator.async_change_params(
            lambda p: d.set_fn(p, on), lambda p: d.verify_fn(p, on)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

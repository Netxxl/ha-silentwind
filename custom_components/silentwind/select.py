"""Load output operating modes (disabled by default)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SilentwindConfigEntry
from .entity import SilentwindEntity
from .protocol import LOAD_MODES, P_LOAD1_MODE, P_LOAD2_MODE

MODE_BY_NAME = {name: bit for bit, name in LOAD_MODES.items()}

SELECTS = (
    (SelectEntityDescription(key="load1_mode", translation_key="load_mode", options=list(MODE_BY_NAME)), P_LOAD1_MODE),
    (SelectEntityDescription(key="load2_mode", translation_key="load_mode", options=list(MODE_BY_NAME)), P_LOAD2_MODE),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: SilentwindConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(SilentwindLoadMode(entry.runtime_data, d, off) for d, off in SELECTS)


class SilentwindLoadMode(SilentwindEntity, SelectEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, description, offset: int) -> None:
        super().__init__(coordinator, description)
        self._offset = offset
        self._attr_translation_placeholders = {"n": description.key[4]}

    @property
    def current_option(self) -> str | None:
        return LOAD_MODES.get(self.coordinator.data.params[self._offset])

    async def async_select_option(self, option: str) -> None:
        bit = MODE_BY_NAME[option]
        await self.coordinator.async_change_params(
            lambda p: p.set_raw(self._offset, bit), lambda p: p[self._offset] == bit
        )

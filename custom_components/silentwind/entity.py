"""Base entity."""

from __future__ import annotations

from homeassistant.const import CONF_ADDRESS
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SilentwindCoordinator


class SilentwindEntity(CoordinatorEntity[SilentwindCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SilentwindCoordinator, description: EntityDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        address = coordinator.config_entry.data[CONF_ADDRESS]
        self._attr_unique_id = f"{address}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            connections={(CONNECTION_BLUETOOTH, address)},
            manufacturer="Silentwind",
            model="Hybrid Boost (Win-Power)",
            name="Silentwind Windgenerator",
        )

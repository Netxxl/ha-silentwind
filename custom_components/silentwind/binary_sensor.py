"""Status flags of the controller."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SilentwindConfigEntry
from .entity import SilentwindEntity

PROBLEM = BinarySensorDeviceClass.PROBLEM

BINARY_SENSORS = (
    BinarySensorEntityDescription(key="battery_overvoltage", translation_key="battery_overvoltage", device_class=PROBLEM),
    BinarySensorEntityDescription(key="battery_undervoltage", translation_key="battery_undervoltage", device_class=PROBLEM),
    BinarySensorEntityDescription(key="load_overload", translation_key="load_overload", device_class=PROBLEM),
    BinarySensorEntityDescription(key="load_short_circuit", translation_key="load_short_circuit", device_class=PROBLEM),
    BinarySensorEntityDescription(key="dumping", translation_key="dumping", device_class=BinarySensorDeviceClass.RUNNING),
    BinarySensorEntityDescription(key="overspeed_brake", translation_key="overspeed_brake", device_class=PROBLEM),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: SilentwindConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(SilentwindBinarySensor(entry.runtime_data, d) for d in BINARY_SENSORS)


class SilentwindBinarySensor(SilentwindEntity, BinarySensorEntity):
    @property
    def is_on(self) -> bool:
        return getattr(self.coordinator.data.live, self.entity_description.key)

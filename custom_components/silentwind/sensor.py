"""Measured values of the controller."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_HAS_SOLAR
from .coordinator import SilentwindConfigEntry, SilentwindData, entry_option
from .entity import SilentwindEntity

STATE_OPTIONS = ["normal", "dumping", "overspeed_brake", "manual_brake"]


def _state(data: SilentwindData) -> str:
    # Same precedence as the app's status field.
    live = data.live
    if live.manual_brake:
        return "manual_brake"
    if live.overspeed_brake:
        return "overspeed_brake"
    if live.dumping:
        return "dumping"
    return "normal"


@dataclass(frozen=True, kw_only=True)
class SilentwindSensorDescription(SensorEntityDescription):
    value_fn: Callable[[SilentwindData], float | int | str | None]
    solar: bool = False


def _measure(key: str, device_class: SensorDeviceClass, unit: str, **kw) -> SilentwindSensorDescription:
    return SilentwindSensorDescription(
        key=key,
        translation_key=key,
        value_fn=lambda d: getattr(d.live, key),
        device_class=device_class,
        native_unit_of_measurement=unit,
        state_class=SensorStateClass.MEASUREMENT,
        **kw,
    )


V, A, W = UnitOfElectricPotential.VOLT, UnitOfElectricCurrent.AMPERE, UnitOfPower.WATT
VOLTAGE, CURRENT, POWER = SensorDeviceClass.VOLTAGE, SensorDeviceClass.CURRENT, SensorDeviceClass.POWER

SENSORS: tuple[SilentwindSensorDescription, ...] = (
    SilentwindSensorDescription(
        key="state", translation_key="state", value_fn=_state,
        device_class=SensorDeviceClass.ENUM, options=STATE_OPTIONS,
    ),
    _measure("battery_voltage", VOLTAGE, V, suggested_display_precision=2),
    _measure("battery_current", CURRENT, A, suggested_display_precision=1),
    _measure("battery_power", POWER, W, suggested_display_precision=1),
    _measure("wind_voltage", VOLTAGE, V, suggested_display_precision=1),
    _measure("wind_current", CURRENT, A, suggested_display_precision=1),
    _measure("wind_power", POWER, W, suggested_display_precision=1),
    _measure("solar_voltage", VOLTAGE, V, solar=True, suggested_display_precision=1),
    _measure("solar_current", CURRENT, A, solar=True, suggested_display_precision=1),
    _measure("solar_power", POWER, W, solar=True, suggested_display_precision=1),
    _measure("load1_current", CURRENT, A, suggested_display_precision=1),
    _measure("load2_current", CURRENT, A, suggested_display_precision=1),
    SilentwindSensorDescription(
        key="system_voltage", translation_key="system_voltage",
        value_fn=lambda d: d.params.scale.system_voltage,
        device_class=VOLTAGE, native_unit_of_measurement=V, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SilentwindSensorDescription(
        key="rated_power", translation_key="rated_power",
        value_fn=lambda d: d.params.scale.rated_power,
        device_class=POWER, native_unit_of_measurement=W, entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: SilentwindConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    has_solar = entry_option(entry, CONF_HAS_SOLAR, False)
    async_add_entities(
        SilentwindSensor(entry.runtime_data, d) for d in SENSORS if has_solar or not d.solar
    )


class SilentwindSensor(SilentwindEntity, SensorEntity):
    entity_description: SilentwindSensorDescription

    @property
    def native_value(self) -> float | int | str | None:
        return self.entity_description.value_fn(self.coordinator.data)

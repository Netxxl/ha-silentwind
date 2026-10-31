"""Controller settings from the parameter block (disabled by default)."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.const import EntityCategory, UnitOfElectricCurrent, UnitOfElectricPotential, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import protocol as p
from .coordinator import SilentwindConfigEntry
from .entity import SilentwindEntity
from .protocol import Params, Scale

VOLT, AMP, HOUR = "volt", "amp", "hour"


@dataclass(frozen=True, kw_only=True)
class SilentwindNumberDescription(NumberEntityDescription):
    offset: int
    kind: str


def _factor(kind: str, scale: Scale) -> float:
    return {VOLT: scale.kv_set, AMP: scale.ki_set}.get(kind, 1.0)


def _number(key: str, offset: int, kind: str) -> SilentwindNumberDescription:
    unit, device_class = {
        VOLT: (UnitOfElectricPotential.VOLT, NumberDeviceClass.VOLTAGE),
        AMP: (UnitOfElectricCurrent.AMPERE, NumberDeviceClass.CURRENT),
        HOUR: (UnitOfTime.HOURS, NumberDeviceClass.DURATION),
    }[kind]
    return SilentwindNumberDescription(
        key=key, translation_key=key, offset=offset, kind=kind,
        native_unit_of_measurement=unit, device_class=device_class,
    )


NUMBERS = (
    _number("absorption_voltage", p.P_ABSORPTION_V, VOLT),
    _number("storm_brake_current", p.P_STORM_BRAKE_A, AMP),
    _number("overvoltage", p.P_OVERVOLTAGE_V, VOLT),
    _number("undervoltage_off", p.P_UNDERVOLTAGE_OFF_V, VOLT),
    _number("undervoltage_on", p.P_UNDERVOLTAGE_ON_V, VOLT),
    _number("low_wind_boost_voltage", p.P_LOW_WIND_BOOST_V, VOLT),
    _number("light_on_voltage", p.P_LIGHT_ON_V, VOLT),
    _number("light_off_voltage", p.P_LIGHT_OFF_V, VOLT),
    _number("load1_half_power_hours", p.P_LOAD1_HALF_POWER_H, HOUR),
    _number("load1_off_hours", p.P_LOAD1_OFF_H, HOUR),
    _number("load2_half_power_hours", p.P_LOAD2_HALF_POWER_H, HOUR),
    _number("load2_off_hours", p.P_LOAD2_OFF_H, HOUR),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: SilentwindConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(SilentwindNumber(entry.runtime_data, d) for d in NUMBERS)


class SilentwindNumber(SilentwindEntity, NumberEntity):
    entity_description: SilentwindNumberDescription
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0

    @property
    def _factor(self) -> float:
        return _factor(self.entity_description.kind, self.coordinator.data.params.scale)

    @property
    def native_step(self) -> float:
        return self._factor

    @property
    def native_max_value(self) -> float:
        return round(255 * self._factor, 1)

    @property
    def native_value(self) -> float:
        return round(self.coordinator.data.params[self.entity_description.offset] * self._factor, 1)

    async def async_set_native_value(self, value: float) -> None:
        offset, factor = self.entity_description.offset, self._factor
        raw = int(round(value / factor))

        def change(params: Params) -> None:
            params.set_scaled(offset, value, factor)

        await self.coordinator.async_change_params(change, lambda params: params[offset] == raw)

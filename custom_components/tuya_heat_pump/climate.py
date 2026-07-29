"""Climate platform for Tuya Heat Pump."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ATTR_TEMPERATURE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import TuyaScaleDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up model-driven Tuya heat-pump climate entities."""
    coordinator: TuyaScaleDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]
    climate_configs = coordinator.model_mapping.get("climates", {})
    entities = [
        TuyaHeatpumpClimate(coordinator, climate_key, climate_config)
        for climate_key, climate_config in climate_configs.items()
    ]
    if entities:
        async_add_entities(entities)


class TuyaHeatpumpClimate(ClimateEntity):
    """Unified Home Assistant climate control for a Tuya heat pump."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE

    def __init__(
        self,
        coordinator: TuyaScaleDataUpdateCoordinator,
        climate_key: str,
        config: dict[str, Any],
    ) -> None:
        """Initialize the climate entity."""
        self.coordinator = coordinator
        self._config = config
        self._power_code = config["power_code"]
        self._current_temperature_code = config["current_temperature_code"]
        self._target_temperature_code = config["target_temperature_code"]
        self._mode_code = config["mode_code"]

        raw_modes = config.get("hvac_modes", {})
        self._hvac_mode_by_tuya = {
            tuya_mode: HVACMode(home_assistant_mode)
            for tuya_mode, home_assistant_mode in raw_modes.items()
        }
        self._tuya_mode_by_hvac = {
            home_assistant_mode: tuya_mode
            for tuya_mode, home_assistant_mode in self._hvac_mode_by_tuya.items()
        }

        self._attr_unique_id = f"{coordinator.device_id}_{climate_key}"
        self._attr_name = config.get("name", "Heat Pump")
        self._attr_device_info = coordinator.device_info
        self._attr_hvac_modes = [
            HVACMode.OFF,
            *dict.fromkeys(self._hvac_mode_by_tuya.values()),
        ]
        self._attr_min_temp = float(config.get("min_temp", 5))
        self._attr_max_temp = float(config.get("max_temp", 40))
        self._attr_target_temperature_step = float(
            config.get("target_temperature_step", 1)
        )

    def _value(self, code: str) -> Any | None:
        """Return a current coordinator value."""
        if not self.coordinator.data or code not in self.coordinator.data:
            return None
        return self.coordinator.data[code].get("value")

    @property
    def current_temperature(self) -> float | None:
        """Return the current water temperature."""
        value = self._value(self._current_temperature_code)
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def target_temperature(self) -> float | None:
        """Return the target water temperature."""
        value = self._value(self._target_temperature_code)
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current Home Assistant HVAC mode."""
        if not bool(self._value(self._power_code)):
            return HVACMode.OFF
        raw_mode = self._value(self._mode_code)
        return self._hvac_mode_by_tuya.get(raw_mode, HVACMode.AUTO)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target water temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        if not self._attr_min_temp <= temperature <= self._attr_max_temp:
            raise HomeAssistantError(
                f"Target temperature must be between "
                f"{self._attr_min_temp:g} and {self._attr_max_temp:g} °C"
            )
        if not await self.coordinator.send_command(
            self._target_temperature_code, temperature
        ):
            raise HomeAssistantError("Failed to set target temperature")

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set power and operating mode."""
        if hvac_mode == HVACMode.OFF:
            if not await self.coordinator.send_command(self._power_code, False):
                raise HomeAssistantError("Failed to turn off the heat pump")
            return

        tuya_mode = self._tuya_mode_by_hvac.get(hvac_mode)
        if tuya_mode is None:
            raise HomeAssistantError(f"Unsupported HVAC mode: {hvac_mode}")

        if self._value(self._mode_code) != tuya_mode:
            if not await self.coordinator.send_command(self._mode_code, tuya_mode):
                raise HomeAssistantError(f"Failed to set HVAC mode to {hvac_mode}")

        if not bool(self._value(self._power_code)):
            if not await self.coordinator.send_command(self._power_code, True):
                raise HomeAssistantError("Failed to turn on the heat pump")

    @property
    def available(self) -> bool:
        """Return whether all climate datapoints are available."""
        required_codes = (
            self._power_code,
            self._current_temperature_code,
            self._target_temperature_code,
            self._mode_code,
        )
        return bool(
            self.coordinator.last_update_success
            and self.coordinator.data
            and all(code in self.coordinator.data for code in required_codes)
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

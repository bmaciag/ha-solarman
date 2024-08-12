from __future__ import annotations

import logging
import asyncio
import voluptuous as vol

from functools import cached_property, partial

from homeassistant.components.template.sensor import SensorTemplate
from homeassistant.components.template.sensor import TriggerSensorEntity
from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.helpers.template import Template

from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, STATE_OFF, STATE_ON
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo, format_mac
from homeassistant.helpers.entity import Entity, ToggleEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import *
from .common import *
from .services import *
from .api import Inverter
from .coordinator import InverterCoordinator
from .entity import SolarmanCoordinatorEntity, SolarmanEntity, SolarmanDiagnosticEntity

_LOGGER = logging.getLogger(__name__)

_PLATFORM = get_current_file_name(__name__)

def _create_sensor(coordinator, sensor, battery_nominal_voltage, battery_life_cycle_rating):
    try:
        if "artificial" in sensor:
            match sensor["artificial"]:
                case "state":
                    entity = SolarmanConnectionState(coordinator, sensor)
                case "interval":
                    entity = SolarmanInterval(coordinator, sensor)
        elif sensor["name"] in ("Battery SOH", "Battery State", "Today Battery Life Cycles", "Total Battery Life Cycles"):
            entity = SolarmanBatterySensor(coordinator, sensor, battery_nominal_voltage, battery_life_cycle_rating)
        else:
            entity = SolarmanSensor(coordinator, sensor, battery_nominal_voltage, battery_life_cycle_rating)

        entity.update()

        return entity
    except BaseException as e:
        _LOGGER.error(f"Configuring {sensor} failed. [{format_exception(e)}]")
        raise

async def async_setup_entry(hass: HomeAssistant, config: ConfigEntry, async_add_entities: AddEntitiesCallback) -> bool:
    _LOGGER.debug(f"async_setup_entry: {config.options}")
    coordinator = hass.data[DOMAIN][config.entry_id]

    options = config.options

    battery_nominal_voltage = options.get(CONF_BATTERY_NOMINAL_VOLTAGE)
    battery_life_cycle_rating = options.get(CONF_BATTERY_LIFE_CYCLE_RATING)

    sensors = coordinator.inverter.get_sensors()

    # Add entities.
    #
    _LOGGER.debug(f"async_setup: async_add_entities")

    async_add_entities(_create_sensor(coordinator, sensor, battery_nominal_voltage, battery_life_cycle_rating) for sensor in sensors if ((not "class" in sensor or not sensor["class"] in PLATFORMS) and not "configurable" in sensor))

    return True

async def async_unload_entry(hass: HomeAssistant, config: ConfigEntry) -> bool:
    _LOGGER.debug(f"async_unload_entry: {config.options}")

    return True

class SolarmanConnectionState(SolarmanDiagnosticEntity):
    def __init__(self, coordinator, sensor):
        super().__init__(coordinator, _PLATFORM, sensor)

    @property
    def available(self) -> bool:
        return True

    def update(self):
        self._attr_state = self.coordinator.inverter.get_connection_state()
        self._attr_extra_state_attributes["updated"] = self.coordinator.inverter.state_updated.strftime("%m/%d/%Y, %H:%M:%S")

class SolarmanInterval(SolarmanDiagnosticEntity):
    def __init__(self, coordinator, sensor):
        super().__init__(coordinator, _PLATFORM, sensor)
        self._attr_extra_state_attributes = { "state_class": "measurement" }

    @property
    def available(self) -> bool:
        return self._attr_state > 0

    def update(self):
        self._attr_state = self.coordinator.inverter.state_interval.total_seconds()

class SolarmanSensor(SolarmanEntity):
    def __init__(self, coordinator, sensor, battery_nominal_voltage, battery_life_cycle_rating):
        super().__init__(coordinator, _PLATFORM, sensor)

        if "name" in sensor and sensor["name"] == "Battery":
            self._attr_extra_state_attributes = self._attr_extra_state_attributes | { "Nominal Voltage": battery_nominal_voltage, "Life Cycle Rating": battery_life_cycle_rating }

class SolarmanBatterySensor(SolarmanSensor):
    def __init__(self, coordinator, sensor, battery_nominal_voltage, battery_life_cycle_rating):
        SolarmanSensor.__init__(self, coordinator, sensor, battery_nominal_voltage, battery_life_cycle_rating)
        self._battery_nominal_voltage = battery_nominal_voltage
        self._battery_life_cycle_rating = battery_life_cycle_rating
        self._digits = sensor["digits"] if "digits" in sensor else DEFAULT_DIGITS

    def update(self):
        #super().update()
        c = len(self.coordinator.data)
        if c > 1 or (c == 1 and self.sensor_name in self.coordinator.data):
            match self.sensor_name:
                case "Battery SOH":
                    total_battery_charge = self.get_data("Total Battery Charge", None)
                    battery_capacity = self.get_data("Battery Corrected Capacity", None)
                    if battery_capacity <= 0:
                        battery_capacity = self.get_data("Battery Capacity", None)
                    if total_battery_charge and battery_capacity and self._battery_nominal_voltage and self._battery_life_cycle_rating:
                        self._attr_state = get_number(100 - total_battery_charge / get_battery_power_capacity(battery_capacity, self._battery_nominal_voltage) / (self._battery_life_cycle_rating * 0.05), self._digits)
                case "Battery State":
                    battery_power = self.get_data("Battery Power", None)
                    if battery_power:
                        self._attr_state = "discharging" if battery_power > 50 else "charging" if battery_power < -50 else "idle"
                case "Today Battery Life Cycles":
                    today_battery_charge = self.get_data("Today Battery Charge", None)
                    if today_battery_charge == 0:
                        self._attr_state = get_number(0, self._digits)
                        return
                    battery_capacity = self.get_data("Battery Corrected Capacity", None)
                    if battery_capacity <= 0:
                        battery_capacity = self.get_data("Battery Capacity", None)
                    if today_battery_charge and battery_capacity and self._battery_nominal_voltage:
                        self._attr_state = get_number(get_battery_cycles(today_battery_charge, battery_capacity, self._battery_nominal_voltage), self._digits)
                case "Total Battery Life Cycles":
                    total_battery_charge = self.get_data("Total Battery Charge", None)
                    battery_capacity = self.get_data("Battery Corrected Capacity", None)
                    if battery_capacity <= 0:
                        battery_capacity = self.get_data("Battery Capacity", None)
                    if total_battery_charge and battery_capacity and self._battery_nominal_voltage:
                        self._attr_state = get_number(get_battery_cycles(total_battery_charge, battery_capacity, self._battery_nominal_voltage), self._digits)

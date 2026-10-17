import logging
from datetime import timedelta
from homeassistant.components.sensor import SensorEntity
from homeassistant.const import UnitOfTemperature, UnitOfRatio
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .availability import AvailabilityMixin
from .const import DOMAIN, SCAN_INTERVAL as _SCAN_INTERVAL_SECONDS
from .device import account_device_info, find_device, zone_device_info, zone_identifier
from .coordinator import signal_zone_update
from .installation_entity import MySairInstallationEntity, installation_entities

_LOGGER = logging.getLogger(__name__)

# Honoured automatically by HA for entities of this platform with
# should_poll=True (MySairMqttStatusSensor); the per-zone sensors use
# should_poll=False (AvailabilityMixin) and are not affected.
SCAN_INTERVAL = timedelta(seconds=_SCAN_INTERVAL_SECONDS)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the MySair sensors (temperatures, mode and humidity per zone)."""
    data = hass.data[DOMAIN][entry.entry_id]
    devices = data["devices"]
    mqtt_client = data["mqtt"]

    entities = [MySairMqttStatusSensor(hass, entry.entry_id, mqtt_client)]
    for inst_ref, device_list in devices.items():
        for dev in device_list:
            dev_id = dev.get("reference") or dev.get("rf") or dev.get("id")
            name = dev.get("name", f"Zone {dev_id}")
            entities.append(MySairTempSensor(hass, inst_ref, dev_id, name))
            entities.append(MySairSetpointSensor(hass, inst_ref, dev_id, name))
            entities.append(MySairModeSensor(hass, inst_ref, dev_id, name))
            entities.append(MySairHumiditySensor(hass, inst_ref, dev_id, name))

    entities += installation_entities(hass, entry, (MySairMainZoneSensor,))

    async_add_entities(entities)
    _LOGGER.info(f"[MySair Sensor] {len(entities)} sensors created.")


# ==========================================================
# CLOUD (MQTT) CONNECTION STATUS SENSOR (D3/D4)
# ==========================================================
class MySairMqttStatusSensor(SensorEntity):
    """MQTT connection state (D3) and reconnect/parse metrics (D4).

    One instance per config entry (not per zone): unlike the other sensors it
    does not depend on zone data or on AvailabilityMixin (being "unavailable"
    makes no sense for it - even "offline" is valid information). It is
    updated by polling (should_poll=True), reading the live state of
    MySairMQTTClient directly.
    """

    _attr_should_poll = True
    _attr_has_entity_name = True
    _attr_translation_key = "cloud_connection"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["online", "offline"]

    def __init__(self, hass, entry_id, mqtt_client):
        self.hass = hass
        self.entry_id = entry_id
        self.mqtt_client = mqtt_client
        self._attr_unique_id = f"mysair_mqtt_status_{entry_id}"
        self._attr_device_info = account_device_info(entry_id)

    @property
    def native_value(self):
        return "online" if self.mqtt_client.connected else "offline"

    @property
    def extra_state_attributes(self):
        last = self.mqtt_client.last_message_at
        return {
            "last_update": last.isoformat() if last else None,
            "reconnect_attempts": self.mqtt_client.reconnect_attempt,
            "total_reconnects": self.mqtt_client.total_reconnects,
            "parse_strict_count": self.mqtt_client.parse_strict_count,
            "parse_fallback_count": self.mqtt_client.parse_fallback_count,
            "parse_error_count": self.mqtt_client.parse_error_count,
            "last_close_code": self.mqtt_client.last_close_code,
        }


# ==========================================================
# CURRENT TEMPERATURE SENSOR
# ==========================================================
class MySairTempSensor(AvailabilityMixin, SensorEntity):
    """Current temperature of the zone."""

    _attr_has_entity_name = True

    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    # No name, no icon: HA derives both from the device class and
    # translates the name ("Temperatur" / "Temperature" / "Temperatura").

    def __init__(self, hass, inst_ref, device_id, name):
        self.hass = hass
        self.inst_ref = inst_ref
        self.device_id = device_id
        self._attr_unique_id = f"mysair_temp_{inst_ref}_{device_id}"
        self._attr_device_info = zone_device_info(inst_ref, device_id, name)
        self._zone_name = name  # for logging only
        self._state = None
        self._unsub = None
        self._init_availability()

    @property
    def native_value(self):
        return self._state

    async def async_added_to_hass(self):
        self._unsub = async_dispatcher_connect(
            self.hass,
            signal_zone_update(self.inst_ref, self.device_id),
            self._handle_zone_update,
        )

    async def async_will_remove_from_hass(self):
        if self._unsub:
            self._unsub()
            self._unsub = None
        self._stop_availability()

    @callback
    def _handle_zone_update(self, zone):
        self._mark_status_received()
        new_val = zone.get("temp_actual")
        if new_val != self._state:
            self._state = new_val
            _LOGGER.debug(f"[MySair Sensor] {self._zone_name} temperature: {new_val}°C")
        self.async_write_ha_state()


# ==========================================================
# TARGET TEMPERATURE SENSOR
# ==========================================================
class MySairSetpointSensor(AvailabilityMixin, SensorEntity):
    """Current target temperature (setpoint) of the zone."""

    _attr_has_entity_name = True

    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_translation_key = "target_temperature"

    def __init__(self, hass, inst_ref, device_id, name):
        self.hass = hass
        self.inst_ref = inst_ref
        self.device_id = device_id
        self._attr_unique_id = f"mysair_setpoint_{inst_ref}_{device_id}"
        self._attr_device_info = zone_device_info(inst_ref, device_id, name)
        self._zone_name = name  # for logging only
        self._state = None
        self._unsub = None
        self._init_availability()

    @property
    def native_value(self):
        return self._state

    async def async_added_to_hass(self):
        self._unsub = async_dispatcher_connect(
            self.hass,
            signal_zone_update(self.inst_ref, self.device_id),
            self._handle_zone_update,
        )

    async def async_will_remove_from_hass(self):
        if self._unsub:
            self._unsub()
            self._unsub = None
        self._stop_availability()

    @callback
    def _handle_zone_update(self, zone):
        self._mark_status_received()
        new_val = zone.get("temp_target")
        if new_val != self._state:
            self._state = new_val
            _LOGGER.debug(
                f"[MySair Sensor] {self._zone_name} target temperature: {new_val}°C"
            )
        self.async_write_ha_state()


# ==========================================================
# HVAC MODE SENSOR
# ==========================================================
class MySairModeSensor(AvailabilityMixin, SensorEntity):
    """Current zone mode and, as attribute ``medium``, the active medium.

    States (translated by HA): ``off``, ``heat``, ``cool``, ``on`` (on, but
    heat/cool unknown). Attribute ``medium``: ``air``, ``floor``,
    ``air_and_floor`` (kept while the zone is off, since ``m`` does not
    depend on ``e``).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "mode"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["off", "heat", "cool", "on"]

    def __init__(self, hass, inst_ref, device_id, name):
        self.hass = hass
        self.inst_ref = inst_ref
        self.device_id = device_id
        self._attr_unique_id = f"mysair_mode_{inst_ref}_{device_id}"
        self._attr_device_info = zone_device_info(inst_ref, device_id, name)
        self._zone_name = name  # for logging only
        self._state = "off"
        self._medium = None
        self._unsub = None
        self._init_availability()

    @property
    def native_value(self):
        return self._state

    @property
    def extra_state_attributes(self):
        return {"medium": self._medium}

    async def async_added_to_hass(self):
        self._unsub = async_dispatcher_connect(
            self.hass,
            signal_zone_update(self.inst_ref, self.device_id),
            self._handle_zone_update,
        )

    async def async_will_remove_from_hass(self):
        if self._unsub:
            self._unsub()
            self._unsub = None
        self._stop_availability()

    @callback
    def _handle_zone_update(self, zone):
        self._mark_status_received()
        # 'e' = power; heat/cool = parity of 'm'. See docs/protocol-findings.md.
        new_state = "off"
        if zone.get("is_on"):
            if zone.get("is_cool"):
                new_state = "cool"
            elif zone.get("is_heat"):
                new_state = "heat"
            else:
                new_state = "on"
        if new_state != self._state:
            self._state = new_state
            _LOGGER.debug(f"[MySair Sensor] {self._zone_name} mode: {self._state}")

        # Active medium (F4, air vs. floor): 'm' distinguishes air only, floor
        # only or both, independent of power (kept while the zone is off).
        # See docs/protocol-findings.md §4.
        if zone.get("is_ac") and zone.get("is_floor"):
            self._medium = "air_and_floor"
        elif zone.get("is_floor"):
            self._medium = "floor"
        elif zone.get("is_ac"):
            self._medium = "air"
        else:
            self._medium = None

        self.async_write_ha_state()


# ==========================================================
# HUMIDITY SENSOR
# ==========================================================
class MySairHumiditySensor(AvailabilityMixin, SensorEntity):
    """Relative humidity of the zone (see ``status_parser.parse_humidity``).

    ``None`` (shown as "unknown" in HA) when the thermostat reports no value.
    """

    _attr_has_entity_name = True

    _attr_native_unit_of_measurement = UnitOfRatio.PERCENTAGE
    _attr_device_class = SensorDeviceClass.HUMIDITY
    # No name, no icon: HA derives both from the device class.

    def __init__(self, hass, inst_ref, device_id, name):
        self.hass = hass
        self.inst_ref = inst_ref
        self.device_id = device_id
        self._attr_unique_id = f"mysair_humidity_{inst_ref}_{device_id}"
        self._attr_device_info = zone_device_info(inst_ref, device_id, name)
        self._zone_name = name  # for logging only
        self._state = None
        self._unsub = None
        self._init_availability()

    @property
    def native_value(self):
        return self._state

    async def async_added_to_hass(self):
        self._unsub = async_dispatcher_connect(
            self.hass,
            signal_zone_update(self.inst_ref, self.device_id),
            self._handle_zone_update,
        )

    async def async_will_remove_from_hass(self):
        if self._unsub:
            self._unsub()
            self._unsub = None
        self._stop_availability()

    @callback
    def _handle_zone_update(self, zone):
        self._mark_status_received()
        new_val = zone.get("humidity")
        if new_val != self._state:
            self._state = new_val
            _LOGGER.debug(f"[MySair Sensor] {self._zone_name} humidity: {new_val}%")
        self.async_write_ha_state()


# ==========================================================
# INSTALLATION SENSORS (on the installation device)
# ==========================================================
class MySairMainZoneSensor(MySairInstallationEntity, SensorEntity):
    """Main zone of the installation (pl="1"), shown by its device name.

    The official app sends "stop" to this zone. The device name is used so
    that a zone renamed in Home Assistant appears under its new name.
    """

    _attr_translation_key = "main_zone"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, inst_ref, name):
        super().__init__(coordinator, inst_ref, name, "main_zone")

    @property
    def native_value(self):
        zone_id = self.installation.main_zone_id
        if zone_id is None:
            return None
        device = find_device(
            self.hass,
            self.coordinator.entry_id,
            zone_identifier(self.inst_ref, zone_id),
        )
        if device is None:
            return zone_id
        return device.name_by_user or device.name or zone_id

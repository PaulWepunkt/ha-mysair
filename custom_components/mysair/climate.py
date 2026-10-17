import logging
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACMode,
    HVACAction,
)
from homeassistant.const import UnitOfTemperature, ATTR_TEMPERATURE
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .availability import AvailabilityMixin
from .command_feedback import CommandFeedbackMixin
from .const import DOMAIN
from .device import zone_device_info
from .coordinator import signal_installation_update, signal_zone_update
from .zone_control import (
    allowed_directions,
    command_error,
    control_error,
    turn_on_mode,
)

_LOGGER = logging.getLogger(__name__)

# Fan speed (command/field "fanspeed"/"vv"): mapping confirmed from the real
# component of the official app (see docs/protocol-findings.md §9).
FAN_MODE_AUTO = "auto"
# "0" = zonal speed: the default, chosen by the control module from the open
# zones (thermostat manual); the app's fan icon toggles between "0" and "4".
FAN_MODE_ZONAL = "zonal"
_FAN_MODE_WIRE_TO_HA = {
    "0": FAN_MODE_ZONAL,
    "1": "1",
    "2": "2",
    "3": "3",
    "4": FAN_MODE_AUTO,
}
_FAN_MODE_HA_TO_WIRE = {v: k for k, v in _FAN_MODE_WIRE_TO_HA.items()}
_FAN_MODES = [FAN_MODE_ZONAL, "1", "2", "3", FAN_MODE_AUTO]


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the MySair thermostats."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data["api"]
    mqtt_client = data["mqtt"]
    coordinator = data["coordinator"]
    devices = data["devices"]

    entities = []
    for inst_ref, device_list in devices.items():
        for dev in device_list:
            dev_id = dev.get("reference") or dev.get("rf") or dev.get("id")
            name = dev.get("name", f"Zone {dev_id}")
            entities.append(
                MySairThermostat(
                    hass, api, mqtt_client, coordinator, entry, inst_ref, dev_id, name
                )
            )

    async_add_entities(entities)
    _LOGGER.info(f"[MySair Climate] {len(entities)} thermostats created.")


class MySairThermostat(CommandFeedbackMixin, AvailabilityMixin, ClimateEntity):
    """Climate entity of a MySair zone thermostat."""

    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.FAN_MODE
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    # The thermostat is the main entity of the zone: it has no name of its
    # own and inherits the device (zone) name. See device.py.
    _attr_has_entity_name = True
    _attr_name = None
    # Only used for the translated fan modes ("zonal", "auto", ...).
    _attr_translation_key = "thermostat"

    def __init__(
        self, hass, api, mqtt_client, coordinator, entry, inst_ref, device_id, name
    ):
        self.hass = hass
        self.api = api
        self.mqtt_client = mqtt_client
        self.coordinator = coordinator
        self._entry = entry
        self.inst_ref = inst_ref
        self.device_id = device_id
        self._attr_unique_id = f"mysair_{inst_ref}_{device_id}"
        self._attr_device_info = zone_device_info(inst_ref, device_id, name)
        self._zone_name = name  # for logging only
        self._target_temperature = 22.0
        self._current_temperature = None
        self._hvac_mode = HVACMode.OFF
        self._hvac_action = HVACAction.IDLE
        # Defaults until the first MQTT status with the zone's real
        # capabilities arrives (allow_heat/allow_cool, tmm/tmx).
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL]
        self._attr_min_temp = 10
        self._attr_max_temp = 30
        self._fan_mode = None
        self._attr_fan_modes = []
        self._unsub = None
        self._unsub_installation = None
        self._init_command_feedback()
        self._init_availability()

    async def async_added_to_hass(self):
        _LOGGER.debug(
            f"[MySair Climate] Entity added: {self._zone_name} ({self.inst_ref}/{self.device_id})"
        )
        self._unsub = async_dispatcher_connect(
            self.hass,
            signal_zone_update(self.inst_ref, self.device_id),
            self._handle_zone_update,
        )
        # The allowed modes depend on the other zones (direction lock).
        self._unsub_installation = async_dispatcher_connect(
            self.hass,
            signal_installation_update(self.inst_ref),
            self._handle_installation_update,
        )
        self._start_feedback_listener()

    async def async_will_remove_from_hass(self):
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._unsub_installation:
            self._unsub_installation()
            self._unsub_installation = None
        self._stop_feedback_listener()
        self._stop_availability()

    @property
    def hvac_mode(self):
        return self._hvac_mode

    @property
    def hvac_action(self):
        return self._hvac_action

    @property
    def current_temperature(self):
        return self._current_temperature

    @property
    def target_temperature(self):
        return self._target_temperature

    @property
    def fan_mode(self):
        return self._fan_mode

    # ------------------------------------------------------------------
    # CONTROL METHODS (using send_zone_command)
    # ------------------------------------------------------------------
    async def async_set_temperature(self, **kwargs):
        if ATTR_TEMPERATURE not in kwargs:
            return
        new_temp = kwargs[ATTR_TEMPERATURE]
        previous_temp = self._target_temperature
        self._target_temperature = new_temp

        if self._hvac_mode == HVACMode.OFF:
            _LOGGER.debug(
                f"[MySair Climate] Zone is off, updating local setpoint to {new_temp}°C"
            )
            self.async_write_ha_state()
            return

        _LOGGER.debug(
            f"[MySair Climate] Setting temperature to {new_temp}°C on {self.name}"
        )
        try:
            response = await self.hass.async_add_executor_job(
                self.api.send_zone_command,
                self.inst_ref,
                self.device_id,
                "temp",
                new_temp,
            )

            def _revert(previous=previous_temp):
                self._target_temperature = previous

            self._track_command_confirmation(response, revert_fn=_revert)
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error(f"[MySair Climate] Failed to send temperature change: {e}")
            self._target_temperature = previous_temp
            self.async_write_ha_state()
            raise command_error(e) from e

    async def async_set_hvac_mode(self, hvac_mode):
        """Turn the zone off, or on in heat/cool, following the app's rules.

        Heat/cool is a property of the whole installation: while any zone
        is on, only its current direction is offered (see zone_control.py).
        """
        if hvac_mode == HVACMode.OFF:
            await self._async_send_mode_change(HVACMode.OFF, "power", None)
            return
        if hvac_mode not in (HVACMode.HEAT, HVACMode.COOL):
            _LOGGER.warning(f"[MySair Climate] Invalid HVAC mode: {hvac_mode}")
            return
        if hvac_mode == self._hvac_mode:
            return

        is_heat = hvac_mode == HVACMode.HEAT
        zone = self.coordinator.zone(self.inst_ref, self.device_id) or {}
        if is_heat not in allowed_directions(
            self.coordinator, self._entry, self.inst_ref, zone
        ):
            raise control_error("direction_locked")
        mode_value = turn_on_mode(
            self.coordinator, self._entry, self.inst_ref, self.device_id, is_heat
        )
        await self._async_send_mode_change(hvac_mode, "mode", mode_value)

    async def _async_send_mode_change(self, hvac_mode, command, mode_value):
        previous_mode = self._hvac_mode
        try:
            if command == "mode":
                _LOGGER.debug(
                    f"[MySair Climate] Turning on {self.name} with m={mode_value} "
                    f"at {self._target_temperature}°C"
                )
                response = await self.hass.async_add_executor_job(
                    self.api.send_zone_command,
                    self.inst_ref,
                    self.device_id,
                    "mode",
                    mode_value,
                    self._target_temperature,
                )
            else:
                _LOGGER.debug(f"[MySair Climate] Turning off {self.name}")
                response = await self.hass.async_add_executor_job(
                    self.api.send_zone_command, self.inst_ref, self.device_id, "power"
                )

            def _revert(previous=previous_mode):
                self._hvac_mode = previous

            self._track_command_confirmation(response, revert_fn=_revert)
            self._hvac_mode = hvac_mode
            self.async_write_ha_state()

        except Exception as e:
            _LOGGER.error(f"[MySair Climate] Failed to change HVAC mode: {e}")
            raise command_error(e) from e

    async def async_set_fan_mode(self, fan_mode):
        if fan_mode not in self._attr_fan_modes:
            _LOGGER.warning(f"[MySair Climate] Invalid fan speed: {fan_mode}")
            return

        wire_value = _FAN_MODE_HA_TO_WIRE.get(fan_mode, fan_mode)
        previous_fan_mode = self._fan_mode
        _LOGGER.debug(
            f"[MySair Climate] Setting fan speed to {fan_mode} on {self.name}"
        )
        try:
            response = await self.hass.async_add_executor_job(
                self.api.send_zone_command,
                self.inst_ref,
                self.device_id,
                "fanspeed",
                wire_value,
            )

            def _revert(previous=previous_fan_mode):
                self._fan_mode = previous

            self._track_command_confirmation(response, revert_fn=_revert)
            self._fan_mode = fan_mode
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error(f"[MySair Climate] Failed to change fan speed: {e}")
            raise command_error(e) from e

    async def async_turn_off(self):
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_turn_on(self):
        """Turn the zone on like the app's power button.

        Uses the heat/cool direction of the installation, turns the air on,
        keeps the floor setting and the current setpoint (zone_control.py).
        """
        if self._hvac_mode != HVACMode.OFF:
            return
        mode_value = turn_on_mode(
            self.coordinator, self._entry, self.inst_ref, self.device_id
        )
        hvac_mode = HVACMode.COOL if int(mode_value) % 2 else HVACMode.HEAT
        await self._async_send_mode_change(hvac_mode, "mode", mode_value)

    # ------------------------------------------------------------------
    # MQTT EVENTS → STATE UPDATE
    # ------------------------------------------------------------------
    @callback
    def _handle_zone_update(self, zone):
        _LOGGER.debug(f"[MySair Climate] Event received for {self._zone_name}")
        self._mark_status_received()
        # A real status is the freshest truth: discard any command awaiting
        # confirmation (and its revert), it is no longer needed.
        self._clear_pending_command()
        if zone.get("temp_actual") is not None:
            self._current_temperature = zone.get("temp_actual")
        if zone.get("temp_target") is not None:
            self._target_temperature = zone.get("temp_target")
        if zone.get("temp_min") is not None:
            self._attr_min_temp = zone.get("temp_min")
        if zone.get("temp_max") is not None:
            self._attr_max_temp = zone.get("temp_max")

        self._update_hvac_modes(zone)

        # Fan speed (vv/fanspeed, see docs/protocol-findings.md §9).
        self._attr_fan_modes = list(_FAN_MODES) if zone.get("allow_fan") else []
        self._fan_mode = _FAN_MODE_WIRE_TO_HA.get(zone.get("fan_mode"))

        # 'e' = power (on/off/standby); heat/cool = parity of 'm'.
        # See docs/protocol-findings.md.
        if not zone.get("is_on"):
            self._hvac_mode = HVACMode.OFF
            self._hvac_action = HVACAction.OFF
        else:
            if zone.get("is_cool"):
                self._hvac_mode = HVACMode.COOL
            elif zone.get("is_heat"):
                self._hvac_mode = HVACMode.HEAT

            if zone.get("is_standby"):
                self._hvac_action = HVACAction.IDLE
            elif zone.get("is_cool"):
                self._hvac_action = HVACAction.COOLING
            elif zone.get("is_heat"):
                self._hvac_action = HVACAction.HEATING

        _LOGGER.debug(
            f"[MySair Climate] {self._zone_name}: {self._current_temperature}°C / "
            f"{self._target_temperature}°C / {self._hvac_mode}"
        )
        self.async_write_ha_state()

    @callback
    def _handle_installation_update(self, from_status=True):
        """Another zone changed: the direction lock may have changed."""
        zone = self.coordinator.zone(self.inst_ref, self.device_id)
        if zone is None:
            return
        modes = self._attr_hvac_modes
        self._update_hvac_modes(zone)
        if modes != self._attr_hvac_modes:
            self.async_write_ha_state()

    def _update_hvac_modes(self, zone):
        """OFF plus heat/cool by capability (c/f) and installation direction."""
        directions = allowed_directions(
            self.coordinator, self._entry, self.inst_ref, zone
        )
        modes = [HVACMode.OFF]
        if True in directions:
            modes.append(HVACMode.HEAT)
        if False in directions:
            modes.append(HVACMode.COOL)
        self._attr_hvac_modes = modes

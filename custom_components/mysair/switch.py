import logging
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .availability import AvailabilityMixin
from .command_feedback import CommandFeedbackMixin
from .const import DOMAIN
from .device import zone_device_info
from .coordinator import signal_zone_update
from .status_parser import compute_mode_value

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the MySair switches (power and floor heating per zone)."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data["api"]
    mqtt_client = data["mqtt"]
    devices = data["devices"]

    entities = []
    for inst_ref, device_list in devices.items():
        for dev in device_list:
            dev_id = dev.get("reference") or dev.get("rf") or dev.get("id")
            zone_name = dev.get("name", f"Zone {dev_id}")
            entities.append(
                MySairSwitch(hass, api, mqtt_client, inst_ref, dev_id, zone_name)
            )
            entities.append(
                MySairFloorSwitch(hass, api, mqtt_client, inst_ref, dev_id, zone_name)
            )

    async_add_entities(entities)
    _LOGGER.info(f"[MySair Switch] {len(entities)} switches created.")


class MySairSwitch(CommandFeedbackMixin, AvailabilityMixin, SwitchEntity):
    """Switch entity to turn a MySair zone on or off."""

    _attr_has_entity_name = True
    _attr_translation_key = "power"

    def __init__(self, hass, api, mqtt_client, inst_ref, device_id, name):
        self.hass = hass
        self.api = api
        self.mqtt_client = mqtt_client
        self.inst_ref = inst_ref
        self.device_id = device_id
        self._attr_unique_id = f"mysair_switch_{inst_ref}_{device_id}"
        self._attr_device_info = zone_device_info(inst_ref, device_id, name)
        self._zone_name = name  # for logging only
        self._is_on = False
        # Last known air mode, preserved when turning on: "0"=heat, "1"=cool.
        # Defaults to heat (turning on must NEVER force cool). See docs/protocol-findings.md.
        self._last_ac_mode = "0"
        self._unsub = None
        self._init_command_feedback()
        self._init_availability()

    @property
    def is_on(self):
        return self._is_on

    async def async_turn_on(self, **kwargs):
        previous_is_on = self._is_on
        try:
            # Turning on = sending the 'mode' command (there is no power "1"). The
            # last known heat/cool mode is preserved; default heat. See docs/protocol-findings.md.
            _LOGGER.debug(
                f"[MySair Switch] Turning on {self.name} (mode {self._last_ac_mode})"
            )
            response = await self.hass.async_add_executor_job(
                self.api.send_zone_command,
                self.inst_ref,
                self.device_id,
                "mode",
                self._last_ac_mode,
                22.0,
            )

            def _revert(previous=previous_is_on):
                self._is_on = previous

            self._track_command_confirmation(response, revert_fn=_revert)
            self._is_on = True
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error(f"[MySair Switch] Failed to turn on {self.name}: {e}")

    async def async_turn_off(self, **kwargs):
        previous_is_on = self._is_on
        try:
            _LOGGER.debug(f"[MySair Switch] Turning off {self.name}")
            response = await self.hass.async_add_executor_job(
                self.api.send_zone_command, self.inst_ref, self.device_id, "power"
            )

            def _revert(previous=previous_is_on):
                self._is_on = previous

            self._track_command_confirmation(response, revert_fn=_revert)
            self._is_on = False
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error(f"[MySair Switch] Failed to turn off {self.name}: {e}")

    async def async_added_to_hass(self):
        self._unsub = async_dispatcher_connect(
            self.hass,
            signal_zone_update(self.inst_ref, self.device_id),
            self._handle_zone_update,
        )
        self._start_feedback_listener()

    async def async_will_remove_from_hass(self):
        if self._unsub:
            self._unsub()
            self._unsub = None
        self._stop_feedback_listener()
        self._stop_availability()

    @callback
    def _handle_zone_update(self, zone):
        self._mark_status_received()
        self._clear_pending_command()
        self._is_on = bool(zone.get("is_on"))
        # Remember the air mode (heat/cool) to preserve it when turning on again.
        if zone.get("is_ac") and zone.get("mode_raw") in ("0", "1"):
            self._last_ac_mode = zone.get("mode_raw")
        _LOGGER.debug(
            f"[MySair Switch] State {self.name}: {'ON' if self._is_on else 'OFF'}"
        )
        self.async_write_ha_state()


class MySairFloorSwitch(CommandFeedbackMixin, AvailabilityMixin, SwitchEntity):
    """Switch entity to turn the floor heating of a zone on or off (F4).

    The protocol has no dedicated command: the official app recalculates the
    value of `m` (preserving heat/cool and the air state) and sends it with
    the same `mode` command that `climate.py` uses
    (`toggleRadiatingFloor`/`setModeHeat`, see docs/protocol-findings.md §4).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "floor_heating"

    def __init__(self, hass, api, mqtt_client, inst_ref, device_id, name):
        self.hass = hass
        self.api = api
        self.mqtt_client = mqtt_client
        self.inst_ref = inst_ref
        self.device_id = device_id
        self._attr_unique_id = f"mysair_floor_{inst_ref}_{device_id}"
        self._attr_device_info = zone_device_info(inst_ref, device_id, name)
        self._zone_name = name  # for logging only
        self._is_on = False
        self._allow_floor = False
        # Current known state, preserved when recalculating 'm'.
        self._current_is_heat = True
        self._current_is_ac = True
        self._current_temp_target = 22.0
        self._unsub = None
        self._init_command_feedback()
        self._init_availability()

    @property
    def is_on(self):
        return self._is_on

    @property
    def available(self):
        # Floor heating is not a universal capability (unlike power on/off):
        # the entity always exists but is marked "unavailable" for zones
        # without floor heating, just as all entities are "unavailable"
        # until the first status arrives.
        return super().available and self._allow_floor

    async def async_turn_on(self, **kwargs):
        await self._async_set_floor(True)

    async def async_turn_off(self, **kwargs):
        await self._async_set_floor(False)

    async def _async_set_floor(self, floor_on):
        previous_is_on = self._is_on
        try:
            new_mode = compute_mode_value(
                self._current_is_heat, self._current_is_ac, floor_on
            )
            _LOGGER.debug(
                f"[MySair Switch] Setting floor heating {'ON' if floor_on else 'OFF'} on {self.name} (m={new_mode})"
            )
            response = await self.hass.async_add_executor_job(
                self.api.send_zone_command,
                self.inst_ref,
                self.device_id,
                "mode",
                new_mode,
                self._current_temp_target,
            )

            def _revert(previous=previous_is_on):
                self._is_on = previous

            self._track_command_confirmation(response, revert_fn=_revert)
            self._is_on = floor_on
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error(
                f"[MySair Switch] Failed to change floor heating of {self.name}: {e}"
            )

    async def async_added_to_hass(self):
        self._unsub = async_dispatcher_connect(
            self.hass,
            signal_zone_update(self.inst_ref, self.device_id),
            self._handle_zone_update,
        )
        self._start_feedback_listener()

    async def async_will_remove_from_hass(self):
        if self._unsub:
            self._unsub()
            self._unsub = None
        self._stop_feedback_listener()
        self._stop_availability()

    @callback
    def _handle_zone_update(self, zone):
        self._mark_status_received()
        self._clear_pending_command()
        self._allow_floor = bool(zone.get("allow_floor"))
        self._is_on = bool(zone.get("is_floor"))
        if zone.get("is_heat") is not None:
            self._current_is_heat = bool(zone.get("is_heat"))
        if zone.get("is_ac") is not None:
            self._current_is_ac = bool(zone.get("is_ac"))
        if zone.get("temp_target") is not None:
            self._current_temp_target = zone.get("temp_target")
        _LOGGER.debug(
            f"[MySair Switch] Floor heating {self.name}: {'ON' if self._is_on else 'OFF'}"
        )
        self.async_write_ha_state()

import logging
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.restore_state import RestoreEntity

from .availability import AvailabilityMixin
from .command_feedback import CommandFeedbackMixin
from .const import DOMAIN
from .device import zone_device_info
from .coordinator import signal_zone_update
from .installation_entity import MySairInstallationEntity, installation_entities
from .zone_control import command_error, floor_command, turn_on_mode

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the MySair switches (power and floor heating per zone)."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data["api"]
    mqtt_client = data["mqtt"]
    coordinator = data["coordinator"]
    devices = data["devices"]

    entities = []
    for inst_ref, device_list in devices.items():
        for dev in device_list:
            dev_id = dev.get("reference") or dev.get("rf") or dev.get("id")
            zone_name = dev.get("name", f"Zone {dev_id}")
            for cls in (MySairSwitch, MySairFloorSwitch):
                entities.append(
                    cls(
                        hass,
                        api,
                        mqtt_client,
                        coordinator,
                        entry,
                        inst_ref,
                        dev_id,
                        zone_name,
                    )
                )

    entities += installation_entities(hass, entry, (MySairHoldDirectionSwitch,))

    async_add_entities(entities)
    _LOGGER.info(f"[MySair Switch] {len(entities)} switches created.")


class MySairSwitch(CommandFeedbackMixin, AvailabilityMixin, SwitchEntity):
    """Switch entity to turn a MySair zone on or off."""

    _attr_has_entity_name = True
    _attr_translation_key = "power"

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
        self._attr_unique_id = f"mysair_switch_{inst_ref}_{device_id}"
        self._attr_device_info = zone_device_info(inst_ref, device_id, name)
        self._zone_name = name  # for logging only
        self._is_on = False
        self._unsub = None
        self._init_command_feedback()
        self._init_availability()

    @property
    def is_on(self):
        return self._is_on

    async def async_turn_on(self, **kwargs):
        # Turning on = sending the 'mode' command (there is no power "1"),
        # exactly like the app's power button: direction of the installation,
        # air on, floor as before, current setpoint (zone_control.py).
        zone = self.coordinator.zone(self.inst_ref, self.device_id) or {}
        if zone.get("is_on"):
            # Already on: sending 'mode' again could change the media.
            return
        mode_value = turn_on_mode(
            self.coordinator, self._entry, self.inst_ref, self.device_id
        )
        previous_is_on = self._is_on
        try:
            _LOGGER.debug(f"[MySair Switch] Turning on {self.name} (m={mode_value})")
            response = await self.hass.async_add_executor_job(
                self.api.send_zone_command,
                self.inst_ref,
                self.device_id,
                "mode",
                mode_value,
                zone.get("temp_target"),
            )

            def _revert(previous=previous_is_on):
                self._is_on = previous

            self._track_command_confirmation(response, revert_fn=_revert)
            self._is_on = True
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error(f"[MySair Switch] Failed to turn on {self.name}: {e}")
            raise command_error(e) from e

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
            raise command_error(e) from e

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
        _LOGGER.debug(
            f"[MySair Switch] State {self.name}: {'ON' if self._is_on else 'OFF'}"
        )
        self.async_write_ha_state()


class MySairFloorSwitch(CommandFeedbackMixin, AvailabilityMixin, SwitchEntity):
    """Switch entity to turn the floor heating of a zone on or off (F4).

    The protocol has no dedicated command: the official app recalculates the
    value of `m` (direction of the installation, air state preserved) and
    sends it with the `mode` command, or turns the zone off if neither air
    nor floor stays active (`toggleRadiatingFloor`, see zone_control.py and
    docs/protocol-findings.md §4).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "floor_heating"

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
        self._attr_unique_id = f"mysair_floor_{inst_ref}_{device_id}"
        self._attr_device_info = zone_device_info(inst_ref, device_id, name)
        self._zone_name = name  # for logging only
        self._is_on = False
        self._allow_floor = False
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
        command, mode_value = floor_command(
            self.coordinator, self._entry, self.inst_ref, self.device_id, floor_on
        )
        zone = self.coordinator.zone(self.inst_ref, self.device_id) or {}
        previous_is_on = self._is_on
        try:
            _LOGGER.debug(
                f"[MySair Switch] Setting floor {'ON' if floor_on else 'OFF'} on "
                f"{self.name} ({command} {mode_value})"
            )
            if command == "mode":
                response = await self.hass.async_add_executor_job(
                    self.api.send_zone_command,
                    self.inst_ref,
                    self.device_id,
                    "mode",
                    mode_value,
                    zone.get("temp_target"),
                )
            else:
                response = await self.hass.async_add_executor_job(
                    self.api.send_zone_command, self.inst_ref, self.device_id, "power"
                )

            def _revert(previous=previous_is_on):
                self._is_on = previous

            self._track_command_confirmation(response, revert_fn=_revert)
            self._is_on = floor_on
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error(f"[MySair Switch] Failed to change floor of {self.name}: {e}")
            raise command_error(e) from e

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
        # The floor only runs while the zone is on; the floor bit of 'm' is
        # kept while the zone is off (it is restored when turning on).
        self._is_on = bool(zone.get("is_on") and zone.get("is_floor"))
        _LOGGER.debug(
            f"[MySair Switch] Floor heating {self.name}: {'ON' if self._is_on else 'OFF'}"
        )
        self.async_write_ha_state()


class MySairHoldDirectionSwitch(MySairInstallationEntity, RestoreEntity, SwitchEntity):
    """Configuration: hold the heat/cool direction ("Betriebsart festhalten").

    For installations whose heat pump is not switched by MySair (it has to
    be set to heating or cooling at its own controller). When on, the
    "Betriebsart" select describes the heat pump: Home Assistant only turns
    zones on in that direction and reports a problem when the installation
    runs in the other one. Off by default: the behaviour of the app.
    Nothing is sent to MySair; the setting is kept over a restart.
    """

    _attr_translation_key = "hold_direction"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, inst_ref, name):
        super().__init__(coordinator, inst_ref, name, "hold_direction")

    async def async_added_to_hass(self):
        last = await self.async_get_last_state()
        if last is not None and last.state == "on":
            self.coordinator.set_hold(self.inst_ref, True)
        await super().async_added_to_hass()

    @property
    def available(self):
        return True

    @property
    def is_on(self):
        return self.coordinator.is_held(self.inst_ref)

    async def async_turn_on(self, **kwargs):
        self.coordinator.set_hold(self.inst_ref, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self.coordinator.set_hold(self.inst_ref, False)
        self.async_write_ha_state()

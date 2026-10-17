"""Per-config-entry coordinator for MQTT events (efficiency refactor, C1).

Before: every entity (climate/sensor/switch) subscribed to the bus event
`f"{DOMAIN}_update"` on its own and repeated the same manual filtering by
topic/ctl/zone_id over the full zone list of the message. With N zones and
6 entities per zone, a single MQTT message triggered 6×N listeners walking
the same list from top to bottom.

Now: a single `MySairCoordinator` instance per config entry listens to
`f"{DOMAIN}_update"` once, filters by topic suffix and its own
installations, and redistributes each zone separately via
`homeassistant.helpers.dispatcher` with a dedicated signal per (ctl,
zone_id). Each entity subscribes only to its own signal: it no longer
repeats the filtering and directly receives its already isolated zone.

The `f"{DOMAIN}_update"` event (fired by `mqtt_message_callback` in
`__init__.py`, unchanged) is still the only entry point: both real MQTT and
the tests (`_fire_status` in test_entities.py) work as before.
"""

import logging
from dataclasses import dataclass

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN
from .device import find_device, installation_identifier
from .status_parser import has_direction_conflict, installation_is_heat

_LOGGER = logging.getLogger(__name__)

# Dispatcher signal prefix: an internal detail of this mechanism (only used by
# this module and the entities), not a protocol/domain constant, so it lives
# here rather than in const.py.
_SIGNAL_ZONE_UPDATE = f"{DOMAIN}_zone_update"


def signal_zone_update(inst_ref: str, device_id: str) -> str:
    """Dispatcher signal name for a specific zone.

    Every entity of a zone (climate/sensor/switch) subscribes to this same
    signal to receive only the data of its own zone, already filtered and
    isolated by the coordinator.
    """
    return f"{_SIGNAL_ZONE_UPDATE}_{inst_ref}_{device_id}"


def signal_installation_update(inst_ref: str) -> str:
    """Dispatcher signal sent when the state of an installation changed.

    Sent after all zones of a status message were stored (argument
    ``from_status=True``) and after a local change such as a chosen direction
    (``from_status=False``). Entities whose allowed actions depend on the
    other zones (heat/cool direction of the installation) listen to it.
    """
    return f"{_SIGNAL_ZONE_UPDATE}_{inst_ref}"


@dataclass(frozen=True)
class InstallationState:
    """State of a whole installation derived from its zones."""

    # Direction HA uses for turning zones on (True heat, False cool, None
    # unknown): the held direction if "hold" is on, otherwise the direction
    # of the installation (or the one chosen while all zones are off).
    is_heat: bool | None
    is_on: bool  # at least one zone is on
    main_zone_id: str | None  # zone with pl="1"
    has_conflict: bool  # running zones report different directions
    # Direction the installation actually runs in (rule of the app), None
    # if unknown.
    running_is_heat: bool | None = None
    # Held direction ("Betriebsart festhalten"), None if hold is off.
    held_is_heat: bool | None = None

    @property
    def direction_mismatch(self) -> bool:
        """The installation runs in the other direction than the held one."""
        return (
            self.is_on
            and self.held_is_heat is not None
            and self.running_is_heat is not None
            and self.running_is_heat != self.held_is_heat
        )


class MySairCoordinator:
    """Redistribute the `status` messages of a config entry per zone.

    Replaces the N×6 direct bus subscriptions (one per entity) with a single
    subscription per config entry; see the module docstring.
    """

    def __init__(
        self, hass: HomeAssistant, installation_refs: list, entry_id: str = ""
    ) -> None:
        self.hass = hass
        self._entry_id = entry_id
        self._installation_refs = set(installation_refs)
        # ctl -> {zone_id: last zone dict}; insertion order = order in which
        # the controller first reported the zones (relevant for the direction).
        self._zones = {}
        self._firmware = {}  # ctl -> last reported controller firmware
        # ctl -> direction chosen while all zones are off (app: season change).
        # Only held locally, like in the app; it is sent with the next zone
        # that is turned on and dropped as soon as a zone is on.
        self._chosen_is_heat = {}
        # ctl -> True if the direction is held ("Betriebsart festhalten"):
        # for installations whose heat pump is not switched by MySair, the
        # user sets the direction to match the heat pump and HA keeps it.
        self._hold = {}
        self._held_is_heat = {}  # ctl -> held direction (kept when hold is off)
        # (ctl, zone_id, is_heat) -> medium chosen for turning the zone on
        # (select entities per zone; restored by them after a restart).
        self._medium_choice = {}
        self._unsub = None

    def start(self) -> None:
        """Subscribe to the `mysair_update` event (once per entry)."""
        self._unsub = self.hass.bus.async_listen(
            f"{DOMAIN}_update", self._handle_update
        )

    def stop(self) -> None:
        """Cancel the bus subscription."""
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _handle_update(self, event) -> None:
        topic = event.data.get("topic", "")
        if not topic.endswith("/status"):
            return

        data = event.data.get("data", {})
        ctl = data.get("ctl")
        if ctl not in self._installation_refs:
            return

        self._update_firmware(ctl, data.get("firmware"))

        zones = self._zones.setdefault(ctl, {})
        updated = []
        for zone in data.get("zones", []):
            zone_id = zone.get("zone_id")
            if zone_id is None:
                continue
            zones[zone_id] = zone
            updated.append((zone_id, zone))
        if any(z.get("is_on") for z in zones.values()):
            self._chosen_is_heat.pop(ctl, None)

        # All zones are stored before dispatching, so every entity sees the
        # complete installation state when it handles its own zone.
        for zone_id, zone in updated:
            _LOGGER.debug(
                f"[MySair Coordinator] Zone {ctl}/{zone_id} updated, dispatching"
            )
            async_dispatcher_send(self.hass, signal_zone_update(ctl, zone_id), zone)
        if updated:
            async_dispatcher_send(self.hass, signal_installation_update(ctl), True)

    def choose_direction(self, ctl: str, is_heat: bool) -> bool:
        """Choose the heat/cool direction while all zones are off.

        Returns False if a zone is on (the direction is then locked). Nothing
        is sent: like the app, the next zone that is turned on uses it.
        """
        if self._hold.get(ctl):
            # Held direction: it describes the heat pump and can be changed
            # at any time; nothing is sent to MySair.
            self._held_is_heat[ctl] = is_heat
            async_dispatcher_send(self.hass, signal_installation_update(ctl), False)
            return True
        if self.installation_state(ctl).is_on:
            return False
        self._chosen_is_heat[ctl] = is_heat
        async_dispatcher_send(self.hass, signal_installation_update(ctl), False)
        return True

    def held_direction(self, ctl: str) -> bool | None:
        """Held direction, also while hold is off (for restoring it)."""
        return self._held_is_heat.get(ctl)

    def restore_held_direction(self, ctl: str, is_heat: bool) -> None:
        self._held_is_heat[ctl] = bool(is_heat)

    def is_held(self, ctl: str) -> bool:
        return bool(self._hold.get(ctl))

    def set_hold(self, ctl: str, enabled: bool) -> None:
        """Turn holding the direction on or off.

        When turned on without a held direction yet, the current direction
        of the installation is taken over.
        """
        self._hold[ctl] = bool(enabled)
        if enabled and ctl not in self._held_is_heat:
            current = self.installation_state(ctl).is_heat
            if current is not None:
                self._held_is_heat[ctl] = current
        async_dispatcher_send(self.hass, signal_installation_update(ctl), False)

    def _update_firmware(self, ctl: str, firmware) -> None:
        """Show the controller firmware as software version of the installation device."""
        if not firmware or self._firmware.get(ctl) == firmware:
            return
        self._firmware[ctl] = firmware
        device = find_device(self.hass, self._entry_id, installation_identifier(ctl))
        if device is not None and device.sw_version != firmware:
            dr.async_get(self.hass).async_update_device(device.id, sw_version=firmware)

    def medium_choice(self, ctl: str, zone_id: str, is_heat: bool) -> str:
        """Medium chosen for a zone and direction (default: like the app)."""
        return self._medium_choice.get((ctl, zone_id, bool(is_heat)), "app")

    def set_medium_choice(self, ctl, zone_id, is_heat, medium: str) -> None:
        self._medium_choice[(ctl, zone_id, bool(is_heat))] = medium

    @callback
    def async_options_updated(self) -> None:
        """Options changed: allowed media may differ, refresh dependent entities."""
        for ctl in self._installation_refs:
            async_dispatcher_send(self.hass, signal_installation_update(ctl), False)

    @property
    def entry_id(self) -> str:
        return self._entry_id

    def zone(self, ctl: str, zone_id: str) -> dict | None:
        """Last known state of a zone, or None before its first status."""
        return self._zones.get(ctl, {}).get(zone_id)

    def zones(self) -> dict[str, dict[str, dict]]:
        """Last known state of all zones, per installation (copy)."""
        return {ctl: dict(zones) for ctl, zones in self._zones.items()}

    def installation_state(self, ctl: str) -> InstallationState:
        """Direction, on state and main zone of an installation."""
        zones = list(self._zones.get(ctl, {}).values())
        main_zone_id = next((z.get("zone_id") for z in zones if z.get("is_main")), None)
        is_on = any(z.get("is_on") for z in zones)
        running = installation_is_heat(zones)
        is_heat = running
        if not is_on and ctl in self._chosen_is_heat:
            is_heat = self._chosen_is_heat[ctl]
        held = self._held_is_heat.get(ctl) if self._hold.get(ctl) else None
        if held is not None:
            is_heat = held
        return InstallationState(
            is_heat=is_heat,
            is_on=is_on,
            main_zone_id=main_zone_id,
            has_conflict=has_direction_conflict(zones),
            running_is_heat=running,
            held_is_heat=held,
        )

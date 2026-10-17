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

    is_heat: bool | None  # direction (True heat, False cool, None unknown)
    is_on: bool  # at least one zone is on
    main_zone_id: str | None  # zone with pl="1"
    has_conflict: bool  # running zones report different directions


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
        if self.installation_state(ctl).is_on:
            return False
        self._chosen_is_heat[ctl] = is_heat
        async_dispatcher_send(self.hass, signal_installation_update(ctl), False)
        return True

    def _update_firmware(self, ctl: str, firmware) -> None:
        """Show the controller firmware as software version of the installation device."""
        if not firmware or self._firmware.get(ctl) == firmware:
            return
        self._firmware[ctl] = firmware
        device = find_device(self.hass, self._entry_id, installation_identifier(ctl))
        if device is not None and device.sw_version != firmware:
            dr.async_get(self.hass).async_update_device(device.id, sw_version=firmware)

    @property
    def entry_id(self) -> str:
        return self._entry_id

    def zone(self, ctl: str, zone_id: str) -> dict | None:
        """Last known state of a zone, or None before its first status."""
        return self._zones.get(ctl, {}).get(zone_id)

    def installation_state(self, ctl: str) -> InstallationState:
        """Direction, on state and main zone of an installation."""
        zones = list(self._zones.get(ctl, {}).values())
        main_zone_id = next((z.get("zone_id") for z in zones if z.get("is_main")), None)
        is_on = any(z.get("is_on") for z in zones)
        is_heat = installation_is_heat(zones)
        if not is_on and ctl in self._chosen_is_heat:
            is_heat = self._chosen_is_heat[ctl]
        return InstallationState(
            is_heat=is_heat,
            is_on=is_on,
            main_zone_id=main_zone_id,
            has_conflict=has_direction_conflict(zones),
        )

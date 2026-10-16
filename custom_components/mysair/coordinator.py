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

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN

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


class MySairCoordinator:
    """Redistribute the `status` messages of a config entry per zone.

    Replaces the N×6 direct bus subscriptions (one per entity) with a single
    subscription per config entry; see the module docstring.
    """

    def __init__(self, hass: HomeAssistant, installation_refs: list) -> None:
        self.hass = hass
        self._installation_refs = set(installation_refs)
        self._zones = {}  # (ctl, zone_id) -> last zone dict received
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

        for zone in data.get("zones", []):
            zone_id = zone.get("zone_id")
            if zone_id is None:
                continue
            self._zones[(ctl, zone_id)] = zone
            _LOGGER.debug(
                f"[MySair Coordinator] Zone {ctl}/{zone_id} updated, dispatching"
            )
            async_dispatcher_send(self.hass, signal_zone_update(ctl, zone_id), zone)

"""Entity availability based on the freshness of MQTT data (C5).

Mixin shared by the climate/sensor/switch entities. Without an application
heartbeat (see docs/known-unknowns.md #11) there is no direct way to tell
whether the device is still "alive"; instead of showing potentially stale
data as if it were live, the entity is marked unavailable when it has not
received a ``status`` for more than ``MQTT_STALE_AFTER_SECONDS``. It starts
unavailable until the first status after startup/reload.

With ``should_poll=False`` nothing re-evaluates ``available`` on its own:
if MQTT drops and no further status arrives, the last published state would
stay "available" forever. That is why every received status also arms a
timer (``async_call_later``) that forces an `async_write_ha_state` after
``MQTT_STALE_AFTER_SECONDS`` if nothing newer has arrived.
"""

from datetime import timedelta

import homeassistant.util.dt as dt_util
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later

from .const import MQTT_STALE_AFTER_SECONDS


class AvailabilityMixin:
    """The using class must call ``self._init_availability()`` and
    ``self._stop_availability()`` in ``async_will_remove_from_hass``."""

    _attr_should_poll = False

    def _init_availability(self):
        self._last_status_at = None
        self._cancel_stale_check = None

    def _stop_availability(self):
        if self._cancel_stale_check:
            self._cancel_stale_check()
            self._cancel_stale_check = None

    def _mark_status_received(self):
        self._last_status_at = dt_util.utcnow()
        if self._cancel_stale_check:
            self._cancel_stale_check()
        self._cancel_stale_check = async_call_later(
            self.hass, MQTT_STALE_AFTER_SECONDS, self._on_stale_check
        )

    @callback
    def _on_stale_check(self, now):
        """Force a re-evaluation of `available` when the data may have gone stale."""
        self._cancel_stale_check = None
        self.async_write_ha_state()

    @property
    def available(self):
        if self._last_status_at is None:
            return False
        return dt_util.utcnow() - self._last_status_at < timedelta(
            seconds=MQTT_STALE_AFTER_SECONDS
        )

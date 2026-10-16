"""Tracking of command acknowledgements (ACK) via the MQTT feedback topic.

Mixin shared by MySairThermostat (climate.py) and MySairSwitch (switch.py):
both send commands over HTTP and need the same logic to correlate the
response (``orderId``) with the ACK that arrives later over MQTT (see
docs/protocol-findings.md §8, docs/known-unknowns.md #23).

If no confirmation arrives in time, the optimistic state is reverted to the
last known value (``revert_fn``, optional in ``_track_command_confirmation``).
If a real MQTT status arrives first (newly confirmed truth), any pending
revert is discarded: it is no longer needed, the fresh data wins.
"""

import logging

from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later

from .api import extract_order_id
from .const import DOMAIN, FEEDBACK_TIMEOUT_SECONDS

_LOGGER = logging.getLogger(__name__)


class CommandFeedbackMixin:
    """The using class must define ``self.hass``, ``self.inst_ref`` and ``self.name``."""

    def _init_command_feedback(self):
        self._pending_order_id = None
        self._pending_revert_fn = None
        self._cancel_feedback_timeout = None
        self._unsub_feedback = None

    def _start_feedback_listener(self):
        self._unsub_feedback = self.hass.bus.async_listen(
            f"{DOMAIN}_feedback", self._handle_feedback_event
        )

    def _stop_feedback_listener(self):
        if self._unsub_feedback:
            self._unsub_feedback()
            self._unsub_feedback = None
        if self._cancel_feedback_timeout:
            self._cancel_feedback_timeout()
            self._cancel_feedback_timeout = None

    def _track_command_confirmation(self, response, revert_fn=None):
        """Record the ``orderId`` of a command just sent and arm the timeout.

        ``revert_fn``, if given, is a function without arguments that restores
        the local state to the value before the optimistic change; it is only
        called if no confirmation arrives in time (it does not write the
        state, the caller does that after invoking it).
        """
        order_id = extract_order_id(response)
        if not order_id:
            return
        if self._cancel_feedback_timeout:
            self._cancel_feedback_timeout()
        self._pending_order_id = order_id
        self._pending_revert_fn = revert_fn
        self._cancel_feedback_timeout = async_call_later(
            self.hass, FEEDBACK_TIMEOUT_SECONDS, self._on_feedback_timeout
        )

    def _clear_pending_command(self):
        """Discard any command awaiting confirmation (and its revert).

        Called when a real MQTT status for this zone arrives: that fresh data
        is the truth, there is no need to keep waiting/reverting.
        """
        self._pending_order_id = None
        self._pending_revert_fn = None
        if self._cancel_feedback_timeout:
            self._cancel_feedback_timeout()
            self._cancel_feedback_timeout = None

    @callback
    def _handle_feedback_event(self, event):
        if event.data.get("ctl") != self.inst_ref:
            return
        if (
            not self._pending_order_id
            or event.data.get("order_id") != self._pending_order_id
        ):
            return
        _LOGGER.debug(
            f"[MySair] Command confirmed for {self.name} (orderId={self._pending_order_id})"
        )
        self._pending_order_id = None
        self._pending_revert_fn = None
        if self._cancel_feedback_timeout:
            self._cancel_feedback_timeout()
            self._cancel_feedback_timeout = None

    @callback
    def _on_feedback_timeout(self, now):
        if self._pending_order_id:
            mqtt_client = getattr(self, "mqtt_client", None)
            if mqtt_client is not None and not mqtt_client.connected:
                reason = "MQTT was disconnected at that moment"
            else:
                reason = (
                    "with MQTT connected - possibly a lost ACK or a backend problem"
                )

            revert_fn = self._pending_revert_fn
            revert_note = (
                " - reverting to the last confirmed state" if revert_fn else ""
            )
            _LOGGER.warning(
                f"[MySair] No MQTT confirmation for {self.name} after "
                f"{FEEDBACK_TIMEOUT_SECONDS}s ({reason}, orderId={self._pending_order_id})"
                f"{revert_note}"
            )
            self._pending_order_id = None
            self._pending_revert_fn = None
            if revert_fn:
                revert_fn()
                self.async_write_ha_state()
        self._cancel_feedback_timeout = None

"""Button of an installation: stop all zones at once."""

import logging

from homeassistant.components.button import ButtonEntity

from .const import DOMAIN
from .installation_entity import MySairInstallationEntity, installation_entities
from .zone_control import async_stop_installation

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the stop button of each installation."""
    entities = installation_entities(hass, entry, (MySairStopButton,))
    async_add_entities(entities)
    _LOGGER.info(f"[MySair Button] {len(entities)} buttons created.")


class MySairStopButton(MySairInstallationEntity, ButtonEntity):
    """Stop the whole installation, like the app's stop button.

    Same command as the service mysair.stop_installation. Always available:
    the command goes over HTTP and also works while no fresh MQTT status
    has arrived (then without main zone, see zone_control.py).
    """

    _attr_translation_key = "stop_installation"

    def __init__(self, coordinator, inst_ref, name):
        super().__init__(coordinator, inst_ref, name, "stop_installation")

    @property
    def available(self):
        return True

    async def async_press(self) -> None:
        api = self.hass.data[DOMAIN][self.platform.config_entry.entry_id]["api"]
        await async_stop_installation(self.hass, api, self.coordinator, self.inst_ref)

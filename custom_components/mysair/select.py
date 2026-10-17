"""Select of an installation: heat/cool direction (season change)."""

import logging

from homeassistant.components.select import SelectEntity

from .installation_entity import MySairInstallationEntity, installation_entities
from .zone_control import choose_direction

_LOGGER = logging.getLogger(__name__)

_HEAT = "heat"
_COOL = "cool"


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the operating mode select of each installation."""
    entities = installation_entities(hass, entry, (MySairOperatingModeSelect,))
    async_add_entities(entities)
    _LOGGER.info(f"[MySair Select] {len(entities)} selects created.")


class MySairOperatingModeSelect(MySairInstallationEntity, SelectEntity):
    """Heat/cool direction of the whole installation.

    Shows the direction also while all zones are off (rule of the app:
    first zone that is on, otherwise the first zone). It can only be changed
    while all zones are off; like in the app nothing is sent then, the next
    zone that is turned on starts in the chosen direction
    (docs/protocol-findings.md §4b).
    """

    _attr_translation_key = "operating_mode"
    _attr_options = [_HEAT, _COOL]

    def __init__(self, coordinator, inst_ref, name):
        super().__init__(coordinator, inst_ref, name, "operating_mode")

    @property
    def current_option(self):
        is_heat = self.installation.is_heat
        if is_heat is None:
            return None
        return _HEAT if is_heat else _COOL

    async def async_select_option(self, option: str) -> None:
        if option == self.current_option:
            return
        choose_direction(self.coordinator, self.inst_ref, option == _HEAT)

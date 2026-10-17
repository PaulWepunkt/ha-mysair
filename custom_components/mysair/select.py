"""Selects: heat/cool direction of an installation and medium per zone."""

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.restore_state import RestoredExtraData, RestoreEntity

from .availability import AvailabilityMixin
from .const import DOMAIN
from .coordinator import signal_installation_update, signal_zone_update
from .device import zone_device_info
from .installation_entity import MySairInstallationEntity, installation_entities
from .status_parser import MEDIUM_APP
from .zone_control import choose_direction, zone_media_options

_LOGGER = logging.getLogger(__name__)

_HEAT = "heat"
_COOL = "cool"


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the operating mode select of each installation."""
    entities = installation_entities(hass, entry, (MySairOperatingModeSelect,))
    data = hass.data[DOMAIN][entry.entry_id]
    for inst_ref, device_list in data["devices"].items():
        for dev in device_list:
            dev_id = dev.get("reference") or dev.get("rf") or dev.get("id")
            name = dev.get("name", f"Zone {dev_id}")
            for is_heat in (True, False):
                entities.append(
                    MySairMediumSelect(
                        data["coordinator"], entry, inst_ref, dev_id, name, is_heat
                    )
                )
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


class MySairMediumSelect(AvailabilityMixin, RestoreEntity, SelectEntity):
    """Medium a zone is turned on with in one direction ("Heizen mit"/"Kühlen mit").

    Applies to every turn-on through Home Assistant (thermostat, power
    switch, automations). The choices offered follow the options of the
    integration (allowed media per direction) and whether the zone has a
    floor. "app" keeps the behaviour of the official app: air on, floor as
    before. The choice is kept locally and restored after a restart; nothing
    is sent to MySair when it changes.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, inst_ref, device_id, name, is_heat):
        self.coordinator = coordinator
        self._entry = entry
        self.inst_ref = inst_ref
        self.device_id = device_id
        self.is_heat = is_heat
        key = "heating_medium" if is_heat else "cooling_medium"
        self._attr_translation_key = key
        self._attr_unique_id = f"mysair_{key}_{inst_ref}_{device_id}"
        self._attr_device_info = zone_device_info(inst_ref, device_id, name)
        self._init_availability()

    async def async_added_to_hass(self):
        # The choice itself is stored as extra data: the displayed state can
        # differ (e.g. "app" while the chosen medium is not allowed) or be
        # unavailable when Home Assistant saves it.
        extra = await self.async_get_last_extra_data()
        choice = extra.as_dict().get("choice") if extra is not None else None
        if choice:
            self.coordinator.set_medium_choice(
                self.inst_ref, self.device_id, self.is_heat, choice
            )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_zone_update(self.inst_ref, self.device_id),
                self._handle_zone_update,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_installation_update(self.inst_ref),
                self._handle_options_update,
            )
        )

    async def async_will_remove_from_hass(self):
        self._stop_availability()

    @property
    def extra_restore_state_data(self) -> RestoredExtraData:
        return RestoredExtraData(
            {
                "choice": self.coordinator.medium_choice(
                    self.inst_ref, self.device_id, self.is_heat
                )
            }
        )

    @property
    def _zone(self) -> dict:
        return self.coordinator.zone(self.inst_ref, self.device_id) or {}

    @property
    def options(self) -> list[str]:
        return zone_media_options(self._entry, self._zone, self.is_heat)

    @property
    def available(self):
        # Not offered if the zone cannot run in this direction with the
        # media allowed in the options.
        capable = self._zone.get("allow_heat" if self.is_heat else "allow_cool")
        return super().available and bool(capable) and bool(self.options)

    @property
    def current_option(self):
        choice = self.coordinator.medium_choice(
            self.inst_ref, self.device_id, self.is_heat
        )
        options = self.options
        if choice in options:
            return choice
        return MEDIUM_APP if MEDIUM_APP in options else None

    async def async_select_option(self, option: str) -> None:
        self.coordinator.set_medium_choice(
            self.inst_ref, self.device_id, self.is_heat, option
        )
        self.async_write_ha_state()

    @callback
    def _handle_zone_update(self, zone):
        self._mark_status_received()
        self.async_write_ha_state()

    @callback
    def _handle_options_update(self, from_status=True):
        if not from_status:
            self.async_write_ha_state()

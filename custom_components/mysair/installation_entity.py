"""Base class for entities of an installation (MySair controller).

These entities describe the installation as a whole (heat/cool direction,
running state, main zone, direction conflict). They hang on the
installation device and are refreshed by the coordinator's installation
signal, which is sent after all zones of a status message were stored.
"""

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .availability import AvailabilityMixin
from .const import DOMAIN
from .coordinator import InstallationState, signal_installation_update
from .device import installation_device_info


class MySairInstallationEntity(AvailabilityMixin, Entity):
    """Entity on the installation device, fed by the coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, inst_ref: str, name: str | None, key: str):
        self.coordinator = coordinator
        self.inst_ref = inst_ref
        self._attr_unique_id = f"mysair_{key}_{inst_ref}"
        self._attr_device_info = installation_device_info(inst_ref, name)
        self._init_availability()

    async def async_added_to_hass(self):
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_installation_update(self.inst_ref),
                self._handle_installation_update,
            )
        )

    async def async_will_remove_from_hass(self):
        self._stop_availability()

    @property
    def installation(self) -> InstallationState:
        return self.coordinator.installation_state(self.inst_ref)

    @callback
    def _handle_installation_update(self, from_status=True):
        if from_status:
            self._mark_status_received()
        self.async_write_ha_state()


def installation_entities(hass, entry, classes):
    """One entity of each class per installation of the config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    names = data.get("installation_names", {})
    return [
        cls(data["coordinator"], inst_ref, names.get(inst_ref))
        for inst_ref in data["installations"]
        for cls in classes
    ]

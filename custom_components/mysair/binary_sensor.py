"""Binary sensors of an installation: running state and direction conflict."""

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory

from .installation_entity import MySairInstallationEntity, installation_entities

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the binary sensors of each installation."""
    entities = installation_entities(
        hass,
        entry,
        (
            MySairInstallationActiveSensor,
            MySairDirectionConflictSensor,
            MySairDirectionMismatchSensor,
        ),
    )
    async_add_entities(entities)
    _LOGGER.info(f"[MySair BinarySensor] {len(entities)} binary sensors created.")


class MySairInstallationActiveSensor(MySairInstallationEntity, BinarySensorEntity):
    """On while at least one zone of the installation is on."""

    _attr_translation_key = "installation_active"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator, inst_ref, name):
        super().__init__(coordinator, inst_ref, name, "installation_active")

    @property
    def is_on(self):
        return self.installation.is_on


class MySairDirectionConflictSensor(MySairInstallationEntity, BinarySensorEntity):
    """Problem: zones that are on report different heat/cool directions.

    The heat pumps can only deliver one direction. The integration and the
    official app prevent this, but another client or a change at a
    thermostat could still cause it (docs/protocol-findings.md §4b).
    """

    _attr_translation_key = "direction_conflict"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, inst_ref, name):
        super().__init__(coordinator, inst_ref, name, "direction_conflict")

    @property
    def is_on(self):
        return self.installation.has_conflict


class MySairDirectionMismatchSensor(MySairInstallationEntity, BinarySensorEntity):
    """Problem: the installation runs in another direction than the heat pump.

    Only relevant when "MySair schaltet Wärmepumpe" is off: the held
    direction ("Betriebsart") matches the heat pump, so a zone switched on in the other direction (e.g. at a
    wall thermostat or in the app) works against the heat pump.
    """

    _attr_translation_key = "direction_mismatch"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator, inst_ref, name):
        super().__init__(coordinator, inst_ref, name, "direction_mismatch")

    @property
    def is_on(self):
        return self.installation.direction_mismatch

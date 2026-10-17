"""Device information shared by all entities of a zone.

The 7 entities of a zone (climate, 4 sensors, 2 switches) belong to the same
device and used to repeat the same ``device_info`` block word for word.
Since the entities use ``has_entity_name``, the device name is no longer
decorative: Home Assistant prepends it to each entity name to build both the
visible name and the ``entity_id``, so it has to be the zone name ("Living
room") rather than an internal identifier.
"""

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntry, DeviceInfo

from .const import DOMAIN


def find_device(hass, entry_id: str, identifier: str) -> DeviceEntry | None:
    """Registered device of this config entry with the given identifier.

    Searches the devices of the entry instead of using the global lookup,
    which is deprecated because identifiers are only unique per entry.
    """
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry_id):
        if (DOMAIN, identifier) in device.identifiers:
            return device
    return None


def zone_identifier(inst_ref: str, device_id: str) -> str:
    """Device identifier of a zone (unchanged since the first versions)."""
    return f"{inst_ref}_{device_id}"


def installation_identifier(inst_ref: str) -> str:
    """Device identifier of an installation (controller).

    Prefixed so it can never collide with a zone (``{inst_ref}_{device_id}``)
    or the account device (``entry_id``).
    """
    return f"installation_{inst_ref}"


def installation_device_info(inst_ref: str, name: str | None) -> DeviceInfo:
    """Device of an installation (MySair controller).

    Hangs below the account device (cloud connection); the zones hang below
    it (links set in ``__init__._register_devices``). The software version
    (controller firmware) is filled in by the coordinator from the status
    messages.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, installation_identifier(inst_ref))},
        name=name or f"MySair {inst_ref}",
        manufacturer="MySair",
        model="Zonensteuerung",
    )


def zone_device_info(inst_ref: str, device_id: str, zone_name: str) -> DeviceInfo:
    """Device grouping all entities of a zone.

    ``identifiers`` is unchanged (``{(DOMAIN, f"{inst_ref}_{device_id}")}``):
    it is the key Home Assistant uses to recognise already registered
    devices, and also the one ``_cleanup_stale_zone_devices``
    (``__init__.py``) uses to detect zones that disappeared from the account.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, zone_identifier(inst_ref, device_id))},
        name=zone_name,
        manufacturer="MySair",
        model="Klima-Zonenregler",
    )


def account_device_info(entry_id: str) -> DeviceInfo:
    """Account-level device grouping everything that does not belong to a zone.

    Holds the cloud connection sensor (one MQTT connection per account); the
    installation devices hang below it. It is identified by
    ``entry_id`` instead of an installation reference, which is how
    ``_cleanup_stale_zone_devices`` recognises it and never removes it as an
    orphaned zone.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        name="MySair",
        manufacturer="MySair",
        model="Integration",
    )

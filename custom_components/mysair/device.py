"""Device information shared by all entities of a zone.

The 7 entities of a zone (climate, 4 sensors, 2 switches) belong to the same
device and used to repeat the same ``device_info`` block word for word.
Since the entities use ``has_entity_name``, the device name is no longer
decorative: Home Assistant prepends it to each entity name to build both the
visible name and the ``entity_id``, so it has to be the zone name ("Living
room") rather than an internal identifier.
"""

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


def zone_device_info(inst_ref: str, device_id: str, zone_name: str) -> DeviceInfo:
    """Device grouping all entities of a zone.

    ``identifiers`` is unchanged (``{(DOMAIN, f"{inst_ref}_{device_id}")}``):
    it is the key Home Assistant uses to recognise already registered
    devices, and also the one ``_cleanup_stale_zone_devices``
    (``__init__.py``) uses to detect zones that disappeared from the account.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, f"{inst_ref}_{device_id}")},
        name=zone_name,
        manufacturer="MySair",
        model="Klima-Zonenregler",
    )


def account_device_info(entry_id: str) -> DeviceInfo:
    """Account-level device grouping everything that does not belong to a zone.

    Currently only the cloud connection sensor. It is identified by
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

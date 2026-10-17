"""Rules for switching zones on, off and between media, as the official app does.

MySair has one heat/cool direction per installation (the heat pumps can only
deliver one), and the official app protects it on the client side
(docs/protocol-findings.md §4):

- While at least one zone is on, zones can only be turned on in the current
  direction of the installation. Changing the direction requires turning all
  zones off first.
- Turning a zone on turns its air on, keeps its floor setting and uses the
  direction of the installation (``togglePowerDevice``).
- Switching the floor keeps the air state; if neither air nor floor stays
  active, the zone is turned off (``toggleRadiatingFloor``).

In addition the options decide which media (air, floor) may be used for
heating and for cooling, and each zone can choose the medium it is turned on
with (select entities "Heizen mit"/"Kühlen mit"). The floor is not used for
cooling unless the user enabled it, because floor cooling needs dew-point
protection that MySair does not report.

The functions here only decide which command to send; the entities send it.
"""

import logging

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import DOMAIN, MEDIA_PERMISSION_DEFAULTS, MEDIA_PERMISSION_KEYS
from .status_parser import (
    compute_mode_value,
    floor_change_command,
    media_options,
    select_media,
)

_LOGGER = logging.getLogger(__name__)


def control_error(translation_key: str) -> ServiceValidationError:
    """Translated error shown to the user in the Home Assistant UI."""
    return ServiceValidationError(
        translation_domain=DOMAIN, translation_key=translation_key
    )


def command_error(err: Exception) -> HomeAssistantError:
    """Translated error for a command that could not be sent to MySair.

    Shown in the Home Assistant UI instead of failing silently; the technical
    cause stays in the log.
    """
    return HomeAssistantError(
        translation_domain=DOMAIN, translation_key="command_failed"
    )


def media_permissions(entry, is_heat: bool) -> tuple[bool, bool]:
    """Whether air and floor may be used in a direction (options of the entry)."""
    air_key, floor_key = MEDIA_PERMISSION_KEYS[bool(is_heat)]
    options = entry.options
    return (
        bool(options.get(air_key, MEDIA_PERMISSION_DEFAULTS[air_key])),
        bool(options.get(floor_key, MEDIA_PERMISSION_DEFAULTS[floor_key])),
    )


def floor_cooling_allowed(entry) -> bool:
    """Whether the user allowed the floor to be used for cooling."""
    return media_permissions(entry, False)[1]


def zone_media_options(entry, zone: dict, is_heat: bool) -> list[str]:
    """Media a zone can be turned on with in a direction (empty: not possible)."""
    allow_air, allow_floor = media_permissions(entry, is_heat)
    return media_options(allow_air, allow_floor and bool(zone.get("allow_floor")))


def _direction_possible(entry, zone: dict, is_heat: bool) -> bool:
    """Zone capability (c/f) and at least one allowed medium."""
    capable = zone.get("allow_heat") if is_heat else zone.get("allow_cool")
    return bool(capable) and bool(zone_media_options(entry, zone, is_heat))


def allowed_directions(coordinator, entry, ctl: str, zone: dict) -> list[bool]:
    """Directions (True heat, False cool) a zone may currently be turned on in.

    Combines the capabilities of the zone (``c``/``f``), the media allowed
    in the options and the direction lock of the installation. A zone that
    is already on always keeps its own direction in the list, so its current
    state stays valid.
    """
    capable = [d for d in (True, False) if _direction_possible(entry, zone, d)]

    installation = coordinator.installation_state(ctl)
    allowed = capable
    if installation.is_on and installation.running_is_heat is not None:
        allowed = [d for d in allowed if d == installation.running_is_heat]
    if installation.held_is_heat is not None:
        allowed = [d for d in allowed if d == installation.held_is_heat]

    own = zone.get("is_heat")
    if zone.get("is_on") and own is not None and own not in allowed:
        allowed.append(bool(own))
    return allowed


def turn_on_mode(coordinator, entry, ctl: str, zone_id: str, is_heat=None) -> str:
    """``m`` value for turning a zone on.

    ``is_heat=None`` means "like the app's power button": use the direction
    of the installation. An explicit direction is only accepted if it does
    not contradict a running installation. Air and floor follow the medium
    chosen for the zone and direction, within what the options allow.
    """
    zone = coordinator.zone(ctl, zone_id) or {}
    installation = coordinator.installation_state(ctl)

    if is_heat is None:
        is_heat = installation.is_heat
        if is_heat is None:
            raise control_error("direction_unknown")
        # Fall back only on the zone's capability (c/f), never on the media
        # options: a zone must not start cooling because air heating is off.
        possible = [
            d
            for d, ok in (
                (True, zone.get("allow_heat")),
                (False, zone.get("allow_cool")),
            )
            if ok
        ]
        if possible and is_heat not in possible:
            # The zone cannot run in the installation direction. While all
            # zones are off the direction is still free (app: season change),
            # unless it is held to match the heat pump.
            if installation.is_on or installation.held_is_heat is not None:
                raise control_error("direction_locked")
            is_heat = possible[0]
    if installation.held_is_heat is not None and is_heat != installation.held_is_heat:
        raise control_error("direction_held")
    if installation.is_on and installation.running_is_heat not in (None, is_heat):
        raise control_error("direction_locked")

    allow_air, allow_floor = media_permissions(entry, is_heat)
    media = select_media(
        coordinator.medium_choice(ctl, zone_id, is_heat),
        allow_air,
        allow_floor and bool(zone.get("allow_floor")),
        bool(zone.get("is_floor")),
    )
    if media is None:
        raise control_error("no_medium_allowed")
    air, floor = media
    return compute_mode_value(is_heat, air, floor)


def floor_command(coordinator, entry, ctl: str, zone_id: str, floor_on: bool):
    """Command for switching the floor of a zone: ``("mode", m)`` or ``("power", None)``."""
    zone = coordinator.zone(ctl, zone_id) or {}
    installation = coordinator.installation_state(ctl)
    # A running installation keeps its direction; otherwise the one HA uses.
    is_heat = (
        installation.running_is_heat if installation.is_on else installation.is_heat
    )
    if is_heat is None:
        raise control_error("direction_unknown")
    if installation.held_is_heat is not None and is_heat != installation.held_is_heat:
        raise control_error("direction_held")
    allow_air, allow_floor = media_permissions(entry, is_heat)
    if floor_on and not allow_floor:
        raise control_error(
            "floor_heating_disabled" if is_heat else "floor_cooling_disabled"
        )
    # Air only stays on if it is allowed in this direction.
    zone_is_ac = bool(zone.get("is_ac")) and allow_air
    return floor_change_command(is_heat, zone.get("is_on"), zone_is_ac, floor_on)


def choose_direction(coordinator, ctl: str, is_heat: bool) -> None:
    """Choose heat/cool for the whole installation (app: season change).

    Only possible while all zones are off; the next zone that is turned on
    starts in this direction. If the direction is held (to match a heat pump
    that MySair does not switch), it can be changed at any time.
    """
    if not coordinator.choose_direction(ctl, is_heat):
        raise control_error("direction_locked")


async def async_stop_installation(hass, api, coordinator, ctl: str) -> None:
    """Stop all zones of an installation with one command.

    Like the official app, the command goes to the main zone (pl="1"); the
    device stays empty if no status has arrived yet.
    """
    main_zone = (
        coordinator.installation_state(ctl).main_zone_id if coordinator else None
    )
    try:
        await hass.async_add_executor_job(
            api.send_installation_command, ctl, "stop", None, main_zone or ""
        )
    except Exception as err:
        _LOGGER.error(f"[MySair] Failed to stop installation {ctl}: {err}")
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="stop_failed"
        ) from err

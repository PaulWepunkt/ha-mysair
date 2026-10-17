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

In addition the integration refuses to use the floor for cooling unless the
user enabled it in the options, because floor cooling needs dew-point
protection that MySair does not report.

The functions here only decide which command to send; the entities send it.
"""

import logging

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import CONF_ALLOW_FLOOR_COOLING, DOMAIN
from .status_parser import floor_change_command, turn_on_mode_value

_LOGGER = logging.getLogger(__name__)


def control_error(translation_key: str) -> ServiceValidationError:
    """Translated error shown to the user in the Home Assistant UI."""
    return ServiceValidationError(
        translation_domain=DOMAIN, translation_key=translation_key
    )


def floor_cooling_allowed(entry) -> bool:
    """Whether the user allowed the floor to be used for cooling."""
    return bool(entry.options.get(CONF_ALLOW_FLOOR_COOLING, False))


def allowed_directions(coordinator, ctl: str, zone: dict) -> list[bool]:
    """Directions (True heat, False cool) a zone may currently be turned on in.

    Combines the capabilities of the zone (``c``/``f``) with the direction
    lock of the installation. A zone that is already on always keeps its own
    direction in the list, so its current state stays valid.
    """
    capable = []
    if zone.get("allow_heat"):
        capable.append(True)
    if zone.get("allow_cool"):
        capable.append(False)

    installation = coordinator.installation_state(ctl)
    if installation.is_on and installation.is_heat is not None:
        allowed = [d for d in capable if d == installation.is_heat]
    else:
        allowed = capable

    own = zone.get("is_heat")
    if zone.get("is_on") and own is not None and own not in allowed:
        allowed.append(bool(own))
    return allowed


def turn_on_mode(coordinator, entry, ctl: str, zone_id: str, is_heat=None) -> str:
    """``m`` value for turning a zone on.

    ``is_heat=None`` means "like the app's power button": use the direction
    of the installation. An explicit direction is only accepted if it does
    not contradict a running installation.
    """
    zone = coordinator.zone(ctl, zone_id) or {}
    installation = coordinator.installation_state(ctl)

    if is_heat is None:
        is_heat = installation.is_heat
        if is_heat is None:
            raise control_error("direction_unknown")
        capable = [
            d
            for d, ok in (
                (True, zone.get("allow_heat")),
                (False, zone.get("allow_cool")),
            )
            if ok
        ]
        if capable and is_heat not in capable:
            # The zone cannot run in the installation direction. While all
            # zones are off the direction is still free (app: season change).
            if installation.is_on:
                raise control_error("direction_locked")
            is_heat = capable[0]
    elif installation.is_on and installation.is_heat not in (None, is_heat):
        raise control_error("direction_locked")

    keep_floor = bool(zone.get("is_floor"))
    if keep_floor and not is_heat and not floor_cooling_allowed(entry):
        keep_floor = False
    return turn_on_mode_value(is_heat, keep_floor)


def floor_command(coordinator, entry, ctl: str, zone_id: str, floor_on: bool):
    """Command for switching the floor of a zone: ``("mode", m)`` or ``("power", None)``."""
    zone = coordinator.zone(ctl, zone_id) or {}
    is_heat = coordinator.installation_state(ctl).is_heat
    if is_heat is None:
        raise control_error("direction_unknown")
    if floor_on and not is_heat and not floor_cooling_allowed(entry):
        raise control_error("floor_cooling_disabled")
    return floor_change_command(is_heat, zone.get("is_on"), zone.get("is_ac"), floor_on)


def choose_direction(coordinator, ctl: str, is_heat: bool) -> None:
    """Choose heat/cool for the whole installation (app: season change).

    Only possible while all zones are off; the next zone that is turned on
    starts in this direction.
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

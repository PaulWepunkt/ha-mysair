"""Pure parsers for MySair MQTT messages.

This module deliberately does NOT depend on Home Assistant, so the state
parsing logic can be imported and tested in isolation (see
docs/testing-strategy.md).
"""

import json
import logging

_LOGGER = logging.getLogger(__name__)


def parse_status_value(raw_value):
    """Decode the ``value`` field of a ``status`` message.

    The backend sends ``value`` as a string containing nested JSON and
    sometimes a trailing ``;``. Always returns a ``dict``:
      - ``raw_value`` is a string with valid JSON → the decoded object;
      - it is already a ``dict`` → returned as is;
      - anything else (invalid JSON, unexpected type) → ``{}``.
    """
    if isinstance(raw_value, dict):
        return raw_value

    if not isinstance(raw_value, str):
        return {}

    cleaned = raw_value.strip()
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1]

    try:
        parsed = json.loads(cleaned)
    except (ValueError, TypeError) as err:
        _LOGGER.warning(
            "[MySair MQTT] Failed to decode nested JSON: %s -> %s...",
            err,
            raw_value[:120],
        )
        return {}

    return parsed if isinstance(parsed, dict) else {}


def _to_float(value):
    """Convert to float; return None if missing or not convertible."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_str(value):
    return None if value is None else str(value)


# Humidity fields in order of preference. The official web app displays
# ``hm`` (``getHumidity(){return this.hm}``). The zone JSON can carry both
# fields, with ``hum`` reporting "0" while ``hm`` holds the measured value
# shown in the app. ``hum`` is kept as a fallback for backend variants that
# might only send that field.
_HUMIDITY_FIELDS = ("hm", "hum")


def parse_humidity(zone):
    """Return the relative humidity of a raw zone dict, or ``None``.

    Takes the first field from ``_HUMIDITY_FIELDS`` holding a positive number.
    A value of 0 is treated as "unknown" rather than 0 %: a real room never
    reads 0 % relative humidity, so a zero means the thermostat has no
    humidity sensor or has not measured yet. Reporting ``None`` keeps false
    zeros out of long-term statistics and humidity-based automations.
    """
    for field in _HUMIDITY_FIELDS:
        value = _to_float(zone.get(field))
        if value is not None and value > 0:
            return value
    return None


def parse_mode(m):
    """Interpret the ``m`` field (mode, 0-5) of the status payload.

    Semantics CONFIRMED from the official app (see docs/protocol-findings.md):
      - even = heat, odd = cool
      - air (AC) active if m ∈ {0,1,4,5}; floor heating active if m ∈ {2,3,4,5}

    Returns the tuple (mode_raw, is_heat, is_cool, is_ac, is_floor); all
    booleans are None if ``m`` is missing or not numeric.
    """
    if m is None:
        return None, None, None, None, None
    mode_raw = str(m)
    try:
        n = int(mode_raw)
    except ValueError:
        return mode_raw, None, None, None, None
    is_heat = n % 2 == 0
    return mode_raw, is_heat, not is_heat, n in (0, 1, 4, 5), n in (2, 3, 4, 5)


def compute_mode_value(is_heat, is_ac, is_floor):
    """Compute the ``m`` value to send with the ``mode`` command for the
    desired state (inverse of ``parse_mode``, F4 - floor heating control).

    Confirmed from the official app (`docs/protocol-findings.md` §4):
    ``base``: air=0, floor=2, air+floor=4 (heat); ``+1`` for cool.

    Neither air nor floor active is not a valid ``m``: the app turns the zone
    off instead (see ``floor_change_command``). As a safety net air is forced
    on here so that no ``m`` value unseen in the bundle is ever sent.
    """
    if not is_ac and not is_floor:
        is_ac = True
    base = 4 if (is_ac and is_floor) else (2 if is_floor else 0)
    return str(base if is_heat else base + 1)


def installation_is_heat(zones):
    """Heat/cool direction of a whole installation, as the official app sees it.

    The app does not store a direction per zone: every zone shows the
    direction of the installation, which is the parity of ``m`` of the
    first zone that is on, or of the first zone if all are off
    (docs/protocol-findings.md §4). ``zones`` must be in the order the
    controller reports them.

    Returns True for heat, False for cool, None if no zone has a valid ``m``.
    """
    first = None
    for zone in zones:
        if zone.get("is_heat") is None:
            continue
        if zone.get("is_on"):
            return bool(zone["is_heat"])
        if first is None:
            first = zone
    return bool(first["is_heat"]) if first is not None else None


def turn_on_mode_value(is_heat, keep_floor):
    """``m`` to send when a zone is turned on (app: ``togglePowerDevice``).

    The app turns the air on, keeps the floor bit the zone already had and
    uses the direction of the installation.
    """
    return compute_mode_value(is_heat, True, keep_floor)


def floor_change_command(is_heat, zone_is_on, zone_is_ac, floor_on):
    """Command for switching the floor medium of a zone (app: ``toggleRadiatingFloor``).

    The air keeps its state (it only counts as active while the zone is on).
    If neither air nor floor stays active, the app turns the zone off.

    Returns ``("mode", m)`` or ``("power", None)``.
    """
    air_on = bool(zone_is_on and zone_is_ac)
    if not air_on and not floor_on:
        return "power", None
    return "mode", compute_mode_value(is_heat, air_on, floor_on)


def parse_status_payload(payload):
    """Normalise the payload of a ``status`` message to ``{"ctl", "zones"}``.

    Zone field semantics (CONFIRMED from the official app, see
    docs/protocol-findings.md):
      - ``e``  → POWER: "0"=off, "1"=on, "2"=standby  (NOT the mode)
      - ``m``  → MODE 0-5: even=heat, odd=cool; {0,1,4,5}=air, {2,3,4,5}=floor
      - ``tr``/``tc``/``tmm``/``tmx`` → current/target/min/max temperature
      - humidity: ``hm`` first, ``hum`` as fallback, 0 = unknown
        (see ``parse_humidity``)
      - capabilities: ``c``=heat allowed, ``f``=cool allowed, ``v``=fan,
        ``s``=floor heating

    Each zone in ``zones`` contains: ``ctl``, ``zone_id``, ``zone_name``,
    ``temp_actual``, ``temp_target``, ``temp_min``, ``temp_max``, ``humidity``,
    ``power`` (raw e), ``is_on``, ``is_standby``, ``mode_raw``, ``is_heat``,
    ``is_cool``, ``is_ac``, ``is_floor``, ``fan_mode`` and ``allow_*`` flags.

    Validation (E2/E4): a ``payload`` that is not even a ``dict`` is
    **rejected** by returning ``None`` (instead of an "empty" dict that does
    nothing downstream anyway). Other unexpected shapes (missing ``ctl``,
    ``t`` that is not a list, a zone without ``rf``) degrade safely, but are
    **logged** so they can be debugged. Unknown extra keys in the payload are
    deliberately not rejected (tolerant of new backend fields, see
    `docs/known-unknowns.md`).
    """
    if not isinstance(payload, dict):
        _LOGGER.warning(
            "[MySair] status: payload is not a dict (%s), message rejected",
            type(payload).__name__,
        )
        return None

    ctl_ref = payload.get("ctl")
    if ctl_ref is None:
        _LOGGER.warning(
            "[MySair] status: payload without 'ctl', message has no recipient"
        )
    parsed_value = parse_status_value(payload.get("value", ""))

    t_list = parsed_value.get("t", [])
    if not isinstance(t_list, list):
        _LOGGER.warning(
            "[MySair] status: field 't' has an unexpected shape (%s), ignored",
            type(t_list).__name__,
        )
        t_list = []

    zone_states = []
    for t in t_list:
        if not isinstance(t, dict):
            continue
        if t.get("rf") is None:
            _LOGGER.warning(
                "[MySair] status: zone without 'rf' (zone_id), created without identifier"
            )
        power = _to_str(t.get("e", "0"))
        mode_raw, is_heat, is_cool, is_ac, is_floor = parse_mode(t.get("m"))
        zone_states.append(
            {
                "ctl": ctl_ref,
                "zone_id": t.get("rf"),
                "zone_name": t.get("n"),
                "temp_actual": _to_float(t.get("tr")),
                "temp_target": _to_float(t.get("tc")),
                "temp_min": _to_float(t.get("tmm")),
                "temp_max": _to_float(t.get("tmx")),
                "humidity": parse_humidity(t),
                "power": power,  # raw e: "0"/"1"/"2"
                "is_on": power != "0",  # standby ("2") also counts as on
                "is_standby": power == "2",
                "mode_raw": mode_raw,  # raw m: "0".."5"
                "is_heat": is_heat,
                "is_cool": is_cool,
                "is_ac": is_ac,
                "is_floor": is_floor,
                "fan_mode": _to_str(t.get("vv")),
                "allow_heat": _to_str(t.get("c")) == "1",
                "allow_cool": _to_str(t.get("f")) == "1",
                "allow_fan": _to_str(t.get("v")) == "1",
                "allow_floor": _to_str(t.get("s")) == "1",
                # Main zone of the installation (pl="1"): the official app
                # sends the "stop" command with this zone as the device.
                "is_main": _to_str(t.get("pl")) == "1",
            }
        )

    return {"ctl": ctl_ref, "zones": zone_states}


def parse_feedback_payload(payload):
    """Normalise the payload of the ``.../usr/{aws_mqtt_user}/feedback`` topic.

    Confirmed from the official app (see docs/protocol-findings.md §8): the
    ACK of an instruction arrives as a flat ``{orderId, ctl, ...}`` on the
    same object delivered by the MQTT wrapper (same shape as ``payload`` in
    ``mqtt_handler._on_message``). As a defensive fallback, the nested
    ``status`` shape (``value`` as a JSON string) is tried as well, in case
    the backend wraps this topic the same way (`known-unknowns` #23).

    Returns ``{"order_id", "ctl", "raw"}``; ``order_id``/``ctl`` are ``None``
    if found in neither shape.

    Validation (E4): a ``payload`` that is not a ``dict`` is rejected by
    returning ``None``. If it is a dict but neither ``orderId`` nor ``ctl``
    is found in either known shape, this is logged, and the dict with
    ``None`` values is still returned, since the general message shape was
    recognised.
    """
    if not isinstance(payload, dict):
        _LOGGER.warning(
            "[MySair] feedback: payload is not a dict (%s), message rejected",
            type(payload).__name__,
        )
        return None

    order_id = payload.get("orderId")
    ctl = payload.get("ctl")

    if order_id is None or ctl is None:
        nested = parse_status_value(payload.get("value", ""))
        if order_id is None:
            order_id = nested.get("orderId")
        if ctl is None:
            ctl = nested.get("ctl", payload.get("ctl"))

    if order_id is None and ctl is None:
        _LOGGER.warning(
            "[MySair] feedback: neither 'orderId' nor 'ctl' found (flat or nested)"
        )

    return {"order_id": order_id, "ctl": ctl, "raw": payload}

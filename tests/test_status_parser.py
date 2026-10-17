"""P0 tests of the pure 'status' message parser (no Home Assistant).

Semantics confirmed from the official app (docs/protocol-findings.md):
  - e = power: "0"=off, "1"=on, "2"=standby
  - m = mode 0-5: even=heat, odd=cool; {0,1,4,5}=air, {2,3,4,5}=floor
"""

import pytest

from status_parser import (
    compute_mode_value,
    floor_change_command,
    has_direction_conflict,
    media_options,
    select_media,
    installation_is_heat,
    turn_on_mode_value,
    parse_mode,
    parse_status_payload,
    parse_status_value,
    parse_feedback_payload,
)


# --- parse_status_value ---


def test_parse_status_value_string_with_trailing_semicolon():
    assert parse_status_value('{"a":1};') == {"a": 1}


def test_parse_status_value_plain_string_json():
    assert parse_status_value('{"a":1}') == {"a": 1}


def test_parse_status_value_dict_passthrough():
    assert parse_status_value({"a": 1}) == {"a": 1}


def test_parse_status_value_invalid_json_returns_empty():
    assert parse_status_value("no-json") == {}


def test_parse_status_value_non_str_non_dict_returns_empty():
    assert parse_status_value(None) == {}
    assert parse_status_value(123) == {}
    assert parse_status_value(["x"]) == {}


def test_parse_status_value_json_non_object_returns_empty():
    assert parse_status_value("[1,2,3]") == {}


# --- parse_mode ---


@pytest.mark.parametrize(
    "m,expected",
    [
        ("0", ("0", True, False, True, False)),  # air heat
        ("1", ("1", False, True, True, False)),  # air cool
        ("2", ("2", True, False, False, True)),  # floor heat
        ("3", ("3", False, True, False, True)),  # floor cool
        ("4", ("4", True, False, True, True)),  # air+floor heat
        ("5", ("5", False, True, True, True)),  # air+floor cool
        (0, ("0", True, False, True, False)),  # int is valid too
    ],
)
def test_parse_mode_values(m, expected):
    assert parse_mode(m) == expected


def test_parse_mode_none():
    assert parse_mode(None) == (None, None, None, None, None)


def test_parse_mode_non_numeric():
    assert parse_mode("x") == ("x", None, None, None, None)


# --- compute_mode_value (F4, inversa de parse_mode) ---


@pytest.mark.parametrize(
    "is_heat,is_ac,is_floor,expected",
    [
        (True, True, False, "0"),  # air heat
        (False, True, False, "1"),  # air cool
        (True, False, True, "2"),  # floor heat
        (False, False, True, "3"),  # floor cool
        (True, True, True, "4"),  # air+floor heat
        (False, True, True, "5"),  # air+floor cool
    ],
)
def test_compute_mode_value_matches_confirmed_table(is_heat, is_ac, is_floor, expected):
    assert compute_mode_value(is_heat, is_ac, is_floor) == expected


def test_compute_mode_value_roundtrips_with_parse_mode():
    for m in ("0", "1", "2", "3", "4", "5"):
        _, is_heat, _, is_ac, is_floor = parse_mode(m)
        assert compute_mode_value(is_heat, is_ac, is_floor) == m


def test_compute_mode_value_forces_ac_when_neither_medium_active():
    # Combination the app never produces: air is forced on instead of
    # sending an 'm' value never seen in the bundle.
    assert compute_mode_value(is_heat=True, is_ac=False, is_floor=False) == "0"


# --- Installation direction and app rules (docs/protocol-findings.md §4) ---


def _z(is_on, is_heat):
    return {"is_on": is_on, "is_heat": is_heat}


def test_installation_is_heat_first_running_zone_wins():
    assert installation_is_heat([_z(False, True), _z(True, False)]) is False


def test_installation_is_heat_all_off_uses_first_zone():
    assert installation_is_heat([_z(False, False), _z(False, True)]) is False


def test_installation_is_heat_skips_zones_without_valid_mode():
    assert installation_is_heat([_z(True, None), _z(False, True)]) is True


def test_installation_is_heat_unknown():
    assert installation_is_heat([]) is None
    assert installation_is_heat([_z(False, None)]) is None


@pytest.mark.parametrize(
    "is_heat, keep_floor, expected",
    [(True, False, "0"), (False, False, "1"), (True, True, "4"), (False, True, "5")],
)
def test_turn_on_mode_value_turns_air_on_and_keeps_floor(is_heat, keep_floor, expected):
    assert turn_on_mode_value(is_heat, keep_floor) == expected


@pytest.mark.parametrize(
    "is_heat, zone_on, is_ac, floor_on, expected",
    [
        (True, True, True, True, ("mode", "4")),  # air on → air+floor
        (False, True, True, True, ("mode", "5")),
        (True, True, False, True, ("mode", "2")),  # floor only
        (True, False, True, True, ("mode", "2")),  # zone off: air counts as off
        (True, True, True, False, ("mode", "0")),  # air stays on
        (True, True, False, False, ("power", None)),  # nothing left → off
        (True, False, True, False, ("power", None)),
    ],
)
def test_floor_change_command(is_heat, zone_on, is_ac, floor_on, expected):
    assert floor_change_command(is_heat, zone_on, is_ac, floor_on) == expected


# --- parse_status_payload ---


def test_parse_status_payload_full(status_payload):
    result = parse_status_payload(status_payload)
    assert result["ctl"] == "INST_A"
    assert len(result["zones"]) == 1
    zone = result["zones"][0]
    assert zone == {
        "ctl": "INST_A",
        "zone_id": "DEV_1",
        "zone_name": "Salon",
        "temp_actual": 22.5,
        "temp_target": 21.0,
        "temp_min": 10.0,
        "temp_max": 30.0,
        "humidity": 45.0,
        "power": "1",
        "is_on": True,
        "is_standby": False,
        "mode_raw": "0",
        "is_heat": True,
        "is_cool": False,
        "is_ac": True,
        "is_floor": False,
        "fan_mode": "0",
        "allow_heat": True,
        "allow_cool": True,
        "allow_fan": False,
        "allow_floor": False,
        "is_main": False,
    }


@pytest.mark.parametrize(
    "e,is_on,is_standby",
    [("0", False, False), ("1", True, False), ("2", True, True)],
)
def test_parse_status_payload_power_field(e, is_on, is_standby):
    payload = {"ctl": "X", "value": '{"t":[{"rf":"D","e":"%s","m":"0"}]}' % e}
    zone = parse_status_payload(payload)["zones"][0]
    assert zone["is_on"] is is_on
    assert zone["is_standby"] is is_standby


def test_parse_status_payload_cool_mode():
    # m=1 (air cool), on
    payload = {"ctl": "X", "value": '{"t":[{"rf":"D","e":"1","m":"1"}]}'}
    zone = parse_status_payload(payload)["zones"][0]
    assert zone["is_on"] is True
    assert zone["is_heat"] is False
    assert zone["is_cool"] is True


def test_parse_status_payload_off_keeps_mode_field():
    # off (e="0") but m still present: is_on False, m still interpretable
    payload = {"ctl": "X", "value": '{"t":[{"rf":"D","e":"0","m":"1"}]}'}
    zone = parse_status_payload(payload)["zones"][0]
    assert zone["is_on"] is False
    assert zone["mode_raw"] == "1"
    assert zone["is_cool"] is True


def test_parse_status_payload_missing_mode_is_none():
    payload = {"ctl": "X", "value": '{"t":[{"rf":"D","e":"1"}]}'}
    zone = parse_status_payload(payload)["zones"][0]
    assert zone["is_on"] is True
    assert zone["mode_raw"] is None
    assert zone["is_heat"] is None
    assert zone["is_cool"] is None


def test_parse_status_payload_missing_temps_are_none():
    payload = {"ctl": "X", "value": '{"t":[{"rf":"D","e":"1","m":"0"}]}'}
    zone = parse_status_payload(payload)["zones"][0]
    assert zone["temp_actual"] is None
    assert zone["temp_target"] is None
    assert zone["humidity"] is None


def test_parse_status_payload_humidity_prefers_hm_over_zero_hum():
    # Both fields present: "hum" reports "0", "hm" carries the measured
    # value shown in the official app.
    payload = {
        "ctl": "X",
        "value": '{"t":[{"rf":"D","e":"0","m":"1","hum":"0","hm":"58"}]}',
    }
    zone = parse_status_payload(payload)["zones"][0]
    assert zone["humidity"] == 58.0


def test_parse_status_payload_humidity_prefers_hm_when_both_non_zero():
    payload = {
        "ctl": "X",
        "value": '{"t":[{"rf":"D","e":"1","m":"0","hum":"55","hm":"60"}]}',
    }
    zone = parse_status_payload(payload)["zones"][0]
    assert zone["humidity"] == 60.0


def test_parse_status_payload_humidity_reads_hm_field():
    payload = {"ctl": "X", "value": '{"t":[{"rf":"D","e":"1","m":"0","hm":"40"}]}'}
    zone = parse_status_payload(payload)["zones"][0]
    assert zone["humidity"] == 40.0


def test_parse_status_payload_humidity_falls_back_to_hum_field():
    # Fallback for backend variants that might only send "hum".
    payload = {"ctl": "X", "value": '{"t":[{"rf":"D","e":"1","m":"0","hum":"55"}]}'}
    zone = parse_status_payload(payload)["zones"][0]
    assert zone["humidity"] == 55.0


def test_parse_status_payload_humidity_zero_is_unknown():
    # 0 % is not a real room humidity: no sensor / not measured -> None.
    payload = {
        "ctl": "X",
        "value": '{"t":[{"rf":"D","e":"1","m":"0","hum":"0","hm":"0"}]}',
    }
    zone = parse_status_payload(payload)["zones"][0]
    assert zone["humidity"] is None


def test_parse_status_payload_humidity_zero_hm_falls_back_to_hum():
    payload = {
        "ctl": "X",
        "value": '{"t":[{"rf":"D","e":"1","m":"0","hm":"0","hum":"52"}]}',
    }
    zone = parse_status_payload(payload)["zones"][0]
    assert zone["humidity"] == 52.0


def test_parse_status_payload_humidity_invalid_is_unknown():
    payload = {"ctl": "X", "value": '{"t":[{"rf":"D","e":"1","m":"0","hm":""}]}'}
    zone = parse_status_payload(payload)["zones"][0]
    assert zone["humidity"] is None


def test_parse_status_payload_default_power_off_when_missing_e():
    payload = {"ctl": "X", "value": '{"t":[{"rf":"D"}]}'}
    zone = parse_status_payload(payload)["zones"][0]
    assert zone["power"] == "0"
    assert zone["is_on"] is False


def test_parse_status_payload_invalid_value_yields_no_zones():
    assert parse_status_payload({"ctl": "X", "value": "no-json"}) == {
        "ctl": "X",
        "firmware": None,
        "zones": [],
    }


def test_parse_status_payload_missing_value():
    assert parse_status_payload({"ctl": "Z"}) == {
        "ctl": "Z",
        "firmware": None,
        "zones": [],
    }


def test_parse_status_payload_non_dict_input():
    # E4: a payload that is not even a dict is rejected (None) instead of
    # returning an "empty" dict that does nothing downstream anyway.
    assert parse_status_payload(None) is None


def test_parse_status_payload_non_dict_input_logs_warning(caplog):
    caplog.set_level("WARNING")
    parse_status_payload(None)
    assert "is not a dict" in caplog.text


def test_parse_status_payload_t_not_a_list_logs_warning_and_yields_no_zones(caplog):
    caplog.set_level("WARNING")
    payload = {"ctl": "X", "value": '{"t": 42}'}

    result = parse_status_payload(payload)

    assert result == {"ctl": "X", "firmware": None, "zones": []}
    assert "field 't' has an unexpected shape" in caplog.text


def test_parse_status_payload_missing_ctl_logs_warning(caplog):
    caplog.set_level("WARNING")
    payload = {"value": '{"t":[{"rf":"D1","e":"1","m":"0"}]}'}

    result = parse_status_payload(payload)

    assert result["ctl"] is None
    assert "without 'ctl'" in caplog.text


def test_parse_status_payload_zone_missing_rf_logs_warning(caplog):
    caplog.set_level("WARNING")
    payload = {"ctl": "X", "value": '{"t":[{"e":"1","m":"0"}]}'}

    zones = parse_status_payload(payload)["zones"]

    assert len(zones) == 1
    assert zones[0]["zone_id"] is None
    assert "zone without 'rf'" in caplog.text


def test_parse_status_payload_skips_non_dict_thermostats():
    payload = {"ctl": "X", "value": '{"t":["bogus",{"rf":"D1","e":"1","m":"0"}]}'}
    zones = parse_status_payload(payload)["zones"]
    assert len(zones) == 1
    assert zones[0]["zone_id"] == "D1"


# --- parse_feedback_payload (topic .../feedback, known-unknowns #23) ---


def test_parse_feedback_payload_flat_form():
    payload = {"orderId": "abc-123", "ctl": "INST_A"}
    assert parse_feedback_payload(payload) == {
        "order_id": "abc-123",
        "ctl": "INST_A",
        "raw": payload,
    }


def test_parse_feedback_payload_nested_value_fallback():
    payload = {"ctl": "INST_A", "value": '{"orderId":"abc-123","ctl":"INST_A"};'}
    result = parse_feedback_payload(payload)
    assert result["order_id"] == "abc-123"
    assert result["ctl"] == "INST_A"


def test_parse_feedback_payload_missing_fields():
    result = parse_feedback_payload({"foo": "bar"})
    assert result["order_id"] is None
    assert result["ctl"] is None


def test_parse_feedback_payload_non_dict_input():
    # E4: rejected (None) instead of a dict with raw preserved.
    assert parse_feedback_payload("not-a-dict") is None


def test_parse_feedback_payload_non_dict_input_logs_warning(caplog):
    caplog.set_level("WARNING")
    parse_feedback_payload("not-a-dict")
    assert "is not a dict" in caplog.text


def test_parse_feedback_payload_missing_order_id_and_ctl_logs_warning(caplog):
    caplog.set_level("WARNING")
    result = parse_feedback_payload({"unrelated": "field"})

    assert result == {"order_id": None, "ctl": None, "raw": {"unrelated": "field"}}
    assert "neither 'orderId' nor 'ctl' found" in caplog.text


def test_parse_status_payload_main_zone():
    payload = {
        "ctl": "INST_A",
        "value": {"t": [{"rf": "DEV_1", "pl": "0"}, {"rf": "DEV_2", "pl": "1"}]},
    }
    zones = parse_status_payload(payload)["zones"]
    assert [z["is_main"] for z in zones] == [False, True]


def test_parse_status_payload_firmware():
    payload = {"ctl": "INST_A", "value": {"v": "1.2.3", "t": []}}
    assert parse_status_payload(payload)["firmware"] == "1.2.3"


def test_has_direction_conflict():
    assert has_direction_conflict([_z(True, True), _z(True, False)]) is True
    # Zones that are off do not count.
    assert has_direction_conflict([_z(True, True), _z(False, False)]) is False
    assert has_direction_conflict([_z(True, None), _z(True, True)]) is False
    assert has_direction_conflict([]) is False


# --- Medium per zone and direction (options + selects) ---


def test_media_options():
    assert media_options(True, True) == ["app", "air", "floor", "air_and_floor"]
    assert media_options(True, False) == ["app", "air"]
    assert media_options(False, True) == ["app", "floor"]
    assert media_options(False, False) == []


@pytest.mark.parametrize(
    "choice, allow_air, allow_floor, last_floor, expected",
    [
        # Like the app: air on, floor as before.
        ("app", True, True, False, (True, False)),
        ("app", True, True, True, (True, True)),
        # Air not allowed: the floor takes over.
        ("app", False, True, False, (False, True)),
        # Floor not allowed (or no floor): air only.
        ("app", True, False, True, (True, False)),
        ("floor", True, True, False, (False, True)),
        ("air", True, True, True, (True, False)),
        ("air_and_floor", True, True, False, (True, True)),
        # A choice that is no longer allowed falls back to the app rule.
        ("floor", True, False, False, (True, False)),
        ("air_and_floor", False, True, False, (False, True)),
        # Nothing allowed.
        ("app", False, False, True, None),
    ],
)
def test_select_media(choice, allow_air, allow_floor, last_floor, expected):
    assert select_media(choice, allow_air, allow_floor, last_floor) == expected

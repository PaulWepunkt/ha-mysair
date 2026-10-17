"""Consistency checks for the translation and icon files (no HA needed)."""

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "mysair"
LANGUAGES = ("en", "es", "de")


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _keys(data, prefix=""):
    keys = set()
    for key, value in data.items():
        path = f"{prefix}/{key}"
        keys |= _keys(value, path) if isinstance(value, dict) else {path}
    return keys


def test_strings_json_matches_english_translation():
    assert _load(BASE / "strings.json") == _load(BASE / "translations" / "en.json")


def test_all_languages_have_the_same_keys():
    reference = _keys(_load(BASE / "strings.json"))
    for lang in LANGUAGES:
        assert _keys(_load(BASE / "translations" / f"{lang}.json")) == reference, lang


def test_every_entity_translation_key_has_an_icon():
    entities = _load(BASE / "strings.json")["entity"]
    icons = _load(BASE / "icons.json")["entity"]
    for platform, keys in entities.items():
        for key in keys:
            assert "default" in icons[platform][key], f"{platform}.{key}"


def test_translated_states_cover_enum_options():
    mode = _load(BASE / "strings.json")["entity"]["sensor"]["mode"]
    assert set(mode["state"]) == {"off", "heat", "cool", "on"}
    medium = mode["state_attributes"]["medium"]["state"]
    assert set(medium) == {"air", "floor", "air_and_floor"}


def test_every_control_error_is_translated():
    import re

    source = (BASE / "zone_control.py").read_text(encoding="utf-8")
    source += (BASE / "climate.py").read_text(encoding="utf-8")
    used = set(re.findall(r'control_error\("([a-z_]+)"\)', source))
    used |= set(re.findall(r'translation_key="([a-z_]+)"', source))
    assert used
    exceptions = _load(BASE / "strings.json")["exceptions"]
    assert used <= set(exceptions)


def test_options_flow_is_translated():
    options = _load(BASE / "strings.json")["options"]["step"]["init"]
    keys = {
        "allow_air_heating",
        "allow_floor_heating",
        "allow_air_cooling",
        "allow_floor_cooling",
    }
    assert set(options["data"]) == keys
    assert set(options["data_description"]) == keys
    errors = _load(BASE / "strings.json")["options"]["error"]
    assert set(errors) == {"no_medium_heat", "no_medium_cool"}


def test_medium_select_states_cover_all_media():
    selects = _load(BASE / "strings.json")["entity"]["select"]
    for key in ("heating_medium", "cooling_medium"):
        assert set(selects[key]["state"]) == {"app", "air", "floor", "air_and_floor"}


def test_fan_modes_are_translated():
    climate = _load(BASE / "strings.json")["entity"]["climate"]["thermostat"]
    states = climate["state_attributes"]["fan_mode"]["state"]
    assert set(states) == {"zonal", "1", "2", "3", "auto"}

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

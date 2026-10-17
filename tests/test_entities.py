"""P2 tests of entities and MQTT events (Home Assistant harness, see Dockerfile.test).

Covers climate/sensor/switch reacting to `mysair_update` and sending commands
via `send_zone_command`. No real network: MySairAPI and MySairMQTTClient are
patched (as in test_init_setup_unload.py).
"""

import logging
from datetime import timedelta

import pytest

pytest.importorskip("homeassistant")

import homeassistant.util.dt as dt_util
from homeassistant.components.climate.const import HVACMode, HVACAction
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import async_update_entity
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.mysair.const import (
    CONF_ALLOW_AIR_HEATING,
    CONF_ALLOW_FLOOR_COOLING,
    CONF_ALLOW_FLOOR_HEATING,
    DOMAIN,
    FEEDBACK_TIMEOUT_SECONDS,
)
from custom_components.mysair.api import MySairAPI
from custom_components.mysair.mqtt_handler import MySairMQTTClient


def _patch_happy_api(monkeypatch, send_zone_command_calls=None):
    def _refresh_tokens(self):
        self.access_token = "ACCESS"
        self.refresh_token_value = "REFRESH"
        return True

    monkeypatch.setattr(MySairAPI, "refresh_tokens", _refresh_tokens)
    monkeypatch.setattr(MySairAPI, "get_locations", lambda self: [{"id": 1001}])
    monkeypatch.setattr(
        MySairAPI,
        "get_installations",
        lambda self, location_id: [{"reference": "INST_A"}],
    )
    monkeypatch.setattr(
        MySairAPI,
        "get_devices",
        lambda self, ref: [{"reference": "DEV_1", "name": "Salon"}],
    )
    monkeypatch.setattr(
        MySairAPI,
        "send_instruction",
        lambda self, instruction: {"msg": "Creado", "error": []},
    )
    monkeypatch.setattr(MySairMQTTClient, "start", lambda self: None)

    if send_zone_command_calls is not None:

        def _send_zone_command(
            self, ctl, device, command_type, value=None, temperature=None
        ):
            send_zone_command_calls.append(
                {
                    "ctl": ctl,
                    "device": device,
                    "command_type": command_type,
                    "value": value,
                    "temperature": temperature,
                }
            )
            order_id = f"order-{len(send_zone_command_calls)}"
            return {
                "msg": "Creado",
                "error": [],
                "entity": {"value": [{"orderId": order_id}]},
            }

        monkeypatch.setattr(MySairAPI, "send_zone_command", _send_zone_command)


async def _setup_entry(hass, monkeypatch, send_zone_command_calls=None):
    _patch_happy_api(monkeypatch, send_zone_command_calls)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"email": "user@example.com", "refresh_token": "OLD_REFRESH"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _fire_status(hass, ctl, zone):
    hass.bus.async_fire(
        f"{DOMAIN}_update",
        {
            "topic": f"pro/v1/get/ctl/{ctl}/status",
            "data": {"ctl": ctl, "zones": [zone]},
        },
    )


def _zone(**overrides):
    zone = {
        "zone_id": "DEV_1",
        "temp_actual": 21.5,
        "temp_target": 22.0,
        "temp_min": 10.0,
        "temp_max": 30.0,
        "humidity": 45.0,
        "is_on": True,
        "is_standby": False,
        "is_heat": True,
        "is_cool": False,
        "is_ac": True,
        "mode_raw": "0",
        "allow_heat": True,
        "allow_cool": True,
        "allow_fan": True,
        "fan_mode": "4",
    }
    zone.update(overrides)
    return zone


async def test_climate_updates_from_mqtt_event(hass, monkeypatch):
    await _setup_entry(hass, monkeypatch)

    _fire_status(
        hass,
        "INST_A",
        _zone(temp_actual=23.0, temp_target=24.0, is_heat=True, is_cool=False),
    )
    await hass.async_block_till_done()

    state = hass.states.get("climate.salon")
    assert state.state == HVACMode.HEAT
    assert state.attributes["current_temperature"] == 23.0
    assert state.attributes["temperature"] == 24.0
    assert state.attributes["hvac_action"] == HVACAction.HEATING


async def test_climate_reflects_off_and_standby(hass, monkeypatch):
    await _setup_entry(hass, monkeypatch)

    _fire_status(hass, "INST_A", _zone(is_on=False))
    await hass.async_block_till_done()
    assert hass.states.get("climate.salon").state == HVACMode.OFF

    _fire_status(
        hass, "INST_A", _zone(is_on=True, is_standby=True, is_heat=True, is_cool=False)
    )
    await hass.async_block_till_done()
    state = hass.states.get("climate.salon")
    assert state.state == HVACMode.HEAT
    assert state.attributes["hvac_action"] == HVACAction.IDLE


async def test_sensors_update_from_mqtt_event(hass, monkeypatch):
    await _setup_entry(hass, monkeypatch)

    _fire_status(
        hass,
        "INST_A",
        _zone(temp_actual=19.5, temp_target=21.0, is_heat=True, is_cool=False),
    )
    await hass.async_block_till_done()

    assert hass.states.get("sensor.salon_temperature").state == "19.5"
    assert hass.states.get("sensor.salon_target_temperature").state == "21.0"
    assert hass.states.get("sensor.salon_mode").state == "heat"


async def test_mode_sensor_reflects_off(hass, monkeypatch):
    await _setup_entry(hass, monkeypatch)

    _fire_status(hass, "INST_A", _zone(is_on=False))
    await hass.async_block_till_done()

    assert hass.states.get("sensor.salon_mode").state == "off"


@pytest.mark.parametrize(
    "is_ac,is_floor,expected_medium",
    [
        (True, False, "air"),
        (False, True, "floor"),
        (True, True, "air_and_floor"),
        (False, False, None),
    ],
)
async def test_mode_sensor_medium_attribute(
    hass, monkeypatch, is_ac, is_floor, expected_medium
):
    await _setup_entry(hass, monkeypatch)

    _fire_status(hass, "INST_A", _zone(is_ac=is_ac, is_floor=is_floor))
    await hass.async_block_till_done()

    assert hass.states.get("sensor.salon_mode").attributes["medium"] == expected_medium


async def test_mode_sensor_medium_persists_while_off(hass, monkeypatch):
    # 'm' (and therefore the medium) is kept while the zone is off.
    await _setup_entry(hass, monkeypatch)

    _fire_status(hass, "INST_A", _zone(is_on=False, is_ac=True, is_floor=True))
    await hass.async_block_till_done()

    assert hass.states.get("sensor.salon_mode").state == "off"
    assert hass.states.get("sensor.salon_mode").attributes["medium"] == "air_and_floor"


async def test_switch_updates_from_mqtt_event(hass, monkeypatch):
    await _setup_entry(hass, monkeypatch)

    _fire_status(hass, "INST_A", _zone(is_on=True, is_ac=True, mode_raw="1"))
    await hass.async_block_till_done()
    assert hass.states.get("switch.salon_power").state == "on"

    _fire_status(hass, "INST_A", _zone(is_on=False))
    await hass.async_block_till_done()
    assert hass.states.get("switch.salon_power").state == "off"


async def test_event_from_other_installation_is_ignored(hass, monkeypatch):
    await _setup_entry(hass, monkeypatch)
    before = hass.states.get("climate.salon").state

    _fire_status(hass, "OTHER_INST", _zone(is_on=True, is_heat=True, is_cool=False))
    await hass.async_block_till_done()

    assert hass.states.get("climate.salon").state == before


async def test_malformed_status_payload_does_not_fire_update_event(hass, monkeypatch):
    # E4: mqtt_message_callback (__init__.py) rejects (does not fire
    # mysair_update) when parse_status_payload returns None because the
    # payload is not even a dict.
    entry = await _setup_entry(hass, monkeypatch)
    mqtt_client = hass.data[DOMAIN][entry.entry_id]["mqtt"]

    events = []
    hass.bus.async_listen(f"{DOMAIN}_update", events.append)

    mqtt_client.message_callback(
        {"topic": "pro/v1/get/ctl/INST_A/status", "payload": "not-a-dict"}
    )
    await hass.async_block_till_done()

    assert events == []


async def test_climate_set_hvac_mode_sends_command(hass, monkeypatch):
    calls = []
    await _setup_entry(hass, monkeypatch, send_zone_command_calls=calls)
    # C5: the entity is "unavailable" until the first MQTT status.
    # All zones off: the direction may be chosen freely (season change).
    _fire_status(hass, "INST_A", _zone(is_on=False))
    await hass.async_block_till_done()

    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": "climate.salon", "hvac_mode": "cool"},
        blocking=True,
    )

    assert calls == [
        {
            "ctl": "INST_A",
            "device": "DEV_1",
            "command_type": "mode",
            "value": "1",
            "temperature": 22.0,
        }
    ]
    assert hass.states.get("climate.salon").state == HVACMode.COOL


async def test_climate_set_temperature_while_off_does_not_send_command(
    hass, monkeypatch
):
    calls = []
    await _setup_entry(hass, monkeypatch, send_zone_command_calls=calls)
    # C5: the entity is "unavailable" until the first MQTT status.
    _fire_status(hass, "INST_A", _zone(is_on=False))
    await hass.async_block_till_done()
    assert hass.states.get("climate.salon").state == HVACMode.OFF

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": "climate.salon", "temperature": 25.0},
        blocking=True,
    )

    assert calls == []
    assert hass.states.get("climate.salon").attributes["temperature"] == 25.0


async def test_switch_turn_on_off_sends_commands(hass, monkeypatch):
    calls = []
    await _setup_entry(hass, monkeypatch, send_zone_command_calls=calls)
    # C5: the entity is "unavailable" until the first MQTT status.
    _fire_status(hass, "INST_A", _zone(is_on=False, temp_target=21.5))
    await hass.async_block_till_done()

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.salon_power"}, blocking=True
    )
    # Like the app: air on, installation direction, current setpoint (no 22 °C).
    assert calls[-1] == {
        "ctl": "INST_A",
        "device": "DEV_1",
        "command_type": "mode",
        "value": "0",
        "temperature": 21.5,
    }
    assert hass.states.get("switch.salon_power").state == "on"

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.salon_power"}, blocking=True
    )
    assert calls[-1] == {
        "ctl": "INST_A",
        "device": "DEV_1",
        "command_type": "power",
        "value": None,
        "temperature": None,
    }
    assert hass.states.get("switch.salon_power").state == "off"


async def test_switch_turn_on_uses_installation_direction(hass, monkeypatch):
    calls = []
    await _setup_entry(hass, monkeypatch, send_zone_command_calls=calls)

    # The installation is in cool mode ("1"); turning on must keep it.
    _fire_status(
        hass,
        "INST_A",
        _zone(is_on=False, is_ac=True, mode_raw="1", is_heat=False, is_cool=True),
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.salon_power"}, blocking=True
    )

    assert calls[-1]["value"] == "1"


# --- Floor heating control (F4) ---


async def test_floor_switch_unavailable_without_capability(hass, monkeypatch):
    await _setup_entry(hass, monkeypatch)

    _fire_status(hass, "INST_A", _zone(allow_floor=False, is_floor=False))
    await hass.async_block_till_done()

    assert hass.states.get("switch.salon_floor_heating").state == "unavailable"


async def test_floor_switch_available_and_reflects_state_with_capability(
    hass, monkeypatch
):
    await _setup_entry(hass, monkeypatch)

    _fire_status(hass, "INST_A", _zone(allow_floor=True, is_floor=False))
    await hass.async_block_till_done()
    assert hass.states.get("switch.salon_floor_heating").state == "off"

    _fire_status(
        hass, "INST_A", _zone(allow_floor=True, is_floor=True, mode_raw="4", is_ac=True)
    )
    await hass.async_block_till_done()
    assert hass.states.get("switch.salon_floor_heating").state == "on"


async def test_floor_switch_turn_on_preserves_heat_cool_and_ac(hass, monkeypatch):
    calls = []
    entry = await _setup_entry(hass, monkeypatch, send_zone_command_calls=calls)
    hass.config_entries.async_update_entry(
        entry, options={CONF_ALLOW_FLOOR_COOLING: True}
    )
    # Zone in air-only cool (m="1"): enabling floor must switch to air+floor cool (m="5").
    _fire_status(
        hass,
        "INST_A",
        _zone(
            allow_floor=True,
            is_floor=False,
            is_ac=True,
            mode_raw="1",
            is_heat=False,
            is_cool=True,
            temp_target=23.0,
        ),
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.salon_floor_heating"}, blocking=True
    )

    assert calls[-1] == {
        "ctl": "INST_A",
        "device": "DEV_1",
        "command_type": "mode",
        "value": "5",
        "temperature": 23.0,
    }
    assert hass.states.get("switch.salon_floor_heating").state == "on"


async def test_floor_switch_turn_off_preserves_heat_cool_and_ac(hass, monkeypatch):
    calls = []
    await _setup_entry(hass, monkeypatch, send_zone_command_calls=calls)
    # Air+floor heat (m="4"): disabling floor must return to air-only heat ("0").
    _fire_status(
        hass,
        "INST_A",
        _zone(
            allow_floor=True,
            is_floor=True,
            is_ac=True,
            mode_raw="4",
            is_heat=True,
            is_cool=False,
            temp_target=21.0,
        ),
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.salon_floor_heating"},
        blocking=True,
    )

    assert calls[-1] == {
        "ctl": "INST_A",
        "device": "DEV_1",
        "command_type": "mode",
        "value": "0",
        "temperature": 21.0,
    }
    assert hass.states.get("switch.salon_floor_heating").state == "off"


async def test_humidity_sensor_updates_from_mqtt_event(hass, monkeypatch):
    await _setup_entry(hass, monkeypatch)

    _fire_status(hass, "INST_A", _zone(humidity=52.0))
    await hass.async_block_till_done()

    assert hass.states.get("sensor.salon_humidity").state == "52.0"


async def test_climate_current_humidity_from_status(hass, monkeypatch):
    await _setup_entry(hass, monkeypatch)

    _fire_status(hass, "INST_A", _zone(humidity=52.0))
    await hass.async_block_till_done()
    assert hass.states.get("climate.salon").attributes["current_humidity"] == 52.0

    # No reading (thermostat reports 0, parsed as None): the attribute is
    # dropped instead of keeping a stale value.
    _fire_status(hass, "INST_A", _zone(humidity=None))
    await hass.async_block_till_done()
    assert "current_humidity" not in hass.states.get("climate.salon").attributes


async def test_climate_capability_attributes(hass, monkeypatch):
    await _setup_entry(hass, monkeypatch)

    # Unavailable (and without attributes) until the first status.
    assert "can_heat" not in hass.states.get("climate.salon").attributes

    _fire_status(
        hass,
        "INST_A",
        _zone(
            allow_heat=True,
            allow_cool=False,
            allow_floor=True,
            allow_fan=False,
            allow_timer=True,
            allow_programs=False,
            power="2",
            mode_raw="4",
            fan_mode="0",
        ),
    )
    await hass.async_block_till_done()

    attrs = hass.states.get("climate.salon").attributes
    assert attrs["can_heat"] is True
    assert attrs["can_cool"] is False
    assert attrs["has_floor"] is True
    assert attrs["has_fan"] is False
    assert attrs["has_timer"] is True
    assert attrs["has_programs"] is False
    # Raw MySair codes, unchanged.
    assert attrs["raw_power"] == "2"
    assert attrs["raw_mode"] == "4"
    assert attrs["raw_fan"] == "0"


async def test_climate_min_max_temp_from_status(hass, monkeypatch):
    await _setup_entry(hass, monkeypatch)
    # Before the first status: default values (10-30).
    assert hass.states.get("climate.salon").attributes["min_temp"] == 10
    assert hass.states.get("climate.salon").attributes["max_temp"] == 30

    _fire_status(hass, "INST_A", _zone(temp_min=16.0, temp_max=28.0))
    await hass.async_block_till_done()

    state = hass.states.get("climate.salon")
    assert state.attributes["min_temp"] == 16.0
    assert state.attributes["max_temp"] == 28.0


async def test_climate_hvac_modes_restricted_by_capabilities(hass, monkeypatch):
    await _setup_entry(hass, monkeypatch)
    # Before the first status: the 3 default modes.
    assert set(hass.states.get("climate.salon").attributes["hvac_modes"]) == {
        HVACMode.OFF,
        HVACMode.HEAT,
        HVACMode.COOL,
    }

    # Zone that only allows heat (allow_cool=False, e.g. no cooling unit).
    _fire_status(hass, "INST_A", _zone(allow_heat=True, allow_cool=False))
    await hass.async_block_till_done()

    modes = set(hass.states.get("climate.salon").attributes["hvac_modes"])
    assert modes == {HVACMode.OFF, HVACMode.HEAT}
    assert HVACMode.COOL not in modes


async def test_climate_set_hvac_mode_rejected_when_not_allowed(hass, monkeypatch):
    calls = []
    await _setup_entry(hass, monkeypatch, send_zone_command_calls=calls)

    _fire_status(hass, "INST_A", _zone(allow_heat=True, allow_cool=False))
    await hass.async_block_till_done()

    # Home Assistant validates the mode against hvac_modes before calling the
    # entity and rejects the call; the guard in climate.py still protects the
    # internal path (async_turn_on).
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": "climate.salon", "hvac_mode": "cool"},
            blocking=True,
        )

    assert calls == []
    assert hass.states.get("climate.salon").state != "cool"


# --- Command confirmation via feedback (E7, docs/protocol-findings.md §8) ---


async def test_climate_command_confirmed_via_feedback(hass, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    calls = []
    await _setup_entry(hass, monkeypatch, send_zone_command_calls=calls)
    _fire_status(hass, "INST_A", _zone(is_on=False))
    await hass.async_block_till_done()

    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": "climate.salon", "hvac_mode": "heat"},
        blocking=True,
    )
    assert len(calls) == 1

    hass.bus.async_fire(
        f"{DOMAIN}_feedback", {"order_id": "order-1", "ctl": "INST_A", "raw": {}}
    )
    await hass.async_block_till_done()

    assert "Command confirmed" in caplog.text


async def test_climate_command_feedback_from_other_installation_ignored(
    hass, monkeypatch, caplog
):
    caplog.set_level(logging.INFO)
    calls = []
    await _setup_entry(hass, monkeypatch, send_zone_command_calls=calls)
    _fire_status(hass, "INST_A", _zone(is_on=False))
    await hass.async_block_till_done()

    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": "climate.salon", "hvac_mode": "heat"},
        blocking=True,
    )
    assert len(calls) == 1

    hass.bus.async_fire(
        f"{DOMAIN}_feedback", {"order_id": "order-1", "ctl": "OTHER_INST", "raw": {}}
    )
    await hass.async_block_till_done()

    assert "Command confirmed" not in caplog.text


async def test_climate_command_timeout_logs_warning_when_mqtt_disconnected(
    hass, monkeypatch, caplog
):
    # MySairMQTTClient.start is patched to a no-op: mqtt_client.connected
    # stays False, as if MQTT were down (a real cause of this warning).
    caplog.set_level(logging.INFO)
    calls = []
    entry = await _setup_entry(hass, monkeypatch, send_zone_command_calls=calls)
    _fire_status(hass, "INST_A", _zone(is_on=False))
    await hass.async_block_till_done()

    mqtt_client = hass.data[DOMAIN][entry.entry_id]["mqtt"]
    assert mqtt_client.connected is False

    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": "climate.salon", "hvac_mode": "heat"},
        blocking=True,
    )
    assert len(calls) == 1

    future = dt_util.utcnow() + timedelta(seconds=FEEDBACK_TIMEOUT_SECONDS + 1)
    async_fire_time_changed(hass, future)
    await hass.async_block_till_done()

    assert "No MQTT confirmation" in caplog.text
    assert "MQTT was disconnected" in caplog.text


async def test_climate_command_timeout_logs_warning_when_mqtt_connected(
    hass, monkeypatch, caplog
):
    caplog.set_level(logging.INFO)
    calls = []
    entry = await _setup_entry(hass, monkeypatch, send_zone_command_calls=calls)
    _fire_status(hass, "INST_A", _zone(is_on=False))
    await hass.async_block_till_done()

    mqtt_client = hass.data[DOMAIN][entry.entry_id]["mqtt"]
    mqtt_client.connected = True

    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": "climate.salon", "hvac_mode": "heat"},
        blocking=True,
    )
    assert len(calls) == 1

    future = dt_util.utcnow() + timedelta(seconds=FEEDBACK_TIMEOUT_SECONDS + 1)
    async_fire_time_changed(hass, future)
    await hass.async_block_till_done()

    assert "No MQTT confirmation" in caplog.text
    assert "with MQTT connected" in caplog.text


async def test_switch_command_confirmed_via_feedback(hass, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    calls = []
    await _setup_entry(hass, monkeypatch, send_zone_command_calls=calls)
    _fire_status(hass, "INST_A", _zone(is_on=False))
    await hass.async_block_till_done()

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.salon_power"}, blocking=True
    )
    assert len(calls) == 1

    hass.bus.async_fire(
        f"{DOMAIN}_feedback", {"order_id": "order-1", "ctl": "INST_A", "raw": {}}
    )
    await hass.async_block_till_done()

    assert "Command confirmed" in caplog.text


# --- Availability based on MQTT data freshness (C5) ---


async def test_entities_unavailable_until_first_status(hass, monkeypatch):
    await _setup_entry(hass, monkeypatch)

    assert hass.states.get("climate.salon").state == "unavailable"
    assert hass.states.get("switch.salon_power").state == "unavailable"
    assert hass.states.get("sensor.salon_temperature").state == "unavailable"
    assert hass.states.get("sensor.salon_humidity").state == "unavailable"


async def test_entities_become_available_after_status(hass, monkeypatch):
    await _setup_entry(hass, monkeypatch)

    _fire_status(hass, "INST_A", _zone())
    await hass.async_block_till_done()

    assert hass.states.get("climate.salon").state != "unavailable"
    assert hass.states.get("switch.salon_power").state != "unavailable"
    assert hass.states.get("sensor.salon_temperature").state != "unavailable"


async def test_availability_mixin_disables_polling():
    from custom_components.mysair.availability import AvailabilityMixin

    assert AvailabilityMixin._attr_should_poll is False


async def test_entities_become_unavailable_after_stale_timeout(hass, monkeypatch):
    # `available` calls dt_util.utcnow() again when evaluated; for the expiry
    # check to be positive the real clock has to be frozen
    # (async_fire_time_changed only fires the scheduled callback, it does not
    # move the clock that `available` reads independently).
    freezegun = pytest.importorskip("freezegun")
    from custom_components.mysair.const import MQTT_STALE_AFTER_SECONDS

    with freezegun.freeze_time(dt_util.utcnow()) as frozen:
        await _setup_entry(hass, monkeypatch)
        _fire_status(hass, "INST_A", _zone())
        await hass.async_block_till_done()
        assert hass.states.get("climate.salon").state != "unavailable"

        frozen.tick(timedelta(seconds=MQTT_STALE_AFTER_SECONDS + 1))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()

    assert hass.states.get("climate.salon").state == "unavailable"
    assert hass.states.get("switch.salon_power").state == "unavailable"


# --- Fan speed (F2, docs/protocol-findings.md §9) ---


async def test_climate_fan_mode_updates_from_mqtt_event(hass, monkeypatch):
    await _setup_entry(hass, monkeypatch)

    _fire_status(hass, "INST_A", _zone(allow_fan=True, fan_mode="2"))
    await hass.async_block_till_done()

    state = hass.states.get("climate.salon")
    assert state.attributes["fan_mode"] == "2"
    assert set(state.attributes["fan_modes"]) == {"zonal", "1", "2", "3", "auto"}


async def test_climate_fan_mode_auto_mapping(hass, monkeypatch):
    await _setup_entry(hass, monkeypatch)

    _fire_status(hass, "INST_A", _zone(allow_fan=True, fan_mode="4"))
    await hass.async_block_till_done()

    assert hass.states.get("climate.salon").attributes["fan_mode"] == "auto"


async def test_climate_fan_mode_none_when_not_allowed(hass, monkeypatch):
    await _setup_entry(hass, monkeypatch)

    _fire_status(hass, "INST_A", _zone(allow_fan=False, fan_mode="2"))
    await hass.async_block_till_done()

    state = hass.states.get("climate.salon")
    assert state.attributes["fan_modes"] == []


async def test_climate_fan_mode_zonal_when_wire_is_zero(hass, monkeypatch):
    # "0" = zonal speed, the default chosen by the control module (thermostat
    # manual); the app's fan icon toggles between "0" and "4".
    await _setup_entry(hass, monkeypatch)

    _fire_status(hass, "INST_A", _zone(allow_fan=True, fan_mode="0"))
    await hass.async_block_till_done()

    state = hass.states.get("climate.salon")
    assert state.attributes["fan_mode"] == "zonal"
    assert state.attributes["fan_modes"] == ["zonal", "1", "2", "3", "auto"]


async def test_climate_set_fan_mode_zonal_sends_zero(hass, monkeypatch):
    calls = []
    await _setup_entry(hass, monkeypatch, send_zone_command_calls=calls)
    _fire_status(hass, "INST_A", _zone(allow_fan=True, fan_mode="4"))
    await hass.async_block_till_done()

    await hass.services.async_call(
        "climate",
        "set_fan_mode",
        {"entity_id": "climate.salon", "fan_mode": "zonal"},
        blocking=True,
    )
    assert calls[-1]["command_type"] == "fanspeed"
    assert calls[-1]["value"] == "0"


async def test_climate_set_fan_mode_sends_command(hass, monkeypatch):
    calls = []
    await _setup_entry(hass, monkeypatch, send_zone_command_calls=calls)
    _fire_status(hass, "INST_A", _zone(allow_fan=True, fan_mode="1"))
    await hass.async_block_till_done()

    await hass.services.async_call(
        "climate",
        "set_fan_mode",
        {"entity_id": "climate.salon", "fan_mode": "auto"},
        blocking=True,
    )

    assert calls[-1] == {
        "ctl": "INST_A",
        "device": "DEV_1",
        "command_type": "fanspeed",
        "value": "4",
        "temperature": None,
    }
    assert hass.states.get("climate.salon").attributes["fan_mode"] == "auto"


async def test_climate_set_fan_mode_rejected_when_not_allowed(hass, monkeypatch):
    # Unlike hvac_mode (which only warns in this HA version), fan_mode
    # validation does reject with ServiceValidationError at service level when
    # fan_modes is empty: the call never reaches climate.async_set_fan_mode.
    calls = []
    await _setup_entry(hass, monkeypatch, send_zone_command_calls=calls)
    _fire_status(hass, "INST_A", _zone(allow_fan=False))
    await hass.async_block_till_done()

    with pytest.raises(Exception):
        await hass.services.async_call(
            "climate",
            "set_fan_mode",
            {"entity_id": "climate.salon", "fan_mode": "auto"},
            blocking=True,
        )

    assert calls == []


# --- E7 part 2: revert the optimistic state if no confirmation arrives ---


async def test_climate_hvac_mode_reverts_on_timeout(hass, monkeypatch):
    calls = []
    await _setup_entry(hass, monkeypatch, send_zone_command_calls=calls)
    _fire_status(
        hass, "INST_A", _zone(is_on=False, is_heat=True, is_cool=False, mode_raw="0")
    )
    await hass.async_block_till_done()
    assert hass.states.get("climate.salon").state == HVACMode.OFF

    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": "climate.salon", "hvac_mode": "cool"},
        blocking=True,
    )
    assert hass.states.get("climate.salon").state == HVACMode.COOL  # optimista

    future = dt_util.utcnow() + timedelta(seconds=FEEDBACK_TIMEOUT_SECONDS + 1)
    async_fire_time_changed(hass, future)
    await hass.async_block_till_done()

    assert hass.states.get("climate.salon").state == HVACMode.OFF  # reverted


async def test_climate_temperature_reverts_on_timeout(hass, monkeypatch):
    calls = []
    await _setup_entry(hass, monkeypatch, send_zone_command_calls=calls)
    _fire_status(hass, "INST_A", _zone(temp_target=22.0))
    await hass.async_block_till_done()
    assert hass.states.get("climate.salon").attributes["temperature"] == 22.0

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": "climate.salon", "temperature": 26.0},
        blocking=True,
    )
    assert hass.states.get("climate.salon").attributes["temperature"] == 26.0

    future = dt_util.utcnow() + timedelta(seconds=FEEDBACK_TIMEOUT_SECONDS + 1)
    async_fire_time_changed(hass, future)
    await hass.async_block_till_done()

    assert hass.states.get("climate.salon").attributes["temperature"] == 22.0


async def test_climate_fan_mode_reverts_on_timeout(hass, monkeypatch):
    calls = []
    await _setup_entry(hass, monkeypatch, send_zone_command_calls=calls)
    _fire_status(hass, "INST_A", _zone(allow_fan=True, fan_mode="1"))
    await hass.async_block_till_done()
    assert hass.states.get("climate.salon").attributes["fan_mode"] == "1"

    await hass.services.async_call(
        "climate",
        "set_fan_mode",
        {"entity_id": "climate.salon", "fan_mode": "auto"},
        blocking=True,
    )
    assert hass.states.get("climate.salon").attributes["fan_mode"] == "auto"

    future = dt_util.utcnow() + timedelta(seconds=FEEDBACK_TIMEOUT_SECONDS + 1)
    async_fire_time_changed(hass, future)
    await hass.async_block_till_done()

    assert hass.states.get("climate.salon").attributes["fan_mode"] == "1"


async def test_switch_reverts_on_timeout(hass, monkeypatch):
    calls = []
    await _setup_entry(hass, monkeypatch, send_zone_command_calls=calls)
    _fire_status(hass, "INST_A", _zone(is_on=False))
    await hass.async_block_till_done()
    assert hass.states.get("switch.salon_power").state == "off"

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.salon_power"}, blocking=True
    )
    assert hass.states.get("switch.salon_power").state == "on"

    future = dt_util.utcnow() + timedelta(seconds=FEEDBACK_TIMEOUT_SECONDS + 1)
    async_fire_time_changed(hass, future)
    await hass.async_block_till_done()

    assert hass.states.get("switch.salon_power").state == "off"


async def test_climate_pending_revert_cleared_by_real_status(hass, monkeypatch):
    # If a real status arrives before the timeout, the pending revert is
    # discarded: the fresh data wins, there is no going back to an old value.
    calls = []
    await _setup_entry(hass, monkeypatch, send_zone_command_calls=calls)
    _fire_status(
        hass, "INST_A", _zone(is_on=False, is_heat=True, is_cool=False, mode_raw="0")
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": "climate.salon", "hvac_mode": "cool"},
        blocking=True,
    )
    assert hass.states.get("climate.salon").state == HVACMode.COOL

    # A real status arrives confirming the change (fresh truth).
    _fire_status(
        hass, "INST_A", _zone(is_on=True, is_heat=False, is_cool=True, mode_raw="1")
    )
    await hass.async_block_till_done()
    assert hass.states.get("climate.salon").state == HVACMode.COOL

    future = dt_util.utcnow() + timedelta(seconds=FEEDBACK_TIMEOUT_SECONDS + 1)
    async_fire_time_changed(hass, future)
    await hass.async_block_till_done()

    # Still COOL: no revert, the pending command was cleared by the real status.
    assert hass.states.get("climate.salon").state == HVACMode.COOL


async def test_mqtt_status_sensor_reflects_connection_state(hass, monkeypatch):
    entry = await _setup_entry(hass, monkeypatch)
    mqtt_client = hass.data[DOMAIN][entry.entry_id]["mqtt"]

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"mysair_mqtt_status_{entry.entry_id}"
    )
    assert entity_id is not None
    assert hass.states.get(entity_id).state == "offline"

    mqtt_client.connected = True
    mqtt_client.last_message_at = dt_util.utcnow()
    mqtt_client.parse_strict_count = 3
    mqtt_client.parse_fallback_count = 1
    mqtt_client.parse_error_count = 2
    mqtt_client.total_reconnects = 4
    await async_update_entity(hass, entity_id)

    state = hass.states.get(entity_id)
    assert state.state == "online"
    assert state.attributes["last_update"] is not None
    assert state.attributes["parse_strict_count"] == 3
    assert state.attributes["parse_fallback_count"] == 1
    assert state.attributes["parse_error_count"] == 2
    assert state.attributes["total_reconnects"] == 4


async def test_climate_turn_on_uses_only_allowed_mode(hass, monkeypatch):
    """In a cool-only zone, turn_on must turn on in cool mode.

    It used to always fall back to HVACMode.HEAT without checking capabilities;
    the async_set_hvac_mode guard rejected it and the call silently did nothing.
    """
    calls = []
    await _setup_entry(hass, monkeypatch, send_zone_command_calls=calls)

    _fire_status(hass, "INST_A", _zone(is_on=False, allow_heat=False, allow_cool=True))
    await hass.async_block_till_done()

    await hass.services.async_call(
        "climate", "turn_on", {"entity_id": "climate.salon"}, blocking=True
    )
    await hass.async_block_till_done()

    assert [c["command_type"] for c in calls] == ["mode"]
    assert calls[0]["value"] == "1"  # 1 = cool
    assert hass.states.get("climate.salon").state == HVACMode.COOL


async def test_climate_turn_on_still_prefers_heat_when_both_allowed(hass, monkeypatch):
    calls = []
    await _setup_entry(hass, monkeypatch, send_zone_command_calls=calls)

    _fire_status(hass, "INST_A", _zone(is_on=False, allow_heat=True, allow_cool=True))
    await hass.async_block_till_done()

    await hass.services.async_call(
        "climate", "turn_on", {"entity_id": "climate.salon"}, blocking=True
    )
    await hass.async_block_till_done()

    assert calls[0]["value"] == "0"  # 0 = heat


async def test_entity_names_follow_ha_language(hass, monkeypatch):
    """Names come from HA translations / device classes, not hardcoded text."""
    hass.config.language = "de"
    await _setup_entry(hass, monkeypatch)
    _fire_status(hass, "INST_A", _zone(allow_floor=True))
    await hass.async_block_till_done()

    names = {s.entity_id: s.name for s in hass.states.async_all()}
    assert names["sensor.salon_temperatur"] == "Salon Temperatur"
    assert names["sensor.salon_soll_temperatur"] == "Salon Soll-Temperatur"
    assert names["sensor.salon_luftfeuchtigkeit"] == "Salon Luftfeuchtigkeit"
    assert names["sensor.salon_modus"] == "Salon Modus"
    assert names["switch.salon_ein_aus"] == "Salon Ein/Aus"
    assert names["switch.salon_fussbodenheizung"] == "Salon Fußbodenheizung"
    assert names["sensor.mysair_cloud_verbindung"] == "MySair Cloud-Verbindung"
    # Installation device ("MySair INST_A" without a name in the account).
    assert names["select.mysair_inst_a_betriebsart"] == "MySair INST_A Betriebsart"
    assert (
        names["button.mysair_inst_a_anlage_stoppen"] == "MySair INST_A Anlage stoppen"
    )
    assert names["sensor.mysair_inst_a_hauptzone"] == "MySair INST_A Hauptzone"
    assert names["binary_sensor.mysair_inst_a_in_betrieb"] == "MySair INST_A In Betrieb"
    assert "binary_sensor.mysair_inst_a_heiz_kuhlkonflikt" in names
    assert names["select.salon_heizen_mit"] == "Salon Heizen mit"
    assert "switch.mysair_inst_a_mysair_schaltet_warmepumpe" in names
    assert "binary_sensor.mysair_inst_a_passt_nicht_zur_warmepumpe" in names
    assert names["select.salon_kuhlen_mit"] == "Salon Kühlen mit"


async def test_mode_sensor_is_enum_with_translated_options(hass, monkeypatch):
    await _setup_entry(hass, monkeypatch)
    _fire_status(hass, "INST_A", _zone(is_heat=False, is_cool=True, mode_raw="1"))
    await hass.async_block_till_done()

    state = hass.states.get("sensor.salon_mode")
    assert state.state == "cool"
    assert state.attributes["device_class"] == "enum"
    assert state.attributes["options"] == ["off", "heat", "cool", "on"]


async def test_cloud_connection_sensor_is_diagnostic(hass, monkeypatch):
    from homeassistant.helpers import entity_registry as er

    await _setup_entry(hass, monkeypatch)
    entry = er.async_get(hass).async_get("sensor.mysair_cloud_connection")
    assert entry.entity_category == "diagnostic"
    assert hass.states.get("sensor.mysair_cloud_connection").state in (
        "online",
        "offline",
    )


# --- Steering like the official app (direction lock, turn on, floor) ---


async def _setup_two_zones(hass, monkeypatch, calls, options=None):
    """Installation with two zones: DEV_1 (climate.salon) and DEV_2 (climate.cocina)."""
    entry = await _setup_entry(hass, monkeypatch, send_zone_command_calls=calls)
    monkeypatch.setattr(
        MySairAPI,
        "get_devices",
        lambda self, ref: [
            {"reference": "DEV_1", "name": "Salon"},
            {"reference": "DEV_2", "name": "Cocina"},
        ],
    )
    if options is not None:
        hass.config_entries.async_update_entry(entry, options=options)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _mode_zone(zone_id, m, is_on, **overrides):
    """Zone with the flags parse_mode derives from m."""
    n = int(m)
    return _zone(
        zone_id=zone_id,
        mode_raw=m,
        is_on=is_on,
        is_heat=n % 2 == 0,
        is_cool=n % 2 == 1,
        is_ac=n in (0, 1, 4, 5),
        is_floor=n in (2, 3, 4, 5),
        **{"allow_floor": True, **overrides},
    )


def _fire_zones(hass, *zones):
    hass.bus.async_fire(
        f"{DOMAIN}_update",
        {
            "topic": "pro/v1/get/ctl/INST_A/status",
            "data": {"ctl": "INST_A", "zones": list(zones)},
        },
    )


async def test_running_zone_locks_direction_of_other_zones(hass, monkeypatch):
    calls = []
    await _setup_two_zones(hass, monkeypatch, calls)
    _fire_zones(hass, _mode_zone("DEV_1", "0", False), _mode_zone("DEV_2", "0", True))
    await hass.async_block_till_done()

    assert hass.states.get("climate.salon").attributes["hvac_modes"] == ["off", "heat"]

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": "climate.salon", "hvac_mode": "cool"},
            blocking=True,
        )
    assert calls == []


async def test_all_zones_off_allow_both_directions(hass, monkeypatch):
    calls = []
    await _setup_two_zones(hass, monkeypatch, calls)
    _fire_zones(hass, _mode_zone("DEV_1", "0", False), _mode_zone("DEV_2", "0", False))
    await hass.async_block_till_done()

    modes = hass.states.get("climate.salon").attributes["hvac_modes"]
    assert modes == ["off", "heat", "cool"]


async def test_direction_lock_follows_other_zone_updates(hass, monkeypatch):
    calls = []
    await _setup_two_zones(hass, monkeypatch, calls)
    _fire_zones(hass, _mode_zone("DEV_1", "0", False), _mode_zone("DEV_2", "0", False))
    await hass.async_block_till_done()

    # Only DEV_2 reports: it now runs in cool → DEV_1 may only cool.
    _fire_zones(hass, _mode_zone("DEV_2", "1", True))
    await hass.async_block_till_done()
    assert hass.states.get("climate.salon").attributes["hvac_modes"] == ["off", "cool"]


async def test_climate_turn_on_uses_installation_direction(hass, monkeypatch):
    calls = []
    await _setup_two_zones(hass, monkeypatch, calls)
    _fire_zones(
        hass,
        _mode_zone("DEV_1", "0", False, temp_target=24.0),
        _mode_zone("DEV_2", "1", True),
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        "climate", "turn_on", {"entity_id": "climate.salon"}, blocking=True
    )
    assert calls == [
        {
            "ctl": "INST_A",
            "device": "DEV_1",
            "command_type": "mode",
            "value": "1",
            "temperature": 24.0,
        }
    ]
    assert hass.states.get("climate.salon").state == HVACMode.COOL


async def test_turn_on_keeps_floor_setting(hass, monkeypatch):
    calls = []
    await _setup_two_zones(hass, monkeypatch, calls)
    # DEV_1 off with floor-only heat in m: turning on adds the air (m=4).
    _fire_zones(hass, _mode_zone("DEV_1", "2", False), _mode_zone("DEV_2", "0", False))
    await hass.async_block_till_done()

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.salon_power"}, blocking=True
    )
    assert calls[-1]["value"] == "4"


async def test_turn_on_in_cooling_drops_floor_unless_allowed(hass, monkeypatch):
    calls = []
    await _setup_two_zones(hass, monkeypatch, calls)
    _fire_zones(hass, _mode_zone("DEV_1", "5", False), _mode_zone("DEV_2", "1", True))
    await hass.async_block_till_done()

    await hass.services.async_call(
        "climate", "turn_on", {"entity_id": "climate.salon"}, blocking=True
    )
    assert calls[-1]["value"] == "1"  # air only: floor cooling not allowed


async def test_turn_on_in_cooling_keeps_floor_when_allowed(hass, monkeypatch):
    calls = []
    await _setup_two_zones(
        hass, monkeypatch, calls, options={CONF_ALLOW_FLOOR_COOLING: True}
    )
    _fire_zones(hass, _mode_zone("DEV_1", "5", False), _mode_zone("DEV_2", "1", True))
    await hass.async_block_till_done()

    await hass.services.async_call(
        "climate", "turn_on", {"entity_id": "climate.salon"}, blocking=True
    )
    assert calls[-1]["value"] == "5"


async def test_switch_turn_on_rejected_when_zone_cannot_follow_direction(
    hass, monkeypatch
):
    calls = []
    await _setup_two_zones(hass, monkeypatch, calls)
    _fire_zones(
        hass,
        _mode_zone("DEV_1", "1", False, allow_heat=False),
        _mode_zone("DEV_2", "0", True),
    )
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": "switch.salon_power"}, blocking=True
        )
    assert err.value.translation_key == "direction_locked"
    assert calls == []


async def test_floor_switch_rejects_floor_cooling_by_default(hass, monkeypatch):
    calls = []
    await _setup_two_zones(hass, monkeypatch, calls)
    _fire_zones(hass, _mode_zone("DEV_1", "1", True), _mode_zone("DEV_2", "1", False))
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": "switch.salon_floor_heating"},
            blocking=True,
        )
    assert err.value.translation_key == "floor_cooling_disabled"
    assert calls == []


async def test_floor_switch_turn_off_last_medium_turns_zone_off(hass, monkeypatch):
    calls = []
    await _setup_two_zones(hass, monkeypatch, calls)
    _fire_zones(hass, _mode_zone("DEV_1", "2", True), _mode_zone("DEV_2", "0", False))
    await hass.async_block_till_done()
    assert hass.states.get("switch.salon_floor_heating").state == "on"

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.salon_floor_heating"}, blocking=True
    )
    assert calls[-1]["command_type"] == "power"


async def test_floor_switch_turn_on_while_zone_off_starts_floor_only(hass, monkeypatch):
    calls = []
    await _setup_two_zones(hass, monkeypatch, calls)
    _fire_zones(
        hass,
        _mode_zone("DEV_1", "0", False, temp_target=21.0),
        _mode_zone("DEV_2", "0", False),
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.salon_floor_heating"}, blocking=True
    )
    assert calls[-1] == {
        "ctl": "INST_A",
        "device": "DEV_1",
        "command_type": "mode",
        "value": "2",
        "temperature": 21.0,
    }


async def test_floor_switch_is_off_while_zone_is_off(hass, monkeypatch):
    # Like the app: the floor only counts as on while the zone is on.
    calls = []
    await _setup_two_zones(hass, monkeypatch, calls)
    _fire_zones(hass, _mode_zone("DEV_1", "4", False), _mode_zone("DEV_2", "0", False))
    await hass.async_block_till_done()
    assert hass.states.get("switch.salon_floor_heating").state == "off"


# --- Installation device and its entities (block B) ---


def _device(hass, identifier):
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id):
        if (DOMAIN, identifier) in device.identifiers:
            return device
    return None


def _installation_state(hass, unique_id, domain="sensor"):
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id is not None, unique_id
    return hass.states.get(entity_id)


async def test_device_tree_account_installation_zones(hass, monkeypatch):
    entry = await _setup_two_zones(hass, monkeypatch, [])
    account = _device(hass, entry.entry_id)
    installation = _device(hass, "installation_INST_A")
    zone = _device(hass, "INST_A_DEV_1")
    assert installation.via_device_id == account.id
    assert zone.via_device_id == installation.id
    assert installation.name == "MySair INST_A"

    # The controller firmware arrives with the status and becomes sw_version.
    hass.bus.async_fire(
        f"{DOMAIN}_update",
        {
            "topic": "pro/v1/get/ctl/INST_A/status",
            "data": {"ctl": "INST_A", "firmware": "1.2.3", "zones": [_zone()]},
        },
    )
    await hass.async_block_till_done()
    installation = _device(hass, "installation_INST_A")
    assert installation.sw_version == "1.2.3"


async def test_installation_device_uses_installation_name(hass, monkeypatch):
    _patch_happy_api(monkeypatch)
    monkeypatch.setattr(
        MySairAPI,
        "get_installations",
        lambda self, location_id: [{"reference": "INST_A", "name": "Casa"}],
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"email": "user@example.com", "refresh_token": "OLD_REFRESH"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    installation = _device(hass, "installation_INST_A")
    assert installation.name == "Casa"


async def test_installation_entities_unavailable_until_first_status(hass, monkeypatch):
    await _setup_two_zones(hass, monkeypatch, [])
    state = _installation_state(hass, "mysair_operating_mode_INST_A", "select")
    assert state.state == "unavailable"


async def test_operating_mode_and_running_follow_zones(hass, monkeypatch):
    await _setup_two_zones(hass, monkeypatch, [])
    _fire_zones(hass, _mode_zone("DEV_1", "1", False), _mode_zone("DEV_2", "1", False))
    await hass.async_block_till_done()

    # All off: the direction is still shown (from the first zone).
    assert (
        _installation_state(hass, "mysair_operating_mode_INST_A", "select").state
        == "cool"
    )
    running = _installation_state(
        hass, "mysair_installation_active_INST_A", "binary_sensor"
    )
    assert running.state == "off"

    _fire_zones(hass, _mode_zone("DEV_2", "0", True))
    await hass.async_block_till_done()
    assert (
        _installation_state(hass, "mysair_operating_mode_INST_A", "select").state
        == "heat"
    )
    running = _installation_state(
        hass, "mysair_installation_active_INST_A", "binary_sensor"
    )
    assert running.state == "on"


async def test_direction_conflict_sensor(hass, monkeypatch):
    await _setup_two_zones(hass, monkeypatch, [])
    _fire_zones(hass, _mode_zone("DEV_1", "0", True), _mode_zone("DEV_2", "0", True))
    await hass.async_block_till_done()
    conflict = _installation_state(
        hass, "mysair_direction_conflict_INST_A", "binary_sensor"
    )
    assert conflict.state == "off"

    _fire_zones(hass, _mode_zone("DEV_2", "1", True))
    await hass.async_block_till_done()
    conflict = _installation_state(
        hass, "mysair_direction_conflict_INST_A", "binary_sensor"
    )
    assert conflict.state == "on"


async def test_main_zone_sensor_shows_device_name(hass, monkeypatch):
    await _setup_two_zones(hass, monkeypatch, [])
    _fire_zones(
        hass,
        _mode_zone("DEV_1", "0", False, is_main=False),
        _mode_zone("DEV_2", "0", False, is_main=True),
    )
    await hass.async_block_till_done()
    assert _installation_state(hass, "mysair_main_zone_INST_A").state == "Cocina"

    # A zone renamed in Home Assistant shows its new name.
    registry = dr.async_get(hass)
    zone = _device(hass, "INST_A_DEV_2")
    registry.async_update_device(zone.id, name_by_user="Kitchen")
    _fire_zones(hass, _mode_zone("DEV_2", "0", False, is_main=True))
    await hass.async_block_till_done()
    assert _installation_state(hass, "mysair_main_zone_INST_A").state == "Kitchen"


# --- Season change (select) and stop button (block C, items 11-12) ---


def _entity_id(hass, domain, unique_id):
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)


async def test_select_changes_direction_only_while_all_off(hass, monkeypatch):
    calls = []
    await _setup_two_zones(hass, monkeypatch, calls)
    select = _entity_id(hass, "select", "mysair_operating_mode_INST_A")
    _fire_zones(hass, _mode_zone("DEV_1", "1", False), _mode_zone("DEV_2", "1", False))
    await hass.async_block_till_done()
    assert hass.states.get(select).state == "cool"

    # All off: like the app, the choice is only kept locally ...
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": select, "option": "heat"},
        blocking=True,
    )
    assert calls == []
    assert hass.states.get(select).state == "heat"

    # ... and the next zone that is turned on starts in that direction.
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.salon_power"}, blocking=True
    )
    assert calls[-1]["value"] == "0"


async def test_select_locked_while_a_zone_is_on(hass, monkeypatch):
    calls = []
    await _setup_two_zones(hass, monkeypatch, calls)
    select = _entity_id(hass, "select", "mysair_operating_mode_INST_A")
    _fire_zones(hass, _mode_zone("DEV_1", "1", True), _mode_zone("DEV_2", "1", False))
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": select, "option": "heat"},
            blocking=True,
        )
    assert err.value.translation_key == "direction_locked"
    assert hass.states.get(select).state == "cool"


async def test_chosen_direction_dropped_when_a_zone_turns_on(hass, monkeypatch):
    await _setup_two_zones(hass, monkeypatch, [])
    select = _entity_id(hass, "select", "mysair_operating_mode_INST_A")
    _fire_zones(hass, _mode_zone("DEV_1", "1", False), _mode_zone("DEV_2", "1", False))
    await hass.async_block_till_done()
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": select, "option": "heat"},
        blocking=True,
    )

    # Another client turns a zone on in cool: the real direction wins.
    _fire_zones(hass, _mode_zone("DEV_2", "1", True))
    await hass.async_block_till_done()
    _fire_zones(hass, _mode_zone("DEV_2", "1", False))
    await hass.async_block_till_done()
    assert hass.states.get(select).state == "cool"


async def test_stop_button_sends_stop_to_main_zone(hass, monkeypatch):
    await _setup_two_zones(hass, monkeypatch, [])
    calls = []
    monkeypatch.setattr(
        MySairAPI,
        "send_installation_command",
        lambda self, ctl, command_type, value=None, device="": calls.append(
            (ctl, command_type, device)
        ),
    )
    button = _entity_id(hass, "button", "mysair_stop_installation_INST_A")
    # Available even before the first status (command goes over HTTP).
    assert hass.states.get(button).state != "unavailable"

    _fire_zones(
        hass,
        _mode_zone("DEV_1", "0", True, is_main=True),
        _mode_zone("DEV_2", "0", True),
    )
    await hass.async_block_till_done()
    await hass.services.async_call(
        "button", "press", {"entity_id": button}, blocking=True
    )
    assert [c for c in calls if c[1] == "stop"] == [("INST_A", "stop", "DEV_1")]


async def test_stop_button_failure_shows_translated_error(hass, monkeypatch):
    await _setup_two_zones(hass, monkeypatch, [])

    def _fail(self, ctl, command_type, value=None, device=""):
        raise ConnectionError("boom")

    monkeypatch.setattr(MySairAPI, "send_installation_command", _fail)
    button = _entity_id(hass, "button", "mysair_stop_installation_INST_A")
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "button", "press", {"entity_id": button}, blocking=True
        )
    assert err.value.translation_key == "stop_failed"


# --- Failed commands are shown in the UI (translated HomeAssistantError) ---


def _patch_failing_command(monkeypatch):
    def _fail(self, ctl, device, command_type, value=None, temperature=None):
        raise ConnectionError("backend unreachable")

    monkeypatch.setattr(MySairAPI, "send_zone_command", _fail)


async def test_failed_temperature_command_raises_and_restores_setpoint(
    hass, monkeypatch
):
    await _setup_entry(hass, monkeypatch)
    _fire_status(hass, "INST_A", _zone(temp_target=22.0))
    await hass.async_block_till_done()
    _patch_failing_command(monkeypatch)

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": "climate.salon", "temperature": 25.0},
            blocking=True,
        )
    assert err.value.translation_key == "command_failed"
    assert hass.states.get("climate.salon").attributes["temperature"] == 22.0


async def test_failed_hvac_mode_command_raises_and_keeps_state(hass, monkeypatch):
    await _setup_entry(hass, monkeypatch)
    _fire_status(hass, "INST_A", _zone(is_on=False))
    await hass.async_block_till_done()
    _patch_failing_command(monkeypatch)

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": "climate.salon", "hvac_mode": "heat"},
            blocking=True,
        )
    assert err.value.translation_key == "command_failed"
    assert hass.states.get("climate.salon").state == HVACMode.OFF


async def test_failed_fan_mode_command_raises(hass, monkeypatch):
    await _setup_entry(hass, monkeypatch)
    _fire_status(hass, "INST_A", _zone(allow_fan=True, fan_mode="1"))
    await hass.async_block_till_done()
    _patch_failing_command(monkeypatch)

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "climate",
            "set_fan_mode",
            {"entity_id": "climate.salon", "fan_mode": "auto"},
            blocking=True,
        )
    assert err.value.translation_key == "command_failed"
    assert hass.states.get("climate.salon").attributes["fan_mode"] == "1"


@pytest.mark.parametrize(
    "entity_id, service, is_on",
    [
        ("switch.salon_power", "turn_on", False),
        ("switch.salon_power", "turn_off", True),
        ("switch.salon_floor_heating", "turn_on", True),
    ],
)
async def test_failed_switch_command_raises_and_keeps_state(
    hass, monkeypatch, entity_id, service, is_on
):
    await _setup_entry(hass, monkeypatch)
    _fire_status(hass, "INST_A", _zone(is_on=is_on, allow_floor=True, is_floor=False))
    await hass.async_block_till_done()
    before = hass.states.get(entity_id).state
    _patch_failing_command(monkeypatch)

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "switch", service, {"entity_id": entity_id}, blocking=True
        )
    assert err.value.translation_key == "command_failed"
    assert hass.states.get(entity_id).state == before


# --- Medium per direction: options and per-zone selects ---


async def _select_medium(hass, entity_id, option):
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": entity_id, "option": option},
        blocking=True,
    )


async def test_heating_medium_select_floor_only(hass, monkeypatch):
    calls = []
    await _setup_two_zones(hass, monkeypatch, calls)
    heat_with = _entity_id(hass, "select", "mysair_heating_medium_INST_A_DEV_1")
    _fire_zones(hass, _mode_zone("DEV_1", "0", False), _mode_zone("DEV_2", "0", False))
    await hass.async_block_till_done()

    state = hass.states.get(heat_with)
    assert state.state == "app"
    assert state.attributes["options"] == ["app", "air", "floor", "air_and_floor"]

    await _select_medium(hass, heat_with, "floor")
    assert calls == []  # nothing is sent when the choice changes

    await hass.services.async_call(
        "climate", "turn_on", {"entity_id": "climate.salon"}, blocking=True
    )
    assert calls[-1]["value"] == "2"  # floor only, heat


async def test_medium_choice_is_restored_after_restart(hass, monkeypatch):
    from pytest_homeassistant_custom_component.common import (
        mock_restore_cache_with_extra_data,
    )
    from homeassistant.core import State

    # The displayed state may be unavailable when HA saves it; the choice
    # itself is kept in the extra data.
    mock_restore_cache_with_extra_data(
        hass,
        [(State("select.salon_heat_with", "unavailable"), {"choice": "air_and_floor"})],
    )
    calls = []
    await _setup_two_zones(hass, monkeypatch, calls)
    _fire_zones(hass, _mode_zone("DEV_1", "0", False), _mode_zone("DEV_2", "0", False))
    await hass.async_block_till_done()
    heat_with = _entity_id(hass, "select", "mysair_heating_medium_INST_A_DEV_1")
    assert heat_with == "select.salon_heat_with"
    assert hass.states.get(heat_with).state == "air_and_floor"

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.salon_power"}, blocking=True
    )
    assert calls[-1]["value"] == "4"


async def test_air_heating_disallowed_uses_floor(hass, monkeypatch):
    calls = []
    await _setup_two_zones(
        hass, monkeypatch, calls, options={CONF_ALLOW_AIR_HEATING: False}
    )
    _fire_zones(
        hass,
        _mode_zone("DEV_1", "0", False),
        _mode_zone("DEV_2", "0", False, allow_floor=False),
    )
    await hass.async_block_till_done()

    heat_with = _entity_id(hass, "select", "mysair_heating_medium_INST_A_DEV_1")
    assert hass.states.get(heat_with).attributes["options"] == ["app", "floor"]

    # "Like the app" would turn the air on; air heating is not allowed.
    await hass.services.async_call(
        "climate", "turn_on", {"entity_id": "climate.salon"}, blocking=True
    )
    assert calls[-1]["value"] == "2"

    # A zone without floor cannot heat at all: no HEAT, no heating select.
    cocina = hass.states.get("climate.cocina")
    assert "heat" not in cocina.attributes["hvac_modes"]
    heat_with_2 = _entity_id(hass, "select", "mysair_heating_medium_INST_A_DEV_2")
    assert hass.states.get(heat_with_2).state == "unavailable"


async def test_zone_without_heating_medium_does_not_start_cooling(hass, monkeypatch):
    calls = []
    await _setup_two_zones(
        hass, monkeypatch, calls, options={CONF_ALLOW_AIR_HEATING: False}
    )
    _fire_zones(
        hass,
        _mode_zone("DEV_1", "0", False, allow_floor=False),
        _mode_zone("DEV_2", "0", False),
    )
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": "switch.salon_power"}, blocking=True
        )
    assert err.value.translation_key == "no_medium_allowed"
    assert calls == []


async def test_floor_switch_rejects_disallowed_floor_heating(hass, monkeypatch):
    calls = []
    await _setup_two_zones(
        hass, monkeypatch, calls, options={CONF_ALLOW_FLOOR_HEATING: False}
    )
    _fire_zones(hass, _mode_zone("DEV_1", "0", True), _mode_zone("DEV_2", "0", False))
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": "switch.salon_floor_heating"},
            blocking=True,
        )
    assert err.value.translation_key == "floor_heating_disabled"
    assert calls == []


async def test_options_change_updates_select_options(hass, monkeypatch):
    entry = await _setup_two_zones(hass, monkeypatch, [])
    _fire_zones(hass, _mode_zone("DEV_1", "1", False), _mode_zone("DEV_2", "1", False))
    await hass.async_block_till_done()
    cool_with = _entity_id(hass, "select", "mysair_cooling_medium_INST_A_DEV_1")
    assert hass.states.get(cool_with).attributes["options"] == ["app", "air"]

    hass.config_entries.async_update_entry(
        entry, options={CONF_ALLOW_FLOOR_COOLING: True}
    )
    await hass.async_block_till_done()
    assert hass.states.get(cool_with).attributes["options"] == [
        "app",
        "air",
        "floor",
        "air_and_floor",
    ]


# --- Heat pump not switched by MySair ("MySair schaltet Wärmepumpe" off) ---


async def _hold(hass, on=True):
    """Turn "MySair schaltet Wärmepumpe" off (hold) or back on."""
    switch = _entity_id(hass, "switch", "mysair_hold_direction_INST_A")
    await hass.services.async_call(
        "switch", "turn_off" if on else "turn_on", {"entity_id": switch}, blocking=True
    )
    return switch


async def test_heat_pump_switch_is_config_and_on_by_default(hass, monkeypatch):
    await _setup_two_zones(hass, monkeypatch, [])
    switch = _entity_id(hass, "switch", "mysair_hold_direction_INST_A")
    entry = er.async_get(hass).async_get(switch)
    assert entry.entity_category == "config"
    assert hass.states.get(switch).state == "on"


async def test_heat_pump_switch_on_releases_held_direction(hass, monkeypatch):
    calls = []
    await _setup_two_zones(hass, monkeypatch, calls)
    _fire_zones(hass, _mode_zone("DEV_1", "0", False), _mode_zone("DEV_2", "0", False))
    await hass.async_block_till_done()
    switch = await _hold(hass)
    assert hass.states.get(switch).state == "off"
    assert hass.states.get("climate.salon").attributes["hvac_modes"] == ["off", "heat"]

    await _hold(hass, on=False)
    assert hass.states.get(switch).state == "on"
    assert hass.states.get("climate.salon").attributes["hvac_modes"] == [
        "off",
        "heat",
        "cool",
    ]


async def test_hold_takes_over_current_direction_and_limits_turn_on(hass, monkeypatch):
    calls = []
    await _setup_two_zones(hass, monkeypatch, calls)
    select = _entity_id(hass, "select", "mysair_operating_mode_INST_A")
    _fire_zones(hass, _mode_zone("DEV_1", "0", False), _mode_zone("DEV_2", "0", False))
    await hass.async_block_till_done()
    await _hold(hass)

    assert hass.states.get(select).state == "heat"
    # All zones off: only the held direction is offered.
    assert hass.states.get("climate.salon").attributes["hvac_modes"] == ["off", "heat"]

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": "climate.salon", "hvac_mode": "cool"},
            blocking=True,
        )
    assert calls == []


async def test_held_direction_changes_anytime_without_sending(hass, monkeypatch):
    calls = []
    await _setup_two_zones(hass, monkeypatch, calls)
    select = _entity_id(hass, "select", "mysair_operating_mode_INST_A")
    _fire_zones(hass, _mode_zone("DEV_1", "0", True), _mode_zone("DEV_2", "0", False))
    await hass.async_block_till_done()
    await _hold(hass)

    # A zone runs in heat; the heat pump was switched to cool at its own
    # controller: the held direction can still be changed.
    await _select_medium(hass, select, "cool")
    assert calls == []
    assert hass.states.get(select).state == "cool"

    mismatch = _entity_id(hass, "binary_sensor", "mysair_direction_mismatch_INST_A")
    assert hass.states.get(mismatch).state == "on"

    # No zone can be turned on while the installation runs against the held
    # direction (neither in the held nor in the running direction).
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": "switch.cocina_power"}, blocking=True
        )
    assert err.value.translation_key == "direction_locked"
    assert hass.states.get("climate.cocina").attributes["hvac_modes"] == ["off"]
    assert calls == []


async def test_mismatch_sensor_reports_zone_started_elsewhere(hass, monkeypatch):
    await _setup_two_zones(hass, monkeypatch, [])
    mismatch = _entity_id(hass, "binary_sensor", "mysair_direction_mismatch_INST_A")
    _fire_zones(hass, _mode_zone("DEV_1", "1", False), _mode_zone("DEV_2", "1", False))
    await hass.async_block_till_done()
    await _hold(hass)
    assert hass.states.get(mismatch).state == "off"

    # Someone turns a zone on in heat at the wall thermostat.
    _fire_zones(hass, _mode_zone("DEV_2", "0", True))
    await hass.async_block_till_done()
    assert hass.states.get(mismatch).state == "on"
    # The held direction stays; it is not taken over from the zones.
    select = _entity_id(hass, "select", "mysair_operating_mode_INST_A")
    assert hass.states.get(select).state == "cool"


async def test_mismatch_sensor_off_without_hold(hass, monkeypatch):
    await _setup_two_zones(hass, monkeypatch, [])
    mismatch = _entity_id(hass, "binary_sensor", "mysair_direction_mismatch_INST_A")
    _fire_zones(hass, _mode_zone("DEV_1", "0", True), _mode_zone("DEV_2", "0", False))
    await hass.async_block_till_done()
    assert hass.states.get(mismatch).state == "off"


async def test_held_turn_on_uses_held_direction(hass, monkeypatch):
    calls = []
    await _setup_two_zones(hass, monkeypatch, calls)
    select = _entity_id(hass, "select", "mysair_operating_mode_INST_A")
    _fire_zones(hass, _mode_zone("DEV_1", "0", False), _mode_zone("DEV_2", "0", False))
    await hass.async_block_till_done()
    await _hold(hass)
    await _select_medium(hass, select, "cool")

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.salon_power"}, blocking=True
    )
    assert calls[-1]["value"] == "1"


async def test_hold_and_held_direction_are_restored(hass, monkeypatch):
    from pytest_homeassistant_custom_component.common import (
        mock_restore_cache_with_extra_data,
    )
    from homeassistant.core import State

    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State("switch.mysair_inst_a_mysair_switches_heat_pump", "off"),
                {"mysair_switches_heat_pump": False},
            ),
            (
                State("select.mysair_inst_a_operating_mode", "cool"),
                {"held_is_heat": False},
            ),
        ],
    )
    calls = []
    await _setup_two_zones(hass, monkeypatch, calls)
    _fire_zones(hass, _mode_zone("DEV_1", "0", False), _mode_zone("DEV_2", "0", False))
    await hass.async_block_till_done()

    switch = _entity_id(hass, "switch", "mysair_hold_direction_INST_A")
    select = _entity_id(hass, "select", "mysair_operating_mode_INST_A")
    assert switch == "switch.mysair_inst_a_mysair_switches_heat_pump"
    assert hass.states.get(switch).state == "off"
    assert hass.states.get(select).state == "cool"
    assert hass.states.get("climate.salon").attributes["hvac_modes"] == ["off", "cool"]


async def test_hold_saved_by_3_3_0_is_migrated(hass, monkeypatch):
    # Version 3.3.0 saved the inverted switch ("Betriebsart festhalten" on)
    # without extra data: it must come back as "MySair schaltet Wärmepumpe"
    # off, i.e. the direction stays held.
    from pytest_homeassistant_custom_component.common import (
        mock_restore_cache_with_extra_data,
    )
    from homeassistant.core import State

    mock_restore_cache_with_extra_data(
        hass,
        [
            (State("switch.mysair_inst_a_mysair_switches_heat_pump", "on"), {}),
            (
                State("select.mysair_inst_a_operating_mode", "cool"),
                {"held_is_heat": False},
            ),
        ],
    )
    await _setup_two_zones(hass, monkeypatch, [])
    _fire_zones(hass, _mode_zone("DEV_1", "0", False), _mode_zone("DEV_2", "0", False))
    await hass.async_block_till_done()

    switch = _entity_id(hass, "switch", "mysair_hold_direction_INST_A")
    assert hass.states.get(switch).state == "off"
    assert hass.states.get("climate.salon").attributes["hvac_modes"] == ["off", "cool"]

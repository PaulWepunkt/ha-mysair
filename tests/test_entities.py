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
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import async_update_entity
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.mysair.const import (
    CONF_ALLOW_FLOOR_COOLING,
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
    assert set(state.attributes["fan_modes"]) == {"1", "2", "3", "auto"}


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


async def test_climate_fan_mode_none_when_wire_is_zero(hass, monkeypatch):
    await _setup_entry(hass, monkeypatch)

    _fire_status(hass, "INST_A", _zone(allow_fan=True, fan_mode="0"))
    await hass.async_block_till_done()

    assert hass.states.get("climate.salon").attributes["fan_mode"] is None


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
        allow_floor=True,
        **overrides,
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

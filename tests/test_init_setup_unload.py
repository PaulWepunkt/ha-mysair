"""P2 lifecycle tests (Home Assistant harness, see Dockerfile.test).

No real network: MySairAPI and MySairMQTTClient are patched. The goal is to
validate the lifecycle (setup/unload, ConfigEntryAuthFailed/NotReady), not
the protocol (already covered in tests/test_api.py, tests/test_status_parser.py).
"""

import pytest

pytest.importorskip("homeassistant")

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mysair.const import DOMAIN, SERVICE_STOP_INSTALLATION
from custom_components.mysair.api import (
    MySairAPI,
    MySairAuthError,
    MySairConnectionError,
)
from custom_components.mysair.mqtt_handler import MySairMQTTClient


def _mock_refresh_tokens_ok(self):
    self.access_token = "ACCESS"
    self.refresh_token_value = "REFRESH_ROTATED"
    self._notify_tokens()  # the real code calls it after renewing; the mock replaces the whole method
    return True


def _mock_refresh_tokens_raises(exc):
    def _refresh(self):
        raise exc

    return _refresh


def _patch_happy_api(monkeypatch):
    monkeypatch.setattr(MySairAPI, "refresh_tokens", _mock_refresh_tokens_ok)
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


def _make_entry(refresh_token="OLD_REFRESH", extra_data=None):
    data = {"email": "user@example.com", "refresh_token": refresh_token}
    if extra_data:
        data.update(extra_data)
    return MockConfigEntry(domain=DOMAIN, unique_id="user@example.com", data=data)


def _fire_status(hass, ctl, zone):
    hass.bus.async_fire(
        f"{DOMAIN}_update",
        {
            "topic": f"pro/v1/get/ctl/{ctl}/status",
            "data": {"ctl": ctl, "zones": [zone]},
        },
    )


async def test_setup_entry_success(hass, monkeypatch):
    _patch_happy_api(monkeypatch)
    entry = _make_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    stored = hass.data[DOMAIN][entry.entry_id]
    assert stored["installations"] == ["INST_A"]
    assert stored["devices"] == {"INST_A": [{"reference": "DEV_1", "name": "Salon"}]}
    # The rotated refresh_token is persisted in the config entry (on_tokens_refreshed callback).
    assert entry.data["refresh_token"] == "REFRESH_ROTATED"


async def test_setup_entry_migrates_legacy_password_out(hass, monkeypatch):
    # A6: entries created before the change stored password/access_token in plain text.
    _patch_happy_api(monkeypatch)
    entry = _make_entry(
        extra_data={"password": "plaintext-leftover", "access_token": "OLD_TOKEN"}
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert "password" not in entry.data
    assert "access_token" not in entry.data


async def test_setup_entry_missing_refresh_token_triggers_reauth(hass, monkeypatch):
    entry = _make_entry(refresh_token=None)
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_setup_entry_invalid_session_triggers_reauth(hass, monkeypatch):
    monkeypatch.setattr(
        MySairAPI,
        "refresh_tokens",
        _mock_refresh_tokens_raises(MySairAuthError("expired")),
    )
    entry = _make_entry()
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_setup_entry_connection_error_retries(hass, monkeypatch):
    monkeypatch.setattr(
        MySairAPI,
        "refresh_tokens",
        _mock_refresh_tokens_raises(MySairConnectionError("boom")),
    )
    entry = _make_entry()
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_entry_no_locations_retries(hass, monkeypatch):
    _patch_happy_api(monkeypatch)
    monkeypatch.setattr(MySairAPI, "get_locations", lambda self: [])
    entry = _make_entry()
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_entry_no_installations_retries(hass, monkeypatch):
    _patch_happy_api(monkeypatch)
    monkeypatch.setattr(MySairAPI, "get_installations", lambda self, location_id: [])
    entry = _make_entry()
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


def _dead_session(self, *args, **kwargs):
    """Response of an endpoint whose session is no longer valid (Passport: 404)."""
    raise MySairAuthError(
        "Locations error: 404 No query results for model "
        "[Laravel\\Passport\\RefreshToken]"
    )


@pytest.mark.parametrize(
    "method", ["get_locations", "get_installations", "get_devices"]
)
async def test_setup_entry_auth_error_during_discovery_starts_reauth(
    hass, monkeypatch, method
):
    """Regression test: a dead session during discovery must request reauth.

    Discovery used to return [] on any failure, so a dead session was read as
    an "empty account" → SETUP_RETRY in a loop, without HA ever offering the
    reauthenticate button.
    """
    _patch_happy_api(monkeypatch)
    monkeypatch.setattr(MySairAPI, method, _dead_session)
    entry = _make_entry()
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f for f in flows if f["context"]["source"] == "reauth"]


async def test_setup_entry_discovery_connection_error_retries(hass, monkeypatch):
    """A backend outage still means retry, not reauth."""
    _patch_happy_api(monkeypatch)
    monkeypatch.setattr(
        MySairAPI,
        "get_locations",
        lambda self: (_ for _ in ()).throw(MySairConnectionError("503")),
    )
    entry = _make_entry()
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_mqtt_auth_failure_starts_reauth(hass, monkeypatch):
    """If the session dies at runtime, the MQTT thread requests reauth without a restart."""
    _patch_happy_api(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        MySairMQTTClient,
        "start",
        lambda self: captured.update(on_auth_failure=self.on_auth_failure),
    )

    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # The MQTT thread invokes the callback from its own thread.
    assert captured["on_auth_failure"] is not None
    captured["on_auth_failure"]()
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f for f in flows if f["context"]["source"] == "reauth"]


async def test_unload_entry_cleans_up(hass, monkeypatch):
    _patch_happy_api(monkeypatch)
    stop_calls = []
    monkeypatch.setattr(MySairMQTTClient, "stop", lambda self: stop_calls.append(True))

    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert entry.entry_id not in hass.data.get(DOMAIN, {})
    assert stop_calls == [True]


async def test_reload_entry_does_not_duplicate_entities_or_service(hass, monkeypatch):
    # P3 (docs/testing-strategy.md): a reload must not leave duplicate
    # entities or dangling listeners of the previous coordinator/service, nor
    # break updates via MQTT after reloading.
    _patch_happy_api(monkeypatch)
    monkeypatch.setattr(MySairMQTTClient, "stop", lambda self: None)

    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.async_entity_ids("climate") == ["climate.salon"]
    assert hass.services.has_service(DOMAIN, SERVICE_STOP_INSTALLATION)

    # The coordinator/dispatcher re-attached cleanly after the reload.
    _fire_status(
        hass,
        "INST_A",
        {"zone_id": "DEV_1", "is_on": True, "is_heat": True, "is_cool": False},
    )
    await hass.async_block_till_done()
    assert hass.states.get("climate.salon").state != "unavailable"


# --- SERVICIO mysair.stop_installation (F5) ---


async def test_stop_installation_service_calls_api(hass, monkeypatch):
    _patch_happy_api(monkeypatch)
    calls = []
    monkeypatch.setattr(
        MySairAPI,
        "send_installation_command",
        lambda self, ctl, command_type, value=None, device="": calls.append(
            (ctl, command_type, device)
        ),
    )

    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # The periodic refresh (__init__.py) also sends "status"; only "stop" counts.

    assert hass.services.has_service(DOMAIN, SERVICE_STOP_INSTALLATION)
    await hass.services.async_call(
        DOMAIN, SERVICE_STOP_INSTALLATION, {"installation_ref": "INST_A"}, blocking=True
    )
    # No status yet: the main zone is unknown, the device stays empty.
    assert [c for c in calls if c[1] == "stop"] == [("INST_A", "stop", "")]

    # Like the official app, the stop goes to the main zone (pl="1").
    hass.bus.async_fire(
        f"{DOMAIN}_update",
        {
            "topic": "pro/v1/get/ctl/INST_A/status",
            "data": {
                "ctl": "INST_A",
                "zones": [
                    {"zone_id": "DEV_1", "is_main": False},
                    {"zone_id": "DEV_2", "is_main": True},
                ],
            },
        },
    )
    await hass.async_block_till_done()
    calls.clear()
    await hass.services.async_call(
        DOMAIN, SERVICE_STOP_INSTALLATION, {"installation_ref": "INST_A"}, blocking=True
    )
    assert [c for c in calls if c[1] == "stop"] == [("INST_A", "stop", "DEV_2")]


async def test_stop_installation_service_unknown_installation_raises(hass, monkeypatch):
    _patch_happy_api(monkeypatch)
    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_STOP_INSTALLATION,
            {"installation_ref": "NO_EXISTE"},
            blocking=True,
        )


async def test_stop_installation_service_removed_after_last_unload(hass, monkeypatch):
    _patch_happy_api(monkeypatch)
    monkeypatch.setattr(MySairMQTTClient, "stop", lambda self: None)

    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.services.has_service(DOMAIN, SERVICE_STOP_INSTALLATION)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.services.has_service(DOMAIN, SERVICE_STOP_INSTALLATION)


# --- Several installations in one account / topology change (P3) ---


async def test_setup_entry_multiple_installations(hass, monkeypatch):
    # The discovery code (loop over `installations`) and the coordinator's
    # filter by `ctl` already supported N>1 installations; only one was tested
    # before.
    _patch_happy_api(monkeypatch)
    monkeypatch.setattr(
        MySairAPI,
        "get_installations",
        lambda self, location_id: [{"reference": "INST_A"}, {"reference": "INST_B"}],
    )

    def _get_devices(self, ref):
        if ref == "INST_A":
            return [{"reference": "DEV_1", "name": "Salon"}]
        return [{"reference": "DEV_2", "name": "Dormitorio"}]

    monkeypatch.setattr(MySairAPI, "get_devices", _get_devices)

    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    stored = hass.data[DOMAIN][entry.entry_id]
    assert stored["installations"] == ["INST_A", "INST_B"]
    assert stored["devices"] == {
        "INST_A": [{"reference": "DEV_1", "name": "Salon"}],
        "INST_B": [{"reference": "DEV_2", "name": "Dormitorio"}],
    }
    assert hass.states.get("climate.salon") is not None
    assert hass.states.get("climate.dormitorio") is not None

    # A status of INST_B must only update its own zone.
    _fire_status(
        hass,
        "INST_B",
        {"zone_id": "DEV_2", "is_on": True, "is_heat": True, "is_cool": False},
    )
    await hass.async_block_till_done()

    assert hass.states.get("climate.dormitorio").state != "unavailable"
    assert hass.states.get("climate.salon").state == "unavailable"  # no own status yet


async def test_topology_change_removes_orphaned_zone_device_and_entities(
    hass, monkeypatch
):
    # _cleanup_stale_zone_devices (__init__.py): if a zone disappears from
    # get_devices() between restarts, its device (and all its entities:
    # climate + 4 sensors + 2 switches) is removed from the registry instead of
    # staying orphaned forever; the new zone is created normally.
    _patch_happy_api(monkeypatch)
    monkeypatch.setattr(MySairMQTTClient, "stop", lambda self: None)

    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    _fire_status(
        hass,
        "INST_A",
        {"zone_id": "DEV_1", "is_on": True, "is_heat": True, "is_cool": False},
    )
    await hass.async_block_till_done()
    assert hass.states.get("climate.salon").state != "unavailable"

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("climate", DOMAIN, "mysair_INST_A_DEV_1")
        == "climate.salon"
    )

    # Topology change: DEV_1 disappears, a new DEV_2 appears.
    monkeypatch.setattr(
        MySairAPI,
        "get_devices",
        lambda self, ref: [{"reference": "DEV_2", "name": "Dormitorio"}],
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    # The new zone is created and works normally.
    assert hass.states.get("climate.dormitorio") is not None
    _fire_status(
        hass,
        "INST_A",
        {"zone_id": "DEV_2", "is_on": True, "is_heat": True, "is_cool": False},
    )
    await hass.async_block_till_done()
    assert hass.states.get("climate.dormitorio").state != "unavailable"

    # The device and entities of the removed zone no longer exist.
    assert (
        registry.async_get_entity_id("climate", DOMAIN, "mysair_INST_A_DEV_1") is None
    )
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, "mysair_temp_INST_A_DEV_1")
        is None
    )
    assert hass.states.get("climate.salon") is None


async def test_existing_entity_ids_survive_the_naming_change(hass, monkeypatch):
    """An existing installation keeps its entity_ids after adopting has_entity_name.

    Home Assistant 2026 builds the entity_id from area + device + entity, and
    the integration uses ``has_entity_name`` so this composition yields clean
    names ("Salon" + entity name). The entity registry takes precedence over any
    suggestion of the integration as long as the ``unique_id`` does not change,
    so existing installations must not see any renamed entity_id (their
    automations and dashboards keep working).
    """
    _patch_happy_api(monkeypatch)
    entry = _make_entry()
    entry.add_to_hass(hass)

    registry = er.async_get(hass)
    legacy = {
        ("climate", "mysair_INST_A_DEV_1"): "salon",
        ("sensor", "mysair_temp_INST_A_DEV_1"): "salon_temperatura_actual",
        ("switch", "mysair_switch_INST_A_DEV_1"): "salon",
        ("switch", "mysair_floor_INST_A_DEV_1"): "salon_suelo",
    }
    for (domain, unique_id), object_id in legacy.items():
        registry.async_get_or_create(
            domain, DOMAIN, unique_id, suggested_object_id=object_id, config_entry=entry
        )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    for (domain, unique_id), object_id in legacy.items():
        assert registry.async_get_entity_id(domain, DOMAIN, unique_id) == (
            f"{domain}.{object_id}"
        )

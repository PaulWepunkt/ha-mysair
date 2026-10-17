"""P2 tests of the config flow (Home Assistant harness, see Dockerfile.test)."""

import pytest

pytest.importorskip("homeassistant")

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mysair.const import (
    CONF_ALLOW_AIR_COOLING,
    CONF_ALLOW_AIR_HEATING,
    CONF_ALLOW_FLOOR_COOLING,
    CONF_ALLOW_FLOOR_HEATING,
    DOMAIN,
)
from custom_components.mysair.api import (
    MySairAPI,
    MySairAuthError,
    MySairConnectionError,
)


@pytest.fixture(autouse=True)
def skip_entry_setup(monkeypatch):
    """The flow tests do not exercise the setup of the created entry.

    Without this, creating or reloading the entry runs the real setup, which
    tries to reach the MySair backend (covered by test_init_setup_unload.py).
    """
    import custom_components.mysair as integration

    async def _setup_entry(hass, entry):
        return True

    monkeypatch.setattr(integration, "async_setup_entry", _setup_entry)


def _mock_login_ok(refresh_token="REFRESH_NEW"):
    def _login(self):
        self.access_token = "ACCESS_NEW"
        self.refresh_token_value = refresh_token
        return True

    return _login


def _mock_login_raises(exc):
    def _login(self):
        raise exc

    return _login


async def test_user_flow_success_creates_entry(hass, monkeypatch):
    monkeypatch.setattr(MySairAPI, "login", _mock_login_ok())

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "user@example.com", "password": "secret"}
    )
    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == "MySair"
    # A6: neither password nor access_token is stored, only email + refresh_token.
    assert result2["data"] == {
        "email": "user@example.com",
        "refresh_token": "REFRESH_NEW",
    }


async def test_user_flow_invalid_auth(hass, monkeypatch):
    monkeypatch.setattr(
        MySairAPI, "login", _mock_login_raises(MySairAuthError("bad creds"))
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "user@example.com", "password": "wrong"}
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(hass, monkeypatch):
    monkeypatch.setattr(
        MySairAPI, "login", _mock_login_raises(MySairConnectionError("boom"))
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "user@example.com", "password": "secret"}
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "cannot_connect"}


async def test_user_flow_duplicate_account_aborts(hass, monkeypatch):
    # C2: the same account (same email, lower-cased) cannot be added twice.
    existing = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"email": "user@example.com", "refresh_token": "OLD"},
    )
    existing.add_to_hass(hass)

    monkeypatch.setattr(MySairAPI, "login", _mock_login_ok())

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "USER@example.com", "password": "secret"}
    )
    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


async def test_reauth_flow_success_updates_refresh_token(hass, monkeypatch):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"email": "user@example.com", "refresh_token": "STALE"},
    )
    entry.add_to_hass(hass)

    monkeypatch.setattr(
        MySairAPI, "login", _mock_login_ok(refresh_token="REFRESH_AFTER_REAUTH")
    )

    result = await entry.start_reauth_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "the-new-password"}
    )
    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reauth_successful"
    assert entry.data["refresh_token"] == "REFRESH_AFTER_REAUTH"


async def test_reauth_flow_invalid_auth_shows_error(hass, monkeypatch):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"email": "user@example.com", "refresh_token": "STALE"},
    )
    entry.add_to_hass(hass)

    monkeypatch.setattr(
        MySairAPI, "login", _mock_login_raises(MySairAuthError("still bad"))
    )

    result = await entry.start_reauth_flow(hass)
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "still-wrong"}
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "invalid_auth"}
    # The refresh_token does not change if reauthentication fails.
    assert entry.data["refresh_token"] == "STALE"


async def test_options_flow_floor_cooling_defaults_off_and_saves(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"email": "user@example.com", "refresh_token": "R"},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    # Defaults keep the behaviour of the app; floor cooling stays off.
    assert defaults == {
        CONF_ALLOW_AIR_HEATING: True,
        CONF_ALLOW_FLOOR_HEATING: True,
        CONF_ALLOW_AIR_COOLING: True,
        CONF_ALLOW_FLOOR_COOLING: False,
    }

    user_input = {
        CONF_ALLOW_AIR_HEATING: False,
        CONF_ALLOW_FLOOR_HEATING: True,
        CONF_ALLOW_AIR_COOLING: True,
        CONF_ALLOW_FLOOR_COOLING: True,
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=user_input
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == user_input


@pytest.mark.parametrize(
    "user_input, error",
    [
        (
            {
                CONF_ALLOW_AIR_HEATING: False,
                CONF_ALLOW_FLOOR_HEATING: False,
                CONF_ALLOW_AIR_COOLING: True,
                CONF_ALLOW_FLOOR_COOLING: False,
            },
            "no_medium_heat",
        ),
        (
            {
                CONF_ALLOW_AIR_HEATING: True,
                CONF_ALLOW_FLOOR_HEATING: True,
                CONF_ALLOW_AIR_COOLING: False,
                CONF_ALLOW_FLOOR_COOLING: False,
            },
            "no_medium_cool",
        ),
    ],
)
async def test_options_flow_requires_a_medium_per_direction(hass, user_input, error):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"email": "user@example.com", "refresh_token": "R"},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=user_input
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert entry.options == {}

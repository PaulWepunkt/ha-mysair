import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback

from .const import (
    DOMAIN,
    MEDIA_PERMISSION_DEFAULTS,
    MEDIA_PERMISSION_KEYS,
)
from .api import MySairAPI, MySairAuthError, MySairConnectionError

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema(
    {
        vol.Required("email"): str,
        vol.Required("password"): str,
    }
)

REAUTH_SCHEMA = vol.Schema({vol.Required("password"): str})


class MySairConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for the MySair integration."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Options of an existing entry (behaviour settings, no credentials)."""
        return MySairOptionsFlow()

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        """First step of the config flow (sign-in)."""
        errors = {}

        if user_input is not None:
            email = user_input["email"]
            password = user_input["password"]

            api = MySairAPI(email, password)

            try:
                # Run the login in an executor thread (does not block the event loop)
                await self.hass.async_add_executor_job(api.login)
            except MySairAuthError:
                _LOGGER.error("[MySair ConfigFlow] Login failed: invalid credentials.")
                errors["base"] = "invalid_auth"
            except MySairConnectionError as e:
                _LOGGER.error(
                    f"[MySair ConfigFlow] Could not connect to the MySair API: {e}"
                )
                errors["base"] = "cannot_connect"
            except Exception as e:
                _LOGGER.exception(f"[MySair ConfigFlow] Unexpected error: {e}")
                errors["base"] = "unknown"
            else:
                _LOGGER.info("[MySair ConfigFlow] Login successful for %s", email)

                await self.async_set_unique_id(email.lower())
                self._abort_if_unique_id_configured()

                # The password is not stored: only the refresh_token, which
                # renews the session on every startup without plain-text
                # credentials (see docs/security-and-privacy.md §3).
                return self.async_create_entry(
                    title="MySair",
                    data={
                        "email": email,
                        "refresh_token": api.refresh_token_value,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data) -> ConfigFlowResult:
        """Triggered when the stored session is no longer valid (ConfigEntryAuthFailed)."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None) -> ConfigFlowResult:
        """Ask for the password again to restore the refresh_token."""
        errors = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            email = reauth_entry.data.get("email")
            password = user_input["password"]
            api = MySairAPI(email, password)

            try:
                await self.hass.async_add_executor_job(api.login)
            except MySairAuthError:
                errors["base"] = "invalid_auth"
            except MySairConnectionError as e:
                _LOGGER.error(
                    f"[MySair ConfigFlow] Could not connect to the MySair API: {e}"
                )
                errors["base"] = "cannot_connect"
            except Exception as e:
                _LOGGER.exception(
                    f"[MySair ConfigFlow] Unexpected error during reauth: {e}"
                )
                errors["base"] = "unknown"
            else:
                # The account cannot change on reauth: the email comes from the
                # existing entry, but it is still checked so that a backend
                # returning another identity cannot reassign the entry.
                await self.async_set_unique_id(email.lower())
                self._abort_if_unique_id_mismatch(reason="account_mismatch")

                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data={
                        **reauth_entry.data,
                        "email": email,
                        "refresh_token": api.refresh_token_value,
                    },
                    reason="reauth_successful",
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"email": reauth_entry.data.get("email", "")},
        )


class MySairOptionsFlow(OptionsFlow):
    """Options of the MySair integration: media allowed for heating and cooling.

    Entities read the options at the moment a command is sent, so no reload
    is needed after a change.
    """

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            # Each direction needs at least one medium, otherwise no zone
            # could ever run in it.
            for is_heat, (air_key, floor_key) in MEDIA_PERMISSION_KEYS.items():
                if not user_input.get(air_key) and not user_input.get(floor_key):
                    errors["base"] = "no_medium_heat" if is_heat else "no_medium_cool"
            if not errors:
                return self.async_create_entry(data=user_input)

        current = {**MEDIA_PERMISSION_DEFAULTS, **self.config_entry.options}
        if user_input is not None:
            current.update(user_input)
        schema = vol.Schema(
            {
                vol.Required(key, default=bool(current[key])): bool
                for key in (
                    *MEDIA_PERMISSION_KEYS[True],
                    *MEDIA_PERMISSION_KEYS[False],
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

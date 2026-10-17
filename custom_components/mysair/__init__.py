import asyncio
import logging
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import device_registry as dr

from .api import MySairAPI, MySairAuthError, MySairConnectionError
from .coordinator import MySairCoordinator
from .device import (
    account_device_info,
    installation_device_info,
    installation_identifier,
    zone_device_info,
)
from .mqtt_handler import MySairMQTTClient
from .status_parser import parse_status_payload, parse_feedback_payload
from .const import DOMAIN, SERVICE_STOP_INSTALLATION, ATTR_INSTALLATION_REF

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["binary_sensor", "climate", "sensor", "switch"]

STOP_INSTALLATION_SCHEMA = vol.Schema({vol.Required(ATTR_INSTALLATION_REF): str})


@callback
def _persist_refresh_token(
    hass: HomeAssistant, entry: ConfigEntry, refresh_token
) -> None:
    """Persist the refresh_token if it rotated (every renewal invalidates the previous one)."""
    if not refresh_token or refresh_token == entry.data.get("refresh_token"):
        return
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "refresh_token": refresh_token}
    )


def _register_devices(
    hass: HomeAssistant, entry: ConfigEntry, installation_names: dict, all_devices
) -> None:
    """Create the device tree before the platforms load.

    Account (cloud connection) → installation (controller) → zones. The links
    (``via_device_id``) need the registry ids of the parents, so the devices
    are created here; the entities later register the same identifiers,
    which leaves the links untouched.
    """
    device_registry = dr.async_get(hass)
    account = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, **account_device_info(entry.entry_id)
    )
    for inst_ref, name in installation_names.items():
        installation = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            via_device_id=account.id,
            **installation_device_info(inst_ref, name),
        )
        for dev in all_devices.get(inst_ref, []):
            dev_id = dev.get("reference") or dev.get("rf") or dev.get("id")
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                via_device_id=installation.id,
                **zone_device_info(inst_ref, dev_id, dev.get("name", f"Zone {dev_id}")),
            )


def _cleanup_stale_zone_devices(
    hass: HomeAssistant, entry: ConfigEntry, all_devices: dict
) -> None:
    """Remove devices (and their entities) of zones that no longer exist.

    Topology change between restarts (zone removed from the MySair account):
    Home Assistant does not remove entities that an integration stops
    creating on its own (see docs/testing-strategy.md) - they stay orphaned
    forever unless cleaned up. Every zone has its own device identified by
    ``(DOMAIN, f"{inst_ref}_{device_id}")`` (see ``device.py``); the account
    device of the cloud connection sensor uses ``entry.entry_id`` as its
    identifier and is never touched here, since it is not a zone.
    Installation devices of installations that still exist are kept too.
    """
    current_zone_ids = {
        f"{inst_ref}_{dev.get('reference') or dev.get('rf') or dev.get('id')}"
        for inst_ref, devices in all_devices.items()
        for dev in devices
    } | {installation_identifier(inst_ref) for inst_ref in all_devices}
    device_registry = dr.async_get(hass)
    for device_entry in dr.async_entries_for_config_entry(
        device_registry, entry.entry_id
    ):
        zone_ids = {
            identifier
            for (domain, identifier) in device_entry.identifiers
            if domain == DOMAIN
        }
        if entry.entry_id in zone_ids:
            continue  # account device (cloud connection sensor), not a zone
        if not zone_ids & current_zone_ids:
            _LOGGER.info(
                f"[MySair] Removing orphaned device (zone no longer exists): {zone_ids}"
            )
            # Since HA 2026.8 a device belongs to a single config entry, so
            # "removing it from the entry" no longer makes sense and
            # `async_update_device(remove_config_entry_id=...)` is deprecated
            # (removed in 2027.8). The equivalent now is deleting it.
            device_registry.async_remove_device(device_entry.id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up the MySair integration."""
    email = entry.data.get("email")
    refresh_token = entry.data.get("refresh_token")
    if not email or not refresh_token:
        raise ConfigEntryAuthFailed(
            "Missing credentials; reauthenticate the MySair integration."
        )

    _LOGGER.info(f"[MySair] Authenticating user {email}")

    def _on_tokens_refreshed(access_token, refresh_token_value):
        # Called from an executor thread: switch back to the loop to touch hass.
        hass.loop.call_soon_threadsafe(
            _persist_refresh_token, hass, entry, refresh_token_value
        )

    api = MySairAPI(email, on_tokens_refreshed=_on_tokens_refreshed)
    api.refresh_token_value = refresh_token

    # --- SESSION: renew tokens from the stored refresh_token (A6: the password
    # is neither stored nor used after the initial setup). ---
    try:
        await hass.async_add_executor_job(api.refresh_tokens)
    except MySairAuthError as err:
        raise ConfigEntryAuthFailed(
            f"MySair session invalid or expired: {err}"
        ) from err
    except MySairConnectionError as err:
        raise ConfigEntryNotReady(f"Could not connect to MySair: {err}") from err

    # Migration: entries created before A6 stored password/access_token in
    # plain text; they are no longer used and are removed from the config
    # entry on the first successful startup after the update.
    stale_keys = {"password", "access_token"} & entry.data.keys()
    if stale_keys:
        hass.config_entries.async_update_entry(
            entry, data={k: v for k, v in entry.data.items() if k not in stale_keys}
        )

    # --- STRUCTURE: Locations → Installations → Devices ---
    # The discovery methods propagate MySairAuthError/MySairConnectionError
    # (they used to return [] on any failure, which turned a dead session into
    # an "empty account" → ConfigEntryNotReady → retry loop without reauth).
    # An empty list here now only means an account without data.
    try:
        locations = await hass.async_add_executor_job(api.get_locations)
        if not locations:
            raise ConfigEntryNotReady("No locations found in the MySair account.")

        first_loc = locations[0]
        location_id = first_loc["id"]
        installations = await hass.async_add_executor_job(
            api.get_installations, location_id
        )
        if not installations:
            raise ConfigEntryNotReady("No installations found in the MySair location.")

        _LOGGER.info(
            f"[MySair] Installations found: {[i['reference'] for i in installations]}"
        )

        all_devices = {}
        installation_refs = []
        installation_names = {}
        for inst in installations:
            ref = inst["reference"]
            installation_refs.append(ref)
            installation_names[ref] = inst.get("name")
            devices = await hass.async_add_executor_job(api.get_devices, ref)
            all_devices[ref] = devices
            _LOGGER.info(
                f"[MySair] Installation {ref}: {len(devices)} thermostats found"
            )
    except MySairAuthError as err:
        raise ConfigEntryAuthFailed(
            f"MySair session invalid during discovery: {err}"
        ) from err
    except MySairConnectionError as err:
        raise ConfigEntryNotReady(f"Could not connect to MySair: {err}") from err

    _cleanup_stale_zone_devices(hass, entry, all_devices)
    _register_devices(hass, entry, installation_names, all_devices)

    # Store runtime data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "devices": all_devices,
        "installations": installation_refs,
        "installation_names": installation_names,
        "mqtt": None,
        "coordinator": None,
    }

    # --- MQTT CALLBACK (parses status messages) ---
    def mqtt_message_callback(data):
        """Process incoming messages from AWS IoT."""
        try:
            topic = data.get("topic", "")
            payload = data.get("payload", {})
            _LOGGER.debug(f"[MySair MQTT] {topic} -> {payload}")

            # Parse "status" messages
            if topic.endswith("/status"):
                parsed_data = parse_status_payload(payload)
                # E4: a payload that is not even a dict is rejected
                # (parse_status_payload returns None) - no event is fired
                # instead of propagating a meaningless empty value.
                if parsed_data is None:
                    _LOGGER.warning(
                        f"[MySair MQTT] Status payload rejected (unexpected shape): {topic}"
                    )
                    return
                hass.loop.call_soon_threadsafe(
                    hass.bus.async_fire,
                    f"{DOMAIN}_update",
                    {"topic": topic, "data": parsed_data},
                )
                _LOGGER.debug(f"[MySair MQTT] Parsed state: {parsed_data}")

            # Acknowledgement (ACK) of an instruction sent over HTTP (E7,
            # docs/protocol-findings.md §8). Separate event, not mysair_update.
            elif topic.endswith("/feedback"):
                feedback = parse_feedback_payload(payload)
                if feedback is None:
                    _LOGGER.warning(
                        f"[MySair MQTT] Feedback payload rejected (unexpected shape): {topic}"
                    )
                    return
                _LOGGER.debug(
                    f"[MySair MQTT] Confirmation received: orderId={feedback['order_id']} "
                    f"ctl={feedback['ctl']}"
                )
                hass.loop.call_soon_threadsafe(
                    hass.bus.async_fire,
                    f"{DOMAIN}_feedback",
                    feedback,
                )

            else:
                # Other messages (not status)
                hass.loop.call_soon_threadsafe(
                    hass.bus.async_fire,
                    f"{DOMAIN}_update",
                    {"topic": topic, "data": payload},
                )

        except Exception as e:
            _LOGGER.error(f"[MySair MQTT] Error in callback: {e}")

    # --- LIVE REAUTH ---
    # The session can die while the integration is running (MySair invalidates
    # the refresh_token on the server: sign-out from the official app,
    # password change, cleanup of old tokens). Without this nobody noticed
    # until the next HA restart, because both the MQTT thread and the
    # periodic task retry silently forever.
    def _request_reauth():
        """Ask HA to start the reauth flow (from any thread).

        ``async_start_reauth`` already deduplicates if a reauth or reconfigure
        flow is open, so no own bookkeeping is needed.
        """
        hass.loop.call_soon_threadsafe(entry.async_start_reauth, hass)

    # --- MQTT CLIENT ---
    mqtt_client = MySairMQTTClient(
        api,
        installation_refs,
        mqtt_message_callback,
        on_auth_failure=_request_reauth,
    )
    hass.data[DOMAIN][entry.entry_id]["mqtt"] = mqtt_client

    # Start the MQTT thread without blocking the loop
    await hass.async_add_executor_job(mqtt_client.start)

    # --- COORDINATOR (C1): dispatches mysair_update per zone instead of every
    # entity repeating the same topic/ctl/zone_id filtering (see
    # coordinator.py). Started before the platforms so it is already
    # listening when the entities are added. ---
    coordinator = MySairCoordinator(hass, installation_refs, entry.entry_id)
    coordinator.start()
    hass.data[DOMAIN][entry.entry_id]["coordinator"] = coordinator

    # --- PLATFORMS ---
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info("[MySair] Platforms loaded")

    # --- PERIODIC STATUS REFRESH ---
    async def refresh_status_periodic():
        """Request a fallback 'status' from all installations every 2 minutes.

        The real-time state arrives over MQTT; this POST only forces a periodic
        sync in case a message gets lost.
        """
        while True:
            try:
                for ref in installation_refs:
                    await hass.async_add_executor_job(
                        api.send_installation_command, ref, "status"
                    )
                    _LOGGER.debug(f"[MySair] Status request sent to installation {ref}")
            except MySairAuthError as e:
                _LOGGER.error(
                    f"[MySair] Session invalid, reauthentication required: {e}"
                )
                _request_reauth()
            except Exception as e:
                _LOGGER.warning(f"[MySair] Failed to send status request: {e}")
            await asyncio.sleep(120)  # 2 minutes

    # Start the periodic task bound to the entry lifecycle:
    # Home Assistant cancels it automatically on unload.
    entry.async_create_background_task(
        hass, refresh_status_periodic(), name="mysair_status_refresh"
    )

    # --- SERVICE mysair.stop_installation (F5) ---
    # Shared by all config entries of the domain: registered once and
    # removed when the last entry is unloaded.
    if not hass.services.has_service(DOMAIN, SERVICE_STOP_INSTALLATION):

        async def _async_handle_stop_installation(call: ServiceCall) -> None:
            installation_ref = call.data[ATTR_INSTALLATION_REF]
            for entry_data in hass.data.get(DOMAIN, {}).values():
                if installation_ref in entry_data.get("installations", []):
                    # Like the official app, the stop goes to the main zone
                    # (pl="1"). Empty device if no status has arrived yet.
                    coordinator = entry_data.get("coordinator")
                    main_zone = (
                        coordinator.installation_state(installation_ref).main_zone_id
                        if coordinator
                        else None
                    )
                    try:
                        await hass.async_add_executor_job(
                            entry_data["api"].send_installation_command,
                            installation_ref,
                            "stop",
                            None,
                            main_zone or "",
                        )
                    except Exception as e:
                        raise HomeAssistantError(
                            f"Failed to stop installation {installation_ref}: {e}"
                        ) from e
                    return
            raise ServiceValidationError(
                f"Installation '{installation_ref}' not found in any configured MySair account."
            )

        hass.services.async_register(
            DOMAIN,
            SERVICE_STOP_INSTALLATION,
            _async_handle_stop_installation,
            schema=STOP_INSTALLATION_SCHEMA,
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the MySair integration and release resources (MQTT, tasks, state).

    Shutdown order: first the platforms are unloaded (entities disconnect
    from the coordinator), then the coordinator and the MQTT client are
    stopped, and finally the runtime state is cleared. The periodic task is
    cancelled automatically because it was created with
    entry.async_create_background_task.
    """
    _LOGGER.info("[MySair] Stopping integration and closing sessions...")

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if data:
            coordinator = data.get("coordinator")
            if coordinator:
                coordinator.stop()
            mqtt_client = data.get("mqtt")
            if mqtt_client:
                await hass.async_add_executor_job(mqtt_client.stop)
        # Service shared by all entries: only removed when the last one is
        # unloaded (F5).
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_STOP_INSTALLATION)

    return unload_ok

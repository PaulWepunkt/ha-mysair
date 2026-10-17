import requests
import datetime
import time
import hmac
import hashlib
import urllib.parse
import logging
from threading import Lock

_LOGGER = logging.getLogger(__name__)


def _truncate(text, limit=200):
    """Limit the length of a response body before logging it (D2).

    Prevents an unexpected backend error body (e.g. one that echoes part of
    the request) from leaking more than necessary into the logs.
    """
    text = str(text) if text is not None else ""
    return text if len(text) <= limit else text[:limit] + "…(truncated)"


class MySairAuthError(Exception):
    """Invalid/expired credentials or refresh_token: reauthentication required."""


class MySairConnectionError(Exception):
    """Network or backend failure, unrelated to the credentials."""


def _http_error(status_code, body, context):
    """Classify a non-2xx HTTP response as an auth or a connection error.

    A single rule for all endpoints instead of listing the codes the backend
    is "supposed" to use. Listing codes is exactly what failed before: MySair
    runs on Laravel Passport, which answers **404** (not 401) when the
    refresh_token no longer exists on the server, with the body
    ``No query results for model [Laravel\\Passport\\RefreshToken]``. Being
    classified as a connection error, it ended in ``ConfigEntryNotReady`` and
    Home Assistant retried in a loop without ever offering reauth.

    - 5xx and 429: the backend is down or rate limiting → retrying makes sense.
    - Any other non-2xx (400, 401, 403, 404, …): the credentials are not
      valid → the user has to be asked.

    Returns the constructed exception (the caller raises it).
    """
    detail = f"{context}: {status_code} {_truncate(body)}"
    if status_code >= 500 or status_code == 429:
        _LOGGER.error(f"[MySairAPI] {detail}")
        return MySairConnectionError(detail)
    _LOGGER.error(f"[MySairAPI] Invalid session - {detail}")
    return MySairAuthError(detail)


def extract_order_id(response):
    """Extract the ``orderId`` from the ``POST /send/instruction`` response.

    Confirmed from the official app (see docs/protocol-findings.md §8):
    ``entity.value[0].orderId``. Returns ``None`` if the shape does not match
    (unexpected response) instead of raising, since it is only used to
    correlate with the MQTT ACK - it is not essential for the command itself.
    """
    if not isinstance(response, dict):
        return None
    try:
        return response["entity"]["value"][0].get("orderId")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None


class MySairAPI:
    """API client for MySair."""

    # Exposed as a class attribute so consumers that receive an api instance
    # (e.g. MySairMQTTClient) can recognise a session failure without importing
    # this module: `mqtt_handler` is imported as a top-level module in the
    # P0/P1 tests and therefore cannot use relative imports (see
    # tests/conftest.py).
    AuthError = MySairAuthError

    def __init__(
        self,
        email: str,
        password: "str | None" = None,
        session: "requests.Session | None" = None,
        on_tokens_refreshed=None,
    ):
        self.email = email
        self.password = password
        self.base_url = "https://api.mysair.es/v1"
        self.access_token = None
        self.refresh_token_value = None  # avoids a clash with the method name
        self.entity = None
        self.aws_credentials = None
        self.lock = Lock()
        # Injectable session: makes mocking in tests easy (see docs/testing-strategy.md).
        self.session = session or requests.Session()
        # Optional callback (access_token, refresh_token) -> None, invoked after
        # login()/refresh_tokens(). The refresh_token rotates on every renewal,
        # so the caller (e.g. __init__.py) must persist the new value.
        self.on_tokens_refreshed = on_tokens_refreshed

    def _notify_tokens(self):
        if self.on_tokens_refreshed:
            try:
                self.on_tokens_refreshed(self.access_token, self.refresh_token_value)
            except Exception:
                _LOGGER.exception("[MySairAPI] Failed to notify token refresh")

    # ==========================================================
    # LOGIN
    # ==========================================================
    def login(self):
        """Initial authentication with the MySair API (email + password).

        Raises ``MySairAuthError`` for invalid credentials, or
        ``MySairConnectionError`` on network or backend failures.
        """
        _LOGGER.info(f"[MySairAPI] Login {self.email}")
        try:
            resp = self.session.post(
                f"{self.base_url}/user/login",
                json={"email": self.email, "password": self.password},
                timeout=15,
            )
        except requests.RequestException as e:
            _LOGGER.error(f"[MySairAPI] Login failed: {e}")
            raise MySairConnectionError(f"Network error during login: {e}") from e

        if resp.status_code != 200:
            raise _http_error(resp.status_code, resp.text, "Login error")

        data = resp.json()
        self.entity = data.get("entity", {})
        self.access_token = self.entity.get("access_token")
        self.refresh_token_value = self.entity.get("refresh_token")

        if not self.access_token:
            raise MySairAuthError("No access_token received after login")

        _LOGGER.info("[MySairAPI] Login OK")
        self._notify_tokens()
        return True

    # ==========================================================
    # REFRESH TOKEN
    # ==========================================================
    def refresh_tokens(self):
        """Renew the access_token and refresh_token from the current refresh_token.

        Does not need the ``password`` (see `docs/security-and-privacy.md` §3):
        this is how the session is restored on every startup without storing
        the password in plain text. Raises ``MySairAuthError`` if there is no
        refresh_token or it is invalid/expired (reauthentication required), or
        ``MySairConnectionError`` on network or backend failures.
        """
        if not self.refresh_token_value:
            raise MySairAuthError("No refresh_token available.")

        _LOGGER.debug("[MySairAPI] Renewing session tokens...")
        try:
            resp = self.session.put(
                f"{self.base_url}/user/refreshtokens",
                json={"refresh_token": self.refresh_token_value},
                timeout=10,
            )
        except requests.RequestException as e:
            _LOGGER.error(f"[MySairAPI] Failed to refresh tokens: {e}")
            raise MySairConnectionError(
                f"Network error while refreshing tokens: {e}"
            ) from e

        if resp.status_code != 200:
            raise _http_error(resp.status_code, resp.text, "Refresh tokens error")

        data = resp.json()
        entity = data.get("entity", {})
        self.access_token = entity.get("access_token")
        self.refresh_token_value = entity.get("refresh_token")

        if not self.access_token:
            raise MySairAuthError("No access_token received when refreshing tokens")

        _LOGGER.info("[MySairAPI] Tokens renewed.")
        self._notify_tokens()
        return True

    # ==========================================================
    # AUTHENTICATED REQUEST (base of the data endpoints)
    # ==========================================================
    def _authed_request(self, method, path, context, timeout=10):
        """Send a request with ``Authorization: Bearer`` and classify errors.

        Bundles what every endpoint used to repeat: the header, the timeout,
        the single retry after ``refresh_tokens()`` on a 401 (same pattern as
        ``send_instruction``) and the ``_http_error`` classification.

        Raises ``MySairAuthError`` if the session is not valid (the caller must
        end up requesting reauth) or ``MySairConnectionError`` on network or
        backend failures. Returns the ``Response`` object.
        """

        def _do():
            return self.session.request(
                method,
                f"{self.base_url}/{path}",
                headers={"Authorization": f"Bearer {self.access_token}"},
                timeout=timeout,
            )

        try:
            resp = _do()
            # The access_token expires long before the refresh_token: a single
            # 401 is almost always fixed by renewing the session and retrying.
            # If refresh_tokens() fails, its own classified error propagates.
            if resp.status_code == 401:
                _LOGGER.debug(
                    f"[MySairAPI] Token expired on {path}, renewing session and retrying..."
                )
                self.refresh_tokens()
                resp = _do()
        except requests.RequestException as e:
            _LOGGER.error(f"[MySairAPI] Network error in {context}: {e}")
            raise MySairConnectionError(f"Network error in {context}: {e}") from e

        if resp.status_code != 200:
            raise _http_error(resp.status_code, resp.text, context)

        return resp

    def _authed_get_entity(self, path, context):
        """Authenticated ``GET`` returning the ``entity`` field of the response.

        Common shape of the discovery endpoints. An empty list here means "the
        account has nothing", never "the request failed": failures propagate as
        exceptions (``[]`` used to be returned in both cases, which is why a
        dead token ended in a retry loop instead of the reauth flow).
        """
        return self._authed_request("GET", path, context).json().get("entity", [])

    # ==========================================================
    # AWS CREDENTIALS
    # ==========================================================
    def refresh_aws_credentials(self):
        """Fetch temporary AWS IoT credentials.

        Raises ``MySairAuthError`` if the session is no longer valid: the MQTT
        thread calls this on every (re)connect, so this is where a session that
        died while the integration is running gets detected.
        """
        try:
            _LOGGER.debug("[MySairAPI] Requesting AWS MQTT credentials...")
            resp = self._authed_request(
                "PUT",
                "user/refreshawscredentials",
                "AWS credentials error",
                timeout=15,
            )

            data = resp.json()
            entity = data.get("entity", {})

            required_keys = [
                "aws_mqtt_host",
                "aws_default_region",
                "aws_access_key_id",
                "aws_secret_access_key",
                "aws_security_token",
                "aws_mqtt_user",
            ]

            # Check that all keys are present
            if not all(k in entity for k in required_keys):
                raise Exception("AWS credentials incomplete or invalid")

            # Normalise names for mqtt_handler.
            # aws_base_topic and aws_expires_at are optional (older APIs may not
            # send them); they are used for the dynamic topic and proactive refresh.
            self.aws_credentials = {
                "aws_mqtt_host": entity["aws_mqtt_host"],
                "aws_default_region": entity["aws_default_region"],
                "aws_access_key_id": entity["aws_access_key_id"],
                "aws_secret_access_key": entity["aws_secret_access_key"],
                "aws_security_token": entity["aws_security_token"],
                "aws_mqtt_user": entity["aws_mqtt_user"],
                "aws_base_topic": entity.get("aws_base_topic"),
                "aws_expires_at": entity.get("aws_expires_at"),
            }

            _LOGGER.debug(
                f"[MySairAPI] AWS credentials received for user {entity['aws_mqtt_user']}"
            )
            return self.aws_credentials

        except Exception as e:
            _LOGGER.error(f"[MySairAPI] Failed to get AWS credentials: {e}")
            raise

    def aws_credentials_expired(self, margin_seconds=60):
        """Whether the AWS credentials should be refreshed before (re)connecting.

        Returns True if credentials are missing or expire within
        ``margin_seconds``. Uses ``aws_expires_at`` (unix s) when present;
        without expiry information no time-based refresh is forced.
        Confirmed from the official app (see docs/protocol-findings.md).
        """
        if not self.aws_credentials:
            return True
        expires_at = self.aws_credentials.get("aws_expires_at")
        if not expires_at:
            return False
        try:
            return time.time() >= float(expires_at) - margin_seconds
        except (TypeError, ValueError):
            return False

    def seconds_until_aws_credentials_expire(self, margin_seconds=60):
        """Seconds until the MQTT connection should be refreshed proactively.

        Returns ``None`` if there are no credentials or ``aws_expires_at`` is
        unknown (no time-based refresh can be scheduled). Returns ``0`` if
        already expired or within the margin (refresh now). See
        docs/protocol-findings.md §6b: the official app schedules a
        ``setTimeout(refreshAwsCredentials, getMqttExpirationTime())`` to
        refresh the MQTT session *before* AWS drops it, instead of waiting for
        it to fail.
        """
        if not self.aws_credentials:
            return None
        expires_at = self.aws_credentials.get("aws_expires_at")
        if not expires_at:
            return None
        try:
            return max(float(expires_at) - time.time() - margin_seconds, 0)
        except (TypeError, ValueError):
            return None

    # ==========================================================
    # LOCATIONS / INSTALLATIONS / DEVICES
    # ==========================================================
    def get_locations(self):
        """Return the list of locations."""
        _LOGGER.info("[MySairAPI] Locations...")
        return self._authed_get_entity("locations", "Locations error")

    def get_installations(self, location_id):
        """Return the installations of a location."""
        _LOGGER.info(f"[MySairAPI] Installations loc={location_id}")
        return self._authed_get_entity(
            f"installations?location_id={location_id}&validated=1",
            "Installations error",
        )

    def get_devices(self, installation_ref):
        """Return the devices (thermostats) of an installation."""
        _LOGGER.info(f"[MySairAPI] Devices ref={installation_ref}")
        return self._authed_get_entity(
            f"devices?installation_ref={installation_ref}", "Devices error"
        )

    # ==========================================================
    # SEND INSTRUCTION (status requests and commands)
    # ==========================================================
    def send_instruction(self, instruction):
        """Send an instruction to the /send/instruction endpoint.
        If the token has expired, renews the tokens and AWS credentials and retries once.
        """
        try:
            _LOGGER.debug(f"[MySairAPI] Sending instruction: {instruction}")
            headers = {"Authorization": f"Bearer {self.access_token}"}

            resp = self.session.post(
                f"{self.base_url}/send/instruction",
                headers=headers,
                json=instruction,
                timeout=10,
            )

            # --- If the token expired, refresh and retry once ---
            # refresh_tokens() raises MySairAuthError/MySairConnectionError on
            # failure; it propagates unchanged (caught below as Exception).
            if resp.status_code == 401:
                _LOGGER.debug("[MySairAPI] HTTP token expired, renewing session...")
                self.refresh_tokens()
                _LOGGER.debug(
                    "[MySairAPI] HTTP token renewed, updating AWS credentials..."
                )
                self.refresh_aws_credentials()
                headers = {"Authorization": f"Bearer {self.access_token}"}
                resp = self.session.post(
                    f"{self.base_url}/send/instruction",
                    headers=headers,
                    json=instruction,
                    timeout=10,
                )

            # --- Final validation ---
            # Classified like the other endpoints: a session failure here must
            # arrive as MySairAuthError so the caller (periodic task /
            # entities) can request reauth.
            if resp.status_code != 201:
                raise _http_error(resp.status_code, resp.text, "Instruction error")

            # Success = HTTP 201 and an empty "error" list. "msg" is not
            # checked: its text depends on the language of the MySair account
            # ("Creado", "Created", ...), so comparing it rejected commands
            # that had actually been accepted.
            data = resp.json()
            error = data.get("error", [])
            if error:
                raise Exception(f"Instruction rejected: {_truncate(data)}")

            _LOGGER.debug(f"[MySairAPI] Instruction sent ({data.get('msg', '')})")
            return data

        except Exception as e:
            _LOGGER.error(f"[MySairAPI] Failed to send instruction: {e}")
            raise

    # ==========================================================
    # ZONE COMMAND HELPERS (for climate, switch, etc.)
    # ==========================================================
    def _app_name(self):
        """Return the account's MQTT user for the ``app`` field of an instruction.

        The official app sends ``session.getClientId()`` (= ``aws_mqtt_user``)
        here. If the AWS credentials are not loaded yet (e.g. a command sent
        right after startup), they are fetched first instead of falling back to
        a fixed value, which would identify a different account. If fetching
        fails, its classified error propagates and the command is not sent.
        """
        user = (self.aws_credentials or {}).get("aws_mqtt_user")
        if not user:
            _LOGGER.debug(
                "[MySairAPI] No AWS credentials loaded yet, fetching them before sending"
            )
            self.refresh_aws_credentials()
            user = self.aws_credentials["aws_mqtt_user"]
        return user

    def send_zone_command(
        self, ctl, device, command_type, value=None, temperature=None
    ):
        """
        Send a correctly formatted instruction to control a zone thermostat.

        command_type can be:
            - "mode"     → turn on with the given ``m`` (value = "0".."5": even
                           heat, odd cool; 0/1 air, 2/3 floor, 4/5 both)
                           and setpoint ``temperature``
            - "temp"     → change the setpoint (value = temperature)
            - "power"    → turn off (value = "0")
            - "fanspeed" → fan speed (value = "0".."4"; see docs/protocol-findings.md §9)
        """
        try:
            if not ctl or not device:
                raise ValueError("Missing required parameters (ctl or device).")

            app_name = self._app_name()

            if command_type == "mode":
                if value not in ("0", "1", "2", "3", "4", "5"):
                    raise ValueError("Invalid mode: use '0'..'5'.")
                if temperature is None:
                    raise ValueError("Missing setpoint for the 'mode' command.")
                payload_value = {"mode": value, "temperature": str(temperature)}

            elif command_type == "temp":
                payload_value = str(value)

            elif command_type == "power":
                payload_value = "0"

            elif command_type == "fanspeed":
                if value not in ("0", "1", "2", "3", "4"):
                    raise ValueError("Invalid fan speed: use '0'..'4'.")
                payload_value = str(value)

            else:
                raise ValueError(f"Unsupported command type: {command_type}")

            instruction = [
                {
                    "sender": "WEB",
                    "ctl": ctl,
                    "app": app_name,
                    "device": device,
                    "command": command_type,
                    "value": payload_value,
                }
            ]

            _LOGGER.debug(
                f"[MySairAPI] Sending command '{command_type}' to {device} ({ctl}) → {instruction}"
            )
            return self.send_instruction(instruction)

        except Exception as e:
            _LOGGER.error(
                f"[MySairAPI] Failed to send command {command_type} for {device}: {e}"
            )
            raise

    def send_installation_command(self, ctl, command_type, value=None, device=""):
        """Send an instruction for a whole installation.

        ``device`` is empty for ``status``. For ``stop`` the official app sends
        the main zone (``pl="1"``) as the device.

        command_type can be:
            - "stop"   → stop the whole installation (value = "1", F5)
            - "status" → request a state sync (value = "sync")
        """
        try:
            if not ctl:
                raise ValueError("Missing required parameter 'ctl'.")

            app_name = self._app_name()

            if command_type == "stop":
                payload_value = "1"
            elif command_type == "status":
                payload_value = "sync"
            else:
                raise ValueError(
                    f"Unsupported installation command type: {command_type}"
                )

            instruction = [
                {
                    "sender": "WEB",
                    "ctl": ctl,
                    "app": app_name,
                    "device": device or "",
                    "command": command_type,
                    "value": payload_value,
                }
            ]

            _LOGGER.debug(
                f"[MySairAPI] Sending installation command '{command_type}' to {ctl} → {instruction}"
            )
            return self.send_instruction(instruction)

        except Exception as e:
            _LOGGER.error(
                f"[MySairAPI] Failed to send installation command {command_type} for {ctl}: {e}"
            )
            raise

    # ==========================================================
    # SIGN AWS URL (MQTT WebSocket)
    # ==========================================================
    @staticmethod
    def aws_sign_url(host, region, access_key, secret_key, token):
        """Build a signed URL for the AWS MQTT connection."""
        service = "iotdevicegateway"
        algorithm = "AWS4-HMAC-SHA256"
        t = datetime.datetime.now(datetime.timezone.utc)
        amz_date = t.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = t.strftime("%Y%m%d")
        credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"

        canonical_querystring = (
            f"X-Amz-Algorithm={algorithm}&"
            f"X-Amz-Credential={urllib.parse.quote_plus(access_key + '/' + credential_scope)}&"
            f"X-Amz-Date={amz_date}&"
            f"X-Amz-SignedHeaders=host"
        )

        canonical_headers = f"host:{host}\n"
        payload_hash = hashlib.sha256(b"").hexdigest()
        canonical_request = f"GET\n/mqtt\n{canonical_querystring}\n{canonical_headers}\nhost\n{payload_hash}"

        string_to_sign = (
            f"{algorithm}\n{amz_date}\n{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
        )

        def sign(key, msg):
            return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

        k_date = sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
        k_region = sign(k_date, region)
        k_service = sign(k_region, service)
        k_signing = sign(k_service, "aws4_request")
        signature = hmac.new(
            k_signing, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        canonical_querystring += f"&X-Amz-Signature={signature}"
        canonical_querystring += "&" + urllib.parse.urlencode(
            {"X-Amz-Security-Token": token}
        )

        url = f"wss://{host}/mqtt?{canonical_querystring}"
        _LOGGER.info(f"[MySairAPI] Signed MQTT URL generated for {host}")
        return url

import time
import json
import random
import struct
import secrets
import datetime
import logging
import threading
import websocket

_LOGGER = logging.getLogger(__name__)


def log(msg, level="info"):
    """Logger with a readable timestamp."""
    now = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    getattr(_LOGGER, level.lower())(f"{now} {msg}")


def _redact_client_id(client_id):
    """Mask the ``access_key`` embedded in the clientId before logging it (D2).

    ``build_client_id`` builds ``mqtt-client_<accessKey>_<ts>_<rand>``; the
    access key is already treated as sensitive in ``diagnostics.py``
    (TO_REDACT_API), so it must not appear in plain text in the logs either.
    """
    parts = client_id.split("_")
    if len(parts) < 4 or parts[0] != "mqtt-client":
        return client_id
    return "_".join([parts[0], "***"] + parts[2:])


# ==========================================================
# MQTT helpers
# ==========================================================
def encode_varint(x):
    encoded = b""
    while True:
        byte = x % 128
        x //= 128
        if x > 0:
            byte |= 0x80
        encoded += bytes([byte])
        if x == 0:
            break
    return encoded


def decode_varint(data, offset=0):
    """Decode an MQTT-style variable length integer (remaining length).

    Inverse of ``encode_varint``. Returns ``(value, new_position)``, or
    ``(None, offset)`` if the available bytes do not form a valid varint
    (incomplete or longer than 4 bytes, the maximum allowed by MQTT 3.1.1).
    """
    multiplier = 1
    value = 0
    pos = offset
    while True:
        if pos >= len(data):
            return None, offset
        byte = data[pos]
        value += (byte & 0x7F) * multiplier
        pos += 1
        if not (byte & 0x80):
            break
        multiplier *= 128
        if multiplier > 128**3:
            return None, offset
    return value, pos


# Defensive cap (E2): if the receive buffer grows without completing a packet
# (corrupt/desynchronised stream), it is discarded instead of accumulating
# memory indefinitely.
MAX_RECV_BUFFER_SIZE = 65536


class FrameState:
    """Result of `_next_packet_length` when no usable length is available."""

    INCOMPLETE = "incomplete"  # bytes missing; may complete with more data
    MALFORMED = "malformed"  # the MQTT length varint can never complete


def _next_packet_length(buffer):
    """Total length (fixed header + varint + remaining_length) of the first
    MQTT packet at the start of ``buffer``, or a ``FrameState`` if it cannot
    be determined yet (E2, handling of partial/multi-packet frames).

    Distinguishes "incomplete" (wait for more bytes) from "malformed" (the
    length varint will never terminate, more data does not help):
    ``decode_varint`` returns ``(None, offset)`` in both cases, but it can
    only exhaust the 4 continuation bytes allowed by MQTT 3.1.1 if at least
    4 bytes were available after the fixed header - with fewer, it is always
    due to missing data.
    """
    if len(buffer) < 1:
        return FrameState.INCOMPLETE
    remaining_length, pos = decode_varint(buffer, 1)
    if remaining_length is None:
        return FrameState.MALFORMED if (len(buffer) - 1) >= 4 else FrameState.INCOMPLETE
    return pos + remaining_length


def parse_mqtt_publish(message):
    """Decode a real MQTT PUBLISH frame: fixed header + Topic Name + payload.

    The "ghost character" that showed up before the topic in logs (a ``(``
    for 40-character topics, nothing visible for a 31-character one, see
    docs/known-unknowns.md #6) was neither part of the topic nor an app
    wrapper - it is the low byte of the 2-byte (big-endian) length field
    that precedes the Topic Name in every standard MQTT PUBLISH, and it is
    only visible as text when it happens to be a printable ASCII character.
    Our subscriptions request QoS 0 (``build_mqtt_subscribe``), so the
    PUBLISH carries no Packet Identifier.

    Returns ``(topic, payload_bytes)``, or ``(None, None)`` if the message
    does not have the expected shape - the caller must then fall back to the
    text heuristic (``_on_message``), since edge cases are not fully certain
    without a raw byte capture.
    """
    if not message or (message[0] & 0xF0) != 0x30:
        return None, None

    remaining_length, pos = decode_varint(message, 1)
    if remaining_length is None:
        return None, None

    if len(message) < pos + 2:
        return None, None
    topic_len = struct.unpack("!H", message[pos : pos + 2])[0]
    pos += 2

    if len(message) < pos + topic_len:
        return None, None
    try:
        topic = message[pos : pos + topic_len].decode("utf-8")
    except UnicodeDecodeError:
        return None, None
    pos += topic_len

    if not topic or "{" in topic or not topic.isprintable():
        return None, None  # sanity check: do not trust an odd-looking topic

    qos = (message[0] >> 1) & 0x03
    if qos > 0:
        if len(message) < pos + 2:
            return None, None
        pos += 2  # Packet Identifier, unused (our subscriptions request QoS 0)

    return topic, message[pos:]


def _extract_json(text):
    """Extract and parse the first JSON object `{...}` from a text.

    Used by both the strict method and the fallback heuristic in
    ``_on_message``. Returns ``(data, start)``, where ``start`` is the
    position of the first ``{`` (useful for the heuristic, which uses it to
    derive the topic from the prefix).
    """
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No valid JSON found in the received message.")
    return json.loads(text[start:end]), start


def build_mqtt_connect(client_id, username, password):
    """Build the MQTT CONNECT packet."""
    protocol_name = b"\x00\x04MQTT"
    protocol_level = b"\x04"
    connect_flags = b"\xc2"  # CleanSession + Username + Password
    keep_alive = struct.pack("!H", 60)

    payload = (
        struct.pack("!H", len(client_id))
        + client_id.encode("utf-8")
        + struct.pack("!H", len(username))
        + username.encode("utf-8")
        + struct.pack("!H", len(password))
        + password.encode("utf-8")
    )

    variable_header = protocol_name + protocol_level + connect_flags + keep_alive
    remaining_length = len(variable_header) + len(payload)
    fixed_header = b"\x10" + encode_varint(remaining_length)
    return fixed_header + variable_header + payload


def build_mqtt_subscribe(packet_id, topic):
    """Build the MQTT SUBSCRIBE packet."""
    variable_header = struct.pack("!H", packet_id)
    topic_bytes = topic.encode("utf-8")
    payload = struct.pack("!H", len(topic_bytes)) + topic_bytes + b"\x00"
    remaining_length = len(variable_header) + len(payload)
    fixed_header = b"\x82" + encode_varint(remaining_length)
    return fixed_header + variable_header + payload


def build_client_id(access_key):
    """Unique MQTT clientId per connection, following the official app's pattern.

    The app uses ``mqtt-client_<accessKey>_<Date.now()>``. A random suffix is
    added to guarantee uniqueness even if two attempts fall in the same
    millisecond. Avoids collisions with the app or other connections using
    the same aws_mqtt_user (AWS IoT disconnects duplicate clientIds). See
    docs/protocol-findings.md §6b.
    """
    return f"mqtt-client_{access_key}_{int(time.time() * 1000)}_{secrets.token_hex(3)}"


def build_status_topic(base_topic, ref):
    """Status subscription topic for a controller.

    Confirmed structure: ``{aws_base_topic}get/ctl/{ref}/#`` where
    aws_base_topic is usually ``pro/v1/``. If unknown, the historical default
    is used. See docs/protocol-findings.md §6b.
    """
    base = base_topic or "pro/v1/"
    if not base.endswith("/"):
        base += "/"
    return f"{base}get/ctl/{ref}/#"


def build_feedback_topic(base_topic, mqtt_user):
    """Acknowledgement (ACK) topic for instructions sent over HTTP.

    Structure confirmed from the official app:
    ``{aws_base_topic}get/usr/{aws_mqtt_user}/feedback``, with payload
    ``{orderId, ctl, ...}`` (see docs/protocol-findings.md §8 and
    docs/known-unknowns.md #23).
    """
    base = base_topic or "pro/v1/"
    if not base.endswith("/"):
        base += "/"
    return f"{base}get/usr/{mqtt_user}/feedback"


def compute_backoff_delay(
    attempt, base=10, max_delay=120, jitter_fraction=0.2, rng=None
):
    """Reconnect delay with exponential backoff and jitter (E3).

    ``attempt`` starts at 0 for the first retry after a successful
    connection (CONNACK). The exponential backoff (``base * 2**attempt``,
    capped at ``max_delay``) only applies to **unplanned** disconnects; the
    proactive credential refresh (see ``_on_credential_refresh_due``) still
    reconnects without waiting. The jitter (±``jitter_fraction``) prevents
    several installations/threads from retrying all at once after a shared
    failure (e.g. a general network blip).
    """
    rng = rng or random
    delay = min(base * (2**attempt), max_delay)
    jitter = delay * jitter_fraction
    return max(delay + rng.uniform(-jitter, jitter), 0)


# ==========================================================
# Main MySair MQTT client
# ==========================================================
class MySairMQTTClient:
    """MQTT client for MySair over a direct WebSocket."""

    def __init__(self, api, installation_refs, message_callback, on_auth_failure=None):
        self.api = api
        self.installation_refs = installation_refs
        self.message_callback = message_callback
        # Optional callback () -> None, invoked when the MySair session becomes
        # invalid (MySairAuthError while renewing AWS credentials). The thread
        # keeps retrying in degraded mode; whoever injects it (__init__.py) is
        # responsible for requesting reauth from the HA loop.
        self.on_auth_failure = on_auth_failure
        self._auth_failure_reported = False
        self.stop_event = threading.Event()
        self._thread = None
        self._reconnect_delay = 10  # base of the exponential backoff (E3)
        self._max_reconnect_delay = 120
        self._reconnect_attempt = 0
        self.ws = None
        self.connected = False
        self._base_topic = None  # aws_base_topic, set on connect
        self._mqtt_user = None  # aws_mqtt_user, for the feedback topic
        self._credential_refresh_timer = None
        self._planned_reconnect = False
        # --- Observability (D3/D4): state and metrics exposed to
        # diagnostics.py and MySairMqttStatusSensor (sensor.py). ---
        self.last_message_at = (
            None  # D3: UTC datetime of the last successfully parsed PUBLISH
        )
        self.total_reconnects = (
            0  # D4: cumulative counter, never reset (unlike _reconnect_attempt)
        )
        self.parse_strict_count = 0  # D4: PUBLISH decoded by the strict method
        self.parse_fallback_count = (
            0  # D4: PUBLISH decoded by the fallback text heuristic
        )
        self.parse_error_count = (
            0  # D4: messages that could not be parsed by any method
        )
        self.last_close_code = None  # D4: close code of the last _on_close
        self.last_close_msg = None  # D4: close message of the last _on_close
        self._recv_buffer = b""  # E2: accumulated WS bytes not yet processed (partial/multi-packet frames)

    @property
    def reconnect_attempt(self):
        """Reconnect attempts since the last successful CONNACK (reset on connect)."""
        return self._reconnect_attempt

    def _report_auth_failure(self):
        """Report (only once) that the session requires reauthentication."""
        if self._auth_failure_reported or not self.on_auth_failure:
            return
        self._auth_failure_reported = True
        try:
            self.on_auth_failure()
        except Exception as e:
            log(f"[MySair MQTT] Failed to report session failure: {e}", "warning")

    # ----------------------------------------------------------
    # Main connection
    # ----------------------------------------------------------
    def start(self):
        """Start the MQTT client in a separate thread."""
        log("[MySair MQTT] Starting MQTT WebSocket thread...")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Close the WebSocket connection cleanly."""
        log("[MySair MQTT] Stopping MQTT WebSocket client...")
        self.stop_event.set()
        self._cancel_credential_refresh_timer()
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
        self.connected = False
        log("[MySair MQTT] Client stopped.")

    # ----------------------------------------------------------
    # Proactive credential refresh (prevents AWS from dropping the connection first)
    # ----------------------------------------------------------
    def _cancel_credential_refresh_timer(self):
        if self._credential_refresh_timer:
            self._credential_refresh_timer.cancel()
            self._credential_refresh_timer = None

    def _schedule_credential_refresh_timer(self):
        """Schedule a connection refresh before the current AWS credentials
        expire, instead of waiting for AWS IoT to drop the connection on its
        own (confirmed cause of regular "systematic" disconnects - see
        docs/known-unknowns.md and protocol-findings.md §6b: the official app
        does exactly this with a setTimeout).
        """
        self._cancel_credential_refresh_timer()
        delay = self.api.seconds_until_aws_credentials_expire()
        if delay is None:
            return
        self._credential_refresh_timer = threading.Timer(
            delay, self._on_credential_refresh_due
        )
        self._credential_refresh_timer.daemon = True
        self._credential_refresh_timer.start()
        log(
            f"[MySair MQTT] Proactive connection refresh scheduled in {delay:.0f}s",
            "debug",
        )

    def _on_credential_refresh_due(self):
        """Force a reconnect with fresh credentials before they expire."""
        log(
            "[MySair MQTT] Refreshing connection before the AWS credentials expire...",
            "debug",
        )
        self._planned_reconnect = True
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass

    # ----------------------------------------------------------
    # Connection logic
    # ----------------------------------------------------------
    def _run(self):
        while not self.stop_event.is_set():
            try:
                # Refresh the AWS credentials if missing or about to expire
                # (aws_expires_at). Done on EVERY connection attempt so an
                # expired signature is never reused after a long disconnect.
                if self.api.aws_credentials_expired():
                    self.api.refresh_aws_credentials()

                aws = self.api.aws_credentials
                if not aws:
                    delay = compute_backoff_delay(
                        self._reconnect_attempt,
                        base=self._reconnect_delay,
                        max_delay=self._max_reconnect_delay,
                    )
                    log(
                        f"[MySair MQTT] Could not get AWS credentials. Retrying in {delay:.1f}s.",
                        "error",
                    )
                    self._reconnect_attempt += 1
                    self.total_reconnects += 1
                    time.sleep(delay)
                    continue

                # AWS / MQTT data
                host = aws.get("endpoint") or aws.get("aws_mqtt_host")
                region = aws.get("region") or aws.get("aws_default_region")
                access_key = aws.get("accessKeyId") or aws.get("aws_access_key_id")
                secret_key = aws.get("secretAccessKey") or aws.get(
                    "aws_secret_access_key"
                )
                token = aws.get("sessionToken") or aws.get("aws_security_token")
                # Unique clientId per connection (not aws_mqtt_user) to avoid
                # mutual disconnects with the official app. See docs/protocol-findings.md.
                client_id = build_client_id(access_key)
                username = aws.get("aws_mqtt_user")
                password = aws.get("aws_security_token")
                self._base_topic = aws.get("aws_base_topic")
                self._mqtt_user = username

                # Build the signed URL (never logged: it contains the AWS signature)
                signed_url = self.api.aws_sign_url(
                    host, region, access_key, secret_key, token
                )
                log(
                    f"[MySair MQTT] Connecting to {host} as {_redact_client_id(client_id)}",
                    "debug",
                )

                # Configure the WebSocket client
                headers = {"Sec-WebSocket-Protocol": "mqtt"}
                self.ws = websocket.WebSocketApp(
                    signed_url,
                    header=headers,
                    on_open=lambda ws: self._on_open(ws, client_id, username, password),
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )

                # Refresh the connection before these credentials expire instead
                # of waiting for AWS IoT to drop it (see docs/known-unknowns.md -
                # cause of regular disconnects).
                self._schedule_credential_refresh_timer()

                self.ws.run_forever(ping_interval=30, ping_timeout=10)

            except self.api.AuthError as e:
                # The MySair session became invalid while the integration is
                # running (e.g. the refresh_token was invalidated on the
                # server). Without this the thread retried silently forever and
                # the user only noticed after the next HA restart. Reported
                # only once, then the backoff continues: if the user
                # reauthenticates, the next loop iteration finds the renewed
                # session.
                log(
                    f"[MySair MQTT] Session invalid, reauthentication required: {e}",
                    "error",
                )
                self._report_auth_failure()
            except Exception as e:
                log(f"[MySair MQTT] WebSocket connection error: {e}", "error")

            # Wait before retrying, unless this is a planned proactive refresh
            # (credentials already fresh: reconnect right away). Unplanned
            # disconnects use exponential backoff with jitter (E3), which is
            # reset on the next successful CONNACK.
            if not self.stop_event.is_set():
                if self._planned_reconnect:
                    log(
                        "[MySair MQTT] Reconnecting immediately (proactive credential refresh)..."
                    )
                    self._planned_reconnect = False
                else:
                    delay = compute_backoff_delay(
                        self._reconnect_attempt,
                        base=self._reconnect_delay,
                        max_delay=self._max_reconnect_delay,
                    )
                    log(
                        f"[MySair MQTT] Retrying connection in {delay:.1f}s (attempt {self._reconnect_attempt + 1})..."
                    )
                    self._reconnect_attempt += 1
                    self.total_reconnects += 1
                    time.sleep(delay)

    # ----------------------------------------------------------
    # WebSocket callbacks
    # ----------------------------------------------------------
    def _on_open(self, ws, client_id, username, password):
        """Event: WebSocket opened."""
        try:
            log(
                "[MySair MQTT] WebSocket opened, sending CONNECT packet...",
                "debug",
            )
            pkt = build_mqtt_connect(client_id, username, password)
            ws.send(pkt, opcode=websocket.ABNF.OPCODE_BINARY)
            log("[MySair MQTT] CONNECT sent.", "debug")
        except Exception as e:
            log(f"[MySair MQTT] Failed to send CONNECT: {e}", "error")

    def _on_message(self, ws, message):
        """Event: bytes received from the broker.

        E2: ``message`` is not assumed to be exactly one complete MQTT packet -
        it is appended to ``self._recv_buffer`` and drained in a loop, to
        support both several packets coalesced in one WS message and one
        packet split across two calls.
        """
        try:
            self._recv_buffer += message
            self._drain_recv_buffer(ws)
        except Exception as e:
            log(f"[MySair MQTT] General error in _on_message: {e}", "warning")

    def _drain_recv_buffer(self, ws):
        """Extract and dispatch all complete MQTT packets from the buffer,
        leaving only the incomplete remainder in it (E2).

        No separate cap is needed for the "incomplete, waiting for more
        bytes" case: the ``result > MAX_RECV_BUFFER_SIZE`` check below already
        guarantees the buffer never grows beyond the cap while waiting for a
        legitimate packet (a declared length above the cap is rejected right
        away, before waiting for that many bytes).
        """
        while True:
            result = _next_packet_length(self._recv_buffer)

            if result is FrameState.INCOMPLETE:
                return  # wait for more bytes in the next call

            if result is FrameState.MALFORMED or result > MAX_RECV_BUFFER_SIZE:
                self._recover_from_malformed_stream()
                return

            if len(self._recv_buffer) < result:
                return  # real split: the rest of this packet is missing

            packet, rest = self._recv_buffer[:result], self._recv_buffer[result:]
            if not self._dispatch_packet(ws, packet):
                # The length-delimited "packet" fails content validation
                # (e.g. bytes that are not a real MQTT frame but happen to
                # match a valid varint). As before E2, the fallback text
                # heuristic is applied to the whole remaining buffer and no
                # further splitting is attempted.
                self._dispatch_legacy_fallback(self._recv_buffer)
                self._recv_buffer = b""
                return

            self._recv_buffer = rest  # keep looking for more coalesced packets

    def _recover_from_malformed_stream(self):
        log(
            "[MySair MQTT] Invalid MQTT length varint or absurd packet length; "
            "discarding the receive buffer.",
            "warning",
        )
        self._recv_buffer = b""

    def _dispatch_packet(self, ws, packet):
        """Process an MQTT packet already cut to its exact length.

        Returns ``True`` if it was dispatched (or an unknown type was safely
        ignored), or ``False`` only for a PUBLISH whose strict content parsing
        failed (``parse_mqtt_publish`` inconclusive) - in that case the caller
        applies the fallback text heuristic to the whole buffer, not to this
        isolated `packet`.
        """
        # CONNACK
        if packet[0] == 0x20:
            log("[MySair MQTT] CONNACK received, subscribing to topics...")
            self.connected = True
            self._reconnect_attempt = 0  # connected: reset the backoff (E3)
            # The session works again (e.g. after reauth): re-arm the warning
            # so a future failure is reported again.
            self._auth_failure_reported = False
            packet_id = 1
            for ref in self.installation_refs:
                topic = build_status_topic(self._base_topic, ref)
                pkt = build_mqtt_subscribe(packet_id, topic)
                ws.send(pkt, opcode=websocket.ABNF.OPCODE_BINARY)
                log(f"[MySair MQTT] SUBSCRIBE sent to: {topic}", "debug")
                packet_id += 1

            # Acknowledgement (ACK) of instructions sent over HTTP, see
            # docs/protocol-findings.md §8.
            if self._mqtt_user:
                feedback_topic = build_feedback_topic(self._base_topic, self._mqtt_user)
                pkt = build_mqtt_subscribe(packet_id, feedback_topic)
                ws.send(pkt, opcode=websocket.ABNF.OPCODE_BINARY)
                log(f"[MySair MQTT] SUBSCRIBE sent to: {feedback_topic}", "debug")
            return True

        # SUBACK
        if packet[0] == 0x90:
            log("[MySair MQTT] SUBACK received.", "debug")
            return True

        # PUBLISH (high nibble 0x3; the low bits are DUP/QoS/RETAIN flags)
        if (packet[0] & 0xF0) == 0x30:
            try:
                # Primary method: standard-compliant MQTT decoding (see
                # parse_mqtt_publish, known-unknowns #6). If inconclusive, the
                # caller falls back to the text heuristic.
                strict_topic, strict_payload = parse_mqtt_publish(packet)
                if strict_topic is None:
                    return False

                decoded = strict_payload.decode("utf-8", errors="ignore").strip()
                data, _ = _extract_json(decoded)
                self.parse_strict_count += 1  # D4
                self.last_message_at = datetime.datetime.now(
                    datetime.timezone.utc
                )  # D3
                log(
                    f"[MySair MQTT] MQTT message received ({strict_topic}): {decoded[:200]}...",
                    "debug",
                )

                if self.message_callback:
                    self.message_callback({"topic": strict_topic, "payload": data})
                return True
            except Exception as e:
                self.parse_error_count += 1  # D4
                log(f"[MySair MQTT] Failed to process message: {e}", "warning")
                return True  # already counted as an error; do not retry as heuristic

        # Unknown/unhandled packet type: silently ignored, as before E2.
        return True

    def _dispatch_legacy_fallback(self, buffer):
        """Fallback text heuristic (unchanged since before E2), now applied to
        the whole buffer instead of a raw `message` - equivalent when no
        reassembly is in progress."""
        try:
            payload = buffer.split(b"\x00", 2)[-1]
            decoded = payload.decode("utf-8", errors="ignore").strip()
            data, start = _extract_json(decoded)
            # The broker does not always wrap the topic in parentheses:
            # sometimes it is "(topic){json}", sometimes "topic{json}" without
            # parentheses (e.g. the feedback topic).
            topic = decoded[:start].strip(" ()") if start > 0 else "unknown"
            self.parse_fallback_count += 1  # D4
            self.last_message_at = datetime.datetime.now(datetime.timezone.utc)  # D3
            log(
                f"[MySair MQTT] MQTT message received ({topic}): {decoded[:200]}...",
                "debug",
            )

            if self.message_callback:
                self.message_callback({"topic": topic, "payload": data})
        except Exception as e:
            self.parse_error_count += 1  # D4
            log(f"[MySair MQTT] Failed to process message: {e}", "warning")

    def _on_error(self, ws, error):
        log(f"[MySair MQTT] WebSocket error: {error}", "error")

    def _on_close(self, ws, close_status_code, close_msg):
        log(
            f"[MySair MQTT] Connection closed (code={close_status_code}, msg={close_msg})"
        )
        self.connected = False
        self.last_close_code = close_status_code  # D4
        self.last_close_msg = close_msg  # D4

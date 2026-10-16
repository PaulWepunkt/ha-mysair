# MQTT-Protokoll von MySair (über WebSocket / AWS IoT)

> Alle Angaben stammen aus `mqtt_handler.py`, `status_parser.py` und dem Callback in `__init__.py`, abgeglichen mit der offiziellen App (`docs/protocol-findings.md`). Die Gewissheit ist jeweils angegeben.
> ⚠️ Die Integration implementiert MQTT **von Hand** über WebSocket (ohne `paho-mqtt`). Sie erzeugt nur **CONNECT**- und **SUBSCRIBE**-Pakete, nie **PUBLISH**. Die Verbindung ist **reiner Empfang**.

---

## 1. Verbindungsparameter

| Parameter | Wert / Herkunft | Gewissheit |
|---|---|---|
| Transport | Sicherer WebSocket (`wss://`) zu AWS IoT | Bestätigt (`api.aws_sign_url`) |
| Broker (Host) | `aws_credentials["aws_mqtt_host"]` (aus `PUT /user/refreshawscredentials`) | Bestätigt |
| Port | 443 (implizit durch `wss://`) | Abgeleitet |
| TLS | Ja (WSS) | Bestätigt |
| WS-Pfad | `/mqtt` | Bestätigt (`api.aws_sign_url`) |
| WS-Subprotokoll | Header `Sec-WebSocket-Protocol: mqtt` | Bestätigt (`MySairMQTTClient._run`) |
| Authentifizierung | **AWS SigV4** im Query-String der URL (`X-Amz-*`) | Bestätigt (`api.aws_sign_url`) |
| SigV4-Dienst | `iotdevicegateway` | Bestätigt |
| Client ID (MQTT) | **Eindeutig je Verbindung** `mqtt-client_{accessKey}_{ts}_{rand}` (`build_client_id`) – wie die offizielle App, nicht `aws_mqtt_user` | Bestätigt (#20) |
| Username (MQTT) | `aws_mqtt_user` | Bestätigt |
| Password (MQTT) | `aws_security_token` ⚠️ | Bestätigt |
| Keepalive (MQTT) | 60 s (im CONNECT) | Bestätigt (`build_mqtt_connect`) |
| WebSocket-Ping | `ping_interval=30`, `ping_timeout=10` | Bestätigt (`_run`) |
| Clean Session | **Ja** (Connect-Flags `0xC2` = CleanSession+User+Pass) | Bestätigt |
| MQTT-Version | 3.1.1 (Protocol Level `0x04`, Name `MQTT`) | Bestätigt |
| QoS des Abonnements | 0 (letztes Byte `0x00` im SUBSCRIBE) | Bestätigt (`build_mqtt_subscribe`) |
| Retain | Nicht relevant (es wird nicht veröffentlicht) | Bestätigt |
| Topic-Basis | `aws_base_topic` (=`pro/v1/`), Rückfall auf den festen Wert (`build_status_topic`) | Bestätigt (#5) |
| Ablauf der Zugangsdaten | `aws_expires_at` (Unix-Sekunden); vorausschauende Erneuerung vor dem Ablauf | Bestätigt (#22) |

> **Hinweis (Abgeleitet):** Bei AWS IoT mit SigV4 über WebSocket steckt die eigentliche Authentifizierung in der URL-Signatur; `aws_security_token` als MQTT-*Password* zu senden ist vermutlich **überflüssig oder wird ignoriert**. **Unbekannt**, ob der Broker es verlangt (`known-unknowns.md` #10).

### URL-Signatur (Bestätigt – `api.aws_sign_url`)
- Algorithmus `AWS4-HMAC-SHA256`, Methode `GET`, Pfad `/mqtt`, `SignedHeaders=host`.
- `X-Amz-Date` aus `datetime.now(timezone.utc)`.
- Die Signatur gilt nur zeitlich begrenzt; vor jeder Verbindung wird neu signiert. Weicht die Uhr des Hosts stark ab, schlägt die Signatur fehl.

---

## 2. Verbindungsablauf (Bestätigt)

```
1. _run(): AWS-Zugangsdaten prüfen (fehlend oder bald abgelaufen → refresh_aws_credentials)
2. aws_sign_url() → wss://host/mqtt?X-Amz-...
3. WebSocketApp.run_forever(ping_interval=30, ping_timeout=10)
4. on_open  → sendet CONNECT (0x10) binär
5. on_message: Bytes im Puffer sammeln, vollständige Pakete der Reihe nach verarbeiten
     - 0x20 CONNACK → connected=True → SUBSCRIBE je Installation + feedback-Topic,
                      Timer für vorausschauende Erneuerung starten
     - 0x90 SUBACK  → Log
     - 0x3? PUBLISH → parse_mqtt_publish → message_callback
6. on_close/on_error → connected=False → geplante Wiederverbindung sofort,
                       sonst Wartezeit nach compute_backoff_delay → erneuter Versuch
```

Der Pakettyp wird am **höherwertigen Nibble des ersten Bytes** erkannt (`& 0xF0`), damit auch ein PUBLISH mit QoS-Flags erkannt wird. **Bestätigt** (`_dispatch_packet`).

---

## 3. Topics

| Topic-Muster | Richtung | Zweck | Herkunft | Gewissheit |
|---|---|---|---|---|
| `{aws_base_topic}get/ctl/{ref}/#` (=`pro/v1/get/ctl/{ref}/#`) | **Abonnement** | Alles empfangen, was unter dem Controller veröffentlicht wird (inkl. `.../status`) | `build_status_topic` | ✅ Bestätigt |
| `pro/v1/get/ctl/{ref}/status` | Empfang (PUBLISH des Brokers) | Zustand der Zonen des Controllers | Callback | ✅ Bestätigt |
| `pro/v1/get/usr/{aws_mqtt_user}/feedback` | **Abonnement** | Quittung für Anweisungen mit `orderId` | `build_feedback_topic`, `protocol-findings.md` §8 | ✅ Bestätigt (Topic, Mechanismus und flache Nutzdaten, `known-unknowns.md` #23) |

- **Topic-Aufbau:** `env/version/method/type/device/property` (z. B. `pro/v1/get/ctl/{ref}/status`). **Bestätigt** (offizielle App).
- **Basis:** `aws_base_topic` (=`pro/v1/`), mit Rückfall. **Bestätigt.**
- **Wildcard:** `#` (mehrstufig) am Ende des Status-Abonnements; das feedback-Topic ist exakt (ohne Wildcard). **Bestätigt.**
- **QoS:** 0 (kein PUBACK). **Bestätigt.**
- **Abonnement mit fortlaufender packet_id** (ab 1): zuerst eines je Installation, dann eines für das feedback-Topic. **Bestätigt.**

---

## 4. Format der Nutzdaten und Auswertung

### 4.1 Zerlegen des PUBLISH-Frames (Bestätigt)
`mqtt_handler`:
1. **Puffer** (`_recv_buffer`): Ein WebSocket-Frame muss nicht genau ein MQTT-Paket enthalten. Die Bytes werden gesammelt; `_next_packet_length` ermittelt aus dem MQTT-Längenfeld die Paketlänge und unterscheidet „unvollständig“ (auf weitere Bytes warten) von „fehlerhaft“ (Längenfeld kann nie gültig werden). So werden geteilte Pakete zusammengesetzt und mehrere Pakete in einem Frame einzeln verarbeitet. Eine Obergrenze (`MAX_RECV_BUFFER_SIZE`) verwirft unplausible Längen.
2. **Standardkonformer Decoder** (`parse_mqtt_publish`): Remaining Length (Varint), 2-Byte-Länge + Topic Name, Packet Identifier nur bei QoS>0, Rest = Nutzdaten.
3. **Rückfall-Heuristik**, falls der Decoder kein eindeutiges Ergebnis liefert: Text dekodieren, JSON zwischen erster `{` und letzter `}` herausschneiden, Topic = alles vor der ersten `{` ohne Klammern und Leerzeichen.

Die Zähler `parse_strict_count`, `parse_fallback_count` und `parse_error_count` zeigen, welcher Weg wie oft genutzt wurde (Sensor „Cloud-Verbindung“, Diagnose).

**Herkunft der scheinbaren Klammer:** In Logs erschien manchmal ein `(` vor dem Topic. Das ist das niederwertige Byte des Topic-Längenfelds und wird nur bei genau 40 Zeichen als `(` sichtbar. Ein Parser darf deshalb keine Klammern voraussetzen (`known-unknowns.md` #6).

### 4.2 Nutzdaten von `status` (Bestätigt)
`status_parser.parse_status_payload` erwartet ein JSON-Objekt:
```json
{
  "ctl": "<INSTALLATION_REF>",
  "value": "<STRING mit verschachteltem JSON, ggf. ';' am Ende>"
}
```
- `value` ist ein **String** mit verschachteltem JSON; ein `;` am Ende wird vor `json.loads` entfernt.
- Das verschachtelte JSON enthält `t` = Liste der Thermostate/Zonen.
- Ist die Nachricht kein Objekt, liefert der Parser `None`, und es wird kein Ereignis ausgelöst. Fehlendes `ctl`, ein `t`, das keine Liste ist, und Zonen ohne `rf` werden geloggt. Unbekannte zusätzliche Felder werden toleriert.

**Felder je Zone in `t[]` (Bedeutung BESTÄTIGT aus der offiziellen App – siehe `docs/protocol-findings.md`):**

| Feld | Bedeutung | Werte | Gewissheit |
|---|---|---|---|
| `rf` | Zonenreferenz (`zone_id`) | str | Bestätigt |
| `n` | Zonenname | str | Bestätigt |
| `e` | **EIN/AUS** (nicht der Modus) | `"0"`=aus, `"1"`=ein, `"2"`=Standby | ✅ Bestätigt |
| `m` | **MODUS** | `0`–`5`: gerade=Heizen, ungerade=Kühlen; {0,1,4,5}=Luft, {2,3,4,5}=Fußboden | ✅ Bestätigt |
| `tr` | aktuelle Temperatur (`temp_actual`) | num | Bestätigt |
| `tc` | Soll-Temperatur (`temp_target`) | num | Bestätigt |
| `tmm` / `tmx` | minimale / maximale Temperatur | num | Bestätigt |
| `hm` | Luftfeuchte (Messwert) | num | ✅ Bestätigt (`known-unknowns.md` #25) |
| `hum` | zweites Feuchtefeld, meldet `"0"`; nur Ersatz für `hm` | num/str | Beobachtet |
| `vv` | aktueller Lüftermodus / aktuelle Lüfterstufe | str | Bestätigt |
| `tzv` | aktueller Timerwert | str | Bestätigt |
| `sv` | Fußbodenheizung ein/aus | `"0"`/`"1"` | ✅ Bestätigt (`setFloor`) |
| `c` / `f` | Fähigkeit: Heizen / Kühlen möglich | `"1"`/`"0"` | Bestätigt |
| `v` / `s` | Fähigkeit: Lüfter / Fußboden | `"1"`/`"0"` | Bestätigt |
| `tz` / `hp` | Fähigkeit: Timer / Programme | `"1"`/`"0"` | Bestätigt |
| `pl` | Hauptzone | Flag | Bestätigt |
| `vf`, `hmh`, `mh`, `p`, `ps` | beobachtet, Bedeutung unbekannt; werden ignoriert | – | Unbekannt |

### 4.3 Bereinigtes Beispiel einer `status`-Nachricht
Nutzdaten des PUBLISH (erfundene Werte):
```
{"ctl":"INST_REF","value":"{\"t\":[{\"rf\":\"DEV1\",\"n\":\"Wohnzimmer\",\"e\":\"1\",\"m\":\"0\",\"tr\":22.5,\"tc\":21.0,\"tmm\":10.0,\"tmx\":30.0,\"hm\":45,\"hum\":\"0\"}]};"}
```
Verschachteltes JSON nach dem Auswerten (Zone ein, Luft im Heizbetrieb):
```json
{ "t": [ { "rf":"DEV1", "n":"Wohnzimmer", "e":"1", "m":"0", "tr":22.5, "tc":21.0, "tmm":10.0, "tmx":30.0, "hm":45, "hum":"0" } ] }
```
Normalisierte Struktur, die `parse_status_payload` als `mysair_update.data` auf den Event-Bus gibt (**Bestätigt** – `status_parser.py`; Auszug):
```json
{ "ctl":"INST_REF",
  "zones":[ { "ctl":"INST_REF","zone_id":"DEV1","zone_name":"Wohnzimmer",
              "temp_actual":22.5,"temp_target":21.0,"temp_min":10.0,"temp_max":30.0,"humidity":45.0,
              "power":"1","is_on":true,"is_standby":false,
              "mode_raw":"0","is_heat":true,"is_cool":false,"is_ac":true,"is_floor":false,
              "fan_mode":null,"allow_heat":false,"allow_cool":false,"allow_fan":false,"allow_floor":false } ] }
```

### 4.4 Andere Nachrichten als `status`
- Topic `.../feedback` (Quittung für Anweisungen): ausgewertet mit `parse_feedback_payload` und als eigenes Ereignis `mysair_feedback` weitergegeben (nicht `mysair_update`). `climate.py`/`switch.py` nutzen es über `command_feedback.CommandFeedbackMixin` (siehe §5). **Bestätigt.**
- Jedes andere Topic wird **unverarbeitet** als `mysair_update` auf den Event-Bus gegeben. Der Koordinator beachtet nur Topics auf `/status`, daher bleiben solche Nachrichten praktisch folgenlos. Derzeit sind keine weiteren Topics bekannt.

---

## 5. Zuordnung Befehl ↔ Antwort

- **Die offizielle App** ordnet über das Topic `pro/v1/get/usr/{aws_mqtt_user}/feedback` zu, das je Anweisung eine Quittung mit `orderId` liefert (`_sendInstruction` → `reciveInstruction_{orderId}` → `reciveStatus_{ctl}`). ✅ Bestätigt.
- **Die Integration macht es ebenso:** `api.extract_order_id` liest die `orderId` aus der Antwort von `/send/instruction`; `climate.py`/`switch.py` merken sie sich und werten ein `mysair_feedback` mit derselben `orderId` als Bestätigung. Kommt innerhalb von `FEEDBACK_TIMEOUT_SECONDS` (5 s, wie `VUE_APP_OUTSERVICE_MILISECOND` der App) nichts, wird der optimistische Zustand auf den letzten bekannten Wert zurückgesetzt und ein Hinweis geloggt, ob MQTT in dem Moment verbunden war.
- Die Abstimmung des **Zustands** erfolgt weiterhin über den **vollständigen Status**: Der nächste `status` (Abfrage alle 120 s oder Änderung am Gerät) überschreibt den lokalen Zustand und verwirft eine ausstehende Rücksetzung. **Bestätigt.**

---

## 6. Wiederverbindung, erneutes Abonnieren, Reihenfolge und Wettlaufsituationen

| Aspekt | Verhalten | Gewissheit |
|---|---|---|
| Wiederverbindung | Schleife in `_run` nach `on_close`/`on_error` | Bestätigt |
| Erneutes Abonnieren | Nach jedem CONNACK werden **alle** Topics neu abonniert | Bestätigt |
| Backoff | Exponentiell (Basis 10 s, höchstens 120 s) mit ±20 % Jitter (`compute_backoff_delay`); Zähler wird beim CONNACK zurückgesetzt | Bestätigt |
| Geplante Wiederverbindung | Etwa 60 s vor `aws_expires_at` trennt ein Timer die Verbindung gezielt und verbindet ohne Wartezeit neu | Bestätigt |
| AWS-Zugangsdaten bei Wiederverbindung | Bei jedem Versuch `aws_credentials_expired()`; erneuern, wenn fehlend oder bald abgelaufen | Bestätigt |
| Client ID bei Wiederverbindung | Je Verbindung neu und eindeutig (`build_client_id`), kein Verdrängen der offiziellen App | Bestätigt |
| Ungültige Sitzung | Scheitert die Erneuerung mit `MySairAuthError`, fordert der Thread die erneute Anmeldung an (`on_auth_failure`), versucht es weiter und meldet das nur einmal | Bestätigt |
| Clean Session | CleanSession=1 → keine Offline-Warteschlange; Nachrichten während einer Unterbrechung gehen **verloren** (ausgeglichen durch die periodische Abfrage) | Bestätigt |
| Deduplizierung | Keine. Jeder `status` überschreibt den Zustand; doppelte `feedback`-Quittungen kommen vor | Bestätigt |
| Reihenfolge | QoS 0, keine Garantie; ein alter `status` könnte einen neueren überschreiben | Abgeleitet ⚠️ |
| Geteilte/mehrere Pakete je Frame | Puffer mit Längenauswertung (§4.1) | Bestätigt (mit synthetischen Bytes getestet) |
| MQTT-PINGREQ/PINGRESP | Werden nicht gesendet; es zählt der WebSocket-Ping | Bestätigt |

**Wettlaufsituationen / Risiken:**
1. Optimistischer Zustand einer Entität vs. `status` unterwegs → kurzes Flackern des Zustands. (Offen, geringe Auswirkung.)
2. Doppelte oder vertauschte Nachrichten → bisher ohne eigene Tests (`docs/testing-strategy.md`, P3).

---

## 7. Gewissheiten im Überblick

- **Bestätigt:** WSS+SigV4, CONNECT/SUBSCRIBE von Hand, Topics `pro/v1/get/ctl/{ref}/#` und `pro/v1/get/usr/{aws_mqtt_user}/feedback`, standardkonformes Zerlegen der PUBLISH-Frames mit Puffer, Bedeutung von `e`/`m`/`hm`/`vv`/`sv`, Wiederverbindung mit Backoff und vorausschauender Erneuerung.
- **Abgeleitet:** Überflüssigkeit des MQTT-Passworts, Reihenfolge bei QoS 0.
- **Unbekannt:** weitere Topics/Ereignisse, ob der Broker das Passwort prüft, Bedeutung von `vf`/`hmh`/`mh`/`p`/`ps`.

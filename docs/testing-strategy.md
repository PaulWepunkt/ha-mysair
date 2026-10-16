# Teststrategie

> Das Dokument begann als Vorschlag, bevor es Tests gab. Es beschreibt jetzt den aktuellen Stand und die Überlegungen dahinter. Tabellen mit „✅“ sind umgesetzt; offene Punkte sind entsprechend markiert.
> Bewusst ohne Testanzahlen: Sie veralten mit jeder Änderung. Die aktuelle Zahl zeigt `pytest` bzw. die CI.

---

## 0. Tests ausführen

**P0/P1 (ohne Home Assistant, lokal):**
```bash
python -m venv .venv-test && source .venv-test/bin/activate
pip install -r requirements-test.txt
pytest
```

**P2/P3 (mit Home-Assistant-Testumgebung, über Docker – auf dem Rechner wird nichts installiert):**
```bash
docker compose run --rm test-ha
```
Erfordert Docker. Das Image (`Dockerfile.test`) installiert Python 3.14 und `pytest-homeassistant-custom-component` (`requirements-test-ha.txt`) in einem isolierten Container. Der Code wird als Volume eingebunden; nach Änderungen an Tests oder Code muss das Image nicht neu gebaut werden.

> **Pin aktuell halten:** `pytest-homeassistant-custom-component` legt die installierte Version von `homeassistant` (und damit von Python) fest. Bleibt der Pin hinter der produktiv genutzten HA-Version zurück, prüfen die Tests gegen ein Home Assistant, das niemand mehr nutzt, und Abkündigungen bleiben unbemerkt.

Beide Gruppen liegen gemeinsam in `tests/`. Die Dateien, die Home Assistant brauchen, beginnen mit `pytest.importorskip("homeassistant")` und werden ohne Docker sauber übersprungen statt fehlzuschlagen.

Die CI (`.github/workflows/tests.yml`) führt vier unabhängige Jobs aus: `pure-tests` (P0/P1), `ha-harness-tests` (P2/P3 über Docker), `hassfest` und `ruff`. Zusätzlich prüft `.github/workflows/hacs.yml` die HACS-Konformität.

---

## 1. Ausgangslage vor dem Aufbau der Tests

Anfangs gab es weder Tests noch Fixtures, Mocks oder CI. Hindernisse waren:
- `MySairAPI` nutzte `requests` direkt ohne injizierbare Session → Lösung: Parameter `session`.
- `MySairMQTTClient` baut MQTT von Hand über `websocket.WebSocketApp` → Lösung: reine Builder- und Parser-Funktionen (`encode_varint`, `build_mqtt_connect`, `build_mqtt_subscribe`, `parse_mqtt_publish`) getrennt testen.
- Die Auswertung des Status steckte in einer verschachtelten Funktion in `__init__.py` → Lösung: reines Modul `status_parser.py`.
- Die Entitäten hängen am Event-Bus → Tests mit dem Fixture `hass` aus `pytest-homeassistant-custom-component`.

---

## 2. Grundsätze

1. **Kein echtes Netz:** Alles wird ersetzt. Kein Test greift auf `api.mysair.es` oder AWS IoT zu.
2. **Fixtures nur aus bekannten Feldern:** Beispieldaten verwenden **ausschließlich** Felder, die der Code liest oder die im Protokoll bestätigt sind. **Keine Felder erfinden.**
3. **Generische Testdaten:** keine echten Referenzen, Namen, Messwerte oder sonstigen Daten realer Anlagen.
4. **Reine Funktionen zuerst** (MQTT-Builder, Statusparser, SigV4-Signatur mit fester Uhr) – hoher Nutzen, geringer Aufwand.
5. **Danach der Lebenszyklus** mit der HA-Testumgebung.

---

## 3. Werkzeuge

- `pytest`
- `pytest-homeassistant-custom-component` (liefert `hass`, `MockConfigEntry` usw.)
- eine eigene Fake-Session (`fake_session`/`make_response` in `conftest.py`) für den synchronen `requests`-Client
- `freezegun`, um die Uhr in Tests der SigV4-Signatur und der Verfügbarkeit festzusetzen

Gemeinsame Fixtures liegen in `tests/conftest.py` (`make_response`, `fake_session`, `login_ok`, `aws_credentials_ok`, `status_payload`). Dort wird außerdem `custom_components/mysair` in `sys.path` eingetragen, damit die reinen Module ohne das `__init__.py` des Pakets importierbar sind.

| Datei | Inhalt |
|---|---|
| `test_mqtt_builders.py` | Varint, CONNECT, SUBSCRIBE |
| `test_aws_sign.py` | SigV4-Signatur mit fester Uhr |
| `test_status_parser.py` | Status-/Feedback-Parser, Modus, Luftfeuchte |
| `test_api.py` | HTTP-Client mit Fake-Session |
| `test_mqtt_connection.py` | MQTT-Client: Pakete, Puffer, Backoff, Erneuerungs-Timer, Kennzahlen |
| `test_translations.py` | Vollständigkeit der Übersetzungen und Symbole |
| `test_ha_harness_smoke.py` | HA-Testumgebung findet die Integration |
| `test_config_flow.py` | Einrichtungsdialog und Reauth |
| `test_init_setup_unload.py` | Setup, Unload, Reload, Dienst, Aufbauänderungen |
| `test_entities.py` | Entitäten, Befehle, Bestätigung, Verfügbarkeit |
| `test_coordinator.py` | Verteilung je Zone |
| `test_diagnostics.py` | Diagnose und Schwärzung |

---

## 4. Testfälle nach Priorität

### P0 – Reine Funktionen (ohne HA, ohne Netz) – ✅ umgesetzt
| Test | Prüft |
|---|---|
| `encode_varint`/`decode_varint` | Längen kodieren/dekodieren, Hin- und Rückweg; `_next_packet_length` unterscheidet unvollständig und fehlerhaft |
| `build_mqtt_connect` | Fester Header 0x10, Flags 0xC2, Keepalive 60, Felder client/user/pass – exakte Bytes |
| `build_mqtt_subscribe` | Header 0x82, packet_id, Topic, QoS 0 – exakte Bytes |
| `aws_sign_url` (feste Uhr) | Aufbau der URL, `X-Amz-*`, deterministische Signatur (ohne Prüfung gegen AWS) |
| `parse_status_payload`/`parse_feedback_payload` | `value` als String→JSON, `;` entfernen, `t[]`→Zonen, `e`/`m`, Ablehnung (`None`) von Nicht-Objekten |
| `parse_humidity` | `hm` vor `hum`, Rückfall, 0 → unbekannt |
| `parse_mqtt_publish` | Standardkonformes Zerlegen (Remaining Length + Topic Name + Nutzdaten) |
| `compute_mode_value` | Umkehrung von `parse_mode`: `m` aus Heizen/Kühlen + Luft + Fußboden |
| `compute_backoff_delay` | Exponentiell mit Jitter, Obergrenze, nie negativ |

### P1 – HTTP-Client (Fake-Session) – ✅ umgesetzt (`test_api.py`)
| Test | Szenario |
|---|---|
| Anmeldung OK | 200 mit `entity.access_token` → Token gesetzt |
| Ungültige Zugangsdaten | 401 → `MySairAuthError` |
| Kein Token in der Antwort | 200 ohne `access_token` → `MySairAuthError` |
| Fehlerklassifizierung | 5xx/429 → `MySairConnectionError`; 404 mit Passport-Text und andere → `MySairAuthError` |
| Erkennung | Query-Parameter korrekt; Fehler werden weitergereicht statt `[]` |
| `send_instruction` 201 | Erfolg |
| `send_instruction` mit nicht leerem `error` | Ausnahme „rejected“ |
| `send_instruction` 401 → Erneuerung → Wiederholung | `refreshtokens` + `refreshawscredentials` werden aufgerufen |
| `refresh_aws_credentials` unvollständig | fehlendes `aws_*`-Feld → Ausnahme |
| `send_zone_command` / `send_installation_command` | Aufbau von `value` je Befehl (mode/temp/power/fanspeed/stop/status) |
| `extract_order_id` | `orderId` aus der Antwort, `None` bei unerwarteter Form |

### P1 – Einrichtungsdialog (HA-Testumgebung) – ✅ umgesetzt (`test_config_flow.py`)
| Test | Szenario |
|---|---|
| Erfolgreicher Ablauf | Anmeldung OK → Eintrag mit `email` + `refresh_token` (ohne Passwort) |
| Ungültige Anmeldung | `MySairAuthError` → Fehler `invalid_auth` |
| Verbindungsfehler | `MySairConnectionError` → Fehler `cannot_connect` |
| Doppelte unique_id | dasselbe Konto → Abbruch `already_configured` |
| Reauth erfolgreich | `refresh_token` aktualisiert, Abbruch `reauth_successful` |
| Reauth mit ungültigen Daten | Fehler `invalid_auth`, `refresh_token` unverändert |
| Reauth mit anderem Konto | Abbruch `account_mismatch` – 🔴 noch ohne Test |

### P2 – MQTT (ohne echten Broker) – überwiegend ✅ (`test_mqtt_connection.py`, läuft ohne HA)
| Test | Szenario | Stand |
|---|---|---|
| CONNACK → SUBSCRIBE | Frame `0x20` → SUBSCRIBE auf `pro/v1/get/ctl/{ref}/#` je Installation + feedback-Topic | ✅ |
| SUBACK | Frame `0x90` → keine Nebenwirkung | ✅ |
| PUBLISH status/feedback | Topic mit und ohne scheinbare Klammer → Callback erhält `{topic, payload}` | ✅ |
| Nutzdaten ohne JSON | Warnung, kein Absturz, `parse_error_count` steigt | ✅ |
| Geteilte / mehrere Pakete je Frame | Paket über zwei WS-Nachrichten verteilt oder mehrere in einer | ✅ mit synthetischen Bytes; **mit echtem Mitschnitt noch nicht bestätigt** |
| `connected=False` bei `on_close`, Backoff, Erneuerungs-Timer | Einzelteile getestet | ✅ |
| Wiederverbindung Ende-zu-Ende | Vollständiger Zyklus in `_run()` | 🟡 nicht als Ganzes getestet – `_run()` ist eine blockierende Schleife; nur die Teile werden einzeln geprüft |
| Erneutes Abonnieren nach Wiederverbindung | SUBSCRIBE auf alle Topics nach jedem CONNACK | 🟡 durch den Aufbau gegeben, ohne eigenen Szenariotest |
| Doppelte Nachricht | Zwei gleiche `status` / doppelte `feedback`-Quittung | 🔴 offen |
| Nachricht in falscher Reihenfolge | Älterer `status` nach neuerem → aktuelles Verhalten (überschreibt) festhalten | 🔴 offen |

### P2 – Lebenszyklus (HA-Testumgebung) – ✅ umgesetzt (`test_init_setup_unload.py`)
| Test | Szenario |
|---|---|
| Erfolgreiches Setup | `refresh_tokens` OK + HTTP ersetzt → Eintrag `LOADED`, Entitäten angelegt, neues `refresh_token` gespeichert |
| Migration | Eintrag mit altem `password`/`access_token` → beim ersten Setup entfernt |
| Kein `refresh_token` / ungültige Sitzung | `SETUP_ERROR` (löst Reauth aus) |
| Verbindungsfehler, kein Standort, keine Installation | `SETUP_RETRY` |
| Unload | MQTT `stop()` aufgerufen, `hass.data[DOMAIN]` leer, `NOT_LOADED` |
| Dienst `stop_installation` | ruft die API auf; unbekannte Referenz → `ServiceValidationError`; nach letztem Unload entfernt |
| Bestehende Entity-IDs | bleiben beim Umstieg auf `has_entity_name` erhalten |

### P2 – Entitäten und MQTT-Ereignisse (HA-Testumgebung) – ✅ umgesetzt (`test_entities.py`)
| Test | Szenario |
|---|---|
| `mysair_update` → climate | aktuelle/Soll-Temperatur, `hvac_mode`/`hvac_action` nach `is_on`/`is_cool`/`is_heat`/`is_standby`, Grenzwerte, `hvac_modes` nach Fähigkeiten |
| `mysair_update` → Sensoren | Temperatur, Soll-Temperatur, Modus (`enum`, Attribut `medium`), Luftfeuchte |
| `mysair_update` → Schalter | „Ein/Aus“ und „Fußbodenheizung“ inkl. Verfügbarkeit nach Fähigkeit |
| Filter nach `ctl` | Ereignis einer anderen Installation wird ignoriert |
| Befehle | `set_hvac_mode`, `set_temperature` (bei aus: nur lokal), `set_fan_mode`, Schalter ein/aus – exakte Argumente an `send_zone_command` |
| Bestätigung und Rücksetzung | Quittung per `mysair_feedback`; ohne Quittung Rücksetzung nach 5 s; echter Status verwirft die Rücksetzung |
| Verfügbarkeit | nicht verfügbar bis zum ersten Status und nach `MQTT_STALE_AFTER_SECONDS` |
| Cloud-Verbindung | Zustand und Kennzahlen des MQTT-Clients |
| Namen/Übersetzung | deutsche Namen, `enum`-Optionen, Diagnose-Kategorie |

### P3 – Fortgeschrittene Szenarien / Robustheit
| Test | Szenario | Stand |
|---|---|---|
| Unvollständige Nutzdaten | `t[]` ohne `tr`/`tc` → Feld bleibt `None` | ✅ (`test_status_parser.py`) |
| Unbekannte Felder | zusätzliche Felder werden ohne Fehler ignoriert | ✅ |
| Reload | keine doppelten Entitäten oder Dienste; Status nach Reload kommt an | ✅ |
| Mehrere Anlagen in einem Konto | zwei Installationen → Abonnements und Entitäten je Installation, gegenseitig unabhängig | ✅ |
| Aufbauänderung | Zone verschwindet → Gerät und Entitäten werden entfernt; neue Zone wird angelegt | ✅ |
| Migration der Config Entry | `async_migrate_entry` (sobald `VERSION` > 1) | 🔴 offen – derzeit nicht nötig, `VERSION` ist 1 |
| Doppelte / vertauschte Nachrichten | siehe P2 | 🔴 offen |

---

## 5. Bereinigte Beispieldaten (nur bekannte Felder)

> Erfundene Werte; **keine** echten Daten. Die tatsächlichen Fixtures stehen in `tests/conftest.py`.

Anmeldung:
```json
{ "entity": { "access_token": "TEST_ACCESS", "refresh_token": "TEST_REFRESH" } }
```
Standorte:
```json
{ "entity": [ { "id": 1001 } ] }
```
Installationen:
```json
{ "entity": [ { "reference": "INST_A" } ] }
```
Geräte:
```json
{ "entity": [ { "reference": "DEV_1", "name": "Salon" },
              { "reference": "DEV_2", "name": "Dormitorio" } ] }
```
AWS-Zugangsdaten:
```json
{ "entity": {
  "aws_mqtt_host": "test.iot.eu-west-1.amazonaws.com",
  "aws_default_region": "eu-west-1",
  "aws_access_key_id": "TESTKEYID",
  "aws_secret_access_key": "TESTSECRET",
  "aws_security_token": "TESTTOKEN",
  "aws_mqtt_user": "web0000"
} }
```
MQTT-Status (`value` ist ein String mit verschachteltem JSON, wie im Betrieb):
```json
{ "ctl": "INST_A",
  "value": "{\"t\":[{\"rf\":\"DEV_1\",\"n\":\"Salon\",\"e\":\"1\",\"m\":\"0\",\"tr\":22.5,\"tc\":21.0,\"tmm\":10.0,\"tmx\":30.0,\"hm\":45,\"hum\":\"0\"}]};" }
```

---

## 6. Anfällige Tests vermeiden

- Bytes der SigV4-Signatur nur mit fester Uhr prüfen (`freezegun`).
- Keine Tests, die auf das echte Backoff warten → Funktion `compute_backoff_delay` mit festem Zufallsgenerator prüfen, `time.sleep` ersetzen.
- Zeitabhängiges Verhalten (Verfügbarkeit, Befehlsbestätigung, periodische Abfrage) über die Uhr des Loops bzw. `freezegun` steuern, nicht wirklich warten.
- Log-Texte nur dort prüfen, wo das Log das eigentliche Verhalten ist; die Prüfung muss beim Ändern des Log-Texts mitgezogen werden.

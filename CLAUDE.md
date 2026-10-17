# CLAUDE.md – Leitfaden für Sitzungen mit Claude Code

**Custom-Integration für Home Assistant** für das Klima-Zonensystem **MySair** (Klimaanlage und/oder Fußbodenheizung bzw. -kühlung, je Zone kombinierbar). Entstanden durch Analyse der offiziellen Web-App (HTTP-API + AWS-IoT-MQTT). Die gesamte Referenzdokumentation liegt in `docs/`.

---

## 1. Zweck des Projekts

MySair-Klimazonen aus Home Assistant steuern und überwachen:
- **Erkennung** des Aufbaus per HTTP: `Location → Installation (ctl) → Device (Zone)`.
- **Zustand in Echtzeit** per **MQTT über WebSocket** (AWS IoT).
- **Befehle** (Temperatur, Modus, Ein/Aus, Lüfter) per **HTTP** an `POST /send/instruction` (NICHT per MQTT).

HA-Domain: `mysair`. Typ: `hub`. `iot_class`: `cloud_push` (Push per MQTT, dazu alle 120 s eine HTTP-Anforderung des Status).

---

## 2. Architektur in Kurzform

```
config_flow.py ──login──► api.py (MySairAPI, synchrones HTTP mit requests)
__init__.py (async_setup_entry):
   refresh_tokens → get_locations → get_installations → get_devices
   → _cleanup_stale_zone_devices (verschwundene Zonen entfernen)
   → _register_devices (Gerätebaum: Konto → Installation → Zonen, via_device_id)
   → hass.data[DOMAIN][entry_id] = {api, devices, installations, installation_names, mqtt, coordinator}
   → MySairMQTTClient.start()  (Daemon-Thread, WSS zu AWS IoT)
   → MySairCoordinator.start()  (coordinator.py, einziger Bus-Abonnent je Entry)
   → Plattformen laden: binary_sensor, button, climate, select, sensor, switch
   → refresh_status_periodic()  (Hintergrund-Task, status alle 120 s)

MQTT (mqtt_handler.py) empfängt .../status
   → mqtt_message_callback (__init__.py) → status_parser.parse_status_payload
   → hass.bus.async_fire("mysair_update", {topic, data})
   → MySairCoordinator filtert nach eigener Installation und verteilt jede Zone
     einzeln über homeassistant.helpers.dispatcher (signal_zone_update(ctl, zone_id))
   → jede Entität abonniert nur das Signal ihrer Zone und aktualisiert ihren Zustand

Befehl einer Entität → api.send_zone_command → POST /send/instruction (liefert orderId)
   → Backend leitet an das Gerät weiter → Zustand kommt per MQTT zurück
   → Quittung kommt per MQTT auf .../usr/{aws_mqtt_user}/feedback (orderId, ctl)
   → hass.bus.async_fire("mysair_feedback", {order_id, ctl, raw})
   → CommandFeedbackMixin (command_feedback.py) bestätigt den Befehl oder setzt den
     optimistischen Zustand nach FEEDBACK_TIMEOUT_SECONDS (5 s) zurück
```

Details: `docs/architecture.md`. **Befehle laufen über HTTP, der Zustand über MQTT.**

---

## 3. Befehle

Tests und Lint laufen in der CI (`.github/workflows/tests.yml` mit den Jobs `pure-tests`, `ha-harness-tests`, `hassfest`, `ruff`; dazu `.github/workflows/hacs.yml`):

```bash
# Tests P0/P1 (OHNE Home Assistant, lokal)
python -m venv .venv-test && source .venv-test/bin/activate
pip install -r requirements-test.txt
pytest                      # Parser, MQTT-Builder, SigV4-Signatur, HTTP-Client
                            # (Dateien, die HA brauchen, werden per pytest.importorskip übersprungen)

# Tests P2/P3 (HA-Testumgebung) über Docker, ohne Installation auf dem Rechner
docker compose run --rm test-ha    # komplette Suite

# Lint / Formatierung (ruff, Konfiguration in pyproject.toml – nur Standardregeln)
pip install -r requirements-lint.txt
ruff check custom_components/mysair tests
ruff format custom_components/mysair tests
```

- Die **reinen** Tests (Parser, MQTT-Builder, SigV4-Signatur, `MySairAPI` mit injizierter `session`) laufen ohne HA, weil `tests/conftest.py` `custom_components/mysair` in `sys.path` einträgt und diese Module direkt importiert (ohne das `__init__.py` des Pakets auszuführen).
- Die Tests mit **HA-Testumgebung** (`test_config_flow.py`, `test_init_setup_unload.py`, `test_entities.py` u. a.) benötigen `homeassistant` + `pytest-homeassistant-custom-component` (Python ≥ 3.14, Voraussetzung von HA 2026.9). Beides wird nur in `Dockerfile.test` installiert. **Den Pin von `pytest-homeassistant-custom-component` in `requirements-test-ha.txt` aktuell halten**: Bleibt er hinter der produktiv genutzten HA-Version zurück, bleiben Abkündigungen unbemerkt. Siehe `docs/testing-strategy.md`.

> ⚠️ Den Code **nie** gegen echte MySair-Server ausführen, um etwas „auszuprobieren“.

---

## 4. Dateistruktur

Der Code der Integration liegt in `custom_components/mysair/` (Standardlayout HA/HACS). Tests und Dokumentation liegen im Wurzelverzeichnis.

| Datei (`custom_components/mysair/`) | Aufgabe |
|---|---|
| `__init__.py` | Setup/Unload, MQTT-Callback, periodische Abfrage, Dienst `mysair.stop_installation`, Entfernen verwaister Zonen |
| `api.py` | `MySairAPI`: synchrones HTTP (`requests`, injizierbare `session`), Fehlerklassifizierung (`_http_error`), AWS-SigV4-Signatur |
| `status_parser.py` | **Reine** Parser für `status` (`parse_status_payload`, `parse_mode`, `parse_humidity`, `compute_mode_value`) und `feedback` (`parse_feedback_payload`), ohne HA-Abhängigkeit |
| `mqtt_handler.py` | `MySairMQTTClient`: MQTT von Hand über WebSocket (`websocket-client`), Empfangspuffer, Backoff, vorausschauende Erneuerung |
| `coordinator.py` | `MySairCoordinator`: einziger Abonnent von `mysair_update` je Config Entry; verteilt jede Zone über den Dispatcher |
| `command_feedback.py` | `CommandFeedbackMixin`: ordnet Befehle der Quittung aus `mysair_feedback` zu und setzt bei Zeitüberschreitung zurück (climate/switch) |
| `zone_control.py` | Regeln der App für Einschalten, Richtung der Anlage und Fußboden; übersetzte Fehler (`ServiceValidationError`) |
| `availability.py` | `AvailabilityMixin`: `should_poll=False` + `available` nach Aktualität des letzten MQTT-Status |
| `device.py` | Gerätebaum: `account_device_info` (Konto, Cloud-Verbindung) → `installation_device_info` (Steuerung, Software-Version aus `v`) → `zone_device_info`. Der Zonen-Gerätename ist der Zonenname, weil HA ihn den Entitätsnamen voranstellt (`has_entity_name`). Geräte nur über `find_device` (je Config Entry) suchen, nicht über das abgekündigte `async_get_device` |
| `installation_entity.py` | Basisklasse für Entitäten am Installationsgerät (Signal `signal_installation_update`, `AvailabilityMixin`) |
| `binary_sensor.py` | Je Installation: „In Betrieb“ (mindestens eine Zone an), „Heiz-/Kühlkonflikt“ (Diagnose) und „Abgleich Wärmepumpe“ (nur wenn „MySair schaltet Wärmepumpe“ aus ist) |
| `select.py` | Je Installation: „Betriebsart“ (Heizen/Kühlen); Wechsel nur bei ausgeschalteter Anlage, wird wie in der App nur lokal gemerkt (`MySairCoordinator.choose_direction`). Ist „MySair schaltet Wärmepumpe“ (Schalter in `switch.py`) aus, ist sie die festgehaltene Richtung der Wärmepumpe (`InstallationState.held_is_heat`, über `RestoreEntity` gespeichert). Je Zone: „Heizen mit“/„Kühlen mit“ (Einstellungs-Entitäten, Wahl als Extra-Daten über `RestoreEntity` gespeichert) |
| `button.py` | Je Installation: „Anlage stoppen“ (`zone_control.async_stop_installation`, wie der Dienst) |
| `climate.py` | `MySairThermostat` (ClimateEntity, inkl. Lüfterstufe, Luftfeuchte, Fähigkeiten und Rohwerte `e`/`m`/`vv` der Zone als Attribute `CAPABILITY_ATTRIBUTES`/`RAW_ATTRIBUTES`) |
| `sensor.py` | 4 Sensoren je Zone (Temperatur, Soll-Temperatur, Modus, Luftfeuchte) + `MySairMqttStatusSensor` („Cloud-Verbindung“, einer je Config Entry, Diagnose-Entität) + je Installation „Hauptzone“ (Diagnose) |
| `switch.py` | `MySairSwitch` (Ein/Aus) + `MySairFloorSwitch` (Fußbodenheizung) je Zone; je Installation `MySairHeatPumpSwitch` („MySair schaltet Wärmepumpe“, Einstellungs-Entität, ab Werk an) |
| `config_flow.py` | Einrichtungsdialog (E-Mail + Passwort), Reauth und Optionen (erlaubte Medien je Richtung, `MEDIA_PERMISSION_KEYS` in `const.py`) |
| `const.py` | Konstanten (Zeitlimits, Abfrageintervall des Verbindungssensors, Dienstname) |
| `diagnostics.py` | Diagnose-Download in der HA-Oberfläche mit geschwärzten Zugangsdaten/Token |
| `services.yaml` | Schema des Dienstes `mysair.stop_installation` |
| `strings.json` | Englische Texte (Referenz; HA liest sie bei Custom-Integrationen zur Laufzeit **nicht**) |
| `translations/en.json` | Kopie von `strings.json`; das lädt HA tatsächlich für Englisch |
| `translations/de.json`, `translations/es.json` | Deutsch und Spanisch: Einrichtungsdialog, Dienst, Entitätsnamen und -zustände |
| `icons.json` | Symbole je `translation_key` und Zustand |
| `brand/` | Symbol und Logo (lokal, ab HA 2026.3.0) |
| `manifest.json` | Manifest (`requirements`: nur `websocket-client`; `requests` bringt Home Assistant selbst mit und darf nicht eingetragen werden) |

| Wurzelverzeichnis | Aufgabe |
|---|---|
| `tests/` | Tests + `conftest.py` mit bereinigten Fixtures |
| `pytest.ini`, `requirements-test.txt` | Konfiguration und Abhängigkeiten der reinen Tests |
| `Dockerfile.test`, `docker-compose.yml`, `requirements-test-ha.txt` | HA-Testumgebung |
| `pyproject.toml`, `requirements-lint.txt` | Konfiguration von `ruff` (Standardregeln) und gepinnte Abhängigkeit |
| `docs/` | Dokumentation zu Architektur, Protokollen, Domäne, Tests, Sicherheit |
| `hacs.json` | Metadaten für HACS (inkl. Mindestversion von HA) |
| `README.md` | Installation, Einrichtung, Entitäten, Einschränkungen |
| `CHANGELOG.md` | Versionsgeschichte (Keep a Changelog + SemVer) |
| `LICENSE` | MIT |

---

## 5. Code-Konventionen

- Synchroner HTTP-Client (`requests`) wird IMMER aus dem Loop über `hass.async_add_executor_job(...)` aufgerufen. Keine blockierenden Netzaufrufe im Event-Loop.
- MQTT läuft in einem **Daemon-Thread**; zurück in den Loop geht es mit `hass.loop.call_soon_threadsafe(...)`.
- Entitäten erhalten Daten über **Event-Bus und Koordinator** (`mysair_update` → Dispatcher-Signal je Zone), nicht direkt vom MQTT-Client.
- Ereignis-Handler mit `@callback` dekorieren.
- Log-Meldungen mit Präfix `[MySair ...]`, ohne Emojis.
- Code-Kommentare, Docstrings und Log-Meldungen auf Englisch.
- Sichtbare Texte (Namen, Zustände, Fehler) nur über `strings.json`/`translations/` und `translation_key`, nicht fest im Code.

---

## 6. Wichtige Einschränkungen (VOR jeder Änderung lesen)

0. **Öffentliches Repository – keine personenbezogenen oder anlagenspezifischen Daten.** Gilt für Code, Kommentare, Tests, Dokumentation, Commit-Nachrichten und Pull-Request-Texte:
   - **Git-Identität:** niemals eine private E-Mail-Adresse als Autor oder Committer verwenden. Ausschließlich die GitHub-noreply-Adresse des Repository-Inhabers (`<id>+<login>@users.noreply.github.com`). Vor jedem Push mit `git log --format='%an <%ae> | %cn <%ce>'` prüfen.
   - **Keine Daten einzelner Anlagen oder Personen:** keine Namen, E-Mail-Adressen, Referenzen (`MYS…`), Zonennamen, Messwerte, Firmware-Stände, Datumsangaben von Mitschnitten oder sonstige Details aus Logs realer Installationen. Testdaten sind generisch.
   - **Keine Verweise auf private Konten oder Sitzungen:** keine Links zu Chat-, Sitzungs- oder Kontoseiten (z. B. claude.ai), weder in Commit-Nachrichten noch in Pull-Request-Texten.
   - **Formulierung:** Dokumentation beschreibt das Verhalten allgemein und für sich verständlich, ohne Bezug auf Gesprächsverläufe, einzelne Nutzer oder Testsituationen.
   - **Sprache:** Programmcode und Code-Kommentare auf Englisch; Dokumentation, CHANGELOG, Commit-Nachrichten und Pull-Request-Texte auf Deutsch.

1. **Keine Anfragen an Produktivsysteme.** Während der unterstützten Entwicklung kein `login`, kein `send_instruction` und keine MQTT-Verbindung zu `api.mysair.es` / echtem AWS IoT.
2. **Den Zustand einer echten Anlage nicht verändern.** Befehle (`mode`, `temp`, `power`, `fanspeed`, `stop`) wirken auf echte Hardware.
3. **Geheimnisse:** `password`, `access_token`, `refresh_token`, `aws_secret_access_key`, `aws_security_token` und signierte URLs weder ausgeben noch committen. Siehe `docs/security-and-privacy.md`.
4. **Undokumentiertes Protokoll:** keine Felder oder Endpunkte erfinden. Annahmen mit `# TODO(verify)` markieren und `docs/known-unknowns.md` prüfen.
5. **Kodierung des Modus:** `e` = Ein/Aus/Standby, `m` = Modus 0–5 (gerade = Heizen, ungerade = Kühlen; Luft/Fußboden nach `docs/protocol-findings.md` §4). Diese Logik nur mit neuen Belegen ändern (`known-unknowns` #1, #2, #4).

---

## 7. Anleitungen für die Entwicklung

### Entität hinzufügen (neue Plattform)
1. `neue_plattform.py` mit `async_setup_entry(hass, entry, async_add_entities)` anlegen.
2. `data = hass.data[DOMAIN][entry.entry_id]` lesen; über `data["devices"]` iterieren (dict `inst_ref → [device, ...]`).
3. In der Entität: stabile `unique_id` `mysair_<typ>_{inst_ref}_{device_id}`, `device_info=zone_device_info(...)` aus `device.py`, `_attr_has_entity_name = True` und einen `translation_key` (Texte in `strings.json` und allen `translations/*.json`, Symbol in `icons.json`).
4. In `async_added_to_hass` das Signal der Zone abonnieren: `async_dispatcher_connect(hass, signal_zone_update(inst_ref, device_id), self._handle_zone_update)`; in `async_will_remove_from_hass` lösen.
5. `AvailabilityMixin` einbinden und im Handler (`@callback`) den Status als empfangen markieren.
6. **Die Plattform in `PLATFORMS` in `__init__.py` eintragen** (häufiger Fehler).

### Befehl hinzufügen
1. In `api.MySairAPI.send_zone_command` einen neuen `command_type` mit passendem `value` ergänzen.
2. Die Anweisung lautet `[{sender:"WEB", ctl, app, device, command, value}]` an `POST /send/instruction`.
3. Aus der Entität mit `await hass.async_add_executor_job(api.send_zone_command, inst_ref, device_id, typ, ...)` aufrufen.
4. Optimistischen Zustand setzen und über `CommandFeedbackMixin` eine Rücksetzfunktion übergeben. `value` in `docs/mysair-http-api.md` dokumentieren.

### Unterstützung für eine MQTT-Nachricht hinzufügen
1. Die Auswertung liegt in `mqtt_message_callback` (`__init__.py`). Heute: `.../status` (→ `mysair_update`) und `.../feedback` (→ `mysair_feedback`, siehe `status_parser.parse_feedback_payload`).
2. Für ein neues Topic: einen Zweig nach Topic-Endung ergänzen, auswerten und als eigenes Ereignis mit normalisierter `data` senden (`mysair_update`/`mysair_feedback` nicht für anderes wiederverwenden).
3. Muss ein neues Topic abonniert werden: eine reine Funktion `build_<name>_topic()` in `mqtt_handler.py` anlegen und im CONNACK-Handler abonnieren (Vorbild: `build_feedback_topic`).
4. Betroffene Entitäten anpassen und Topic/Nutzdaten in `docs/mysair-mqtt-protocol.md` dokumentieren.
5. Pakete werden in `mqtt_handler` über den Empfangspuffer und `parse_mqtt_publish` zerlegt; die Text-Heuristik ist nur Rückfall.

### HTTP-API ändern
1. Alle Endpunkte stehen in `api.py`. Neue Methoden über `_authed_request`/`_authed_get_entity` bauen (Header, Zeitlimit, Wiederholung bei 401, Fehlerklassifizierung).
2. Immer aus dem Loop über `async_add_executor_job` aufrufen.
3. Den Endpunkt mit Gewissheitsstufe in `docs/mysair-http-api.md` dokumentieren.

### Ohne echtes Gerät arbeiten
- Die **bereinigten Fixtures** aus `tests/conftest.py` bzw. `docs/testing-strategy.md` §5 nutzen.
- HTTP über die injizierbare `session` ersetzen; MQTT-Frames mit den Builder-Funktionen als Bytes erzeugen.
- Reine Funktionen testen: `encode_varint`, `build_mqtt_connect`, `build_mqtt_subscribe`, `parse_mqtt_publish`, `aws_sign_url` (mit fester Uhr), die Parser in `status_parser.py`.

---

## 8. Checkliste vor einer Code-ÄNDERUNG

- [ ] Relevante Dokumentation in `docs/` gelesen (Architektur + betroffenes Protokoll)?
- [ ] Vermeidet die Änderung Aufrufe an Produktivsysteme und Änderungen am echten Zustand?
- [ ] Betrifft sie die Kodierung des Modus? Wenn ja: neue Belege vorhanden?
- [ ] Blockierendes Netz? Dann über `async_add_executor_job`.
- [ ] Registriert sie Ressourcen (Threads, Tasks, Listener)? Diese müssen beim Unload beendet werden.
- [ ] Logs mit Geheimnissen? Nein.
- [ ] Neue sichtbare Texte? In allen Übersetzungsdateien ergänzt?

## 9. Checkliste vor einem COMMIT

- [ ] Keine Zugangsdaten, Token, echten Mitschnitte oder anlagenspezifischen Daten im Diff (§6, Punkt 0).
- [ ] `ruff check` und `ruff format` ohne Befund (Job `ruff` in der CI, §3).
- [ ] Relevante Tests bestehen (`pytest` bzw. Docker), oder es ist begründet, warum nicht.
- [ ] Dokumentation in `docs/` und `CHANGELOG.md` aktualisiert, wenn sich Protokoll, Architektur oder Verhalten geändert hat.
- [ ] Neue Plattform in `PLATFORMS`?
- [ ] Aussagekräftige Commit-Nachricht auf Deutsch.
- [ ] Autor und Committer sind die noreply-Adresse (§6, Punkt 0).
- [ ] Nicht direkt auf `main` committen.

### Branch-Ablauf (origin)

Auf `origin` existiert dauerhaft nur `main`; Feature-Branches werden nach dem Merge gelöscht.

1. Einen kurzen Feature-Branch **von `main`** anlegen (sprechender Name, z. B. `fix/instruction-msg`).
2. Commits pushen und einen Pull Request **gegen `main`** anlegen. PR-Titel und -Text auf Deutsch, ohne automatisch angehängte Sitzungs- oder Chatlinks.
3. Nach dem Merge den Branch löschen.
4. **Nach einem Merge, der ausgeliefert werden soll**, die Version in `manifest.json` erhöhen, den Abschnitt `[Unreleased]` im `CHANGELOG.md` in eine Version umbenennen und ein GitHub-Release mit Tag `v<version>` anlegen, z. B.:
   ```bash
   gh release create v2.13.2 --target main --title "v2.13.2" --notes "<Abschnitt dieser Version aus CHANGELOG.md>"
   ```
   **Grund:** HACS zeigt nur dann eine sinnvolle Versionsnummer und ein Update an, wenn es ein GitHub-Release mit Tag gibt. Ohne Release zeigt HACS den Hash des verfolgten Commits auf `main` (z. B. `16ed989`), der Nutzern nichts sagt.

---

## 10. Dokumentation

- Architektur und Diagramme: `docs/architecture.md`
- HTTP-API: `docs/mysair-http-api.md`
- MQTT-Protokoll: `docs/mysair-mqtt-protocol.md`
- Erkenntnisse aus der offiziellen App: `docs/protocol-findings.md`
- Domänenmodell: `docs/domain-model.md`
- Lebenszyklus in HA und Bewertung: `docs/home-assistant-integration.md`
- Teststrategie und Fixtures: `docs/testing-strategy.md`
- Sicherheit und Datenschutz: `docs/security-and-privacy.md`
- Offene Fragen: `docs/known-unknowns.md`
- Roadmap und offene Punkte: `docs/development-roadmap.md`
- Zusammenfassung der umgesetzten Aufgaben: `docs/execution-plan.md`

---

## 11. Wichtige Erkenntnisse und offene Punkte

Die vollständige Historie steht im `CHANGELOG.md` und in `docs/execution-plan.md`. Für Änderungen am Code sind vor allem diese Erkenntnisse wichtig:

- **Fehlerklassifizierung nach Bereich** (`api._http_error`): Das Backend (Laravel Passport) antwortet auf ein gelöschtes `refresh_token` mit **404**, nicht 401. `5xx`/`429` gelten als vorübergehend, jeder andere Code außerhalb von 2xx führt zur erneuten Anmeldung. Keine Listen einzelner Codes einführen. Erkennungsmethoden geben Fehler weiter statt `[]`; MQTT-Thread und periodische Abfrage fordern die erneute Anmeldung im laufenden Betrieb an (`entry.async_start_reauth`).
- **Topic ohne Klammern:** Das scheinbare `(` vor manchen Topics ist das niederwertige Byte des MQTT-Längenfelds. Kein Parser darf Klammern voraussetzen (`known-unknowns.md` #6).
- **Luftfeuchte:** `hm` trägt den Messwert, `hum` meldet `"0"`; `hm` hat Vorrang, 0 gilt als unbekannt (`known-unknowns.md` #25, `status_parser.parse_humidity`).
- **Vorausschauende Erneuerung der AWS-Zugangsdaten** vor `aws_expires_at`; ohne sie trennt AWS IoT die Verbindung regelmäßig.
- **Eindeutige MQTT-`client_id` je Verbindung**, sonst verdrängen sich Integration und offizielle App gegenseitig.
- **Entity-IDs:** Mit `has_entity_name` bildet HA die IDs aus Gerät (= Zonenname) und Entitätsname in der eingestellten Sprache. Bestehende Installationen behalten ihre IDs, solange die `unique_id` gleich bleibt (dafür gibt es einen Test). `unique_id`s nie ändern.
- **Erfolg von `/send/instruction`** = HTTP 201 + leere Liste `error`. Den Text in `msg` nie vergleichen: Er hängt von der Kontosprache ab (`"Creado"`, `"Created"` …).
- **Richtung der Anlage wie in der App** (`protocol-findings.md` §4b, `zone_control.py`): Läuft eine Zone, sind nur „Aus“ und die aktuelle Richtung erlaubt. Einschalten mit der Wahl „Standard“ (Wert `app`) = Luft an, Fußboden wie bisher, Richtung der Anlage, aktuelle Soll-Temperatur. Welche Medien je Richtung verwendet werden dürfen, legen die Optionen fest (`zone_control.media_permissions`); das Medium beim Einschalten wählt jede Zone mit „Heizen mit“/„Kühlen mit“ (`status_parser.select_media`). Fußbodenkühlung ist standardmäßig aus. `stop` geht an die Hauptzone (`pl`).
- **„MySair schaltet Wärmepumpe“ aus** (für Wärmepumpen, die nicht von MySair umgeschaltet werden; Schalter `MySairHeatPumpSwitch`, ab Werk an, `unique_id`-Schlüssel `hold_direction` aus 3.3.0 mit umgekehrter Bedeutung, Übernahme alter Zustände ohne Extra-Daten): `InstallationState` unterscheidet `running_is_heat` (tatsächliche Richtung aus den Zonen), `held_is_heat` (festgehalten, sonst `None`) und `is_heat` (Richtung für das Einschalten über HA). Sperren richten sich nach `running_is_heat` und `held_is_heat`.
- **Lüfter:** `vv` = `"0"` ist die zonale Geschwindigkeit (Standard der Anlage), nicht „Aus“ (`protocol-findings.md` §9/§10).
- **Handbücher der Thermostate** (`protocol-findings.md` §10): Die Anlage lehnt Richtungswechsel bei laufendem Betrieb selbst ab; Fußbodenkühlung am Thermostat lässt sich nur in der Anlage sperren.
- **Fehler beim Senden** von Befehlen werden als übersetzte `HomeAssistantError` (`command_failed`, `zone_control.command_error`) angezeigt; der optimistische Zustand wird dabei nicht gesetzt bzw. zurückgenommen.
- **Verwaiste Zonen** werden bei jedem Setup/Reload entfernt (`_cleanup_stale_zone_devices`); das Kontogerät (Kennung `entry_id`) bleibt unangetastet.

Offen:
- 🟡 **Doppelte/vertauschte MQTT-Nachrichten** ohne Tests (doppelte `feedback`-Quittungen kommen vor).
- 🟡 **Uninterpretierte Zonenfelder** `vf`, `hmh`, `mh`, `p`, `ps` (`docs/known-unknowns.md`).

Abgrenzungen (keine Fehler):
- **Nur der erste Standort** (`known-unknowns.md` #15).
- **Keine Timer und Programme von MySair** (F6, `known-unknowns.md` #27): Zeitsteuerung über Home Assistant. Die Fähigkeiten `tz`/`hp` werden nur als Attribute des Thermostats angezeigt.
- **Kein zusätzlicher PR an `home-assistant/brands`**: Nutzer von HA vor 2026.3.0 sehen Symbol und Logo erst nach einem Update.

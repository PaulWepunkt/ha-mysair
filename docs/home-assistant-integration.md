# Einbindung in Home Assistant – Lebenszyklus und Bewertung

> Jede Beobachtung ist eingestuft: ✅ **Korrekt** · 🟡 **Verbesserungsfähig** · 🟠 **Möglicherweise veraltet** · 🔴 **Möglicher Fehler** · 🔎 **Zu untersuchen**.
> Das Dokument beschreibt den aktuellen Stand. Behobene Mängel früherer Versionen stehen im `CHANGELOG.md` und in `docs/execution-plan.md`.

---

## 1. Manifest und Metadaten

`manifest.json` (Bestätigt):

| Feld | Wert | Bewertung |
|---|---|---|
| `domain` | `mysair` | ✅ |
| `config_flow` | `true` | ✅ |
| `iot_class` | `cloud_push` | ✅ Push per MQTT; die zusätzliche Abfrage alle 120 s löst nur eine Push-Meldung aus |
| `integration_type` | `hub` | ✅ |
| `dependencies` | `[]` | ✅ |
| `requirements` | `requests`, `websocket-client` | ✅ nur tatsächlich genutzte Bibliotheken |
| `quality_scale` | `silver` | ✅ von `hassfest` akzeptiert |
| `documentation`, `issue_tracker`, `codeowners` | verweisen auf dieses Repository | ✅ |
| `version` | SemVer | ✅ (für Custom-Integrationen Pflicht) |

Die Mindestversion von Home Assistant (≥ 2026.8.0) steht in `hacs.json` und im README; im Manifest einer Custom-Integration ist die Angabe nicht zulässig.

---

## 2. Einrichtungsdialog und Zugangsdaten

`config_flow.py` (Bestätigt):
- Schritt `async_step_user` mit `email` + `password`; Anmeldung im Executor. ✅
- `async_set_unique_id(email.lower())` verhindert, dass dasselbe Konto zweimal eingerichtet wird. ✅
- In der Config Entry werden nur `email` und `refresh_token` gespeichert; das Passwort bleibt im Speicher. ✅
- Erneute Anmeldung über `async_step_reauth` / `async_step_reauth_confirm`, inklusive Prüfung, dass das Konto gleich bleibt (`account_mismatch`). ✅
- Fehler: `invalid_auth` / `cannot_connect` / `unknown`, in allen Sprachen übersetzt. ✅
- Rückgabetyp `ConfigFlowResult`. ✅
- 🟡 **Kein Options Flow** (es gibt keine Einstellungen wie Abfrageintervall oder Standortauswahl).

---

## 3. Setup / Unload / Reload

- `async_setup_entry` erneuert die Sitzung mit dem `refresh_token`, erkennt den Aufbau, räumt verschwundene Zonen aus dem Geräteregister, startet MQTT-Client und Koordinator, lädt die Plattformen und startet die periodische Abfrage. ✅
- Ungültige Sitzung → `ConfigEntryAuthFailed`; Netz-/Backend-Fehler oder ein Konto ohne Standort/Installation → `ConfigEntryNotReady`. ✅
- Die periodische Abfrage läuft als `entry.async_create_background_task` und wird beim Entladen automatisch beendet. ✅
- `async_unload_entry` steht auf Modulebene, entlädt die Plattformen, stoppt MQTT-Client und Koordinator und räumt `hass.data` auf. ✅
- Reload erzeugt weder doppelte Entitäten noch einen doppelten Dienst (getestet). ✅

---

## 4. Koordinator / Datenaktualisierung

- Push-Muster ohne `DataUpdateCoordinator`: MQTT-Thread → Callback → `hass.bus.async_fire("mysair_update")` → `MySairCoordinator` (ein Abonnent je Config Entry) → Dispatcher-Signal je Zone → Entität. ✅
- `DataUpdateCoordinator` ist für abfragende Integrationen gedacht; für reines Push ist der Dispatcher die übliche Lösung. ✅
- 🟡 Kein zentraler Zustandsspeicher: Jede Entität hält ihre eigene Kopie der Zonenwerte. Für die Diagnose ist das ausreichend.

---

## 5. Entitäten

Gemeinsam für climate/sensor/switch (Bestätigt):
- ✅ Stabile `unique_id`.
- ✅ `has_entity_name` mit `translation_key` bzw. Geräteklasse; Namen und Zustände kommen aus `translations/*.json`, Symbole aus `icons.json`.
- ✅ Gemeinsame `device_info` (`device.py`); eine Zone = ein Gerät.
- ✅ `_attr_should_poll = False` und `available` nach Aktualität der MQTT-Daten (`availability.py`).
- ✅ `@callback` für Handler; Abonnements werden in `async_will_remove_from_hass` gelöst.
- 🟡 **Fehler bei Befehlen werden nur geloggt.** Scheitert ein HTTP-Befehl, sieht der Nutzer keine Fehlermeldung in der Oberfläche; der optimistische Zustand wird später zurückgesetzt. Besser wäre ein `HomeAssistantError` mit übersetzter Meldung (Exception Translations).

### climate.py
- ✅ `supported_features`: `TARGET_TEMPERATURE | FAN_MODE | TURN_ON | TURN_OFF`.
- ✅ `hvac_modes` je nach Fähigkeiten der Zone (`OFF` + `HEAT` und/oder `COOL`).
- ✅ `min_temp`/`max_temp` aus `tmm`/`tmx`.
- ✅ `fan_modes` `1`/`2`/`3`/`auto`, wenn die Zone einen Lüfter hat.
- ✅ `async_turn_on` bevorzugt Heizen, fällt aber auf den ersten von der Zone unterstützten Modus zurück.
- 🟡 `async_set_temperature` bei ausgeschalteter Zone sendet keinen Befehl, sondern ändert nur den lokalen Sollwert; er wird beim nächsten Einschalten mitgesendet. Bis dahin können Anzeige und Gerät voneinander abweichen.
- 🟡 Die Richtung Heizen/Kühlen wird je Zone gesetzt. Bei Anlagen, deren Außeneinheit nur eine Richtung für alle Zonen kann, verhindert die Integration widersprüchliche Einstellungen derzeit nicht.

### switch.py
- ✅ „Ein/Aus“ schaltet mit dem zuletzt bekannten Modus ein.
- ✅ „Fußbodenheizung“ berechnet `m` neu und behält Heizen/Kühlen und Luft bei; in Zonen ohne Fußboden nicht verfügbar.

### sensor.py
- ✅ Temperatur, Soll-Temperatur und Luftfeuchte mit Geräteklasse.
- ✅ Modus als `SensorDeviceClass.ENUM` (`off`/`heat`/`cool`/`on`) mit übersetztem Attribut `medium`.
- ✅ „Cloud-Verbindung“ als Diagnose-Entität (`EntityCategory.DIAGNOSTIC`), `ENUM` mit `online`/`offline`.

---

## 6. Dienste / Diagnose / Repairs / Übersetzungen

| Element | Stand | Bewertung |
|---|---|---|
| Eigene Dienste (`services.yaml`) | `mysair.stop_installation` | ✅ einmal je Domain registriert, nach dem letzten Entladen entfernt |
| `diagnostics.py` | vorhanden | ✅ mit `async_redact_data` |
| Repairs | nicht vorhanden | 🟢 optional |
| `translations/` + `strings.json` | Deutsch, Englisch, Spanisch | ✅ Einrichtungsdialog, Dienst, Entitätsnamen und -zustände |
| `icons.json`, `brand/` | vorhanden | ✅ |

---

## 7. Fehler, Ausnahmen und abgelaufene Anmeldung

- ✅ Einheitliche Klassifizierung der HTTP-Fehler (`api._http_error`): 5xx/429 vorübergehend, alles andere erneute Anmeldung.
- ✅ `401` wird überall einmal mit erneuerter Sitzung wiederholt.
- ✅ Stirbt die Sitzung im laufenden Betrieb, fordern MQTT-Thread und periodische Abfrage sofort die erneute Anmeldung an (`entry.async_start_reauth`).
- 🔴 `send_instruction` akzeptiert als Erfolgsmeldung nur `msg == "Creado"`. Englischsprachige Konten erhalten `"Created"`; ihre Befehle werden ausgeführt, aber als abgelehnt geloggt und lösen keine Bestätigung aus. Korrektur geplant.

---

## 8. Abfrage vs. Push / Herunterfahren

- **Push:** MQTT → Event-Bus → Koordinator. ✅
- **Abfrage:** alle 120 s eine HTTP-Anweisung `status`, die eine Push-Meldung auslöst. ✅ Ausgleich für verlorene Nachrichten (Clean Session).
- **Herunterfahren:** über `async_unload_entry`; der MQTT-Thread ist ein Daemon und blockiert das Beenden nicht. ✅

---

## 9. Speicherung von Zuständen

- Im Speicher: `hass.data[DOMAIN][entry_id]` mit `api`, `devices`, `installations`, `mqtt`, `coordinator`. **Bestätigt.**
- ✅ Kein eigener `Store` nötig.
- 🟡 Entitäten stellen ihren Zustand nach einem Neustart nicht wieder her (`RestoreEntity`); sie sind bis zum ersten Status „nicht verfügbar“. Das ist gewollt: lieber keine Daten als veraltete.

---

## 10. Nebenläufigkeit: Loop und Threads

- ✅ MQTT im Daemon-Thread; der Callback kehrt über `hass.loop.call_soon_threadsafe(...)` in den Loop zurück.
- ✅ Synchrones HTTP immer über `async_add_executor_job`.
- ✅ Die periodische Abfrage ist ein Hintergrund-Task der Config Entry und wird beim Entladen abgebrochen.
- ✅ Der Timer für die vorausschauende Erneuerung ist ein `threading.Timer` und wird bei `stop()` abgebrochen.

---

## 11. Kompatibilität mit aktuellem Home Assistant

| Aspekt | Stand | Gewissheit |
|---|---|---|
| Testumgebung | `pytest-homeassistant-custom-component` passend zu HA 2026.9 (Python 3.14) | Bestätigt |
| Entfernen verwaister Geräte | `async_remove_device` statt des abgekündigten `remove_config_entry_id` | Bestätigt |
| Einheit der Luftfeuchte | `UnitOfRatio.PERCENTAGE` | Bestätigt |
| Entity-IDs | Entstehen aus Gerät + Entitätsname (`has_entity_name`); bestehende IDs bleiben erhalten | Bestätigt |
| Markenbilder | lokal in `brand/` (HA ≥ 2026.3.0) | Bestätigt |

Der Pin in `requirements-test-ha.txt` muss mit der produktiv genutzten HA-Version Schritt halten, sonst bleiben Abkündigungen unbemerkt.

---

## 12. Mögliche nächste Schritte

1. 🔴 Erfolgsprüfung von `/send/instruction` sprachunabhängig machen.
2. 🟡 Fehler bei Befehlen als übersetzte `HomeAssistantError` an die Oberfläche geben.
3. 🟢 Tests für doppelte und vertauschte MQTT-Nachrichten.

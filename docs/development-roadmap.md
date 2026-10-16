# Entwicklungs-Roadmap

> Nach Phasen geordnet; jeder Punkt nennt Priorität bzw. Stand. Die Kennungen (A1, C4, E6 …) werden im Code, im `CHANGELOG.md` und in anderen Dokumenten zitiert und bleiben deshalb unverändert.
> Priorität: 🔴 kritisch · 🟠 hoch · 🟡 mittel · 🟢 niedrig.

---

## Phase A – Stabilisierung (Defektes reparieren)

| # | Aufgabe | Stand |
|---|---|---|
| A1 | **Unload reparieren:** `async_unload_entry` auf Modulebene mit Standardsignatur; MQTT schließen, periodischen Task beenden, `async_unload_platforms`, `hass.data` aufräumen. | ✅ erledigt |
| A2 | **Periodischen Task verwalten:** als `entry.async_create_background_task`, beim Entladen beendet; Intervall 120 s. | ✅ erledigt |
| A3 | **`select.py` entfernen oder neu schreiben** (wurde nicht geladen und war fehlerhaft). | ✅ erledigt (entfernt) |
| A4 | **`requirements` bereinigen:** nur `requests` und `websocket-client`; `aiohttp`, `paho-mqtt`, `boto3` und das Vorladen von paho entfernt. | ✅ erledigt |
| A5 | **Kodierung des Modus klären** (`known-unknowns` #1, #2, #4) und `switch.async_turn_on` reparieren, das immer „Kühlen“ setzte. | ✅ erledigt |
| A6 | **Kein Passwort im Klartext / keine ungenutzten Token** in der Config Entry (`security-and-privacy` §3). | ✅ erledigt |
| A7 | **Vorausschauende Erneuerung der AWS-Zugangsdaten**: bei jeder Wiederverbindung und – wie die offizielle App – **vor** dem Ablauf, damit AWS die Verbindung nicht trennt. | ✅ erledigt |

---

## Phase B – Tests (Netz ersetzt, ohne Gerät)

| # | Aufgabe | Stand |
|---|---|---|
| B1 | Testbarkeit: `parse_status_payload` herauslösen, `requests.Session` injizierbar machen. | ✅ erledigt |
| B2 | `pytest` + `pytest-homeassistant-custom-component` + bereinigte Fixtures. | ✅ erledigt |
| B3 | Tests P0/P1 (reine Funktionen, HTTP, Einrichtungsdialog). | ✅ erledigt |
| B4 | Tests P2/P3 (MQTT, Entitäten, Unload/Reload). | 🟡 weitgehend erledigt: Reload, mehrere Installationen, Aufbauänderung, geteilte/mehrere Pakete (mit synthetischen Bytes). Offen: doppelte und vertauschte Nachrichten sowie die Bestätigung des Paketdecoders mit echtem Datenverkehr. |
| B5 | CI mit GitHub Actions: `hassfest`, `pytest` (P0/P1 + P2 über Docker), `ruff`, HACS-Prüfung. | ✅ erledigt (Standardregeln von `ruff`; strengere Regeln wären eine mögliche Erweiterung) |

---

## Phase C – Modernisierung für Home Assistant

| # | Aufgabe | Stand |
|---|---|---|
| C1 | **Zentraler Koordinator** je Zone statt Verteilung über den Event-Bus an jede Entität. | ✅ erledigt – `MySairCoordinator` hört einmal je Config Entry auf `mysair_update` und verteilt jede Zone über den Dispatcher. |
| C2 | `unique_id` der Config Entry (`async_set_unique_id`) gegen doppelte Einträge. | ✅ erledigt |
| C3 | **Reauth** (`async_step_reauth`) und `ConfigEntryAuthFailed`/`ConfigEntryNotReady`. | ✅ erledigt |
| C4 | Übersetzungen (`strings.json`, `translations/`). | ✅ erledigt – Einrichtungsdialog, Dienst sowie Entitätsnamen und -zustände (`has_entity_name`/`translation_key`) auf Deutsch, Englisch und Spanisch; Symbole über `icons.json`. |
| C5 | `_attr_should_poll = False`; `available` nach Aktualität der MQTT-Daten. | ✅ erledigt |
| C6 | `datetime.utcnow()` durch `datetime.now(timezone.utc)` in der SigV4-Signatur ersetzen. | ✅ erledigt |
| C7 | `FlowResult` → `ConfigFlowResult` und weitere Abkündigungen prüfen. | ✅ erledigt |
| C8 | Echte `tmm`/`tmx` als `min_temp`/`max_temp` von climate. | ✅ erledigt |

---

## Phase D – Beobachtbarkeit

| # | Aufgabe | Stand |
|---|---|---|
| D1 | `diagnostics.py` mit `async_redact_data`. | ✅ erledigt – Config Entry, Installationen, Geräte, API-Sitzung und MQTT-Zustand; Token und Zugangsdaten geschwärzt. |
| D2 | Sensible Logs schwärzen (signierte URL, Token) und Log-Stufen vereinheitlichen. | ✅ erledigt – `_truncate()` (HTTP-Fehlertexte), `_redact_client_id()` (AWS-Key in der clientId), häufige Logs auf DEBUG. |
| D3 | Sensor für den Zustand der MQTT-Verbindung und die letzte Aktualisierung. | ✅ erledigt – Sensor „Cloud-Verbindung“, einer je Config Entry. |
| D4 | Kennzahlen zu Wiederverbindungen und Auswertungsfehlern. | ✅ erledigt – Attribute des Sensors und Diagnose. |

---

## Phase E – Robustheit des Protokolls

| # | Aufgabe | Stand |
|---|---|---|
| E1 | Standardkonformer MQTT-Parser statt `split`/`{...}`. | ✅ erledigt (`known-unknowns` #6) – primäre Methode, Rückfall auf die Heuristik bei unklarem Ergebnis. |
| E2 | Geteilte Frames / mehrere Pakete je WebSocket-Frame. | ✅ erledigt – Empfangspuffer mit Längenauswertung. |
| E3 | Exponentieller Backoff mit Jitter. | ✅ erledigt – Basis 10 s, höchstens 120 s, ±20 %; Rücksetzung beim CONNACK; geplante Wiederverbindungen ohne Wartezeit. |
| E4 | Prüfung der Nutzdaten (unerwartete Formen ablehnen/loggen). | ✅ erledigt – ohne zusätzliche Bibliothek; unbekannte Zusatzfelder werden toleriert. |
| E5 | Eigene, eindeutige `client_id` (`known-unknowns` #20). | ✅ erledigt |
| E6 | Umstieg auf `paho-mqtt` prüfen. | ✅ geprüft – **Entscheidung: kein Umstieg.** `paho-mqtt` unterstützt keine SigV4-signierte WSS-URL, die vor jeder Wiederverbindung neu signiert werden muss. Ersetzt würden nur etwa 150–200 Zeilen MQTT-Framing, während vorausschauende Erneuerung, Backoff und Kennzahlen weiter selbst gebaut werden müssten. Der Aufwand, das bereits erprobte Wiederverbindungsverhalten neu abzusichern, lohnt sich nicht. |
| E7 | Optimistischen Zustand nach Zeitlimit zurücksetzen, wenn keine Bestätigung kommt. | ✅ erledigt – Abonnement von `feedback`, Zuordnung über `orderId`, Rücksetzung von Temperatur/Modus/Lüfter/Schalter; ein echter Status hat Vorrang. |

---

## Phase F – Neue Funktionen

| # | Aufgabe | Stand |
|---|---|---|
| F1 | Feuchtesensor und echte Verfügbarkeit von Heizen/Kühlen in `climate.hvac_modes` nach Fähigkeiten (`c`/`f`). | ✅ erledigt |
| F2 | Lüfterstufe (`fanspeed`/`vv`): `"0"`=kein Modus, `"1"/"2"/"3"`=manuell, `"4"`=Automatik (`known-unknowns` #24). | ✅ erledigt |
| F3 | Modus `auto`, falls das System ihn kennt. | ✅ geprüft – **vom Protokoll nicht unterstützt** (`m` 0–5 deckt genau die sechs Kombinationen ab). Ungenutzte Konstante entfernt. |
| F4 | Funktionierendes `select` für den Modus. | ✅ erledigt als **Umwidmung**: Das alte `select.py` doppelte `climate.hvac_mode`. Stattdessen gibt es einen Schalter „Fußbodenheizung“ je Zone, der den vorhandenen Befehl `mode` nutzt. |
| F5 | Dienst `mysair.stop_installation` (Befehl `stop`, `value:"1"`). | ✅ erledigt – einmal je Domain registriert, nach dem letzten Entladen entfernt. |
| F6 | Timer (`temporizer`) und Programme (`programs`). | 🟢 bewusst nicht umgesetzt – das Format zum Setzen ist im Bundle nicht entschlüsselt (es gibt nicht einmal ein `setPrograms`); eine Umsetzung würde erfundene Felder erfordern. Wieder aufgreifen nur mit einem echten Mitschnitt. Siehe `known-unknowns.md` #27. |

---

## Phase G – Veröffentlichung und Pflege

| # | Aufgabe | Stand |
|---|---|---|
| G1 | `hacs.json` + Struktur `custom_components/mysair/` für HACS. | ✅ erledigt |
| G2 | Vollständige README: Installation, Einrichtung, Einschränkungen, Hinweis „inoffiziell“. | ✅ erledigt |
| G3 | Semantische Versionierung und CHANGELOG. | ✅ erledigt; GitHub-Releases mit Tag `v<version>` nach jedem Merge auf `main` (siehe `CLAUDE.md` §9). |
| G4 | `quality_scale` prüfen. | ✅ erledigt – Markenbilder lokal in `custom_components/mysair/brand/` (HA ≥ 2026.3.0), `quality_scale: silver` von `hassfest` akzeptiert. Kein zusätzlicher PR an `home-assistant/brands` für ältere HA-Versionen. |
| G5 | `.gitignore` erweitern (`.env`, `*.har`, Mitschnitte). | ✅ erledigt |

---

## Offen (ohne Phasennummer)

- **Schutz vor widersprüchlicher Heiz-/Kühlrichtung** für Anlagen, deren Außeneinheit nur eine Richtung für alle Zonen kann – 🟠.
- **Fehler bei Befehlen sichtbar machen** (`HomeAssistantError` mit übersetzter Meldung) – 🟡.
- **Tests für doppelte/vertauschte MQTT-Nachrichten** und für `account_mismatch` im Reauth – 🟢.
- **Uninterpretierte Zonenfelder** `vf`/`hmh`/`mh`/`p`/`ps` – nur mit neuen Belegen (`known-unknowns.md` §6).
- **Paketdecoder (E1/E2) mit echtem Datenverkehr bestätigen** – bisher nur mit synthetischen Bytes getestet.

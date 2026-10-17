# Umsetzungsplan – Zusammenfassung

> Kurzfassung der umgesetzten Aufgaben und der dabei getroffenen Entscheidungen. Das vollständige, frühere Arbeitsprotokoll (auf Spanisch) ist in der Git-Historie dieser Datei erhalten.
> Die Aufgabennummern (Aufgabe 1–31) und die Kennungen der Roadmap (A1, C4 …) werden in anderen Dokumenten und Commit-Nachrichten zitiert und bleiben deshalb erhalten.
> Bei keiner Aufgabe wurden Anfragen an die Produktivsysteme gesendet. Ausführliche Begründungen stehen in `docs/development-roadmap.md`, `docs/known-unknowns.md` und `docs/protocol-findings.md`.

## Übersicht

| # | Aufgabe | Roadmap | Ergebnis |
|---|---|---|---|
| 1 | Unload reparieren, periodischen Task verwalten | A1, A2 | `async_unload_entry` auf Modulebene, Task als Hintergrund-Task der Config Entry, Intervall 120 s |
| 2 | `requirements` bereinigen | A4 | nur `requests` und `websocket-client` |
| 3 | `select.py` | A3 | entfernt (wurde nie geladen, fehlerhaft) |
| 4 | Testbarkeit herstellen | B1 | reines Modul `status_parser.py`, injizierbare `requests.Session` |
| 5 | Erste Tests P0/P1 | B2, B3 | Builder, SigV4, Parser, HTTP-Client |
| 6 | Paketlayout `custom_components/mysair/` | G1 | Voraussetzung für pytest und HACS |
| 7 | Kodierung Modus/Ein-Aus | A5 | `e` = Ein/Aus/Standby, `m` = Modus (Parität = Heizen/Kühlen), Einschalten behält den letzten Modus |
| 8 | Robustheit MQTT | E5, #20, #22, #5 | eindeutige client_id je Verbindung, Topic aus `aws_base_topic`, Ablauf der AWS-Zugangsdaten beachten |
| 9 | unique_id, Passwort aus der Config Entry, Reauth | C2, A6, C3 | nur `email` + `refresh_token` gespeichert; typisierte Ausnahmen; automatische Migration alter Einträge |
| 10 | HACS-Paket und README | G1, G2 | `hacs.json`, README, MIT-Lizenz |
| 11 | Versionierung, `quality_scale`, `.gitignore` | G3, G4, G5 | CHANGELOG nach Keep a Changelog/SemVer; `quality_scale` vorerst zurückgezogen |
| 12 | Tests mit HA-Testumgebung über Docker | B4 | Einrichtungsdialog, Setup/Unload; Fixture, das `custom_components/mysair` für die HA-Testumgebung auffindbar macht |
| 13 | Tests für Entitäten und MQTT-Ereignisse | B4 | climate/sensor/switch, Filter nach `ctl`, Befehlsargumente |
| 14 | CI mit GitHub Actions | B5 | Jobs für pytest P0/P1, pytest P2 (Docker), `hassfest` |
| 15 | Luftfeuchte, Verfügbarkeit Heizen/Kühlen, echte Grenzwerte | F1, C8 | Feuchtesensor; `hvac_modes` nach Fähigkeiten; `min_temp`/`max_temp` aus `tmm`/`tmx` |
| 16 | Befehlsbestätigung über `feedback` (Teil 1) | E7 | Abonnement, Ereignis `mysair_feedback`, Log bei Bestätigung oder Zeitüberschreitung |
| 17 | Zuverlässigkeit | C5, C6, C7 | `should_poll=False`, Verfügbarkeit nach Datenalter (360 s) mit Timer; `datetime.now(timezone.utc)`; `ConfigFlowResult` |
| 18 | Lüfterstufe | F2 | `climate.fan_mode`, Befehl `fanspeed` |
| 19 | Fehler aus echten Nachrichten | #6, #23 | Topic ohne Klammern wurde nicht erkannt (Bestätigung griff nie); Feuchtefeld (später korrigiert, siehe #25) |
| 20 | Ursache regelmäßiger MQTT-Abbrüche | A7, #22 | vorausschauende Erneuerung vor `aws_expires_at`; Hinweis im Log, ob MQTT beim fehlenden ACK verbunden war |
| 21 | Rücksetzen des optimistischen Zustands; standardkonformer Paketdecoder | E7, E1 | Rücksetzung nach 5 s ohne Bestätigung; `parse_mqtt_publish` mit Rückfall-Heuristik |
| 22 | Backoff, Diagnose, Dienst „Anlage stoppen“ | E3, D1, F5 | `compute_backoff_delay`; `diagnostics.py`; `mysair.stop_installation` |
| 23 | Übersetzungen Einrichtungsdialog und Dienst | C4 | `strings.json`, `translations/es.json` (Entitätsnamen damals noch nicht) |
| 24 | Zentraler Koordinator | C1 | `MySairCoordinator`, Verteilung je Zone über den Dispatcher |
| 25 | Beobachtbarkeit | D2, D3, D4 | Logs gekürzt/maskiert, häufige Logs auf DEBUG; Sensor „Cloud-Verbindung“ mit Kennzahlen |
| 26 | Robustheit des Protokolls | E2, E4, E6 | Empfangspuffer; Prüfung der Nutzdaten; kein Umstieg auf `paho-mqtt` |
| 27 | Phase F abschließen | F3, F4, F6 | kein Modus `auto` im Protokoll; Schalter „Fußbodenheizung“; Timer/Programme bewusst nicht umgesetzt |
| 28 | Attribut für das aktive Medium | – | Attribut am Modus-Sensor (heute `medium`: `air`/`floor`/`air_and_floor`) |
| 29 | Markenbilder und `quality_scale` | G4 | lokale Bilder in `brand/` (HA ≥ 2026.3.0); `quality_scale: silver` von `hassfest` akzeptiert |
| 30 | `ruff` in der CI, Tests ohne echte Mitschnitte | B5, B4 | Standardregeln, einmalige Formatierung; Tests für Reload, mehrere Installationen, Aufbauänderung |
| 31 | Verwaiste Geräte nach Aufbauänderung entfernen | – | `_cleanup_stale_zone_devices` bei jedem Setup/Reload |

## Wichtige Entscheidungen und Erkenntnisse

- **Befehle per HTTP, Zustand per MQTT.** Die MQTT-Verbindung ist reiner Empfang; daran richtet sich die gesamte Architektur aus (`docs/architecture.md`).
- **Nichts erfinden.** Felder und Befehle werden nur umgesetzt, wenn sie im Code der offiziellen App oder in echten Nachrichten belegt sind. Deshalb bleiben `vf`/`hmh`/`mh`/`p`/`ps` uninterpretiert. Gewissheitsstufen unterscheiden: belegt im Code der App, belegt in echten Nachrichten, unbekannt.
- **Echte Nachrichten finden, was Tests nicht finden.** Die Fehler aus Aufgabe 19 und die Ursache der Verbindungsabbrüche (Aufgabe 20) ließen sich weder aus dem statischen JS noch mit Unit-Tests erkennen, weil die Mocks die vermutete statt der tatsächlichen Form nachbildeten.
- **Gewissheit nicht vorschnell herabstufen.** „Im Bundle nicht gefunden“ erst nach einer Wiederholung mit Varianten werten (Beispiel `sv`, `known-unknowns.md` #26). Umgekehrt: Bei Demo-Seiten im Bundle prüfen, ob der Code zum echten Datenfluss gehört (Beispiel `vv`, `protocol-findings.md` §9).
- **Ein einzelner Messwert ist kein Beweis.** Die Annahme, `hum` sei das Feuchtefeld, beruhte auf einer Anlage ohne Feuchtemessung, bei der beide Felder `"0"` meldeten (`known-unknowns.md` #25).
- **Fehlerklassifizierung nach Bereich.** Das Backend antwortet bei einem gelöschten `refresh_token` mit `404`. Eine Liste einzelner Codes übersah das und verhinderte die erneute Anmeldung; seither gilt: 5xx/429 vorübergehend, sonst Reauth.
- **Bestehende Entity-IDs bleiben erhalten.** Bei Namensänderungen (Umstieg auf `has_entity_name`, Übersetzungen) gilt das Register; neue IDs entstehen nur bei Neueinrichtung.
- **Keine Abhängigkeit ohne Not.** Kein `paho-mqtt` (E6), kein `boto3`, kein `voluptuous` im reinen Parser.
- **Abgrenzungen:** nur der erste Standort (#15); kein zusätzlicher PR an `home-assistant/brands` für HA-Versionen vor 2026.3.0.

## Offen

Siehe `docs/development-roadmap.md`, Abschnitt „Offen“.

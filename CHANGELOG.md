# Änderungsprotokoll

Alle nennenswerten Änderungen an diesem Projekt werden in dieser Datei dokumentiert.

Das Format folgt [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), und das Projekt verwendet [Semantic Versioning](https://semver.org/lang/de/): `MAJOR.MINOR.PATCH`. `MAJOR` steht für inkompatible Änderungen (z. B. Installationspfad, Schema der Config Entry oder beobachtbares Verhalten), `MINOR` für rückwärtskompatible neue Funktionen und `PATCH` für Fehlerkorrekturen ohne Änderung des erwarteten Verhaltens.

Die Abschnitte bis einschließlich 2.13.1 stammen aus dem Ursprungs-Repository und wurden nachträglich aus dem Spanischen übersetzt. Zitierte Entitätsnamen geben den damaligen Stand wieder.

## [Unreleased]

> ⚠️ **Vor dem Update lesen:** Heizen und Kühlen folgen jetzt der Richtung der ganzen Anlage. Läuft eine Zone, kann eine andere Zone nicht mehr in der Gegenrichtung eingeschaltet werden. Der Schalter „Fußbodenheizung“ zeigt nur noch „an“, solange die Zone läuft. Fußbodenkühlung muss in den Optionen ausdrücklich erlaubt werden.

### Added
- **Option „Fußbodenkühlung erlauben“** (Standard: aus) unter Einstellungen → Geräte & Dienste → MySair → Konfigurieren. Ausgeschaltet nutzt die Integration den Fußboden nicht, solange die Anlage kühlt, weil MySair keinen Taupunktschutz meldet.
- **Eigenes Gerät für die Installation** (die MySair-Steuerung) mit ihrer Software-Version. Die Geräte bilden jetzt einen Baum: Konto (Cloud-Verbindung) → Installation → Zonen.
- **Neue Entitäten je Installation:** Auswahl „Betriebsart“ (Heizen/Kühlen, auch wenn alle Zonen aus sind; umstellbar nur, solange alle Zonen aus sind, wie der Saisonwechsel in der App), Knopf „Anlage stoppen“, „In Betrieb“ (mindestens eine Zone läuft), „Hauptzone“ (Diagnose) und „Heiz-/Kühlkonflikt“ (Diagnose; meldet ein Problem, wenn laufende Zonen unterschiedliche Richtungen haben).
- **Verständliche Fehlermeldungen in der Oberfläche** auf Deutsch, Englisch und Spanisch, wenn eine Bedienung nicht erlaubt ist (Richtungswechsel bei laufender Anlage, Fußbodenkühlung ausgeschaltet, Richtung noch unbekannt).

### Changed
- **Heizen/Kühlen wie in der MySair-App:** Eine Anlage hat eine Richtung für alle Zonen. Läuft mindestens eine Zone, bieten die Thermostate nur „Aus“ und die aktuelle Richtung an; sind alle Zonen aus, ist beides möglich und das Einschalten einer Zone legt die Richtung fest. Bisher konnte jede Zone ihre eigene Richtung senden.
- **Einschalten wie in der App** (Thermostat und Schalter „Ein/Aus“): Klimaanlage an, Fußboden wie zuletzt, Richtung der Anlage und die aktuelle Soll-Temperatur. Bisher wurden immer 22 °C gesendet, der Fußboden ging verloren, und nach dem Ausschalten wurde bevorzugt „Heizen“ gewählt.
- **„Anlage stoppen“** wird wie in der App an die Hauptzone der Anlage gesendet statt ohne Zone.
- Der Schalter „Fußbodenheizung“ zeigt wie die App nur „an“, solange die Zone läuft.
- Dokumentation: Regeln der App zur Richtung der Anlage, zum Einschalten und zum Stoppen beschrieben (`docs/protocol-findings.md` §4b).
- Dokumentation zu Timer und Programmen korrigiert: Die App kann Programme schreiben (`update-program`) und löschen (`delete-program`); die Werte des Timers sind bekannt. Die Umsetzung ist geplant (`docs/known-unknowns.md` #27).

### Fixed
- **Schalter „Fußbodenheizung“ funktionierte nicht:** Die Befehlsprüfung ließ nur die Modi `0` und `1` zu, sodass jeder Befehl mit Fußboden (`2`–`5`) verworfen wurde. Bleibt nach dem Ausschalten des Fußbodens nichts mehr an, wird die Zone jetzt wie in der App ausgeschaltet.

## [3.0.0] - 2026-10-17

Erstes Release dieses Forks.

> ⚠️ **Vor dem Update lesen:** Der Modus-Sensor liefert neue Zustandswerte (`off`/`heat`/`cool`/`on`) und das Attribut heißt jetzt `medium`. Automationen und Dashboards, die auf die alten Werte (`OFF`/`HEAT`/…, `medio`) prüfen, müssen angepasst werden. Bestehende Entity-IDs bleiben erhalten.

### Added
- **Bedienoberfläche auf Deutsch, Englisch und Spanisch.** Entitätsnamen, Zustände und Symbole kommen jetzt aus den Übersetzungs- und Symboldateien von Home Assistant (`translations/*.json`, `icons.json`) statt aus festen spanischen Texten. Angezeigt wird die in Home Assistant eingestellte Sprache. Neu: `translations/de.json` für Entitäten, Einrichtungsdialog und den Dienst „Anlage stoppen“.
- Temperatur- und Feuchtesensor tragen keinen eigenen Namen mehr: Home Assistant benennt sie über ihre Geräteklasse („Temperatur“, „Luftfeuchtigkeit“) und wählt das passende Standardsymbol.
- Der Modus-Sensor ist ein Aufzählungssensor (`enum`); Zustände und das Attribut `medium` werden übersetzt angezeigt.
- Der Sensor „Cloud-Verbindung“ ist als Diagnose-Entität eingestuft und erscheint dadurch nicht mehr in Standard-Dashboards.

### Changed
- **Inkompatibel – Werte des Modus-Sensors:** Zustände lauten `off`/`heat`/`cool`/`on` statt `OFF`/`HEAT`/`COOL`/`ON`. Das Attribut heißt `medium` (Werte `air`/`floor`/`air_and_floor`) statt `medio` (`ac`/`suelo`/`mixto`). Automationen, die auf die alten Werte prüfen, müssen angepasst werden.
- Gerätemodell der Zonen: „Klima-Zonenregler“, des Kontogeräts: „Integration“.
- Bestehende Entity-IDs bleiben erhalten. Bei einer Neueinrichtung erzeugt Home Assistant die IDs aus den Namen in der eingestellten Sprache (z. B. `sensor.<zone>_luftfeuchtigkeit`).
- Code-Kommentare, Docstrings und Log-Meldungen auf Englisch statt Spanisch; Log-Meldungen ohne Emojis. Keine Änderung am Verhalten.
- Ersatzname einer Zone ohne Namen im Konto einheitlich „Zone <Referenz>“ (bisher je nach Plattform „Zona …“ oder „Termostato …“).
- **Luftfeuchte 0 wird als „unbekannt“ gemeldet statt als 0 %.** Eine echte Raumluftfeuchte von 0 % gibt es nicht; eine 0 bedeutet, dass das Thermostat keinen Feuchtesensor hat oder noch nicht gemessen hat. So landen keine falschen Nullen in der Langzeitstatistik und lösen keine feuchtebasierten Automationen aus.
- **Dokumentation auf Deutsch.** README, `CLAUDE.md`, `CHANGELOG.md` und alle Dokumente in `docs/` sind übersetzt und auf den aktuellen Stand gebracht. Die README nennt dieses Repository als Installationsquelle für HACS, beschreibt die aktuellen Entitäten und verweist auf das Ursprungs-Repository. `docs/execution-plan.md` ist eine Zusammenfassung; das frühere Arbeitsprotokoll liegt in der Git-Historie.

- Restliche spanische Texte in Code, Tests und Konfigurationsdateien (Fehlermeldungen beim Start, Kommentare, Namen der CI-Jobs, `services.yaml`) auf Englisch.
- `manifest.json` verweist für Dokumentation, Fehlermeldungen und Codeowner auf dieses Repository.
- Beschreibung des Felds „Anlagenreferenz“ im Dienst `mysair.stop_installation`: Die Referenz steht im Diagnose-Download (seit den neuen Gerätenamen nicht mehr im Gerätenamen). Beispielwerte sind neutrale Platzhalter.

### Fixed
- **Befehle wurden bei MySair-Konten, die nicht auf Spanisch eingestellt sind, fälschlich als abgelehnt gemeldet.** Die Integration verlangte als Antwort den spanischen Text `"Creado"`; je nach Kontosprache antwortet MySair aber anders, z. B. mit `"Created"`. Die Befehle wurden ausgeführt, im Log stand jedoch „Instruction rejected“ (auch bei der periodischen Abfrage alle 2 Minuten), und die Befehlsbestätigung wurde nicht verfolgt. Jetzt gelten HTTP 201 und eine leere Fehlerliste als Erfolg, unabhängig vom Text und damit für jede Kontosprache.
- `manifest.json`: `requests` aus `requirements` entfernt. Home Assistant bringt die Bibliothek selbst mit, und `hassfest` lässt sie im Manifest einer Custom-Integration nicht mehr zu. Keine Änderung am Verhalten.
- **Befehle ohne geladene AWS-Zugangsdaten** wurden mit einer fest eingetragenen, fremden Kontokennung im Feld `app` gesendet. Jetzt werden die Zugangsdaten vorher geholt und die eigene Kennung verwendet; gelingt das nicht, wird der Befehl nicht gesendet.
- Tests mit HA-Testumgebung öffnen keine Netzwerkverbindungen mehr (periodische Abfrage und Einrichtungsdialog), wodurch Fehler beim Aufräumen nach einzelnen Tests entfallen.
- **Luftfeuchte zeigte immer 0 %.** Das Zonen-JSON enthält zwei Feuchtefelder: `hum` meldet `"0"`, `hm` trägt den tatsächlichen Messwert, den auch die offizielle Web-App anzeigt. Die Integration las bisher bevorzugt `hum`. Jetzt gilt: `hm` zuerst, `hum` nur noch als Ersatz, falls `hm` fehlt (`status_parser.parse_humidity`).

## [2.13.1] - 2026-09-12

### Fixed
- **Der Einrichtungsdialog war auf Englisch nicht übersetzt.** Home Assistant liest `strings.json` bei Custom-Integrationen zur Laufzeit nicht, sondern lädt nur `translations/<sprache>.json`. Es gab `es.json`, aber kein `en.json`. Wer HA auf Englisch (Standardsprache) nutzte, sah daher rohe Schlüssel wie `invalid_auth` bei der Anmeldung, bei der erneuten Anmeldung und im Dienst `mysair.stop_installation`.
- `climate.turn_on` bewirkte in einer Zone, die nur kühlen kann, nichts: Es wählte immer Heizen, ohne die Fähigkeiten der Zone zu beachten, und die Prüfung der Entität verwarf das stillschweigend. Jetzt wird Heizen bevorzugt, sonst der erste Modus, den die Zone unterstützt.

### Changed
- `PERCENTAGE` wird im Feuchtesensor durch `UnitOfRatio.PERCENTAGE` ersetzt; die alte Einheit ist seit HA 2026.7 abgekündigt. Gleicher Wert, kein geändertes Verhalten.
- CI: neuer Job zur HACS-Prüfung; bisher prüfte nur `hassfest`.

## [2.13.0] - 2026-09-12

### Changed
- **Entitäts- und Gerätenamen an das Schema von Home Assistant 2026 angepasst.** HA bildet die `entity_id` jetzt aus Bereich + Gerät + Entität. Ohne Anpassung hätte eine Neuinstallation `climate.dev_1_inst_a_salon` statt `climate.salon` angelegt. Die Entitäten nutzen jetzt `has_entity_name`: Das Gerät trägt den Zonennamen („Salón“ statt „DEV_1 (INST_A)“), und jede Entität steuert nur ihren eigenen Teil bei („Temperatura actual“, „Humedad“, „Encendido“, „Suelo radiante“).
  - **Bestehende Installationen verlieren nichts:** Das Entitätsregister behält vorhandene `entity_id`s, solange die `unique_id` gleich bleibt; Automationen und Dashboards funktionieren weiter. Ein Test stellt das sicher.
  - Bei **Neuinstallationen** ändern sich die Namen der beiden Schalter gegenüber dem bisherigen Schema: `switch.<zona>` wird zu `switch.<zona>_encendido` und `switch.<zona>_suelo` zu `switch.<zona>_suelo_radiante`. Alle anderen Entitäten behalten ihre bisherige Kennung.
  - Das Kontogerät heißt jetzt „MySair“ statt „MySair (cuenta)“, sein Sensor „Conexión MQTT“.
- Mindestversion von Home Assistant: **2026.8.0** (bisher 2025.10.0), passend zur getesteten Version.
- Die Testumgebung steigt von Home Assistant 2025.1.4 auf 2026.9 (und damit Python 3.12 → 3.14). Sie lag 20 Versionen hinter der produktiv genutzten Version, sodass Abkündigungen unbemerkt blieben.

### Fixed
- Entfernen verwaister Geräte: `async_update_device(remove_config_entry_id=...)` ist seit HA 2026.8 abgekündigt (ein Gerät gehört nur noch zu einer Config Entry) und soll mit 2027.8 entfallen; ersetzt durch `async_remove_device`. Bisher stand bei jedem Start eine Abkündigungswarnung im Log.

### Removed
- Der Block `device_info`, der in allen sieben Entitäten einer Zone wortgleich wiederholt wurde, ist in `device.py` zusammengefasst.

## [2.12.0] - 2026-09-12

### Fixed
- **Eine abgelaufene Sitzung erzwingt keine Neuinstallation mehr.** MySair läuft auf Laravel Passport, das mit `404` (nicht `401`) antwortet, wenn das `refresh_token` auf dem Server nicht mehr existiert, und dazu den Text `No query results for model [Laravel\Passport\RefreshToken]` liefert. Dieser 404 wurde als Verbindungsfehler eingestuft, führte zu `ConfigEntryNotReady`, und Home Assistant versuchte den Start in einer Schleife (`setup_retry`), ohne je die erneute Anmeldung anzubieten. Jetzt wird nach Bereich eingestuft: `5xx` und `429` sind vorübergehende Fehler (erneut versuchen), jeder andere Code außerhalb von 2xx an einem Sitzungsendpunkt verlangt eine erneute Anmeldung.
- `get_locations()`, `get_installations()` und `get_devices()` liefern bei Fehlern nicht mehr `[]`. Eine abgelaufene Sitzung sah wie ein „leeres Konto“ aus und endete ebenfalls in einer Wiederholungsschleife; eine leere Liste bedeutet jetzt nur noch, dass das Konto keine Daten hat.
- `refresh_aws_credentials()` wiederholt bei einem `401` einmal nach Erneuerung der Sitzung, wie es `send_instruction()` bereits tat. Bisher war es darauf angewiesen, dass die periodische Abfrage das Token zufällig erneuerte.

### Added
- **Erneute Anmeldung im laufenden Betrieb.** Stirbt die Sitzung bei laufender Integration (Abmeldung in der offiziellen App, Passwortänderung, Aufräumen von Token auf dem Server), fordern MQTT-Thread und periodische Zustandsabfrage sofort die erneute Anmeldung an, statt bis zum nächsten Neustart von Home Assistant still weiter zu versuchen. Der MQTT-Thread versucht es eingeschränkt weiter und meldet das nur einmal; nach einer erfolgreichen Verbindung wird die Meldung wieder scharf geschaltet.
- Die erneute Anmeldung prüft, dass das Konto gleich bleibt (`account_mismatch`), mit Übersetzung ins Englische und Spanische.

## [2.11.2] - 2026-07-21

### Added
- Automatisches Entfernen verwaister Entitäten: Verschwindet eine Zone zwischen zwei Starts bzw. Neuladevorgängen aus dem MySair-Konto, werden ihr Gerät und alle ihre Entitäten (Thermostat, Sensoren, Schalter) aus dem Register von Home Assistant entfernt, statt dauerhaft verwaist zu bleiben.

## [2.11.1] - 2026-07-21

### Added
- `ruff` (Lint + Formatierung) eingerichtet und als vierter, unabhängiger Job in die CI aufgenommen (`pyproject.toml`, `requirements-lint.txt`).
- Drei neue Tests, die keinen echten Mitschnitt brauchen: Neuladen der Config Entry (ohne doppelte Entitäten oder doppelten gemeinsamen Dienst), mehrere Installationen in einem Konto und Änderung des Aufbaus (hält fest, dass eine entfernte Zone verwaist im Entitätsregister bleibt, ohne automatisches Aufräumen).

### Fixed
- Drei ungenutzte Imports und eine ungenutzte lokale Variable entfernt (`api.py`, `config_flow.py`, `mqtt_handler.py`), ohne Änderung am Verhalten.

## [2.11.0] - 2026-07-21

### Added
- Symbol und Logo (`custom_components/mysair/brand/`) über den lokalen Mechanismus von Home Assistant ≥ 2026.3.0 (ohne externen PR an `home-assistant/brands`). Ab dieser HA-Version erscheinen sie automatisch in der Oberfläche.
- `manifest.json` deklariert wieder `quality_scale: "silver"` – zu diesem Zeitpunkt war noch offen, ob `hassfest` das für eine Custom-Integration akzeptiert.

## [2.10.2] - 2026-07-21

### Added
- Neues Attribut `medio` an `sensor.<zona>_modo`: zeigt in einem Wert, ob die Zone Klimaanlage (`ac`), Fußbodenheizung (`suelo`) oder beides (`mixto`) nutzt, statt `climate.<zona>` und `switch.<zona>_suelo` von Hand kombinieren zu müssen. Bleibt bei ausgeschalteter Zone erhalten.

## [2.10.1] - 2026-07-21

### Changed
- Das `model` des Geräts (auf der Geräteseite in Home Assistant) ändert sich von „Zonificador de aire“ zu „Zonificador de climatización“, weil auch Fußbodenheizung gesteuert wird und nicht nur Klimaanlage. Rein kosmetisch, ohne Auswirkung auf `unique_id` oder die Zuordnung der Entitäten.

## [2.10.0] - 2026-07-21

### Added
- Steuerung der Fußbodenheizung je Zone: neuer Schalter (einer je Zone mit dieser Fähigkeit), unabhängig vom allgemeinen Ein/Aus-Schalter. Nutzt den vorhandenen Befehl `mode` (berechnet den Wert intern neu und behält Heizen/Kühlen und den Zustand der Klimaanlage bei) – kein neuer Backend-Befehl nötig. In Zonen ohne Fußbodenheizung ist die Entität „nicht verfügbar“.

### Removed
- `const.HVAC_MODES` (enthielt einen Modus `"auto"` ohne echte Unterstützung im System und ohne Nutzer im Code – toter Code).

### Geprüft (ohne Codeänderung)
- Timer und Zeitprogramme werden nicht umgesetzt. Das Backend bietet keinen bestätigten Weg, sie zu setzen (nur zu lesen), und das Format zu raten, wäre nicht verantwortbar. Wird wieder aufgegriffen, falls echte Belege auftauchen (z. B. ein Mitschnitt einer Anlage mit eingerichtetem Timer).

## [2.9.0] - 2026-07-21

### Added
- Zusammensetzen von MQTT-Frames (E2): Der Client geht nicht mehr davon aus, dass jede WebSocket-Nachricht genau ein vollständiges MQTT-Paket enthält. Er sammelt die empfangenen Bytes, verarbeitet so viele vollständige Pakete wie vorhanden (mehrere PUBLISH/CONNACK/SUBACK in einer WS-Nachricht) und setzt ein über zwei WS-Nachrichten verteiltes Paket korrekt zusammen. Eine Obergrenze verwirft den Puffer, wenn er ohne vollständiges Paket wächst oder eine unplausible Länge angegeben ist.
- Prüfung der Nutzdaten (E4): `status_parser.py` weist eine Nachricht, die nicht einmal ein Objekt ist, ausdrücklich zurück (mit Log), statt ein sinnloses leeres Ereignis zu erzeugen; fehlendes `ctl`, ein unerwartet geformtes Feld `t` und Zonen ohne Kennung (`rf`) werden jetzt geloggt (bisher still). Unbekannte zusätzliche Felder werden bewusst toleriert.
- E6 (Umstieg auf `paho-mqtt` prüfen): Entscheidung gegen den Umstieg dokumentiert – siehe `docs/development-roadmap.md`.

### Fixed
- Ein Feld `t` (Zonen) mit unerwarteter Form, das keine Liste war (z. B. eine Zahl), löste einen nicht abgefangenen `TypeError` aus und verwarf die gesamte MQTT-Nachricht mit einer allgemeinen Fehlermeldung; jetzt wird es sicher übergangen und gezielt geloggt.

## [2.8.0] - 2026-07-21

### Added
- Sensor „MySair Conexión MQTT“ (einer je eingerichtetem Konto): Zustand der MQTT-Verbindung (online/offline), Zeitpunkt der letzten empfangenen Aktualisierung sowie Kennzahlen zu Wiederverbindungen und zur Auswertung (Wiederverbindungsversuche, Wiederverbindungen insgesamt, nach strenger bzw. heuristischer Methode dekodierte Nachrichten, nicht auswertbare Nachrichten, Code des letzten Verbindungsabbaus) (D3/D4).
- `diagnostics.py` enthält dieselben Kennzahlen.

### Changed
- Schwärzen in Logs (D2): Fehlertexte von HTTP-Antworten werden vor dem Loggen gekürzt (damit ein unerwarteter Text des Backends nicht mehr preisgibt als nötig), und die MQTT-`clientId` maskiert den eingebetteten AWS-`access_key`. Mehrere häufige Logs (empfangene MQTT-Nachricht, gesendeter Befehl, Befehlsbestätigung) gehen von `INFO` auf `DEBUG`; Lebenszyklus-Ereignisse (Anmeldung, MQTT-Verbindung/-Trennung, Start) bleiben auf `INFO`.

## [2.7.1] - 2026-07-21

### Changed
- Interner Umbau: Ein einziger `MySairCoordinator` je Config Entry hört jetzt einmal auf `mysair_update` und verteilt jede Zone einzeln (`homeassistant.helpers.dispatcher`) an die passende Entität, statt dass die sechs Entitäten je Zone (climate/sensor/switch) dieselbe Filterung nach Topic/Installation/Zone auf dem Bus wiederholen. Keine beobachtbare Verhaltensänderung.

## [2.7.0] - 2026-07-20

### Added
- Übersetzung des Einrichtungsdialogs (Anmeldung, erneute Anmeldung, Fehler) und des Dienstes `mysair.stop_installation`: Englisch als Standard (`strings.json`) und Spanisch (`translations/es.json`). Die Namen der Entitäten (climate/sensor/switch) blieben damals unverändert spanisch.

## [2.6.0] - 2026-07-20

### Added
- Dienst `mysair.stop_installation`: stoppt eine ganze Installation (alle Zonen) mit einem einzigen Befehl, statt jede Zone einzeln auszuschalten.
- `diagnostics.py`: herunterladbare Diagnose aus der Oberfläche von Home Assistant mit dem Zustand der Integration (Installationen, Geräte, Zustand des MQTT-Clients), mit geschwärzten Zugangsdaten und Token.
- Exponentieller Backoff mit Jitter bei der MQTT-Wiederverbindung (bisher feste 10 s); geplante Wiederverbindungen zur Erneuerung der Zugangsdaten warten weiterhin nicht.

## [2.5.0] - 2026-07-20

### Added
- Wird ein Befehl (Temperatur, Modus, Lüfter, Ein/Aus) nicht rechtzeitig per MQTT bestätigt, wird der optimistische Zustand auf den letzten bekannten Wert zurückgesetzt, statt etwas anzuzeigen, das womöglich nie angewendet wurde.
- Standardkonformer Parser für MQTT-Frames (Topic aus dem echten Header statt aus Textheuristiken), mit der bisherigen Heuristik als Rückfall, falls ein Frame nicht die erwartete Form hat.

## [2.4.2] - 2026-07-20

### Fixed
- Ursache der regelmäßigen MQTT-Abbrüche: Die Sitzung wurde nie vor dem Ablauf der AWS-Zugangsdaten erneuert, deshalb trennte AWS IoT die aktive Verbindung bei jedem Ablauf (regelmäßiges Muster). Jetzt wird die Verbindung vorher erneuert, wie es auch die offizielle App macht.
- Der Hinweis „Keine MQTT-Bestätigung“ gibt jetzt an, ob MQTT in dem Moment getrennt war oder die Quittung trotz aktiver Verbindung ausblieb.

## [2.4.1] - 2026-07-20

### Fixed
- Die Befehlsbestätigung über `feedback` (2.2.0) funktionierte nie: Das Topic wurde falsch erkannt, wenn der Broker es ohne umgebende Klammern sendete (der tatsächliche Fall bei `feedback`), sodass der Zweig für die Quittung nie ausgeführt wurde.
- Der Feuchtesensor (2.1.0) zeigte nie einen Wert, weil das Feld `hm` gelesen wurde; umgestellt auf `hum`. (Diese Annahme erwies sich später als falsch, siehe [Unreleased].)

Beide Fehler wurden anhand echter Log-Ausgaben nach einer Temperaturänderung gefunden.

## [2.4.0] - 2026-07-20

### Added
- Steuerung der Lüfterstufe in `climate` (`fan_mode`/`fan_modes`): manuell 1/2/3 oder Automatik, nur in Zonen, die das unterstützen.

## [2.3.0] - 2026-07-20

### Added
- Alle Entitäten werden „nicht verfügbar“, wenn sie mehr als 6 Minuten keinen MQTT-Status erhalten (bisher zeigten sie unbegrenzt den letzten Wert oder vor dem ersten Status Standardwerte).
- `should_poll = False` ausdrücklich in allen Entitäten (reines Push, keine Abfrage durch HA).

### Fixed
- `datetime.utcnow()` (abgekündigt) → `datetime.now(timezone.utc)` in der AWS-SigV4-Signatur.
- Allgemeines `FlowResult` → `ConfigFlowResult` im Einrichtungsdialog (richtiger Typ für aktuelles HA).

## [2.2.0] - 2026-07-20

### Added
- Befehlsbestätigung über das MQTT-Topic `.../usr/{aws_mqtt_user}/feedback`: Jeder von `climate`/`switch` gesendete Befehl wird über seine `orderId` zugeordnet; im Log steht, ob eine Bestätigung kam oder nach 5 s ausblieb. Der optimistische Zustand wird noch nicht zurückgesetzt (die echte Form der Nachricht war noch zu prüfen).

## [2.1.0] - 2026-07-20

### Added
- Feuchtesensor je Zone (`sensor.<zona>_humedad`).
- `climate.hvac_modes` spiegelt die tatsächliche Verfügbarkeit von Heizen/Kühlen jeder Zone (Felder `c`/`f`), statt immer alle drei Modi anzubieten.
- `climate.min_temp`/`max_temp` übernehmen die echten Grenzwerte der Zone (`tmm`/`tmx`) statt fester 10/30.
- Tests mit echter Home-Assistant-Testumgebung über Docker (Einrichtungsdialog, Setup/Unload, Entitäten) und CI mit GitHub Actions (`pytest` + `hassfest`).

### Fixed
- `manifest.json`: Schlüssel `homeassistant` entfernt (für Custom-Integrationen nicht zulässig) und Schlüssel sortiert – von `hassfest` in der CI gefunden.

## [2.0.0] - 2026-07-20

### ⚠️ Inkompatibel mit früheren Installationen (manuell, nicht HACS)

- **Umstellung auf `custom_components/mysair/`**: Das bisherige flache Layout (einzelne Dateien direkt in `custom_components/`) funktioniert nicht mehr. Die alte Installation löschen und den neuen Ordner vollständig kopieren (siehe README, Installation).
- **Korrigierte Kodierung des Zustands**: `e` = Ein/Aus/Standby, `m` = Modus nach Parität (bisher wurde `e` als Modus gelesen). Bestehende Installationen zeigen Modus und Ein/Aus ab dieser Version korrekt an – das kann von der bisherigen Anzeige abweichen. Siehe `docs/protocol-findings.md`.
- **Die Config Entry speichert kein Passwort im Klartext mehr**: Beim ersten Start nach dem Update wird es aus bestehenden Einträgen entfernt. Ist das gespeicherte `refresh_token` ungültig, fordert Home Assistant in der Oberfläche zur erneuten Anmeldung auf.

### Added
- Erneute Anmeldung (`async_step_reauth`) und Verwendung von `ConfigEntryAuthFailed`/`ConfigEntryNotReady` im Setup.
- `unique_id` der Config Entry: verhindert, dass dasselbe Konto zweimal hinzugefügt wird.
- Eindeutige MQTT-`client_id` je Verbindung (verdrängt die offizielle App auf dem Smartphone nicht mehr).
- Vorausschauende Erneuerung der AWS-Zugangsdaten (`aws_expires_at`) und dynamisches MQTT-Topic aus `aws_base_topic`.
- Testsuite P0/P1 ohne Abhängigkeit von Home Assistant (Statusparser, MQTT-Builder, SigV4-Signatur, HTTP-Client).
- Paket für HACS (`hacs.json`), `LICENSE` (MIT) und vollständige README.

### Changed
- `async_unload_entry` auf Modulebene, mit sauberem Schließen von MQTT und Beenden der periodischen Abfrage (alle 120 s).
- `switch.turn_on` erzwingt nicht mehr den Kühlmodus, sondern schaltet mit dem zuletzt bekannten Modus ein.
- `requirements` auf `requests` und `websocket-client` reduziert (ungenutzte `aiohttp`, `paho-mqtt`, `boto3` entfernt).
- `login()`/`refresh_tokens()` werfen typisierte Ausnahmen (`MySairAuthError`/`MySairConnectionError`) statt einer allgemeinen Ausnahme.

### Removed
- `select.py` (toter Code, von der Integration nie geladen, fehlerhaft).
- `quality_scale: silver` im Manifest: durch den damaligen Stand nicht gedeckt (keine Tests mit HA-Testumgebung, keine Übersetzungen, kein Markensymbol). Bis zu einem belegbaren Stand zurückgezogen.

### Security
- Das Log der MQTT-Verbindung gibt die signierte AWS-URL nicht mehr aus (nur Host und `client_id`).
- Passwort nicht mehr in der Config Entry (siehe oben).

## [1.0.0] – erste Version

Erste funktionsfähige Version des ursprünglichen Autors vor der Stabilisierung: Anmeldung, MQTT-Client und einfache Entitäten `climate`/`sensor`/`switch` im flachen Layout (ohne `custom_components/`).

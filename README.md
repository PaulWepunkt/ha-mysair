# MySair für Home Assistant

**Inoffizielle** Home-Assistant-Integration für das Klima-Zonensystem **MySair** (Klimaanlage und/oder Fußbodenheizung bzw. -kühlung, je Zone kombinierbar). Sie beruht auf der Analyse der offiziellen Web-App (HTTP-API und AWS-IoT-MQTT) und steht in keiner Verbindung zu MySair.

> ⚠️ **Inoffiziell.** Der Hersteller dokumentiert das Protokoll nicht öffentlich; ändert MySair sein Backend, kann die Integration ohne Vorwarnung ausfallen. Nutzung auf eigenes Risiko. Offene Fragen zum Protokoll stehen in [`docs/known-unknowns.md`](docs/known-unknowns.md).

## Herkunft

Dieses Repository ist ein Fork von [jclgranados/homeassistant-mysair](https://github.com/jclgranados/homeassistant-mysair). Die Protokollanalyse und der Großteil der Integration stammen von dort – vielen Dank an jclgranados. Der Fork ergänzt unter anderem korrekte Luftfeuchtewerte, eine deutsche Bedienoberfläche und deutsche Dokumentation. Die Änderungen stehen im [`CHANGELOG.md`](CHANGELOG.md).

## Funktionsumfang

- **Erkennt** den Aufbau des Kontos: `Location → Installation → Device (Zone)`.
- Empfängt den **Zustand in Echtzeit** (Temperatur, Soll-Temperatur, Modus, Ein/Aus, Luftfeuchte) per MQTT über WebSocket (AWS IoT); zusätzlich fragt sie den Zustand alle 2 Minuten per HTTP ab.
- **Steuert** jede Zone (Ein/Aus, Heizen/Kühlen, Soll-Temperatur, Lüfterstufe und – falls vorhanden – Fußbodenheizung) aus Home Assistant heraus.

## Entitäten je Zone

Die Bedienoberfläche ist auf Deutsch, Englisch und Spanisch übersetzt; angezeigt wird die in Home Assistant eingestellte Sprache. Jede Zone wird als eigenes Gerät angelegt, dessen Name der Zonenname aus dem MySair-Konto ist. Die Entity-IDs bildet Home Assistant bei der Einrichtung aus Gerätename und Entitätsname in der eingestellten Sprache. Die Spalte „Entity-ID“ zeigt die Form bei deutscher Oberfläche; bei englischer Oberfläche lauten die Endungen `_power`, `_floor_heating`, `_temperature`, `_target_temperature`, `_mode` und `_humidity`.

| Entität | Entity-ID (deutsche Oberfläche) | Beschreibung |
|---|---|---|
| Thermostat | `climate.<zone>` | Ein/Aus, Heizen/Kühlen (je nach Fähigkeiten der Zone), Soll-Temperatur mit den echten Grenzwerten der Zone, Lüfterstufe (1–3 oder Automatik, sofern die Zone das unterstützt) |
| Ein/Aus | `switch.<zone>_ein_aus` | Schaltet die Zone ein oder aus; beim Einschalten bleibt der zuletzt genutzte Modus erhalten |
| Fußbodenheizung | `switch.<zone>_fussbodenheizung` | Fußbodenheizung ein/aus, kombinierbar mit der Klimaanlage derselben Zone. In Zonen ohne Fußbodenheizung „nicht verfügbar“ |
| Temperatur | `sensor.<zone>_temperatur` | Aktuelle Raumtemperatur |
| Soll-Temperatur | `sensor.<zone>_soll_temperatur` | Eingestellte Zieltemperatur |
| Modus | `sensor.<zone>_modus` | Aktueller Modus (`off`/`heat`/`cool`/`on`, angezeigt als Aus/Heizen/Kühlen/Ein). Das Attribut `medium` (`air`/`floor`/`air_and_floor`, angezeigt als Luft/Fußboden/Luft und Fußboden) gibt an, welches Medium aktiv ist; es bleibt auch bei ausgeschalteter Zone erhalten |
| Luftfeuchtigkeit | `sensor.<zone>_luftfeuchtigkeit` | Relative Luftfeuchte; „unbekannt“, wenn das Thermostat keinen Feuchtesensor hat |

## Weitere Entitäten

| Entität | Entity-ID (deutsche Oberfläche) | Beschreibung |
|---|---|---|
| Cloud-Verbindung | `sensor.mysair_cloud_verbindung` | Eine je eingerichtetem Konto, als Diagnose-Entität: Zustand der MQTT-Verbindung (Verbunden/Getrennt), Zeitpunkt der letzten empfangenen Aktualisierung sowie Kennzahlen zu Wiederverbindungen und zur Nachrichtenauswertung (zur Fehlersuche) |

Bestehende Installationen behalten ihre bisherigen Entity-IDs. Wer die neuen, sprachabhängigen IDs möchte, entfernt die Integration und richtet sie neu ein; Dashboards und Automationen müssen danach angepasst werden.

## Installation

### Variante A – HACS (benutzerdefiniertes Repository)

1. HACS → Menü (⋮) → **Benutzerdefinierte Repositories**.
2. `https://github.com/PaulWepunkt/ha-mysair` mit dem Typ **Integration** hinzufügen.
3. In HACS nach „MySair“ suchen und installieren.
4. Home Assistant neu starten.

### Variante B – manuell

1. Den Ordner `custom_components/mysair/` dieses Repositorys nach `<config>/custom_components/mysair/` der Home-Assistant-Installation kopieren.
2. Home Assistant vollständig neu starten (ein Neuladen der Integration genügt nicht).

## Einrichtung

Einstellungen → Geräte & Dienste → Integration hinzufügen → **MySair** → E-Mail-Adresse und Passwort des MySair-Kontos eingeben.

- Das Passwort wird **nicht gespeichert**: Es dient einmalig dazu, ein `refresh_token` zu erhalten. Nur dieses wird gespeichert und erneuert die Sitzung bei jedem Start (siehe [`docs/security-and-privacy.md`](docs/security-and-privacy.md)).
- Wird die Sitzung ungültig (z. B. weil das `refresh_token` abgelaufen ist), fordert Home Assistant über die Integration eine erneute Anmeldung an; die bestehende Konfiguration bleibt dabei erhalten.

## Bekannte Einschränkungen

- **Nach einem Start oder Neuladen dauert es einige Sekunden, bis echte Werte erscheinen**: Bis zum ersten Status per MQTT sind die Entitäten „nicht verfügbar“ (ebenso, wenn die Verbindung länger als 6 Minuten unterbrochen ist), statt Standardwerte als Echtzeitdaten auszugeben.
- **Nur die erste `Location`** des Kontos: Bei mehreren Standorten werden nur die Installationen des ersten geladen (bewusste Abgrenzung, siehe `docs/known-unknowns.md` #15).
- **Keine Zeitprogramme oder Timer**: Das Backend bietet keinen bestätigten Weg, sie zu setzen (nur zu lesen); das Format zu raten, wäre nicht verantwortbar (bewusste Abgrenzung, siehe `docs/known-unknowns.md` #27).
- **Kein automatischer Heiz-/Kühlmodus**: Das ist keine Lücke der Integration – das Protokoll selbst kennt keinen automatischen Wechsel zwischen Heizen und Kühlen (siehe `docs/protocol-findings.md` §4). Die einzige echte Automatik des Systems ist die Lüfterstufe, und die ist umgesetzt.
- **Heizen/Kühlen wird je Zone gesetzt**: Arbeitet die Außeneinheit einer Anlage nur in einer Richtung für alle Zonen, verhindert die Integration widersprüchliche Einstellungen derzeit nicht. In diesem Fall die Richtung für alle Zonen einheitlich wählen.
- Erfordert Home Assistant **≥ 2026.8.0**.

## Technische Dokumentation

Architektur, HTTP-/MQTT-Protokoll, Domänenmodell, Teststrategie und Roadmap stehen im Ordner [`docs/`](docs/) und in [`CLAUDE.md`](CLAUDE.md).

## Entwicklung und Beiträge

Die Tests P0/P1 (Parser, MQTT-Builder, SigV4-Signatur, HTTP-Client) laufen ohne Home Assistant:

```bash
python -m venv .venv-test && source .venv-test/bin/activate
pip install -r requirements-test.txt
pytest
```

Die Tests P2 (Einrichtungsdialog, Setup/Unload, Entitäten) nutzen eine echte Home-Assistant-Testumgebung über Docker – auf dem eigenen Rechner wird nichts installiert:

```bash
docker compose run --rm test-ha
```

Lint und Formatierung mit `ruff` (Konfiguration in `pyproject.toml`, Standardregeln):

```bash
pip install -r requirements-lint.txt
ruff check custom_components/mysair tests
ruff format custom_components/mysair tests
```

Issues und Pull Requests sind willkommen. Vor Änderungen am Protokoll oder an der Anmeldung bitte `CLAUDE.md` lesen.

## Änderungsprotokoll

Siehe [`CHANGELOG.md`](CHANGELOG.md).

## Lizenz

[MIT](LICENSE)

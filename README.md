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
| Thermostat | `climate.<zone>` | Ein/Aus, Heizen/Kühlen (je nach Fähigkeiten der Zone und Richtung der Anlage, siehe unten), Soll-Temperatur mit den echten Grenzwerten der Zone, aktuelle Luftfeuchte, Lüfter („Standard“, bei dem die Anlage die Stufe selbst wählt, Niedrig/Mittel/Hoch oder Automatik, sofern die Zone das unterstützt). Scheitert ein Befehl, erscheint eine Fehlermeldung, und die Anzeige bleibt beim bisherigen Wert. Die Attribute zeigen die Fähigkeiten der Zone mit dem MySair-Feld in Klammern: Heizen möglich (c), Kühlen möglich (f), Fußboden vorhanden (s), Lüfter einstellbar (v), Timer möglich (tz), Programme möglich (hp); dazu die Rohwerte Ein/Aus (e: 0 aus, 1 an, 2 Ziel erreicht), Modus (m: 0–5, gerade = Heizen) und Lüfter (vv: 0 Standard, 1–3, 4 Automatik) |
| Ein/Aus | `switch.<zone>_ein_aus` | Schaltet die Zone ein oder aus. Eingeschaltet wird in der Richtung der Anlage mit der aktuellen Soll-Temperatur und dem Medium aus „Heizen mit“/„Kühlen mit“ |
| Heizen mit / Kühlen mit | `select.<zone>_heizen_mit`, `select.<zone>_kuhlen_mit` | Einstellungs-Entitäten: womit die Zone beim Einschalten über Home Assistant heizt bzw. kühlt – „Standard“ (wie die MySair-App: Klimaanlage an, Fußboden wie zuletzt), „Nur Luft“, „Nur Fußboden“ oder „Luft und Fußboden“. Angeboten wird nur, was die Optionen erlauben und die Zone kann. Beim Umstellen wird nichts gesendet; die Wahl bleibt über einen Neustart erhalten |
| Fußbodenheizung | `switch.<zone>_fussbodenheizung` | Fußboden ein/aus, kombinierbar mit der Klimaanlage derselben Zone; „an“ nur, solange die Zone läuft. Bleibt weder Klimaanlage noch Fußboden an, wird die Zone ausgeschaltet. In Zonen ohne Fußboden „nicht verfügbar“ |
| Temperatur | `sensor.<zone>_temperatur` | Aktuelle Raumtemperatur |
| Soll-Temperatur | `sensor.<zone>_soll_temperatur` | Eingestellte Zieltemperatur |
| Modus | `sensor.<zone>_modus` | Aktueller Modus (`off`/`heat`/`cool`/`on`, angezeigt als Aus/Heizen/Kühlen/Ein). Das Attribut `medium` (`air`/`floor`/`air_and_floor`, angezeigt als Luft/Fußboden/Luft und Fußboden) gibt an, welches Medium aktiv ist; es bleibt auch bei ausgeschalteter Zone erhalten |
| Luftfeuchtigkeit | `sensor.<zone>_luftfeuchtigkeit` | Relative Luftfeuchte; „unbekannt“, wenn das Thermostat keinen Feuchtesensor hat |

## Geräte und weitere Entitäten

Die Geräte bilden einen Baum: **Konto** (MySair) → **Installation** (die MySair-Steuerung, mit ihrer Software-Version) → **Zonen**. In Home Assistant ist das auf der Geräteseite unter „Verbunden über“ zu sehen.

| Entität | Gerät | Entity-ID (deutsche Oberfläche) | Beschreibung |
|---|---|---|---|
| Cloud-Verbindung | Konto | `sensor.mysair_cloud_verbindung` | Diagnose-Entität: Zustand der MQTT-Verbindung (Verbunden/Getrennt), Zeitpunkt der letzten empfangenen Aktualisierung sowie Kennzahlen zu Wiederverbindungen und zur Nachrichtenauswertung (zur Fehlersuche) |
| Betriebsart | Installation | `select.<anlage>_betriebsart` | Heizen oder Kühlen – die Richtung der ganzen Anlage, auch wenn alle Zonen aus sind. Umstellbar nur, solange alle Zonen aus sind; wie in der App wird dabei noch nichts gesendet, die nächste eingeschaltete Zone startet in der gewählten Richtung |
| MySair schaltet Wärmepumpe | Installation | `switch.<anlage>_mysair_schaltet_warmepumpe` | Einstellungs-Entität, ab Werk an (MySair schaltet die Wärmepumpe zwischen Heizen und Kühlen um, wie in der App). **Ausschalten**, wenn Heizen/Kühlen an der Wärmepumpe selbst eingestellt wird: Dann beschreibt „Betriebsart“ die Wärmepumpe. Sie lässt sich dann jederzeit umstellen (es wird nichts gesendet), bleibt über einen Neustart erhalten, und Home Assistant schaltet Zonen nur noch in dieser Richtung ein |
| Abgleich Wärmepumpe | Installation | `binary_sensor.<anlage>_abgleich_warmepumpe` | „Problem“, wenn Zonen in der anderen Richtung laufen als bei „Betriebsart“ für die Wärmepumpe eingestellt, z. B. weil sie am Thermostat oder in der App eingeschaltet wurden. Solange „MySair schaltet Wärmepumpe“ an ist, immer „OK“ |
| Anlage stoppen | Installation | `button.<anlage>_anlage_stoppen` | Schaltet alle Zonen der Anlage mit einem Befehl aus (wie der Dienst `mysair.stop_installation`) |
| In Betrieb | Installation | `binary_sensor.<anlage>_in_betrieb` | An, solange mindestens eine Zone läuft |
| Hauptzone | Installation | `sensor.<anlage>_hauptzone` | Diagnose-Entität: Name der Hauptzone (an sie geht „Anlage stoppen“) |
| Heiz-/Kühlkonflikt | Installation | `binary_sensor.<anlage>_heiz_kuhlkonflikt` | Diagnose-Entität: „Problem“, wenn laufende Zonen unterschiedliche Richtungen melden. Die Integration und die App verhindern das; der Sensor erkennt es, falls es auf anderem Weg doch entsteht |

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

### Optionen

Einstellungen → Geräte & Dienste → MySair → **Konfigurieren**:

Hier wird festgelegt, welche Medien Home Assistant zum Heizen und zum Kühlen überhaupt verwenden darf. Jede Zone wählt innerhalb dieser Grenzen mit „Heizen mit“ und „Kühlen mit“ ihr Medium. Die Einstellungen wirken sofort, ohne Neuladen.

| Option | Standard | Bedeutung |
|---|---|---|
| Heizen mit Luft erlauben | an | Die Luftgeräte dürfen heizen. Ausschalten, wenn die Anlage nur über den Fußboden heizen soll |
| Fußbodenheizung erlauben | an | Der Fußboden darf heizen |
| Kühlen mit Luft erlauben | an | Die Luftgeräte dürfen kühlen |
| Fußbodenkühlung erlauben | aus | Nur einschalten, wenn die Anlage für Fußbodenkühlung ausgelegt und gegen Kondenswasser (Taupunkt) geschützt ist |

Je Richtung muss mindestens ein Medium erlaubt sein. Ist ein Medium nicht erlaubt, verwendet Home Assistant es beim Einschalten nicht, und der Schalter „Fußbodenheizung“ meldet einen Fehler. Eine Zone, die in einer Richtung kein erlaubtes Medium hat (z. B. ohne Fußboden, wenn Heizen mit Luft nicht erlaubt ist), bietet diese Richtung nicht an. Die Optionen gelten nur für Bedienung über Home Assistant, nicht für Wandthermostate oder die MySair-App, und sie ändern nichts an Zonen, die bereits laufen.

> ⚠️ **Fußbodenkühlung am Thermostat:** Laut Handbuch der Thermostate wechselt ein Raum, der am Thermostat mit Fußbodenheizung eingeschaltet wird, während die Anlage kühlt, automatisch auf Fußbodenkühlung. Verhindern lässt sich das nur in der Anlage selbst (Installateur-Menü der Thermostate, „Suelo F“; dort auch die maximale Luftfeuchte unter „Límites“). Die Option in Home Assistant ersetzt diese Einstellung nicht.

## Heizen und Kühlen

Eine MySair-Anlage hat **eine** Richtung (Heizen oder Kühlen) für alle Zonen. Die Integration übernimmt die Regeln der offiziellen App:

- **Läuft mindestens eine Zone**, bieten alle Thermostate nur „Aus“ und die aktuelle Richtung an.
- **Sind alle Zonen aus**, kann eine Zone in beiden Richtungen eingeschaltet werden; damit wechselt die Richtung der ganzen Anlage.
- Um die Richtung zu wechseln, also zuerst alle Zonen ausschalten (z. B. mit dem Knopf „Anlage stoppen“), dann die „Betriebsart“ umstellen oder direkt eine Zone in der neuen Richtung einschalten.
- Änderungen an den Thermostaten in den Räumen oder in der MySair-App übernimmt die Integration aus den Statusmeldungen der Steuerung; wer die Änderung gemacht hat, spielt keine Rolle. Kommt eine Meldung nicht sofort, fragt die Integration den Zustand spätestens alle 2 Minuten ab.
- Eine umgestellte „Betriebsart“ wirkt nur, wenn die nächste Zone über Home Assistant eingeschaltet wird. Wird sie stattdessen am Thermostat oder in der App eingeschaltet, gilt die Richtung, mit der die Anlage dann tatsächlich startet; die Auswahl zeigt danach diese Richtung.
- **Wärmepumpe nicht von MySair gesteuert?** Wenn Heizen/Kühlen an der Wärmepumpe selbst umgestellt werden muss, „MySair schaltet Wärmepumpe“ ausschalten und die „Betriebsart“ bei jedem Saisonwechsel passend zur Wärmepumpe stellen. Home Assistant verhindert dann falsches Einschalten über Home Assistant und meldet über „Abgleich Wärmepumpe“, wenn am Thermostat oder in der App in der anderen Richtung eingeschaltet wurde.
- Der Dienst **„Anlage stoppen“** (`mysair.stop_installation`) wird wie in der App an die Hauptzone der Anlage gesendet.

## Bekannte Einschränkungen

- **Nach einem Start oder Neuladen dauert es einige Sekunden, bis echte Werte erscheinen**: Bis zum ersten Status per MQTT sind die Entitäten „nicht verfügbar“ (ebenso, wenn die Verbindung länger als 6 Minuten unterbrochen ist), statt Standardwerte als Echtzeitdaten auszugeben.
- **Nur die erste `Location`** des Kontos: Bei mehreren Standorten werden nur die Installationen des ersten geladen (bewusste Abgrenzung, siehe `docs/known-unknowns.md` #15).
- **Keine Zeitprogramme oder Timer von MySair**: bewusst nicht umgesetzt, weil sich Zeitsteuerung in Home Assistant mit Automationen und Zeitplänen abbilden lässt. Ob eine Zone sie unterstützt, zeigen die Attribute des Thermostats.
- **Kein automatischer Heiz-/Kühlmodus**: Das ist keine Lücke der Integration – das Protokoll selbst kennt keinen automatischen Wechsel zwischen Heizen und Kühlen (siehe `docs/protocol-findings.md` §4). Die einzige echte Automatik des Systems ist die Lüfterstufe, und die ist umgesetzt.
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

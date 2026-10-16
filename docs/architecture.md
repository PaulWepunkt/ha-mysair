# Architektur – MySair-Integration für Home Assistant

> Gewissheitsstufen in diesem Dokument:
> **Bestätigt** (im Code nachgelesen) · **Abgeleitet** (aus dem Ablauf geschlossen) · **Hypothese** (plausibel, ohne direkten Beleg) · **Unbekannt** (nur mit echtem Gerät zu klären).
> Code-Verweise nennen Datei und Funktion statt Zeilennummern, da sich Zeilen bei jeder Änderung verschieben.

---

## 1. Systemüberblick

MySair ist ein cloudgesteuertes **Klima-Zonensystem** (Klimaanlage und/oder Fußbodenheizung bzw. -kühlung, je Zone kombinierbar – siehe Feld `m` in `docs/protocol-findings.md` §4). Diese Integration (Custom Component, Domain `mysair`) ermöglicht Home Assistant:

- die Anmeldung an der HTTP-API von MySair (`https://api.mysair.es/v1`) – **Bestätigt** (`api.py`, `MySairAPI`);
- das Erkennen des Aufbaus: *Standorte → Installationen → Geräte (Thermostate/Zonen)* – **Bestätigt** (`__init__.py`, `async_setup_entry`);
- den Empfang des Zustands in Echtzeit per **MQTT über WebSocket** von AWS IoT – **Bestätigt** (`mqtt_handler.py`);
- das Senden von Befehlen (Ein/Aus, Temperatur, Modus, Lüfterstufe) per HTTP `POST /send/instruction` – **Bestätigt** (`api.py`, `send_instruction`, `send_zone_command`).

Deklarierte `iot_class`: `cloud_push` (`manifest.json`). In der Praxis ist es ein *Cloud-Push mit zusätzlicher Abfrage*: Der Zustand **kommt** per MQTT (Push), wird aber zusätzlich alle 120 s per HTTP-`POST` **angestoßen** (`refresh_status_periodic` in `__init__.py`).

### Zentrale Architekturtatsache (Bestätigt)
Befehle werden **nicht per MQTT veröffentlicht**. Der MQTT-Client sendet nur `CONNECT` und `SUBSCRIBE`, nie `PUBLISH`. Jeder Befehl geht per HTTP an `/send/instruction`; das MySair-Backend leitet ihn an die Geräte weiter und liefert den Zustand per MQTT zurück. Die MQTT-Verbindung ist **reiner Empfang**.

---

## 2. Aufbau des Repositorys

```
ha-mysair/
├── custom_components/mysair/     # Paket der Integration (Standardlayout HA/HACS)
│   ├── __init__.py               # Setup/Unload, MQTT-Callback, periodische Abfrage, Dienst
│   ├── api.py                    # MySairAPI: synchrones HTTP (requests) + AWS-SigV4-Signatur
│   ├── status_parser.py          # Reine Parser für 'status' und 'feedback' (ohne HA-Abhängigkeit)
│   ├── mqtt_handler.py           # MySairMQTTClient: MQTT von Hand über WebSocket
│   ├── coordinator.py            # MySairCoordinator: verteilt Zustände je Zone
│   ├── command_feedback.py       # Bestätigung von Befehlen über das feedback-Topic
│   ├── availability.py           # Verfügbarkeit nach Aktualität der MQTT-Daten
│   ├── device.py                 # Gemeinsame Geräteinformationen je Zone/Konto
│   ├── climate.py                # MySairThermostat (ClimateEntity)
│   ├── sensor.py                 # 4 Sensoren je Zone + Sensor „Cloud-Verbindung“
│   ├── switch.py                 # Ein/Aus und Fußbodenheizung je Zone
│   ├── config_flow.py            # Einrichtungsdialog (E-Mail + Passwort), Reauth
│   ├── diagnostics.py            # Diagnose-Download mit geschwärzten Zugangsdaten
│   ├── const.py                  # Konstanten
│   ├── services.yaml             # Schema des Dienstes mysair.stop_installation
│   ├── strings.json, translations/, icons.json, brand/
│   └── manifest.json
├── tests/                        # Tests ohne HA (P0/P1) und mit HA-Testumgebung (P2/P3)
├── docs/                         # diese Dokumentation
├── Dockerfile.test · docker-compose.yml · pytest.ini · pyproject.toml
└── README.md · CLAUDE.md · CHANGELOG.md · LICENSE · hacs.json
```

---

## 3. Komponenten und Zuständigkeiten

| Komponente | Datei | Aufgabe | Thread/Loop |
|---|---|---|---|
| `async_setup_entry` | `__init__.py` | Steuert Sitzungserneuerung → Erkennung → MQTT → Koordinator → Plattformen → periodische Abfrage | Event-Loop + Executor |
| `mqtt_message_callback` | `__init__.py` | Wertet `status` und `feedback` aus, feuert `mysair_update` bzw. `mysair_feedback` | MQTT-Thread → `call_soon_threadsafe` |
| `refresh_status_periodic` | `__init__.py` | Fordert alle 120 s per HTTP den Zustand jeder Installation an | Hintergrund-Task der Config Entry |
| `MySairAPI` | `api.py` | Anmeldung, Token-Erneuerung, AWS-Zugangsdaten, Erkennung, Befehle, SigV4-Signatur | Executor (blockierend) |
| `MySairMQTTClient` | `mqtt_handler.py` | WSS-Verbindung, CONNECT/SUBSCRIBE von Hand, Wiederverbindung mit Backoff, vorausschauende Erneuerung der Zugangsdaten | eigener Daemon-Thread |
| `MySairCoordinator` | `coordinator.py` | Hört **einmal** je Config Entry auf `mysair_update`, filtert nach eigener Installation und verteilt jede Zone einzeln über `homeassistant.helpers.dispatcher` | Event-Loop |
| Entitäten | `climate.py`, `sensor.py`, `switch.py` | Abonnieren das Dispatcher-Signal ihrer Zone (`signal_zone_update`) und aktualisieren ihren Zustand | Event-Loop |

### Abhängigkeiten zwischen Modulen (Bestätigt)

```
config_flow.py ─► api.py (Anmeldung)
__init__.py ─► api.py, mqtt_handler.py, status_parser.py, coordinator.py, const.py
mqtt_handler.py ─► api.py (AWS-Zugangsdaten, aws_sign_url)
climate/sensor/switch.py ─► coordinator.py (Signal), device.py, availability.py, command_feedback.py
```

Die Entitäten kennen `MySairMQTTClient` **nicht** direkt. Der Weg ist: MQTT-Thread → Event-Bus (`mysair_update`) → `MySairCoordinator` (einziger Abonnent je Config Entry) → Dispatcher-Signal je Zone → Entität. **Bestätigt.**

---

## 4. Datenherkunft: HTTP oder MQTT

| Daten | Herkunft | Beleg |
|---|---|---|
| Sitzungstoken | HTTP `PUT /user/refreshtokens` (bei Einrichtung/Reauth: `POST /user/login`) | `api.py`: `refresh_tokens`, `login` |
| AWS-IoT-Zugangsdaten | HTTP `PUT /user/refreshawscredentials` | `api.py`: `refresh_aws_credentials` |
| Standorte / Installationen / Geräte | HTTP `GET /locations`, `/installations`, `/devices` | `api.py`: `get_locations`, `get_installations`, `get_devices` |
| Zustand der Zonen (Temperaturen, Grenzwerte, Modus, Feuchte, Lüfter, Fähigkeiten) | **MQTT**-Topic `.../status` | `status_parser.parse_status_payload` |
| Befehlsbestätigung | **MQTT**-Topic `.../usr/{aws_mqtt_user}/feedback` | `status_parser.parse_feedback_payload` |
| Befehle (temp, mode, power, fanspeed, stop) | HTTP `POST /send/instruction` | `api.py`: `send_zone_command`, `send_installation_command` |
| Anstoß einer Zustandsmeldung | HTTP `POST /send/instruction` (`command:status`) | `refresh_status_periodic` |

**Abgeleitet:** Der Aufbau (Zonennamen) wird nur beim Start bzw. Neuladen per HTTP geholt; veränderliche Werte kommen nur per MQTT. Bis zum ersten Status sind die Entitäten „nicht verfügbar“ (`availability.py`).

---

## 5. Übersichtsdiagramm

```mermaid
flowchart TB
    subgraph HA["Home Assistant"]
        CF["config_flow.py<br/>(E-Mail/Passwort)"]
        INIT["__init__.py<br/>async_setup_entry"]
        API["api.py<br/>MySairAPI (requests, synchron)"]
        MQTT["mqtt_handler.py<br/>MySairMQTTClient (Thread)"]
        BUS(["Event-Bus<br/>mysair_update / mysair_feedback"])
        COORD["coordinator.py<br/>MySairCoordinator"]
        CL["climate.py"]
        SE["sensor.py"]
        SW["switch.py"]
        REF["refresh_status_periodic<br/>(alle 120 s)"]
    end

    subgraph CLOUD["MySair-Cloud / AWS"]
        HTTP["HTTP-API<br/>api.mysair.es/v1"]
        IOT["AWS IoT<br/>MQTT/WSS"]
        DEV["Geräte<br/>Zonenregler"]
    end

    CF -->|Anmeldung| API
    INIT --> API
    INIT --> MQTT
    API -->|"POST/GET/PUT"| HTTP
    MQTT -->|"SUBSCRIBE pro/v1/get/ctl/#, usr/.../feedback"| IOT
    HTTP <--> DEV
    IOT <--> DEV
    IOT -->|"PUBLISH .../status, .../feedback"| MQTT
    MQTT -->|Callback| BUS
    REF -->|"POST /send/instruction (status)"| HTTP
    BUS --> COORD
    COORD -->|"Dispatcher, Signal je Zone"| CL & SE & SW
    BUS -->|mysair_feedback| CL & SW
    CL & SW -->|"send_zone_command → POST /send/instruction"| API
```

---

## 6. Sequenzdiagramme

### 6.1 Ersteinrichtung (Config Flow)

```mermaid
sequenceDiagram
    actor U as Nutzer
    participant CF as config_flow.py
    participant API as MySairAPI
    participant H as api.mysair.es
    U->>CF: gibt E-Mail + Passwort ein
    CF->>API: async_add_executor_job(api.login)
    API->>H: POST /user/login
    H-->>API: entity{access_token, refresh_token}
    alt ungültige Zugangsdaten
        API-->>CF: MySairAuthError
        CF-->>U: Fehler invalid_auth
    else OK
        CF->>CF: async_set_unique_id(email) + async_create_entry(email, refresh_token)
        CF-->>U: Eintrag angelegt
    end
```

Das Passwort wird nur während des Dialogs im Speicher gehalten und nie in der Config Entry gespeichert. Siehe `docs/security-and-privacy.md`.

### 6.2 Start von Home Assistant (Setup Entry)

```mermaid
sequenceDiagram
    participant HA as HA-Core
    participant I as async_setup_entry
    participant API as MySairAPI
    participant H as HTTP MySair
    participant M as MySairMQTTClient
    participant IOT as AWS IoT
    HA->>I: async_setup_entry(entry)
    I->>API: refresh_tokens()
    API->>H: PUT /user/refreshtokens
    I->>API: get_locations()
    API->>H: GET /locations
    I->>API: get_installations(location_id)
    API->>H: GET /installations
    loop je Installation
        I->>API: get_devices(ref)
        API->>H: GET /devices
    end
    I->>I: _cleanup_stale_zone_devices()
    I->>I: hass.data[DOMAIN][entry_id] = {...}
    I->>M: start() (Daemon-Thread)
    M->>API: refresh_aws_credentials()
    API->>H: PUT /user/refreshawscredentials
    M->>IOT: WSS-Verbindung + CONNECT + SUBSCRIBE
    I->>I: MySairCoordinator.start()
    I->>HA: async_forward_entry_setups(PLATFORMS)
    I->>I: entry.async_create_background_task(refresh_status_periodic)
```

Fehlerfälle: Eine ungültige Sitzung führt zu `ConfigEntryAuthFailed` (HA bietet die erneute Anmeldung an), Netz- oder Backend-Fehler zu `ConfigEntryNotReady` (HA versucht es später erneut).

### 6.3 Zustandsabfrage (angestoßen)

```mermaid
sequenceDiagram
    participant R as refresh_status_periodic
    participant API as MySairAPI
    participant H as HTTP MySair
    participant DEV as Gerät
    participant IOT as AWS IoT
    participant M as MySairMQTTClient
    loop alle 120 s, je Installation
        R->>API: send_installation_command(ref, "status")
        API->>H: POST /send/instruction (command:status, value:sync)
        H->>DEV: (Backend) fordert Zustand an
        DEV->>IOT: veröffentlicht Zustand
        IOT->>M: PUBLISH .../status
    end
```

### 6.4 Empfang einer MQTT-Aktualisierung

```mermaid
sequenceDiagram
    participant IOT as AWS IoT
    participant M as MySairMQTTClient._on_message
    participant CB as mqtt_message_callback
    participant BUS as Event-Bus
    participant CO as MySairCoordinator
    participant E as Entität (climate/sensor/switch)
    IOT->>M: binärer WSS-Frame (PUBLISH 0x30)
    M->>M: Puffer zusammensetzen, parse_mqtt_publish → Topic + JSON
    M->>CB: callback({topic, payload})
    CB->>CB: parse_status_payload (';' am Ende entfernen, t[] auswerten)
    CB->>BUS: call_soon_threadsafe(async_fire, mysair_update, {topic, data})
    BUS->>CO: _handle_update(event) (einziger Abonnent)
    CO->>CO: ctl in installation_refs prüfen, nach Zone aufteilen
    CO->>E: async_dispatcher_send(signal_zone_update(ctl, zone_id), zone)
    E->>E: _handle_zone_update(zone): Zustand setzen + async_write_ha_state()
```

### 6.5 Befehl aus einer Entität

```mermaid
sequenceDiagram
    actor U as Nutzer/HA
    participant E as MySairThermostat
    participant API as MySairAPI
    participant H as HTTP MySair
    participant IOT as AWS IoT
    U->>E: async_set_hvac_mode(HEAT)
    E->>E: optimistischer lokaler Zustand
    E->>API: async_add_executor_job(send_zone_command, ctl, dev, "mode", "0", temp)
    API->>H: POST /send/instruction
    alt 401 Token abgelaufen
        API->>H: PUT /user/refreshtokens
        API->>H: PUT /user/refreshawscredentials
        API->>H: POST /send/instruction (zweiter Versuch)
    end
    H-->>API: 201 {msg, error:[], entity.value[0].orderId}
    E->>E: orderId merken, Zeitlimit 5 s starten
    IOT-->>E: mysair_feedback mit gleicher orderId → bestätigt
    Note over E: Ohne Bestätigung nach 5 s:<br/>optimistischen Zustand zurücksetzen
    Note over E,IOT: Der echte Zustand kommt danach per MQTT (6.4)
```

Hinweis: `send_instruction` erwartet derzeit `msg == "Creado"`. Das Backend antwortet je nach Kontosprache auch mit `"Created"`; dieser Fall wird noch fälschlich als Ablehnung gewertet (siehe `docs/mysair-http-api.md` §3.1).

### 6.6 Wiederverbindung nach MQTT-Abbruch

```mermaid
sequenceDiagram
    participant M as MySairMQTTClient._run
    participant WS as WebSocketApp
    participant API as MySairAPI
    participant IOT as AWS IoT
    WS-->>M: on_close / on_error
    M->>M: connected=False
    alt geplante Wiederverbindung (Zugangsdaten laufen bald ab)
        M->>M: sofort weiter
    else unerwarteter Abbruch
        M->>M: compute_backoff_delay (10 s … 120 s, ±20 % Jitter)
    end
    loop solange kein stop_event
        M->>API: refresh_aws_credentials() (wenn fehlend oder bald abgelaufen)
        M->>API: aws_sign_url(...) (neue Signatur)
        M->>IOT: WSS-Verbindung + CONNECT (neue, eindeutige client_id)
        IOT-->>M: CONNACK (0x20)
        M->>IOT: alle Topics erneut abonnieren
        M->>M: Timer für vorausschauende Erneuerung vor aws_expires_at
    end
```

---

## 7. Beobachtete technische Entscheidungen

| Entscheidung | Beleg | Kommentar |
|---|---|---|
| **Synchroner** HTTP-Client (`requests`) im Executor | `api.py`, Aufrufe über `async_add_executor_job` | Blockiert den Loop nicht; erlaubt eine injizierbare `session` für Tests. |
| MQTT **von Hand** über WebSocket statt `paho-mqtt` | `mqtt_handler.py` | Bewusst beibehalten: `paho-mqtt` unterstützt keine SigV4-signierte WSS-URL, die vor jeder Wiederverbindung neu signiert werden muss (siehe `docs/development-roadmap.md`, E6). |
| Event-Bus + zentraler Koordinator + Dispatcher | `coordinator.py` | Ein Abonnent je Config Entry statt einer Filterung in jeder Entität. |
| **Optimistischer** Zustand mit Rücksetzung | `command_feedback.py` | Wird zurückgesetzt, wenn nach 5 s keine Bestätigung kommt; ein echter Status hat Vorrang. |
| Kein `DataUpdateCoordinator` | – | Reines Push-Muster; `DataUpdateCoordinator` ist für Abfragen gedacht. |
| Eigene AWS-SigV4-Signatur | `api.py`: `aws_sign_url` | Vermeidet `boto3` als schwere Abhängigkeit. |

---

## 8. Offene Punkte (Kurzfassung)

- Vollständige Struktur der HTTP-Antworten (Felder von `location`, `installation`, `device`). **Unbekannt** – genutzt werden nur `id`, `reference`, `name`. Siehe `docs/mysair-http-api.md`.
- Bedeutung der Zonenfelder `vf`, `hmh`, `mh`, `p`, `ps`. **Unbekannt**. Siehe `docs/known-unknowns.md`.
- Ratenbegrenzung von `/send/instruction`. **Unbekannt**.
- Weitere Topics außer `.../status` und `.../feedback`. Bisher keine beobachtet.

Querverweise: `docs/mysair-http-api.md`, `docs/mysair-mqtt-protocol.md`, `docs/domain-model.md`, `docs/home-assistant-integration.md`, `docs/testing-strategy.md`, `docs/security-and-privacy.md`, `docs/known-unknowns.md`, `docs/development-roadmap.md`.

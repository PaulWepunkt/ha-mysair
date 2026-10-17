# HTTP-API von MySair

> Basis-URL: `https://api.mysair.es/v1` – **Bestätigt** (`api.py`, `MySairAPI.base_url`).
> Alle Angaben stammen aus der **Analyse des Codes dieser Integration und der offiziellen Web-App**, nicht aus einer offiziellen Dokumentation. Gewissheit: **Bestätigt** = der Aufruf existiert im Code; Antwortfelder mit **Abgeleitet/Unbekannt** sind nur insoweit bekannt, wie der Code sie nutzt.
> ⚠️ Es sind keine echten Token, Cookies oder Zugangsdaten enthalten. Alle Beispiele sind **bereinigt**.

---

## Endpunkte im Überblick

| Gruppe | Methode | Pfad | Nutzende Funktion | Gewissheit |
|---|---|---|---|---|
| Anmeldung | POST | `/user/login` | `MySairAPI.login` | Bestätigt |
| Anmeldung | PUT | `/user/refreshtokens` | `MySairAPI.refresh_tokens` | Bestätigt |
| Anmeldung | PUT | `/user/refreshawscredentials` | `MySairAPI.refresh_aws_credentials` | Bestätigt |
| Erkennung | GET | `/locations` | `MySairAPI.get_locations` | Bestätigt |
| Erkennung | GET | `/installations?location_id=&validated=1` | `MySairAPI.get_installations` | Bestätigt |
| Erkennung | GET | `/devices?installation_ref=` | `MySairAPI.get_devices` | Bestätigt |
| Befehle | POST | `/send/instruction` | `MySairAPI.send_instruction` | Bestätigt |

**Authentifizierung (Bestätigt):** Alle Endpunkte außer `/user/login` und `/user/refreshtokens` verwenden den Header `Authorization: Bearer <access_token>`.
**Zeitlimits (Bestätigt):** login 15 s, refreshtokens 10 s, refreshawscredentials 15 s, locations/installations/devices 10 s, send/instruction 10 s.
**Wiederholung (Bestätigt):** Alle Endpunkte mit Bearer-Token wiederholen eine Anfrage **einmal** nach einem `401`, nachdem die Sitzung erneuert wurde (`_authed_request`, `send_instruction`). `send_instruction` erneuert dabei zusätzlich die AWS-Zugangsdaten.
**Gemeinsames Antwortformat (Abgeleitet):** Antworten verpacken die Daten in einem Schlüssel `entity` (Objekt oder Liste), bei Anweisungen zusätzlich `msg`/`error`.

### Fehlerklassifizierung (`api._http_error`)

Das Backend basiert auf **Laravel Passport**. Für alle Endpunkte gilt eine Regel nach Bereich:

- `5xx` und `429` → `MySairConnectionError` (vorübergehend; Home Assistant versucht es später erneut).
- Jeder andere Code außerhalb von 2xx (`400`, `401`, `403`, `404`, …) → `MySairAuthError` (erneute Anmeldung nötig).

Die Regel ist bewusst nach Bereich formuliert: Eine Aufzählung einzelner Codes hatte zuvor versagt, weil niemand an dieser Stelle einen `404` erwartete (siehe §1.2). Netzwerkfehler (`requests.RequestException`) werden zu `MySairConnectionError`.

---

## 1. Anmeldung

### 1.1 `POST /user/login` – **Bestätigt**
- **Zweck:** `access_token` und `refresh_token` erhalten. Wird nur im Einrichtungsdialog und bei der erneuten Anmeldung (Reauth) verwendet.
- **Authentifizierung:** keine.
- **Header:** `Content-Type: application/json` (implizit durch `json=`).
- **Body:**
  ```json
  { "email": "<EMAIL>", "password": "<PASSWORD>" }
  ```
- **Erwartete Antwort (200):**
  ```json
  { "entity": { "access_token": "<REDACTED_JWT>", "refresh_token": "<REDACTED>", "expires_at": 0 } }
  ```
- **Genutzte Felder:** `entity.access_token`, `entity.refresh_token`. Der Rest von `entity` wird in `self.entity` abgelegt, aber nicht genutzt.
- **Fehler:** Klassifizierung siehe oben; fehlt `access_token` in der Antwort → `MySairAuthError`.
- **Genutzt von:** `config_flow.async_step_user` und `async_step_reauth_confirm`.

### 1.2 `PUT /user/refreshtokens` – **Bestätigt**
- **Zweck:** Token mit dem `refresh_token` erneuern. So startet die Integration bei jedem Start ihre Sitzung – ohne gespeichertes Passwort.
- **Authentifizierung:** keine (das refresh_token steht im Body).
- **Body:** `{ "refresh_token": "<REDACTED>" }`
- **Antwort (200):** `{ "entity": { "access_token": "...", "refresh_token": "..." } }`
- **Genutzte Felder:** `entity.access_token`, `entity.refresh_token`. Das `refresh_token` wechselt bei jeder Erneuerung; das neue wird über den Callback `on_tokens_refreshed` in der Config Entry gespeichert.
- **Fehler (bestätigt):** Existiert das `refresh_token` auf dem Server nicht mehr, antwortet das Backend mit **`404`**, nicht `401`, und dem Body `No query results for model [Laravel\Passport\RefreshToken]`. Ein Token wird ungültig durch Abmelden in der offiziellen App, eine Passwortänderung oder das Aufräumen alter Token auf dem Server. Nach der Regel oben führt das zu `MySairAuthError` und damit zur erneuten Anmeldung.
- **Genutzt von:** `async_setup_entry`, `_authed_request` und `send_instruction` (jeweils bei einem `401`).

### 1.3 `PUT /user/refreshawscredentials` – **Bestätigt**
- **Zweck:** temporäre Zugangsdaten für **AWS IoT** (MQTT-Verbindung).
- **Authentifizierung:** `Authorization: Bearer <access_token>`.
- **Body:** keiner.
- **Antwort (200):**
  ```json
  {
    "entity": {
      "aws_mqtt_host": "<REDACTED>.iot.<region>.amazonaws.com",
      "aws_default_region": "<REGION>",
      "aws_access_key_id": "<REDACTED>",
      "aws_secret_access_key": "<REDACTED>",
      "aws_security_token": "<REDACTED>",
      "aws_mqtt_user": "<aws_mqtt_user>",
      "aws_base_topic": "pro/v1/",
      "aws_expires_at": 0
    }
  }
  ```
- **Genutzte Felder (Bestätigt):** Die sechs Pflichtfelder `aws_mqtt_host` bis `aws_mqtt_user`; fehlt eines → Ausnahme „AWS credentials incomplete or invalid“. Optional: `aws_base_topic` (Basis der MQTT-Topics) und `aws_expires_at` (für die vorausschauende Erneuerung).
- **Genutzt von:** `MySairMQTTClient._run` vor jedem Verbindungsversuch, wenn die Zugangsdaten fehlen oder bald ablaufen.
- **Sicherheitshinweis:** Diese Zugangsdaten sind geheim und temporär und werden nicht geloggt; geloggt wird nur `aws_mqtt_user` auf Stufe DEBUG. Siehe `docs/security-and-privacy.md`.

---

## 2. Erkennung des Aufbaus

Die Erkennungsmethoden geben Fehler als `MySairAuthError`/`MySairConnectionError` weiter. Eine leere Liste bedeutet ausschließlich „das Konto enthält keine Daten“. (Früher lieferten sie bei jedem Fehler `[]`; eine abgelaufene Sitzung sah dadurch wie ein leeres Konto aus.)

### 2.1 `GET /locations` – **Bestätigt**
- **Zweck:** Standorte des Kontos auflisten.
- **Authentifizierung:** Bearer.
- **Antwort (200):** `{ "entity": [ { "id": <LOCATION_ID>, ... }, ... ] }`
- **Genutzte Felder:** nur `entity[0].id`. **Es wird nur der erste Standort genutzt** (bewusste Abgrenzung, `docs/known-unknowns.md` #15).
- **Leere Liste** → `ConfigEntryNotReady`.
- **Gewissheit:** Existenz **Bestätigt**; Felder außer `id` **Unbekannt**.

### 2.2 `GET /installations?location_id=<id>&validated=1` – **Bestätigt**
- **Zweck:** Installationen (Anlagen) eines Standorts.
- **Authentifizierung:** Bearer.
- **Query-Parameter:** `location_id` (Pflicht), `validated=1` (fest – schließt Installationen aus, die noch nie Verbindung hatten, siehe `docs/known-unknowns.md` #14).
- **Antwort (200):** `{ "entity": [ { "reference": "<INST_REF>", ... }, ... ] }`
- **Genutzte Felder:** `entity[].reference`. Die `reference` dient als **`ctl`** (Controller) in MQTT und Befehlen.
- **Leere Liste** → `ConfigEntryNotReady`.

### 2.3 `GET /devices?installation_ref=<ref>` – **Bestätigt**
- **Zweck:** Geräte/Thermostate (Zonen) einer Installation.
- **Authentifizierung:** Bearer.
- **Query-Parameter:** `installation_ref` (Pflicht).
- **Antwort (200):** `{ "entity": [ { "reference": "<DEV_REF>", "name": "<ZONE>", ... }, ... ] }`
- **Genutzte Felder:** `reference` (Rückfall auf `rf`/`id`) und `name`.
- **Gewissheit:** Die Rückfälle `rf`/`id` zeigen, dass der Feldname nicht zu 100 % bestätigt ist → **Abgeleitet** (`docs/known-unknowns.md` #12/#13).

---

## 3. Befehle und Anweisungen

### 3.1 `POST /send/instruction` – **Bestätigt**
- **Zweck:** **eine oder mehrere** Anweisungen senden (Steuerbefehle oder Zustandsanforderung). Das Backend übersetzt sie und stellt sie den Geräten zu; der daraus folgende Zustand kommt per **MQTT** zurück.
- **Authentifizierung:** Bearer.
- **Body:** JSON-Array von Anweisungsobjekten.

**Aufbau einer Anweisung (Bestätigt – `send_zone_command`, `send_installation_command`):**
```json
[
  {
    "sender": "WEB",
    "ctl": "<INSTALLATION_REFERENCE>",
    "app": "<aws_mqtt_user>",
    "device": "<DEVICE_REFERENCE oder ''>",
    "command": "<mode|temp|power|fanspeed|stop|status>",
    "value": "<abhängig vom Befehl>"
  }
]
```

`app` ist die Kennung des eigenen Kontos (`aws_mqtt_user`). Sind die AWS-Zugangsdaten noch nicht geladen, holt `MySairAPI._app_name` sie vor dem Senden; scheitert das, wird der Befehl nicht gesendet. Einen festen Ersatzwert gibt es nicht, weil er ein fremdes Konto bezeichnen würde.

**Vollständiger Befehlskatalog (BESTÄTIGT aus der offiziellen App – siehe `docs/protocol-findings.md`):**

| `command` | `value` | Bedeutung | Umgesetzt |
|---|---|---|---|
| `mode` | `{"mode":"0".."5","temperature":"<tc>"}` | **Einschalten / Modus setzen.** `m`: gerade=Heizen, ungerade=Kühlen; {0,1,4,5}=Luft, {2,3,4,5}=Fußboden | ✅ (`climate.py`, `switch.py`; `m` nach den Regeln der App, siehe `protocol-findings.md` §4b und `zone_control.py`) |
| `power` | `"0"` | **Ausschalten** (es gibt kein `power:"1"`; eingeschaltet wird über `mode`) | ✅ |
| `temp` | `"<temperatur>"` (String) | Soll-Temperatur ändern | ✅ |
| `status` | `"sync"` (mit `device:""`) | Zustand anfordern | ✅ |
| `fanspeed` | `"<n>"` (String, `"0"`–`"4"`) | Lüfterstufe | ✅ |
| `stop` | `"1"` (mit `device` = Hauptzone, `pl`=`"1"`) | Installation stoppen | ✅ (`send_installation_command`, Dienst `mysair.stop_installation`; ohne bekannten Status `device:""`) |
| `temporizer` | `"30"`/`"60"`/`"90"`/`"120"`, `"0"` = aus | Timer bis zum Ausschalten | ❌ noch nicht (geplant) |
| `programs` | `""` | Programme lesen | ❌ noch nicht (geplant), siehe `known-unknowns.md` #27 |
| `update-program` / `delete-program` | Programmobjekt | Programm anlegen/ändern bzw. löschen | ❌ noch nicht (geplant), siehe `known-unknowns.md` #27 |

> ✅ Die Kodierung des **Befehls** `mode` (`0`–`5`, gerade = Heizen) ist bestätigt. Siehe `docs/protocol-findings.md` §7.

- **Erwartete Antwort (201):**
  ```json
  { "msg": "<Bestätigungstext>", "error": [], "entity": { "value": [ { "orderId": "<ORDER_ID>" } ] } }
  ```
  `entity.value[0].orderId` dient der Zuordnung zur Quittung im MQTT-Topic `feedback` (`api.extract_order_id`).
- **Prüfung (Bestätigt):** verlangt `status_code == 201` und ein leeres `error`. Der Text in `msg` hängt von der Sprache des MySair-Kontos ab (`"Creado"`, `"Created"` …) und wird deshalb nicht ausgewertet, nur im DEBUG-Log ausgegeben. Erfolgreiche Antworten enthalten außerdem `meta` (`{"version": "1.0", "type": "MqttOrder"}`), das nicht genutzt wird.
- **Bekannte Fehler:**
  - `401` → erneuert Token + AWS-Zugangsdaten und **wiederholt einmal**.
  - Jeder andere Code außer `201` → klassifiziert über `_http_error`.
  - `error` nicht leer → Ausnahme „Instruction rejected“.
- **Zeitlimit:** 10 s. **Wiederholungen:** 1 (nur bei 401).
- **Genutzt von:** `send_zone_command` (climate/switch), `send_installation_command` (Dienst, `refresh_status_periodic`).

**Bereinigtes Beispiel – Zone auf Heizen mit 21 °C:**
```json
[{ "sender":"WEB", "ctl":"INST_REF", "app":"webXXXX", "device":"DEV_REF",
   "command":"mode", "value":{"mode":"0","temperature":"21.0"} }]
```
**Bereinigtes Beispiel – Zustand anfordern:**
```json
[{ "sender":"WEB", "ctl":"INST_REF", "app":"webXXXX", "device":"",
   "command":"status", "value":"sync" }]
```

---

## 4. Weitere Endpunkte

- Im Code gibt es keine weiteren Endpunkte.
- Einen HTTP-Endpunkt zum direkten Lesen des Zustands gibt es nicht; der Zustand kommt nur per MQTT (`docs/known-unknowns.md` #18).
- **Unbekannt:** Endpunkte für Szenen oder zum Schreiben von Zeitprogrammen.

---

## 5. Fehlerbehandlung und Robustheit

| Aspekt | Situation | Gewissheit |
|---|---|---|
| Unterscheidung der HTTP-Codes | Einheitlich nach Bereich (`_http_error`): 5xx/429 vorübergehend, sonst erneute Anmeldung | Bestätigt |
| Vorausschauende Token-Erneuerung | Keine für das HTTP-Token; erneuert wird bei einem `401` | Bestätigt |
| Erkennung bei Fehlern | Fehler werden weitergereicht, nicht als leere Liste verschluckt | Bestätigt |
| Sitzung stirbt im laufenden Betrieb | MQTT-Thread und periodischer Task fordern die erneute Anmeldung sofort an (`entry.async_start_reauth`) | Bestätigt |
| Schema-Prüfung der Antworten | Keine; `entity` wird vorausgesetzt | Bestätigt |

---

## 6. Bereinigter Beispielablauf

```
PUT  /user/refreshtokens             → 200 {entity:{access_token, refresh_token}}
GET  /locations                      → 200 {entity:[{id:...}]}
GET  /installations?location_id=..   → 200 {entity:[{reference:...}]}
GET  /devices?installation_ref=..    → 200 {entity:[{reference, name}]}
PUT  /user/refreshawscredentials     → 200 {entity:{aws_*}}        (für MQTT)
POST /send/instruction  [status]     → 201 {msg, error:[], entity:{value:[{orderId}]}}
POST /send/instruction  [mode/temp]  → 201 {msg, error:[], entity:{value:[{orderId}]}}
```

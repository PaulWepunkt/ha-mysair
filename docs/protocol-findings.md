# Protokollerkenntnisse aus der offiziellen App

> Quelle: JavaScript-Bundle der MySair-Web-App `https://app.mysair.es/js/app.09acea34.js`
> (öffentliche statische Datei; es wurde weder mit der Befehls-API noch mit MQTT oder Geräten interagiert).
> Gewissheit: **Bestätigt** – direkt aus der Logik der App entnommen, sofern nicht anders vermerkt.
> Die Ausschnitte sind Code der App (minifiziert, ohne Geheimnisse). Der Dateiname enthält einen Build-Hash; eine neuere App-Version kann anders heißen und abweichen.

---

## 1. Umschlag einer Anweisung

```js
_generateInstruction(e,t,i,a){
  return { sender:"WEB", ctl:e, app:this.$session.getClientId(), device:t, command:i, value:a }
}
```
- `app` = `session.getClientId()` (entspricht `aws_mqtt_user`).
- Identisch mit dem, was die Integration sendet.

## 2. Befehlskatalog (Mixin für Anweisungen)

```js
setPower(e,t,i,...){ _sendInstruction(e,t,"power",i,...) }        // ausschalten: value "0"
setMode(e,t,i,...){ _sendInstruction(e,t,"mode",i,...) }          // einschalten / Modus setzen
setTemp(e,t,i,...){ _sendInstruction(e,t,"temp",""+i,...) }        // Soll-Temperatur (String)
setFanspeed(e,t,i,...){ _sendInstruction(e,t,"fanspeed",""+i,...) }
setTemporizer(e,t,i,...){ _sendInstruction(e,t,"temporizer",i,...) }
stopInstallation(e,t,...){ _sendInstruction(e,t,"stop","1",...) }
getPrograms(e,t,...){ _sendInstruction(e,t,"programs","",...) }
// Programm-Dialog: save()   -> _sendInstruction(ctl, zone, "update-program", program, ...)
//                  remove() -> _sendInstruction(ctl, zone, "delete-program", program, ...)
getStatus(e,...){ $api.instruction(_generateInstruction(e,"","status","sync")) }
```

| `command` | `value` | Zweck |
|---|---|---|
| `power` | `"0"` | Ausschalten (einzige beobachtete Verwendung von `power`) |
| `mode` | `{mode:"0".."5", temperature:"<tc>"}` | Einschalten / Modus setzen |
| `temp` | `"<temp>"` | Soll-Temperatur |
| `fanspeed` | `"<n>"` | Lüfterstufe |
| `temporizer` | `"30"`/`"60"`/`"90"`/`"120"` (Minuten bis zum Ausschalten), `"0"` = aus | Timer; nur angeboten, wenn `tz` = `"1"` |
| `stop` | `"1"` | Installation stoppen; `device` = Hauptzone (`pl` = `"1"`) |
| `status` | `"sync"` | Zustand anfordern |
| `programs` | `""` | Programme lesen |
| `update-program` | Programmobjekt | Programm anlegen oder ändern (höchstens 3 je Zone) |
| `delete-program` | Programmobjekt | Programm löschen |

Die Felder des Programmobjekts und die Antwort von `programs` sind noch nicht vollständig gegen echten Datenverkehr geprüft; siehe `known-unknowns.md` #27.

## 3. Ein- und Ausschalten

```js
setModeHeat:function(e,t){                       // e = Luft an/aus, t = Fußboden an/aus
  if(!e&&!t) return this.setPower(installation_ref, reference, "0", ...);  // beides aus -> power "0"
  this.status.setMode(this.status.isModeHeat(), e, t);
  this.status.setPower(!0);
  this.setMode(installation_ref, reference, {mode:this.status.getMode(), temperature:this.status.getOrder()}, ...)
}
```
- **Ausschalten** = `command:"power", value:"0"`.
- **Einschalten** = `command:"mode"` mit dem gewünschten `m`. **Es gibt kein `power:"1"`.**

## 4. Zustandsmodell je Zone (Klasse `te`)

```js
isOn()      { return "0" != this.e }              // e: "0"=aus, "1"=ein, "2"=Standby
isStanby()  { return "2" == this.e }
isModeAC()  { return this.isOn() && ["0","1","4","5"].includes(this.m) }
isModeFloor(){ return this.isOn() && ["2","3","4","5"].includes(this.m) }
getMode(){return this.m}  getOrder(){return this.tc}  getTemp(){return this.tr}
getTempMin(){return this.tmm}  getTempMax(){return this.tmx}  getHumidity(){return this.hm}
getFanMode(){return this.vv}  getTimer(){return this.tzv}
allowHeat(){return "1"==this.c}  allowCool(){return "1"==this.f}
allowFan(){return "1"==this.v}   allowFloor(){return "1"==this.s}
allowTimer(){return "1"==this.tz}  allowPrograms(){return "1"==this.hp}

setMode(e,t,i){ let a=null;                        // e=Heizen, t=Luft, i=Fußboden
  t&&!i&&(a=0); !t&&i&&(a=2); t&&i&&(a=4);         // Luft=0, Fußboden=2, Luft+Fußboden=4
  e||(a+=1);                                       // +1, wenn NICHT Heizen (=Kühlen)
  this.m=""+a }
setPower(e){ this.e = e?"1":"0" }
```

Auf Installationsebene (`ae`): `this.heat = ["0","2","4"].includes(t.m)` → bestätigt **gerade = Heizen**.

### Tabelle des Felds `m` (Modus)

| `m` | Luft | Fußboden | Richtung |
|---|:--:|:--:|---|
| `0` | ✓ | | Heizen |
| `1` | ✓ | | Kühlen |
| `2` | | ✓ | Heizen |
| `3` | | ✓ | Kühlen |
| `4` | ✓ | ✓ | Heizen |
| `5` | ✓ | ✓ | Kühlen |

**Gerade = Heizen, ungerade = Kühlen.** Einen automatischen Wechsel zwischen Heizen und Kühlen gibt es nicht: Die sechs Werte decken genau die Kombinationen Luft/Fußboden × Heizen/Kühlen ab.

## 4b. Richtung der Anlage und Schutz durch die App (BESTÄTIGT)

Die App speichert keine Richtung je Zone. Jede Zone zeigt die Richtung der **Installation** an (`te.isModeHeat()` ruft `installation.isModeHeat()` auf):

- **Richtung der Installation** = Parität von `m` der ersten eingeschalteten Zone; ist keine Zone an, die der ersten Zone (Reihenfolge wie vom Controller gemeldet).
- **Installation an** = mindestens eine Zone hat `e` ≠ `"0"`.
- **Richtungswechsel:** Ist die Installation an, zeigt die App nur einen Hinweis („Zuerst die Anlage vollständig ausschalten, dann den Modus wählen, dann ein Thermostat einschalten“) und sendet nichts. Ist sie aus, wird die Richtung nur lokal gemerkt und mit dem nächsten Einschalten gesendet.
- **Einschalten** (`togglePowerDevice(true)`): Luft an, Fußboden wie bisher (Bit aus `m`), Richtung der Installation, Soll-Temperatur `tc`; gesendet als `mode`.
- **Fußboden umschalten** (`toggleRadiatingFloor`): `setModeHeat(isModeAC(), floor)`. Die Luft behält ihren Zustand (zählt nur bei eingeschalteter Zone). Bleibt weder Luft noch Fußboden an, sendet die App `power "0"`.
- **Anlage stoppen** (`stop()`): `device` ist die Referenz der Hauptzone (`pl` = `"1"`), nicht leer.

Der Schutz liegt also in der App, nicht im Backend. Die Integration übernimmt dieselben Regeln (`zone_control.py`); die Auswahl „Betriebsart“ entspricht dem Saisonwechsel der App und merkt die Richtung bei ausgeschalteter Anlage ebenfalls nur lokal (nicht über einen Neustart von Home Assistant hinweg). Wird die nächste Zone an einem Raumthermostat oder in der App eingeschaltet, gilt die Richtung aus dem nächsten Status. **Offen (ungeprüft):** ob Raumthermostate selbst Heizen/Kühlen umstellen können und wie schnell die Steuerung eine Änderung am Thermostat per MQTT meldet. Zusätzlich nutzt sie den Fußboden beim Kühlen nur, wenn die Option „Fußbodenkühlung erlauben“ eingeschaltet ist, weil MySair keinen Taupunktschutz meldet.

## 5. Felder der Zonen-Nutzdaten (Abbildung in `_validateInstallation`)

```js
t = JSON.parse(t.value.slice(0,-1))               // bestätigt das abgeschnittene ';' am Ende
devices: t.t.map(e=>({
  reference:e.rf, name:e.n, principal:e.pl, floor:e.s,
  cold:e.f, hot:e.c, fan:e.v, timer:e.tz, programming:e.hp,
  temperature_max:e.tmx, temperature_min:e.tmm
}))
```

| Feld | Bedeutung | Typ |
|---|---|---|
| `rf` | Zonenreferenz | str |
| `n` | Name | str |
| `e` | **Ein/Aus**: 0=aus, 1=ein, 2=Standby | str |
| `m` | **Modus** 0–5 (siehe §4) | str |
| `tr` | aktuelle Temperatur | num |
| `tc` | Soll-Temperatur (order) | num |
| `tmm` / `tmx` | minimale / maximale Temperatur | num |
| `hm` | Luftfeuchte (siehe Hinweis unten) | num |
| `vv` | aktueller Lüftermodus / aktuelle Lüfterstufe | str |
| `tzv` | aktueller Timerwert | str |
| `sv` | **Fußbodenheizung ein/aus**: "0"/"1" (zu unterscheiden von der Fähigkeit `s`) | str |
| `pl` | Hauptzone | Flag |
| Fähigkeiten | `c`=Heizen möglich, `f`=Kühlen möglich, `v`=Lüfter, `s`=Fußboden, `tz`=Timer, `hp`=Programme | "1"/"0" |
| `v` (Installation) | Version der Steuerung | als Software-Version des Installationsgeräts angezeigt |

**Hinweis zur Luftfeuchte:** Echte Zonendaten können neben `hm` auch ein Feld `hum` enthalten. `hum` meldet dabei `"0"`, `hm` trägt den Messwert. Die Integration liest deshalb `hm` zuerst und `hum` nur als Ersatz; ein Wert von 0 gilt als unbekannt. Siehe `docs/known-unknowns.md` #25.

**Beleg für `sv`:** Eine erste Durchsicht des Bundles fand keinen Zugriff auf `sv`; eine wiederholte Suche mit anderen Suchbegriffen fand:
```js
setFloor(e){this.sv=e?"1":"0"}
// ...
toggleRadiatingFloor:function(e){this.status.setFloor(e),this.setModeHeat(this.status.isModeAC(),e)}
```
`setFloor` wirkt auf `this.status` (dasselbe Live-Objekt, in dem `e`/`m`/`tc` liegen). Damit gehört `sv` zum echten Zonenzustand und nicht nur zur statischen Konfiguration. Siehe `docs/known-unknowns.md` #26.

## 6. MQTT-Abonnement

```js
subscribe(e){ ... this.$emitInstance("suscribe", e, `ctl/${e}/#`) }
```
Entspricht dem Topic `pro/v1/get/ctl/{ref}/#` der Integration (mit dem Präfix `pro/v1/get/`).

---

## 6b. MQTT-Verbindung, Topics und Ablauf der Zugangsdaten (BESTÄTIGT)

### MQTT-Client und clientId
```js
Ha=e=>Fa.device({
  accessKeyId:e.accessKeyId, secretKey:e.secretKey, sessionToken:e.sessionToken,
  clientId:`mqtt-client_${e.accessKeyId}_${Date.now()}`,   // EINDEUTIG je Verbindung
  protocol:"wss", host:e.host
})
```
- Die MQTT-`clientId` ist **je Verbindung eindeutig** (`mqtt-client_<accessKeyId>_<Zeitstempel>`), **nicht** `aws_mqtt_user`.
- `getClientId()` = `aws_mqtt_user` dient nur als Feld `app` der Anweisungen und im Topic für `feedback`.

### Aufbau der Topics
```js
subscribe(e){ e=`${this._basePath}get/${e}`; ... }          // Abonnement
publish(e,t){ e=`${this._basePath}set/${e}`; ... }           // Veröffentlichen (die App nutzt für Befehle HTTP)
processTopic(e){ var t=e.split("/"); return {env:t[0],version:t[1],method:t[2],type:t[3],device:t[4],property:t.slice(5).join("/")} }
```
- `_basePath` = `aws_base_topic` (Feld der Zugangsdaten) = `pro/v1/`.
- Aufbau: `env/version/method/type/device/property` → z. B. `pro/v1/get/ctl/{ref}/status`.
- Die Integration abonniert `pro/v1/get/ctl/{ref}/#` und empfängt `.../status`. **Stimmt überein.**
- Zusätzlich gibt es `pro/v1/get/usr/{aws_mqtt_user}/feedback` (Quittung für Anweisungen mit `orderId`); die App ordnet damit Befehl und Antwort zu. Die Integration nutzt es ebenfalls (siehe §8).

### Ablauf und Erneuerung (Sitzung)
```js
getExpirationTime(){ return 1e3*expires_at - now }          // HTTP-Token
getMqttExpirationTime(){ return 1e3*aws_expires_at - now }  // AWS-Zugangsdaten
getAwsCredential(){ return {host:aws_mqtt_host, basePath:aws_base_topic, accessKeyId, secretKey, sessionToken, clientId:aws_mqtt_user} }
// controlMqtt: setTimeout(refreshAwsCredentials, getMqttExpirationTime()) → erneuert kurz vor Ablauf und verbindet neu
```
- Die AWS-Zugangsdaten enthalten **`aws_expires_at`** (Unix-Sekunden) und **`aws_base_topic`**; die Anmeldung liefert **`expires_at`**.
- Die App **erneuert die Zugangsdaten vorausschauend** vor `aws_expires_at`. Die Integration macht es genauso (`api.seconds_until_aws_credentials_expire` + Timer in `mqtt_handler`). Ohne diese Erneuerung trennt AWS IoT die laufende Verbindung genau beim Ablauf der Zugangsdaten, was zu regelmäßigen Verbindungsabbrüchen führt.

Konfiguration im Bundle: `VUE_APP_API_DOMAIN="https://api.mysair.es"`, `VUE_APP_OUTSERVICE_MILISECOND="5000"`.

## 7. Auswirkungen auf die Integration

| Bereich | Umsetzung in der Integration |
|---|---|
| Befehl `mode` | alle Werte `0`–`5`; Richtung, Luft und Fußboden nach §4b (`zone_control.py`) |
| Richtungsschutz | wie die App (§4b): bei laufender Anlage nur die aktuelle Richtung; `climate.hvac_modes` folgt den anderen Zonen |
| Statusparser: Modus | Ein/Aus aus `e`, Heizen/Kühlen aus der Parität von `m`, Luft/Fußboden aus den Mengen in §4 (`status_parser.parse_mode`) |
| Einschalten über `switch`/`climate` | wie die App: Luft an, Fußboden wie bisher, Richtung der Installation, aktuelle Soll-Temperatur |
| `power` aus | `value:"0"` – korrekt |
| Felder `tr`/`tc`/`tmm`/`tmx` | korrekt; `tmm`/`tmx` setzen `climate.min_temp`/`max_temp` |
| MQTT-`client_id` | eindeutig je Verbindung (`mqtt_handler.build_client_id`) |
| Ablauf der AWS-Zugangsdaten | `aws_expires_at` wird gelesen; Erneuerung vor Ablauf und bei jeder Wiederverbindung |
| Topic-Basis | aus `aws_base_topic`, Rückfall auf `pro/v1/` |
| HTTP-Token `expires_at` | Erneuerung bei `401` (noch nicht vorausschauend) |
| Topic `usr/{user}/feedback` | abonniert; Befehle werden über `orderId` bestätigt, sonst zurückgesetzt (§8) |
| Fähigkeiten `c`/`f` und Luftfeuchte | `climate.hvac_modes` je nach `allow_heat`/`allow_cool`; Feuchtesensor je Zone |
| Fähigkeiten `v`/`s` | Lüfterstufe in `climate` (§9); Schalter „Fußbodenheizung“ je Zone |
| Befehle `fanspeed`/`stop` | umgesetzt (`climate.fan_mode`, Dienst `mysair.stop_installation` an die Hauptzone) |
| Befehle `temporizer`/`programs`/`update-program`/`delete-program` | noch nicht umgesetzt (geplant); siehe `docs/known-unknowns.md` #27 |

---

## 8. Befehlsbestätigung: `orderId` und Topic `feedback` (BESTÄTIGT)

```js
// POST /send/instruction liefert die orderId in der Antwort:
this.$api.instruction(this._generateInstruction(e,t,i,a),
  e=>this.$onceOutInstance("reciveInstruction_"+e.entity.value[0].orderId, ...))

// Abonnement des feedback-Topics (einmal, mit der clientId der Sitzung):
this.$mqtt.subscribe(`usr/${this.$session.getClientId()}/feedback`,
  e=>{this.$emitInstance("reciveInstruction_"+e.orderId,e)})
// (subscribe() stellt `${basePath}get/` voran → pro/v1/get/usr/{aws_mqtt_user}/feedback)
```

- Die Antwort von `POST /send/instruction` enthält `entity.value[0].orderId`.
- Die Quittung kommt per MQTT im Topic `pro/v1/get/usr/{aws_mqtt_user}/feedback` als flaches Objekt mit `orderId` und `ctl` (die App verkettet damit `reciveStatus_{ctl}`) – ohne den JSON-String-Umschlag `value`, den `status` hat. Echte Nachrichten bestätigen diese flache Form (`docs/known-unknowns.md` #23).
- Das Topic von `feedback` steht in der Nachricht ohne Klammern vor den Nutzdaten; der Parser darf keine Klammern voraussetzen (siehe `docs/known-unknowns.md` #6).
- `VUE_APP_OUTSERVICE_MILISECOND="5000"` ist das Zeitlimit, nach dem die App einen Befehl als verloren betrachtet. Die Integration übernimmt den Wert als `FEEDBACK_TIMEOUT_SECONDS`.
- **Umsetzung:** Abonnement (`mqtt_handler.build_feedback_topic`), Ereignis `mysair_feedback` (`__init__.py`) und in `climate.py`/`switch.py` über `command_feedback.CommandFeedbackMixin`: Bei passender `orderId` gilt der Befehl als bestätigt; kommt nach 5 s nichts, wird der optimistische Zustand auf den letzten bekannten Wert zurückgesetzt. Trifft vorher ein echter Status ein, verfällt die Rücksetzung.
- Das Backend sendet Quittungen gelegentlich doppelt (gleicher Inhalt, wenige Millisekunden Abstand). Die Integration behandelt das bislang nicht gesondert.

---

## 9. Lüfterstufe: Bedeutung von `vv`/`fanspeed` (BESTÄTIGT)

```js
// ECHTE Komponente einer Installation (nicht die Demo-/Storybook-Seite der
// UI-Komponenten, die erfundene Beispieldaten nutzt – siehe unten):
data(){return{type:"device",
  fanGroups:[{key:"auto",values:{4:"A"}},{key:"manual",values:{1:"1",2:"2",3:"3"}}],
  ...}}

hasFanMode(){return"0"!=this.vv}
getFanMode(){return this.vv}

// Klick auf das Haupt-Lüftersymbol (ohne Gruppe zu wählen):
updateFanMode:function(e){
  this.status.setFanMode(e),
  this.setFanspeed(this.installation_ref,this.reference,e,e=>dd(e))
}
// onClick des Hauptsymbols (Umschalter): e.updateFanMode(e.status.hasFanMode()?"0":"4")
```

| `vv` | Bedeutung |
|---|---|
| `"0"` | Kein Lüftermodus (`hasFanMode()` → `false`); das Lüftersymbol der App ist dann aus und schaltet beim Antippen auf `"4"` |
| `"1"` / `"2"` / `"3"` | Manuelle Stufe 1 / 2 / 3 |
| `"4"` | Automatik (in der App als „A“ angezeigt) |

**⚠️ Falle bei der Analyse:** Eine Demo-/Storybook-Seite der UI-Komponenten im selben Bundle definiert `ventilationGroups:[{key:"",values:["A"]},{key:"manual",values:["1","2","3"]}]` mit Beispieldaten, die **nur zur Darstellung des Bauteils erfunden** sind und mit keinem echten `vv` verbunden sind. Ein erster flüchtiger Blick auf diese Seite führte zu einem falschen Zwischenstand. Die eigentliche Bestätigung kam aus der echten Installationskomponente (oben), die `fanGroups` über `getFanMode()`/`hasFanMode()` direkt mit `this.vv` verbindet. Lehre: In Bundles mit Demo-Seiten immer prüfen, ob der zitierte Code zum echten Datenfluss gehört (hier: das Objekt `status`/`te` aus §4) und nicht zu einer isolierten Demo mit Füllwerten.

- **Befehl:** `command:"fanspeed"`, `value:""+i` (String des gewünschten `vv`: `"0"`, `"1"`, `"2"`, `"3"` oder `"4"`).
- **Fähigkeit:** Das Bedienelement erscheint nur bei `allowFan()` (`"1"==this.v`) – in `status_parser.py` als `allow_fan` ausgewertet.
- **Umsetzung:** `climate.py` stellt `fan_mode`/`fan_modes` bereit (HA `ClimateEntityFeature.FAN_MODE`): `"0"` ↔ `"off"`, `"1"/"2"/"3"` unverändert, `"4"` ↔ `"auto"`; `async_set_fan_mode` sendet `fanspeed` über `api.send_zone_command`. **Offen (ungeprüft):** was „kein Lüftermodus“ an der Anlage physisch bewirkt.

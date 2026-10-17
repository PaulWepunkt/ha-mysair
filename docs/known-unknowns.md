# Offene Fragen (Known Unknowns)

> Jede Zeile: Frage · Kontext · vorhandene Belege · Hypothese · Prüfweg · Risiko einer falschen Annahme.
> **Regel:** Das Protokoll nicht ändern, solange Zeilen mit Risiko 🔴 offen sind. Derzeit ist keine Zeile mit 🔴 offen.
> Die Nummern sind stabil und werden im Code und in anderen Dokumenten zitiert.

---

## 1. Kodierung des Modus – ✅ GEKLÄRT (Quelle: offizielle App `app.09acea34.js`)

Analyse des Bundles der MySair-Web-App (Modellklassen `te`/`ie` und Mixin für Anweisungen).
Details mit den JS-Zitaten in `docs/protocol-findings.md`.

| # | Frage | BESTÄTIGTE Antwort |
|---|---|---|
| 1 | Kodierung des **Befehls** `mode` | `value = {mode:"0".."5", temperature:"<tc>"}`. Basis von `m`: 0=Luft, 2=Fußboden, 4=Luft+Fußboden; **+1 für Kühlen**. Gerade=Heizen, ungerade=Kühlen. Nur Luft: **0=Heizen, 1=Kühlen**. |
| 2 | Was bedeutet `e` im **Status**? | `e` ist NICHT der Modus, sondern **Ein/Aus**: `"0"`=aus, `"1"`=ein, `"2"`=Standby (`isOn()=="0"!=e`, `isStanby()=="2"==e`). |
| 3 | Warum nutzte das frühere `select.py` das Feld `m`? | Beim Feld lag es richtig: Der echte Modus ist `m` (0–5), nicht `e`. Der Rest von `select.py` war fehlerhaft; die Datei wurde entfernt. |
| 4 | Asymmetrie Befehl (0/1) ↔ Status? | Keine echte Asymmetrie: Der Befehl sendet `m`, der Status liefert `m`. Die Verwirrung entstand, weil ein früherer Parser `e` (Ein/Aus) als Modus las. Behoben in `status_parser.py`. |

---

## 2. MQTT-Nutzdaten

| # | Frage | Stand | Antwort / Beleg |
|---|---|---|---|
| 5 | **Vollständiger** Pfad des Status-Topics | ✅ Geklärt | `pro/v1/get/ctl/{ref}/status`. Aufbau `env/version/method/type/device/property`; Basis = `aws_base_topic` (=`pro/v1/`). Siehe `protocol-findings.md` §6b. |
| 6 | Genaues Binärformat des PUBLISH-Frames | ✅ Geklärt | Ein früherer Parser erkannte das Topic nur, wenn es in Klammern stand (`(topic){json}`). In echten Nachrichten fehlen die Klammern aber teils (z. B. beim Topic `feedback`); solche Nachrichten wurden als `"unknown"` eingestuft, wodurch die Befehlsbestätigung (#23) nie griff. Die eigentliche Erklärung: Die vermeintliche Klammer ist **kein Umschlag der App**, sondern das niederwertige Byte des 2-Byte-Längenfelds (Big Endian), das in jedem standardkonformen MQTT-PUBLISH vor dem Topic Name steht. Es ist nur sichtbar, wenn es zufällig einem druckbaren ASCII-Zeichen entspricht: Ein Topic mit genau 40 Zeichen ergibt `chr(40)='('`, kürzere Topics ergeben ein nicht druckbares Zeichen. Die Länge der echten Topics und das beobachtete Zeichen stimmen in mehreren voneinander unabhängigen Nachrichten exakt überein. Umgesetzt in `mqtt_handler.parse_mqtt_publish`: standardkonformer MQTT-3.1.1-Decoder (Remaining Length als Varint + 2-Byte-Topic-Name + Packet Identifier nur bei QoS>0, was bei unserem Abonnement mit QoS 0 nicht vorkommt). Er ist die primäre Methode; ist das Ergebnis nicht eindeutig, greift die frühere Textheuristik als Sicherheitsnetz, da kein vollständiger Hex-Dump aller Grenzfälle vorliegt. |
| 7 | Weitere Topics/Ereignisse? | ✅ Geklärt | Unter `ctl/{ref}/#` nur `status`. Separat gibt es `pro/v1/get/usr/{aws_mqtt_user}/feedback` (Quittung mit `orderId`), das die Integration auswertet. |
| 8 | Bedeutung von `tmm`/`tmx` | ✅ Geklärt | minimale/maximale Temperatur. |
| 9 | `;` am Ende von `value`? | ✅ Geklärt | Die App führt `value.slice(0,-1)` aus (Abschlusszeichen); es wird vor `json.loads` entfernt. |
| 10 | Verlangt der Broker das MQTT-`password`? | 🟢 Offen | Gering (SigV4 steckt in der URL). Die App nutzt das AWS-IoT-SDK (Zugangsdaten in der Signatur). |
| 11 | Heartbeat auf Anwendungsebene? | ✅ Geklärt | Nein; SDK/WebSocket übernehmen das Keepalive. Unser WS-Ping (30 s) genügt. |
| 23 | Genaue Form der Nutzdaten im Topic `feedback` (flach `{orderId,ctl,...}` oder in `value` verpackt wie bei `status`?) | ✅ Geklärt | Flach, wie das JS vermuten ließ. Beispiel mit Platzhaltern: `{"version":1,"orderId":"<ORDER_ID>","type":null,"sender":"CTL","ctl":"<INST_REF>","app":"<aws_mqtt_user>","command":"","value":"OK","qos":0,"retain":0,"ws":"<…>"}`. `status_parser.parse_feedback_payload` prüft diese Form zuerst; der Rückfall auf eine verschachtelte Form bleibt als Absicherung bestehen. |
| 24 | Bedeutung der Werte von `vv` (Lüfterstufe) | ✅ Geklärt | In der **echten** Installationskomponente gefunden (nicht in der Demo-/Storybook-Seite der UI-Komponenten, deren Beispielwerte `{"auto":["A"]}`/`{"manual":["1","2","3"]}` **erfundene Demodaten** sind). Echte Definition: `fanGroups:[{key:"auto",values:{4:"A"}},{key:"manual",values:{1:"1",2:"2",3:"3"}}]` und `hasFanMode(){return "0"!=this.vv}`. Abbildung: `vv="0"` → kein Lüftermodus, `"1"/"2"/"3"` → manuelle Stufe, `"4"` → Automatik („A“). |
| 25 | Feldname der Luftfeuchte im Zonen-JSON | ✅ Geklärt | Das Zonen-JSON kann **beide** Felder `hm` und `hum` enthalten. `hum` meldet `"0"`, `hm` trägt den Messwert – denselben, den die offizielle Web-App über `getHumidity(){return this.hm}` anzeigt. Auf Anlagen ohne Feuchtemessung stehen beide Felder auf `"0"`; dort wirkte `hum` nur scheinbar wie das richtige Feld, was zwischenzeitlich zu einer falschen Korrektur führte. Regel in `status_parser.parse_humidity`: `hm` hat Vorrang, `hum` dient als Ersatz, ein Wert von 0 gilt als unbekannt (`None`). |
| 26 | Bedeutung des Felds `sv` im Zonen-JSON | ✅ Geklärt | `sv` = `"0"`/`"1"` = Fußbodenheizung aus/ein (zu unterscheiden von der statischen Fähigkeit `s`). Beleg: `setFloor(e){this.sv=e?"1":"0"}`, aufgerufen von `toggleRadiatingFloor:function(e){this.status.setFloor(e),...}` auf dem Live-Zustandsobjekt der Zone (dasselbe Objekt wie `e`/`m`/`tc`). Eine frühere Suche hatte `sv` nicht gefunden und das Feld vorschnell als ungeklärt eingestuft, während andere Dokumente es noch als bestätigt führten. Lehre: „Im Bundle nicht gefunden“ erst nach einer Wiederholung mit Varianten (`.sv`, `setFloor` usw.) als Ergebnis werten, bevor eine bereits dokumentierte Gewissheit herabgestuft wird. |
| 27 | Form der Nutzdaten für Timer (`temporizer`) und Zeitprogramme | 🟡 Teilweise geklärt | **Timer:** `value` ist `"30"`/`"60"`/`"90"`/`"120"` (Minuten bis zum Ausschalten) oder `"0"` (aus); die App bietet ihn nur bei `tz` = `"1"` an. **Programme:** Lesen mit `programs` (`value:""`), Anlegen/Ändern mit `update-program` und Löschen mit `delete-program`, jeweils mit dem Programmobjekt als `value`; höchstens 3 je Zone, nur bei `hp` = `"1"`. Die frühere Annahme, es gebe keinen Schreibbefehl, war falsch. **Offen:** genaue Felder des Programmobjekts und der Antwort von `programs`; vor einer Umsetzung mit echtem Datenverkehr bestätigen. |

---

## 3. HTTP und Erkennung

| # | Frage | Beleg | Hypothese | Prüfweg | Risiko |
|---|---|---|---|---|---|
| 12 | Welche Felder hat ein `device` außer `reference`/`name`? | Rückfälle auf `rf`/`id` im Code deuten auf Unsicherheit | Könnte Typ, Fähigkeiten, Online-Status enthalten | 🟡 Gestützt, nicht abgeschlossen: Es gibt keinen Roh-Dump von `/devices`. Im Betrieb ordnen die Entitäten jede MQTT-Aktualisierung korrekt ihrem Gerät zu, die Rückfallkette löst das Feld also richtig auf. Das JS (`updateDevice`/`deleteDevice`) nutzt durchgehend `e.reference`, nie `rf`/`id` – diese Aliase wirken defensiv und wurden nie beobachtet. Weitere, heute ungenutzte Felder (Typ, Fähigkeiten) sind nicht ausgeschlossen. | 🟡 Mittel |
| 13 | Heißt das Feld `reference` oder `rf`/`id`? | Rückfallkette | `reference` | 🟡 Gestützt: wie #12 – das JS der App nutzt in den Operationen, die ein Gerät identifizieren (`updateDevice`, `deleteDevice`), immer `reference`. Ohne rohe HTTP-Antwort bleibt der Rückfall im Code vorsichtshalber bestehen. | 🟡 Mittel |
| 14 | Was bewirkt `validated=1`? | Feste Query in `get_installations` | Filtert validierte Installationen | ✅ Geklärt: Die App ruft `updateInstallation({...,validated:1,...})` nach dem ersten erfolgreich empfangenen `status` einer Installation auf. `validated` markiert also Installationen, die mindestens einmal Verbindung bestätigt haben; der Filter schließt nie verbundene Installationen aus (z. B. frisch angelegte, noch nicht gekoppelte). | 🟢 Gering |
| 15 | Kann ein Konto mehrere `Location`s haben? Der Code nutzt nur die erste. | `async_setup_entry` | Ja; weitere gingen verloren | Mit einer `Location` funktioniert der Ablauf. **Abgrenzung:** Es wird bewusst nur die erste `Location` genutzt; mehrere Standorte bleiben außen vor, solange niemand sie braucht. | 🟢 Gering (akzeptiert) |
| 16 | Gültigkeitsdauer des `access_token` | ✅ Geklärt | Die Anmeldung liefert `expires_at` (Unix-Sekunden). Die App erneuert per Timer, die Integration erst bei `401`. Möglichkeit: vorausschauende Erneuerung. |
| 17 | Akzeptiert `command:"temp"` einen String als `value`? | ✅ Geklärt | String (`setTemp` sendet `""+i`). |
| 18 | HTTP-Endpunkt zum Lesen des Zustands? | ✅ Geklärt | Gibt es nicht. Die App nutzt dasselbe Muster wie die Integration: `command:"status"` per HTTP (`POST /send/instruction`) senden und die Antwort per MQTT (`.../status`) abwarten. |
| 19 | Ratenbegrenzung bei `/send/instruction`? | 🟡 Offen | Unbekannt; `VUE_APP_OUTSERVICE_MILISECOND=5000` ist ein Zeitlimit der App, keine Ratenbegrenzung. |

---

## 4. Verbindung / Infrastruktur

| # | Frage | Stand | Antwort |
|---|---|---|---|
| 20 | Führt eine geteilte `client_id` zu Rauswürfen? | ✅ Geklärt | Ja, das würde sie: Die App nutzt eine **eindeutige** clientId `mqtt-client_{accessKeyId}_{ts}`. Die Integration erzeugt ebenfalls je Verbindung eine eindeutige clientId (`build_client_id`) und verdrängt die offizielle App dadurch nicht. |
| 21 | Ändert sich `aws_mqtt_user` beim Erneuern? | ✅ Geklärt | `aws_mqtt_user` ist die (stabile) Konto-ID und wird NICHT als MQTT-clientId verwendet. Kein Zusammenhang mit #20. |
| 22 | Gültigkeitsdauer der AWS-Zugangsdaten | ✅ Geklärt | Sie enthalten `aws_expires_at` (Unix-Sekunden). Die App erneuert **vor** dem Ablauf per `setTimeout` (`getMqttExpirationTime`), nicht erst nach einem Abbruch. Ohne diese Vorausschau lebt eine Verbindung genau so lange wie das Sitzungstoken; dann trennt AWS sie einseitig (`code=None, msg=None` im Log) – das erklärt regelmäßige, nicht zufällige Abbrüche. In der Lücke bis zur Wiederverbindung können `feedback`-Quittungen verloren gehen (#23). Umsetzung: `mqtt_handler` plant einen `threading.Timer`, der die Sitzung etwa 60 s vor `aws_expires_at` gezielt schließt und **ohne** Wartezeit neu verbindet (die Zugangsdaten sind dann frisch). |

---

## 5. Wie man Antworten sicher gewinnt

- Bevorzugt die **offizielle Web-App** mit den Entwicklerwerkzeugen des Browsers (Netzwerk + WebSocket-Frames) an einem eigenen Testkonto nutzen.
- Nutzdaten mitschneiden, Token/Zugangsdaten vor dem Speichern **schwärzen** und daraus bereinigte Fixtures ableiten.
- Für rohes MQTT: vorübergehend ein DEBUG-Log ergänzen, das die Bytes des PUBLISH-Frames als Hex ausgibt – **in einer Laborumgebung**, nie in fremden Produktivumgebungen.
- Keine schreibenden Befehle an eine genutzte Anlage senden; eine Zone/Uhrzeit wählen, in der eine Änderung harmlos und umkehrbar ist.
- Mitschnitte und daraus abgeleitete Beispiele nie mit echten Referenzen, Namen oder Messwerten in das Repository übernehmen (siehe `CLAUDE.md` §6.0).

---

## 6. Zusammenfassung

- ✅ **Aus dem offiziellen Bundle geklärt** (`docs/protocol-findings.md`): #1–5, #7–9, #11, #14, #16–18, #20–22, #24, #26.
- ✅ **Mit echten Nachrichten geklärt:** #23 (flache `feedback`-Nutzdaten), #25 (Feuchtefeld, `hm` mit Vorrang) und #6 (Format des PUBLISH-Frames, abgeleitet aus der Übereinstimmung von Topic-Länge und beobachtetem Zeichen – ohne Hex-Dump).
- 🟡 **Offen (geringfügig / erfordern echte Mitschnitte):** #10 (MQTT-Passwort), #19 (Ratenbegrenzung). #12/#13 (HTTP-Felder von `/devices`) sind indirekt gestützt, aber nicht abgeschlossen.
- 🟢 **Bewusste Abgrenzungen:** #15 (nur eine `Location`).
- 🟡 **Teilweise geklärt:** #27 (Timer und Programme: Befehle bekannt, Programmobjekt noch zu bestätigen).
- ⚫ **Ohne Ergebnis untersucht:** Die Felder `vf`, `hmh`, `mh`, `ps`, `p` kommen in echten Zonendaten vor, werden aber an keiner Stelle des JS-Bundles per Eigenschaftszugriff gelesen. Die Live-Zustandsklasse der Zone (`class te`) führt im Konstruktor `Object.assign(this,t)` aus und kopiert damit **alle** Rohfelder auf die Instanz – die Felder existieren also als `this.vf`/`this.hmh` usw., doch kein Getter oder Setter der Klasse (oder an anderer Stelle im Bundle) liest sie je beim Namen. Das klärt nicht ihre *Bedeutung*, stützt aber, dass es keine übersehene Verwendung gibt: Die offizielle App empfängt und ignoriert sie ebenso wie die Integration. Nach der Regel, keine Felder zu erfinden, bleiben sie uninterpretiert, bis äußere Belege auftauchen (andere Bundle-Version oder Netzwerkanalyse eines Ablaufs, in dem sie genutzt werden).

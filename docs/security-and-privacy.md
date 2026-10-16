# Sicherheit und Datenschutz

> Statische Analyse. Es wurden keine echten Anfragen ausgeführt. Es werden keine Geheimnisse wiedergegeben.

---

## 1. Sensible Daten und ihr Speicherort

| Daten | Ort | Dauerhaft gespeichert | Risiko | Gewissheit |
|---|---|---|---|---|
| `email` | `entry.data["email"]` | Config Entry (Datenträger, `.storage/core.config_entries`) | Gering | Bestätigt |
| `password` | Nur im Speicher während Anmeldung/Reauth (`config_flow.py`); wird **nie** in `entry.data` gespeichert | Nein | 🟢 Gering | Bestätigt |
| `refresh_token` | `entry.data["refresh_token"]` (wechselt bei jeder Erneuerung; die neue Version wird über `on_tokens_refreshed` gespeichert) | Config Entry (Datenträger) | 🟡 Mittel (erlaubt eine Sitzungserneuerung ohne Passwort) | Bestätigt (`api.py`, `__init__.py`) |
| `access_token` | Nur im Speicher (`MySairAPI.access_token`); wird bei jedem Start aus dem `refresh_token` neu erzeugt | Nein | 🟢 Gering | Bestätigt |
| AWS-IoT-Zugangsdaten (`aws_access_key_id`, `aws_secret_access_key`, `aws_security_token`) | `MySairAPI.aws_credentials` | Nur Speicher | 🟡 Mittel (temporär) | Bestätigt (`refresh_aws_credentials`) |
| Signierte MQTT-URL (enthält Zugangsdaten + Signatur) | Lokale Variable in `_run` | Nur Speicher; wird **nirgends geloggt** (nur `host`) | 🟢 Gering | Bestätigt (`mqtt_handler.py`) |

**Im Repository sind keine Geheimnisse fest hinterlegt.** Fest eingetragen sind nur der öffentliche Host `https://api.mysair.es/v1` (kein Geheimnis) und ein Ersatzwert für das Feld `app` in `send_zone_command`/`send_installation_command` (Client-Kennung, kein Geheimnis). **Bestätigt.**

---

## 2. Risiken durch Logging

| Log | Möglicherweise sensibler Inhalt | Stufe / Maßnahme | Ort |
|---|---|---|---|
| MQTT-`client_id` (`mqtt-client_{access_key}_...`) | `aws_access_key_id` im Klartext | ✅ wird mit `_redact_client_id` maskiert, Log auf `DEBUG` | `mqtt_handler.py` |
| `resp.text` in HTTP-Fehlerlogs (login, refresh, AWS-Zugangsdaten, locations/installations/devices, send_instruction) | Fehlertext des Backends; am heikelsten beim Endpunkt für AWS-Zugangsdaten | ✅ mit `_truncate()` auf 200 Zeichen gekürzt | `api.py` |
| `AWS credentials received for user {aws_mqtt_user}` | MQTT-Benutzername (kein kritisches Geheimnis) | `DEBUG` | `api.py` |
| `Sending instruction/command: {instruction}` | `app` (=aws_mqtt_user), Referenzen; kein Token | `DEBUG` (hohe Frequenz: jeder Befehl und jede periodische Abfrage) | `api.py` |
| `MQTT message received [:200]` | Zonenzustand (Nutzungsdaten) | `DEBUG` (häufigstes Log des Projekts: eines je MQTT-Nachricht) | `mqtt_handler.py` |
| `Login {email}` | E-Mail-Adresse | `INFO` (Lebenszyklus-Ereignis bei Einrichtung/Reauth) | `api.py` |

**Positiv:** In keinem Log erscheinen `password`, `access_token`, `aws_secret_access_key`, `aws_security_token` oder `aws_access_key_id` vollständig. **Bestätigt.**

**Regeln für Logs:**
- ✅ Kein Teil der signierten URL wird geloggt (auch nicht gekürzt); nur der `host`.
- ✅ Häufige Logs (empfangene MQTT-Nachrichten, gesendete Befehle, Bestätigungen, Nutzeraktionen) stehen auf `DEBUG`. Lebenszyklus-Ereignisse (Anmeldung, Token-Erneuerung, MQTT-Verbindung/-Trennung, Start/Stopp, angelegte Entitäten) stehen auf `INFO`.
- 🟡 Offen: ein allgemeiner Log-Filter, der Muster wie `X-Amz-*` oder `Bearer <...>` in künftigen, nicht eigens geprüften Logs maskiert.
- ⚠️ DEBUG-Logs enthalten Installations- und Zonenreferenzen sowie Messwerte. Vor dem Teilen (z. B. in einem Issue) schwärzen.

---

## 3. Speicherung der Zugangsdaten

1. ✅ Das Passwort wird nicht gespeichert. `config_flow.py` speichert nur `email` + `refresh_token`; `__init__.py` erneuert die Sitzung bei jedem Start mit `MySairAPI.refresh_tokens()` (`PUT /user/refreshtokens`, ohne Passwort). Das `refresh_token` wechselt bei jeder Erneuerung und wird über den Callback `on_tokens_refreshed` (`api.py`) → `_persist_refresh_token` (`__init__.py`) erneut gespeichert.
   - **Automatische Migration:** Enthält eine ältere Config Entry noch `password`/`access_token`, entfernt `async_setup_entry` beides beim ersten erfolgreichen Start.
   - Das `access_token` wird nicht gespeichert; es ist kurzlebig und entsteht bei jedem Start neu im Speicher.
2. ✅ **Reauth:** Wird das `refresh_token` ungültig, löst `async_setup_entry` `ConfigEntryAuthFailed` aus, und Home Assistant bietet die erneute Anmeldung mit Passwort an, ohne den Eintrag zu löschen. Netz- und Backend-Fehler führen zu `ConfigEntryNotReady` (HA versucht es später erneut). Stirbt die Sitzung im laufenden Betrieb, wird die erneute Anmeldung sofort angefordert.
3. ✅ AWS-Zugangsdaten gelten als kurzlebig: Sie werden vor dem Ablauf und bei jeder Wiederverbindung erneuert (`docs/protocol-findings.md` §6b).
4. 🟡 Das `refresh_token` bleibt ein langlebiges Geheimnis auf dem Datenträger. Liegt `.storage` nicht auf einem verschlüsselten Datenträger, kann es lesen, wer Zugriff auf das Dateisystem von Home Assistant hat. Das ist eine Eigenschaft des Config-Entry-Modells von Home Assistant, nicht dieser Integration.

---

## 4. Risiken durch Reverse Engineering und Abhängigkeit von einem externen Dienst

| Risiko | Beschreibung | Schwere |
|---|---|---|
| Undokumentierte API | `api.mysair.es/v1` kann Pfade, Felder oder Authentifizierung ohne Ankündigung ändern → Ausfall ohne Fehlermeldung | 🟡 Mittel |
| Änderungen am MQTT-Format | Standardkonformer Decoder mit Rückfall-Heuristik; unbekannte Felder werden toleriert. Strukturänderungen können den Zustand trotzdem unbrauchbar machen | 🟡 Mittel |
| Nutzungsbedingungen | Inoffizielle Nutzung der API/Infrastruktur von MySair; ein Verstoß gegen Nutzungsbedingungen ist nicht ausgeschlossen | 🔎 rechtlich, außerhalb des technischen Rahmens |
| Ratenbegrenzung | Die Abfrage alle 120 s je Installation könnte Grenzen des Backends auslösen | 🔎 Unbekannt |
| Signatur mit lokaler Uhr | Weicht die Uhr des Hosts stark ab, schlägt die SigV4-Signatur fehl | 🟡 Mittel |

---

## 5. Netzwerkoberfläche

- Nur ausgehende Verbindungen: HTTPS zu `api.mysair.es` und WSS zu `*.iot.<region>.amazonaws.com`. **Bestätigt.**
- Keine eingehenden Ports.
- TLS-Prüfung: `requests` und `websocket-client` prüfen Zertifikate standardmäßig; der Code schaltet die Prüfung **nicht** ab. ✅ **Bestätigt** (kein `verify=False`, kein unsicheres `sslopt`).

---

## 6. Sicherheits-Checkliste für künftige Änderungen

- [ ] Nie `password`, Token, `aws_secret_access_key`, `aws_security_token` oder vollständige signierte URLs loggen.
- [ ] Keine Mitschnitte (`.har`, MQTT-Dumps) mit echten Zugangsdaten committen.
- [ ] Keine Daten realer Anlagen oder Personen ins Repository übernehmen: keine Namen, E-Mail-Adressen, Referenzen (`MYS…`), Zonennamen, Messwerte, Firmware-Stände oder Datumsangaben von Mitschnitten. Testdaten sind generisch (siehe `CLAUDE.md` §6).
- [ ] `.gitignore` deckt `secrets.*`, `*.har`, `.env` und Mitschnittformate ab.
- [ ] Neue Felder in `diagnostics.py` mit `async_redact_data` schwärzen.
- [ ] Während der Entwicklung keine Befehle an echte Anlagen senden (siehe `CLAUDE.md`).
- [ ] Neue Logs von MQTT-Nutzdaten nicht unnötig personenbezogen gestalten.

---

## 7. Aktueller Stand des Repositorys

**Bestätigt:** Das versionierte Repository enthält keine Zugangsdaten, Token oder Mitschnitte. `.gitignore` schließt Editor- und Build-Dateien sowie typische Mitschnittformate (`*.har`, `*.pcap`, `*.pcapng`, `*.mitm`, `.env*`) aus.

# Silentwind Windgenerator für Home Assistant

Eigene Integration („Custom Integration“) für den Wind-/Solar-Laderegler **Silentwind Hybrid Boost**.
Der Regler kommt vom Hersteller Win-Power. Die Integration verbindet sich direkt per **Bluetooth** mit dem
eingebauten Modul, ohne ESP32 und ohne Verkabelung. Sie spricht dasselbe Protokoll wie die
Silentwind-Android-App (Version 1.3.3).

## Was sie kann

- **Messwerte:** Batteriespannung, Ladestrom und -leistung; Wind Spannung, Strom und Leistung;
  optional Solar; Strom der Lastausgänge 1 und 2; Status (Normal / Ableiten / Sturmbremse / manuelle Bremse)
- **Meldungen:** Batterie Über- und Unterspannung, Last überlastet, Last-Kurzschluss, Dumpload aktiv, Sturmbremse
- **Schalter:**
  - **Bremse:** die manuelle Bremse wie in der App
  - **Last 1 / Last 2:** schaltet wie die App auf „Dauernd an“ bzw. „Dauernd aus“
  - **App-Zugriff:** gibt Bluetooth für die Hersteller-App frei (siehe unten)
- **Einstellungen** (anfangs ausgeblendet): Ladeschlussspannung, Sturmbremse-Strom, Über- und
  Unterspannung, Low Wind Boost, Lichtsteuerungs-Spannungen, Lastzeiten, Last-Betriebsarten.
  Jede Änderung wird zur Kontrolle zurückgelesen.
- Die Funktion „Werkseinstellungen“ der App ist absichtlich **nicht** eingebaut.

## Installation (Home Assistant OS)

1. Integration installieren, auf einem der beiden Wege:
   - **HACS:** *HACS → ⋮ → Benutzerdefinierte Repositories* → `https://github.com/netxxl/ha-silentwind`,
     Typ „Integration“. Danach „Silentwind Windgenerator“ herunterladen.
   - **Von Hand:** Den Ordner `custom_components/silentwind` nach `/config/custom_components/silentwind`
     kopieren, z. B. mit dem Add-on „Samba share“ oder „Studio Code Server“.
2. Home Assistant neu starten.
3. **Silentwind-App auf dem Handy schließen** und am besten auch Bluetooth am Handy ausschalten.
   Das Modul nimmt nur eine Verbindung an. Home Assistant hält die Verbindung standardmäßig dauerhaft,
   die App kommt dann nicht mehr dran (siehe Optionen).
4. *Einstellungen → Geräte & Dienste → Integration hinzufügen → „Silentwind Windgenerator“*.
   Die Integration sucht etwa 12 Sekunden nach Geräten mit dem Namen der App, also „Slientwind“
   (sic) oder „Win-Power“. Oft meldet Home Assistant den Regler schon vorher von selbst als
   „Neues Gerät gefunden“, weil er auch BLE-Werbung sendet. Verbunden wird trotzdem über Classic.
5. Den Regler auswählen (er meldet sich als „win-power“). Eine **PIN** fragt er
   normalerweise nicht ab; das Feld wird nur benutzt, falls doch.
   **Kanal** auf 0 lassen. Die Integration koppelt den Regler selbst und sucht den richtigen Kanal.

Der Home-Assistant-Rechner muss Bluetooth haben und in Reichweite sein, etwa 10 m.
**Ein ESPHome-Bluetooth-Proxy hilft hier nicht**, denn Proxys können nur BLE, der Regler funkt Classic.
Ist die Strecke zu weit, hilft ein USB-Bluetooth-Stick an einem Verlängerungskabel.

### Wenn es nicht klappt

Die Fehlermeldung steht direkt im Dialog. Mehr Details gibt es so: *Einstellungen → System → Protokolle*,
vorher in `configuration.yaml` Folgendes eintragen:

```yaml
logger:
  logs:
    custom_components.silentwind: debug
```

- **Regler taucht nicht in der Liste auf:** MAC-Adresse von Hand eintippen. Sie steht in der
  Bluetooth-Geräteliste des Handys, wenn es einmal mit dem Regler gekoppelt war.
- **„pairing failed“:** Am Handy den Regler „entkoppeln“ und Bluetooth aus. Falls er doch eine PIN will: `1234` oder `0000`.

Ab Home Assistant 2026.3 zeigt die Integration ihr eigenes Icon (Ordner `brand/`).

## Optionen

| Option | Bedeutung |
|---|---|
| Abfrageintervall | Sekunden zwischen zwei Abfragen (Standard 20) |
| Dauerhaft verbunden | An (Standard): Verbindung bleibt offen und wird nur neu aufgebaut, wenn sie abreißt. Aus: nach jeder Abfrage trennen, damit die Hersteller-App zwischendurch verbinden kann |
| Solareingang | Solar-Sensoren anlegen |
| Dauer App-Zugriff | Minuten, die der Schalter „App-Zugriff“ Home Assistant pausiert (Standard 10) |

## Hersteller-App nutzen

Der Regler nimmt nur eine Bluetooth-Verbindung an. Soll die Silentwind-App ran, den Schalter
**App-Zugriff** einschalten:

- Home Assistant trennt sofort und fragt nicht mehr ab. Messwerte und Schalter sind solange „nicht verfügbar“.
- Jetzt mit der App verbinden. Das geht auch aus der Ferne, z. B. mit einem alten Handy beim Regler,
  das per scrcpy, AnyDesk o. Ä. ferngesteuert wird.
- Nach der eingestellten Dauer, oder wenn du den Schalter ausschaltest, verbindet sich Home Assistant
  wieder. Vorher die App schließen, sonst schlägt die Verbindung fehl, bis die App den Regler freigibt.

## Protokoll

Alle Rahmen sind 42 Byte lang, in beide Richtungen, über Bluetooth Classic SPP (UUID 1101):

| Bytes | Inhalt |
|---|---|
| 0–3 | `EB 90 EB 90` |
| 4 | Adresse (0) |
| 5 | Typ |
| 6–7 | `00 20` |
| 8–39 | Nutzdaten |
| 40–41 | Summe der Bytes 4–39, 16 Bit, Big Endian |

| Anfrage | Antwort | Bedeutung |
|---|---|---|
| 0 | 6 | Messwerte |
| 1 | 7 | Parameterblock lesen |
| 3 | 9 | **Werkseinstellungen**, wird nicht verwendet |
| 4 + Parameterblock | – | Parameterblock schreiben |

**Messwerte** (Typ 6):
- Byte 8 Statusbits: 0x01 Überspannung, 0x02 Unterspannung, 0x04 Last überlastet,
  0x08 Kurzschluss, 0x10 Ableiten, 0x20 Sturmbremse, 0x40 manuelle Bremse
- Byte 9: Bit 0 Last 1 an, Bit 1 Last 2 an
- 9-Bit-Werte (Byte n << 1 | Byte n+1 >> 7):
  - Batteriespannung ab Byte 10
  - Wind-Spannung ab 12, Solar-Spannung ab 14
  - Wind-Strom ab 22, Solar-Strom ab 24
- 18-Bit-Leistung: Wind ab Byte 16, Solar ab Byte 19
- Laststrom: Last 2 in Byte 26, Last 1 in Byte 27, jeweils in 0,1 A

Die Faktoren hängen von Systemspannung und Nennleistung ab. Beide stehen in Parameterbyte 8.

**Parameterblock** (Typ 7):
- 8: System
- 9: Bit 2 = manuelle Bremse
- 10: Ladeschluss
- 11: Sturmbremse-Strom
- 12/13: Unterspannung aus/ein
- 14: Überspannung
- 15/16: Lichtsteuerung ein/aus
- 22: Low Wind Boost
- 23/26: Betriebsart Last 1/2 (One-Hot: 16 = dauernd an, 32 = dauernd aus)
- 24/25 bzw. 27/28: Lastzeiten in Stunden

## Entwicklung

```
python tests/test_protocol.py
```

## Lizenz

MIT, siehe [LICENSE](LICENSE).

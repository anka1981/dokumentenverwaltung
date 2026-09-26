# Änderungen

## 1.2.2

- Neue Einstiegsadresse (`…/app/`): Die Home-Assistant-App zeigte weiter eine alte,
  gespeicherte Kopie der Oberfläche (Einstellungen ließen sich nicht öffnen, Kamera
  öffnete die Fotoauswahl). Unter der neuen Adresse lädt sie die aktuelle Version.

## 1.2.1

- Diagnose: Jeder Aufruf der Oberfläche wird mit aufrufender App (HA-App/Browser)
  protokolliert, um Probleme in der Home-Assistant-App einzugrenzen.

## 1.2.0

- **Verlustfreie Kompression:** Neue Dokumente werden beim Import verkleinert, vorhandene
  einmalig im Hintergrund. PDFs (auch eingebettete Scans), JPEG, PNG, TIFF und
  Office-Dateien bleiben normale Dateien; jede Fassung wird vor dem Ersetzen auf
  identische Bildpunkte, identischen Text und gleiche Seitenzahl geprüft.
- Speicherplatz und Ersparnis in den Einstellungen ⚙.

## 1.1.3

- Abruf aus Google Drive läuft im Hintergrund; die Oberfläche bleibt bedienbar, auch
  wenn die Texterkennung auf dem Raspberry Pi länger dauert.
- Anfragen haben ein Zeitlimit mit verständlicher Meldung; beim Seitenwechsel erscheint
  eine Ladeanzeige statt der alten Seite.
- Diagnose: Die Seite schreibt Version, Verbindungsart, Kamera-Verfügbarkeit und Fehler
  ins Add-on-Protokoll.

## 1.1.2

- Nach einem Update lädt die Home-Assistant-App jetzt sicher die neue Oberfläche
  (vorher konnte sie die alte Version aus dem Zwischenspeicher weiter anzeigen).
- Versionsnummer und Verbindungsart (http/https) unten in den Einstellungen ⚙.

## 1.1.1

- Absturz beim Import behoben, wenn im erkannten Text IBAN-ähnliche Zeichen über einen
  Zeilenumbruch gehen („Error 500“ beim Abruf aus Google Drive).
- Eine fehlerhafte Datei bricht den Google-Drive-Abruf nicht mehr ab; Fehler werden als
  verständliche Meldung angezeigt statt „Error 500“.
- Kamera: Kann sie nicht starten, steht der Grund jetzt im Scan-Fenster (z. B.
  unverschlüsselte Verbindung oder fehlende Kamera-Berechtigung), statt still die
  Fotoauswahl zu öffnen.

## 1.1.0

- **Kamera direkt in der Seite:** „Seite fotografieren“ öffnet jetzt die Kamera auch in der
  Home-Assistant-App (vorher öffnete sich dort nur die Fotoauswahl). Mehrere Seiten
  nacheinander aufnehmen, dann „Fertig“.
- **Import aus Google Drive:** Mit der Google-Drive-App in den Ordner „Dokumente-Eingang“
  scannen – neue Dateien landen automatisch im Eingang (Einstellungen ⚙).

## 1.0.2

- Fertige Images für Raspberry Pi und PC werden auf GitHub gebaut; Home Assistant lädt sie
  nur noch herunter. Der Build auf dem Pi (der dort abstürzte) entfällt, die Installation
  geht deutlich schneller.

## 1.0.1

- Build auf dem Raspberry Pi repariert: Multi-Arch-Basisimage `base-debian:trixie`
  statt der alten architekturabhängigen Images (`build.yaml` entfällt).
- Backups: Das Add-on wird während der Sicherung kurz angehalten, damit die Datenbank
  in einem sauberen Zustand gesichert wird.

## 1.0.0

- Erste Version: Upload und Handy-Scan, Texterkennung mit Tesseract, Verschlagwortung,
  Ablagevorschlag mit Bestätigung, lernende Vorschläge, Volltextsuche, Ordnerverwaltung.

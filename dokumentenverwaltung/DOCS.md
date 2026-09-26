# Dokumentenverwaltung

Dokumente per Datei oder Handy-Scan einspielen. Der Text wird erkannt, das Dokument wird
verschlagwortet, und es gibt einen Vorschlag, in welchen Ordner es gehört. Abgelegt wird erst,
wenn du den Vorschlag bestätigst oder einen anderen Ordner wählst.

Die Texterkennung (Tesseract, Deutsch und Englisch) läuft komplett auf deinem Home-Assistant-
Gerät. Das ist kostenlos, ohne Konto, ohne Internet und ohne Seitenlimit.

## Bedienung

Nach dem Start erscheint **Dokumente** in der Seitenleiste von Home Assistant, im Browser
(z. B. über deine Home-Assistant-Adresse) genauso wie in der Home-Assistant-App auf dem Handy.

- **Scannen:** öffnet die Handykamera. Seite für Seite fotografieren, dann „Hochladen &
  erkennen“. Mehrere Seiten werden ein PDF und automatisch aufrecht gedreht.
- **Datei:** PDF, Word, LibreOffice, Excel, PowerPoint, RTF, E-Mails (.eml), Bilder.
- **Eingang:** neue Dokumente mit Ordnervorschlag, Sicherheit und Begründung. Titel, Datum,
  Absender und Schlagwörter lassen sich prüfen und ändern, dann **Bestätigen & ablegen**.
- **Archiv:** Volltextsuche, Filter nach Ordner und Schlagwort.
- **Ordner:** Struktur und Erkennungsbegriffe bearbeiten.

Jede Bestätigung und jede Korrektur verbessert die nächsten Vorschläge.

## Optionen

| Option | Bedeutung |
|---|---|
| `passwort` | Nur nötig, wenn du den Port unter „Netzwerk“ freigibst und das Add-on **ohne** Home Assistant direkt aufrufen willst (`http://<raspi>:<port>`, beliebiger Benutzername). Über die Seitenleiste meldet Home Assistant dich an. Ohne Passwort wird direkter Zugriff abgewiesen. |
| `ablage_im_share` | `false` (Standard): Datenbank und Dateien liegen im geschützten Add-on-Speicher. `true`: sie liegen unter `/share/dokumentenverwaltung`. Dann siehst du die abgelegten Dateien mit dem Samba-Add-on im Ordner `share` auf dem PC. |

Beim Umschalten von `ablage_im_share` werden vorhandene Daten **nicht** automatisch
verschoben. Stelle das am besten vor dem ersten Dokument ein.

## Wo liegen die Daten?

Auf dem Raspberry Pi, nirgendwo sonst:

| Pfad (im Add-on) | Inhalt |
|---|---|
| `dms.sqlite` | Datenbank: Metadaten, erkannter Text, Schlagwörter, Suchindex, Gelerntes |
| `ablage/Eingang/` | noch nicht bestätigte Dokumente |
| `ablage/<Ordner>/<Unterordner>/<Datum>_<Titel>.pdf` | abgelegte Dokumente |
| `vorschau/` | Vorschaubilder |

Diese Dateien liegen in `/data` (Standard) oder in `/share/dokumentenverwaltung`.

## Backup

Die Backups von Home Assistant (Einstellungen → System → Backups) enthalten die Daten des
Add-ons automatisch, bei `ablage_im_share: true` den Ordner `share`. Speichere die Backups
unbedingt auch **außerhalb** des Raspberry Pi, z. B. mit dem Google-Drive-Backup-Add-on
oder auf einem Netzlaufwerk. Die SD-Karte ist sonst der einzige Speicherort deiner Dokumente.

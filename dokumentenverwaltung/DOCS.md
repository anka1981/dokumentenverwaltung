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

## Scannen mit der Google-Drive-App (automatischer Import)

Die Scanfunktion der Google-Drive-App schneidet Seiten automatisch zu und entzerrt sie.
Die Dokumentenverwaltung kann einen Drive-Ordner regelmäßig abholen:

1. **Google Cloud Console** (dasselbe Projekt wie für das Backup) → **APIs und Dienste →
   Anmeldedaten → Anmeldedaten erstellen → OAuth-Client-ID** → Anwendungstyp
   **Desktop-App**, Name z. B. `Dokumentenverwaltung` → **Erstellen**. Client-ID und
   Clientschlüssel kopieren. Die Google Drive API muss aktiviert sein.
2. In **Dokumente → ⚙ Einstellungen** Client-ID und Clientschlüssel eintragen, **Speichern**.
3. **Mit Google verbinden** (am besten am PC), Konto wählen, bei „Google hat diese App nicht
   überprüft“ **Erweitert → Weiter zu …**, Zugriff erlauben. Der Browser zeigt danach
   „Seite nicht erreichbar“ – die **komplette Adresse** aus der Adressleiste
   (`http://127.0.0.1:8765/?…`) kopieren, einfügen, **Verbindung herstellen**.
4. In der Google-Drive-App mit **＋ → Scannen** scannen und im Ordner
   **Dokumente-Eingang** speichern (legt die Dokumentenverwaltung beim ersten Abruf an).

Abgeholt wird alle 10 Minuten (einstellbar) oder sofort mit „Aus Google Drive abrufen“ im
Eingang. Importierte Dateien werden in Drive nach `Dokumente-Eingang/importiert` verschoben;
nicht unterstützte Dateien bleiben liegen und werden in den Einstellungen gemeldet.

## Optionen

| Option | Bedeutung |
|---|---|
| `passwort` | Nur nötig, wenn du den Port unter „Netzwerk“ freigibst und das Add-on **ohne** Home Assistant direkt aufrufen willst (`http://<raspi>:<port>`, beliebiger Benutzername). Über die Seitenleiste meldet Home Assistant dich an. Ohne Passwort wird direkter Zugriff abgewiesen. |
| `ablage_im_share` | `false` (Standard): Datenbank und Dateien liegen im geschützten Add-on-Speicher. `true`: sie liegen unter `/share/dokumentenverwaltung`. Dann siehst du die abgelegten Dateien mit dem Samba-Add-on im Ordner `share` auf dem PC. |

Beim Umschalten von `ablage_im_share` werden vorhandene Daten **nicht** automatisch
verschoben. Stelle das am besten vor dem ersten Dokument ein.

## Verlustfreie Kompression

Alle Dokumente werden automatisch verlustfrei verkleinert: PDF-Datenströme werden stärker
komprimiert, Fotos und Scans (auch in PDFs) mit `jpegtran` verlustfrei neu kodiert,
Office-Dateien mit höchster Stufe neu gepackt. Die Dateien bleiben normale PDF-, Bild- und
Office-Dateien – Inhalt und Bildqualität ändern sich nicht. Jede optimierte Datei wird vor
dem Ersetzen geprüft (identische Bildpunkte, identischer Text); schlägt das fehl, bleibt das
Original. Die Ersparnis steht unter ⚙ Einstellungen → Speicherplatz.

## Wo liegen die Daten?

Auf dem Raspberry Pi, nirgendwo sonst:

| Pfad (im Add-on) | Inhalt |
|---|---|
| `dms.sqlite` | Datenbank: Metadaten, erkannter Text, Schlagwörter, Suchindex, Gelerntes |
| `ablage/Eingang/` | noch nicht bestätigte Dokumente |
| `ablage/<Ordner>/<Unterordner>/<Datum>_<Titel>.pdf` | abgelegte Dokumente |
| `vorschau/` | Vorschaubilder |

Diese Dateien liegen in `/data` (Standard) oder in `/share/dokumentenverwaltung`.

## Backup in die Google Cloud (Google Drive)

Home Assistant kann Backups selbst verschlüsselt auf Google Drive ablegen. Die Daten dieses
Add-ons sind darin enthalten: Datenbank, alle Dokumente und die gelernten Vorschläge.

Einrichtung (einmalig):

1. **Einstellungen → Geräte & Dienste → Integration hinzufügen → „Google Drive“**, dann mit
   deinem Google-Konto anmelden und den Zugriff erlauben.
2. **Einstellungen → System → Backups → Backups konfigurieren**:
   - **Automatisches Backup:** z. B. täglich
   - **Speicherorte:** **Google Drive** anhaken (den lokalen Speicher zusätzlich behalten
     ist sinnvoll)
   - **Inhalt:** das App/Add-on **Dokumentenverwaltung** auswählen. Bei
     `ablage_im_share: true` zusätzlich den Ordner **Share**.
   - **Aufbewahrung:** z. B. die letzten 7 Backups
3. Den **Verschlüsselungsschlüssel** der Backups herunterladen und sicher aufbewahren,
   z. B. im Passwortmanager. Ohne ihn lässt sich ein Backup nicht wiederherstellen.

Home Assistant verschlüsselt die Backups, bevor sie das Gerät verlassen. Google sieht deine
Dokumente also nicht im Klartext. Speicherplatz: Das kostenlose Google-Konto hat 15 GB.
Gescannte Seiten brauchen etwa 0,3–1 MB.

Während des Backups ist das Add-on für einige Sekunden bis Minuten gestoppt, damit die
Datenbank konsistent gesichert wird.

**Wiederherstellen:** Einstellungen → System → Backups → Backup aus Google Drive wählen →
nur das Add-on „Dokumentenverwaltung“ (und ggf. Share) wiederherstellen.

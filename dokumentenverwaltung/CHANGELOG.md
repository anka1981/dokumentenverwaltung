# Änderungen

## 1.0.1

- Build auf dem Raspberry Pi repariert: Multi-Arch-Basisimage `base-debian:trixie`
  statt der alten architekturabhängigen Images (`build.yaml` entfällt).
- Backups: Das Add-on wird während der Sicherung kurz angehalten, damit die Datenbank
  in einem sauberen Zustand gesichert wird.

## 1.0.0

- Erste Version: Upload und Handy-Scan, Texterkennung mit Tesseract, Verschlagwortung,
  Ablagevorschlag mit Bestätigung, lernende Vorschläge, Volltextsuche, Ordnerverwaltung.

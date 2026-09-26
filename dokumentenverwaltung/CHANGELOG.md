# Änderungen

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

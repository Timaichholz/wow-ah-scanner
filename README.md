# WoW AH-Scanner

Scannt das Auktionshaus über die **offizielle Blizzard-API**, schätzt Angebot und Nachfrage und sagt dir, **welche Gegenstände sich zu craften lohnen** – inklusive Farmliste mit allen benötigten Materialien.

Das Tool liest nur Daten aus. Es greift nicht ins Spiel ein und verstößt damit nicht gegen Blizzards Regeln.

---

## Automatisch in der Cloud (GitHub Actions)

Dieses Repo scannt **jede Stunde automatisch** über GitHub Actions – ohne dass ein PC laufen muss.
Der aktuelle Bericht erscheint danach auf der GitHub-Pages-Seite des Repos.

Einmalig einrichten:
1. Im Repo **Settings → Secrets and variables → Actions → New repository secret**
   - `BLIZZARD_CLIENT_ID` = deine Client ID
   - `BLIZZARD_CLIENT_SECRET` = dein Client Secret
2. **Actions → AH-Scan → Run workflow** für den ersten Lauf (lädt einmalig alle Rezepte, dauert ca. 10–15 Minuten).

Einstellungen für den Cloud-Lauf stehen in `config.ci.toml` (öffentlich sichtbar, daher **nie** Secrets dort eintragen).
Die Datenbank wird zwischen den Läufen im Actions-Cache gespeichert und hält 10 Tage Verlauf.
Jeder Lauf startet nach ca. 45 Minuten den nächsten selbst (GitHubs Zeitpläne lassen Läufe oft ausfallen). Der Zeitplan zweimal pro Stunde dient nur noch als Sicherheitsnetz, falls die Kette reißt.

## Was der Bericht zeigt

| Bereich | Inhalt |
|---|---|
| **Übersicht** | Goldbedarf für Midnight (aktueller WoW-Marken-Preis × benötigte Marken), Datenstatus, Top-Farmspots, Top-Rohstoffe, Transmog und Easy-Money-Crafts |
| **Farmspots** | Recherchierte Solo-Spots aus `farmspots.json`, bewertet nach dem aktuellen Marktwert und Absatz ihrer Beute, mit Anreise, Methode, Dropchancen und Quellen |
| **Rohstoffe nach Tätigkeit** | Stoff, Leder, Erz, Kräuter, Entzaubern, Fleisch/Fisch, Elementar – was sich am meisten umsetzt, Midnight-Materialien ausgeblendet |
| **Transmog-Markt** | Nicht herstellbare Ausrüstung über 2.000 Gold, die sich auf dem Realm verkauft (mit Wowhead-Link zur Dropquelle) |
| **Crafting** | Profitable Rezepte, eingeordnet als Easy Money / Solide / Zeit- / Kapitalintensiv |

**Eigene Farmspots ergänzen:** einen Eintrag in `farmspots.json` hinzufügen, Beute mit **englischem** Itemnamen
(wie auf Wowhead). Das Tool findet die Items selbst über die Blizzard-Suche.

**Echtes Gold pro Stunde:** Nach einer Farm-Session in `config.ci.toml` unter `[[farm_log]]` Spot-ID, Minuten und Gold
eintragen – der Bericht zeigt dann pro Spot den gemessenen Wert.

---

## Einrichtung (einmalig, ca. 10 Minuten)

1. **Python installieren** (3.8 oder neuer): https://www.python.org/downloads/
   Beim Installieren unbedingt **„Add python.exe to PATH"** anhaken.
2. **Blizzard-API-Zugang anlegen** (kostenlos):
   - https://develop.battle.net/access/clients öffnen und mit deinem Battle.net-Konto anmelden
   - **„Create Client"** → Name beliebig (z. B. „AH-Scanner"), Redirect-URL `http://localhost`, Service-URL leer lassen
   - **Client ID** und **Client Secret** kopieren
3. **`1_einrichten.bat`** doppelklicken. Beim ersten Start wird `config.toml` angelegt und geöffnet:
   - `client_id` und `client_secret` eintragen
   - `slug` deines Realms eintragen (z. B. `blackmoore`, `die-aldor`)
   - speichern, `1_einrichten.bat` erneut starten → sollte „Verbindung funktioniert" melden
4. **`2_rezepte_laden.bat`** – lädt alle Rezepte aller Berufe und Erweiterungen (einmalig, ca. 5–15 Minuten).

> `config.toml` enthält dein API-Secret – nicht teilen und nicht in den Chat kopieren.

## Benutzung

| Datei | Was passiert |
|---|---|
| `3_scan.bat` | Ein einzelner AH-Snapshot |
| `4_dauerscan.bat` | Scannt jede Stunde und erneuert den Bericht. Fenster offen lassen |
| `5_bericht.bat` | Erstellt den Bericht und öffnet ihn im Browser |

**Empfohlener Ablauf:** `4_dauerscan.bat` für **1–2 Tage** laufen lassen (PC an), dann `5_bericht.bat`.
Je länger der Verlauf, desto zuverlässiger die Nachfrage-Schätzung.

Ergebnisse landen im Ordner `output/`:
- `bericht_aktuell.html` – sortierbare Übersicht (Crafts + Materialien)
- `farmliste_aktuell.txt` – **diese Datei gibst du Claude**, um Farm-Routen zu planen
- CSV-Dateien zum Weiterverarbeiten in Excel

## Was die Kennzahlen bedeuten

- **Marktpreis**: Durchschnitt der günstigsten 15 % des Angebots (ein einzelnes Billigangebot verzerrt nichts)
- **Gewinn/Craft**: niedrigster Verkaufspreis × Menge − 5 % AH-Gebühr − Materialkosten
- **Verkauft/Tag**: geschätzt aus dem Vergleich aufeinanderfolgender Scans. Gezählt werden kleiner gewordene Stapel und verschwundene Auktionen, die günstiger waren als alles, was danach noch im AH steht (Abbrechen und Neu-Einstellen zählt so nicht als Verkauf). Erst ab 3 Stunden Verlauf angezeigt
- **Reicht Tage**: aktuelles Angebot ÷ Verkäufe pro Tag. **Klein = knapp = gut für dich**
- **Potenzial/Tag**: Gewinn pro Stück × Verkäufe/Tag × dein Marktanteil (Standard 20 %). Danach wird sortiert
- **Preis vs. Ø**: aktueller Preis gegenüber dem Durchschnitt der letzten 7 Tage. Bei Materialien heißt stark negativ: gerade günstig einkaufen

## Filter (in `config.toml`)

- Nur bestimmte Berufe: `professions = ["Lederverarbeitung"]`
- Nur eine Erweiterung: `tier_keywords = ["Khaz Algar"]` (The War Within)
- Nur Housing-Deko: `category_keywords = ["Deko"]`
- Nur Rezepte, die dein Charakter kennt: Name + Realm unter `[character]` eintragen und `only_known_recipes = true`

Die genauen deutschen Namen der Erweiterungsstufen und Kategorien siehst du im Bericht in der Spalte „Erweiterung".

## Grenzen (ehrlich gesagt)

- **Nachfrage ist geschätzt.** Die API zeigt keine echten Verkäufe. Einzelne Fehlzuordnungen (z. B. ein Verkäufer bricht sein günstigstes Angebot ab) gleichen sich über mehrere Tage aus.
- **Rohstoffe** (Erze, Kräuter, Leder …) sind regionsweit, **Ausrüstung und Deko** realmspezifisch. Für Letztere muss der Realm eingetragen sein.
- **Qualitätsstufen** bei neueren Rezepten (Dragonflight, TWW) werden vereinfacht. Solche Rezepte sind im Bericht markiert.
- **Händlerware** (z. B. Phiolen) kennt die API nicht als Preis. Ohne Eintrag unter `[vendor_prices]` wird sie mit 0 gerechnet.
- **Holz für Housing-Deko** ist kriegsmeutengebunden und nicht im AH. Es erscheint in der Farmliste als „FARMEN".
- Blizzard aktualisiert die AH-Daten nur etwa **stündlich** – öfter scannen bringt nichts.

## Fehlerbehebung

- **„python wird nicht erkannt"** → Python neu installieren mit „Add to PATH".
- **„Anmeldung fehlgeschlagen"** → Client ID/Secret prüfen (keine Leerzeichen).
- **„Realm nicht gefunden"** → Slug klein schreiben, Leerzeichen/Apostrophe als Bindestrich bzw. weglassen.
- **Keine Nachfragedaten** → Zwischen zwei Scans dürfen höchstens 3 Stunden liegen (`max_gap_hours`).

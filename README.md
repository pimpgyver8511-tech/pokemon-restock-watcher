# Pokémon-Restock-Watcher

Überwacht vertrauenswürdige Shops (keine Marktplätze) auf die **Pokémon 30 Jahre Top-Trainer-Box**
(DE: EAN `0196214144842`, EN „30th Celebration Elite Trainer Box“: EAN `0196214144828`)
und schickt eine E-Mail, sobald ein Shop sie **zwischen 50 € und 80 €** (ohne Versand) anbietet.

## Was der Watcher macht

| Wann | Was |
|---|---|
| alle 20 Minuten | Prüft alle Shops aus `config.yaml` (Preis + Verfügbarkeit) und sucht in Google News nach Restock-Meldungen |
| **sofort** | 🚨 Mail, wenn ein Shop **lieferbar/vorbestellbar im Preisbereich** wird – bei Restocks zählt jede Minute |
| **einmal täglich** (ab 8 Uhr) | 📋 Tagesübersicht: neue Restock-Meldungen, Warnungen (z. B. Shop blockt), Stand aller Shops inkl. „zuletzt verfügbar ab …“ |

Die Uhrzeit der Tagesübersicht (`digest_hour`) und ob Treffer sofort kommen (`instant_alerts`) stellst du
in `config.yaml` ein.

## Einrichtung (einmalig, ca. 10 Minuten)

### 1. SMTP-Versand bei GMX freischalten
GMX erlaubt den Versand über externe Programme erst nach einmaliger Freigabe:
1. Bei <https://www.gmx.net> einloggen → **E-Mail → Einstellungen → POP3/IMAP Abruf**
2. Schalter **„POP3- und IMAP-Zugriff erlauben“** einschalten.
3. Ist bei dir die Zwei-Faktor-Authentisierung aktiv, erstellst du unter **Sicherheit** ein
   **anwendungsspezifisches Passwort** und nutzt es statt deines normalen Passworts.

### 2. Secrets im GitHub-Repo eintragen
Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Name | Wert |
|---|---|
| `SMTP_USER` | deine vollständige GMX-Adresse (z. B. `name@gmx.de`) |
| `SMTP_PASSWORD` | dein GMX-Passwort bzw. das anwendungsspezifische Passwort |
| `MAIL_TO` | Empfängeradresse (kann dieselbe sein) |

Server (`mail.gmx.net`, Port `587`) ist voreingestellt. Nur bei einem anderen Anbieter zusätzlich
`SMTP_HOST` und `SMTP_PORT` (`465` oder `587`) setzen.

### 3. Testen
Repo → **Actions → Pokémon-Watcher → Run workflow**
- einmal mit Haken bei **„Nur eine Test-Mail schicken“**: Die Test-Mail sollte ankommen.
- einmal ohne Haken: Im Lauf siehst du unter „Shops prüfen“ bzw. in der Zusammenfassung, was jeder Shop liefert.

Ab dann läuft der Watcher automatisch.

## Anpassen
Alles steht in `config.yaml`: Preisgrenzen, Shops, Such-URLs, News-Feeds.
- **Neuen Shop hinzufügen:** Eintrag mit `name`, `country`, `ships_to_de` und entweder `urls`
  (direkte Produktseite, am zuverlässigsten) oder `search` (Such-URL mit `{ean}`).
- `notify_out_of_range: true` meldet auch Restocks außerhalb des Preisbereichs.

## Grenzen
- **Bot-Schutz:** Große Ketten (v. a. MediaMarkt/Saturn) blocken automatische Abrufe teilweise.
  Der Watcher imitiert einen normalen Chrome-Browser. Wenn ein Shop dauerhaft blockt, kommt eine ⚠️-Mail.
  Dann hilft oft, die direkte Produkt-URL unter `urls` einzutragen.
- **Restock-Termine** geben die Ketten selten offiziell bekannt. Die News-Treffer sind Hinweise aus
  Presse und Community, keine Garantie.
- `ships_to_de: "check"`: Bei diesen EU-Shops den Versand nach Deutschland im Warenkorb prüfen.
- Der Watcher kauft nicht automatisch.

## Lokal ausführen
```bash
pip install -r requirements.txt
python -m watcher.main --dry-run            # alle Shops prüfen, keine Mail, kein Speichern
python -m watcher.main --dry-run --shop Müller
python -m unittest                          # Tests
```

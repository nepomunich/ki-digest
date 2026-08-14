# Fixes am KI-Digest — Sitzung vom 11. August 2026

## Ausgangslage
Der tägliche KI-Journalismus-Digest kam nicht mehr an. Zwei unabhängige Zustellwege waren betroffen: die GitHub-Actions-Cloud-Automatisierung (primär) und der lokale Mac-LaunchAgent (Backup).

## Problem 1: GitHub Actions war deaktiviert
- **Ursache:** GitHub deaktiviert geplante Workflows automatisch nach 60 Tagen ohne Commit im Repo — nicht ohne Workflow-Lauf, sondern ohne Commit. Der letzte Commit lag vom 8. Mai, also ~95 Tage zurück.
- **Fix:** Im Actions-Tab auf den gelben Hinweisbanner geklickt → „Enable workflow". Ein Testlauf danach bestätigte, dass die hinterlegten Secrets (Gmail-Passwort, Anthropic-Key) noch gültig waren.
- **Lehre:** Ein Commit alle paar Wochen (auch trivial) hält den Workflow aktiv. Falls das wieder passiert: Actions-Tab prüfen, „Enable workflow" klicken.

## Problem 2: Falsche Empfängeradresse
- **Ursache:** In `digest.py` stand `RECIPIENT = "post@berndoswald.de"` — eine alte, nicht mehr genutzte Adresse. Deshalb kam trotz erfolgreicher Workflow-Läufe keine Mail im richtigen Postfach an.
- **Fix:** `RECIPIENT` auf `aiformedia@br.de` geändert (Zeile 24 in `digest.py`), direkt im GitHub-Web-Editor committed (Commit `d6bdb26`), da lokal kein gültiges Push-Token vorlag.
- **Lehre:** Bei „Workflow läuft grün, aber keine Mail kommt an" zuerst die `RECIPIENT`-Konstante prüfen, nicht nur die Logs.

## Problem 3: Lokales Backup war seit Monaten kaputt
- **Ursache:** Der im LaunchAgent-Plist und in `.env` hinterlegte Anthropic-API-Key war ungültig. Das Log (`~/Library/Application Support/ki-digest/digest.log`, 1364 Zeilen) zeigte ausnahmslos `AuthenticationError: 401 invalid x-api-key` — es gab keinen einzigen erfolgreichen Lauf im gesamten Log.
- **Falsche Fährte unterwegs:** Ein erster Check meldete, das Verzeichnis `~/Library/Application Support/ki-digest/` existiere gar nicht. Ursache war ein reiner Shell-Fehler (`~` wird in doppelten Anführungszeichen in bash nicht expandiert) — das Verzeichnis war die ganze Zeit da.
- **Gotcha bei der Key-Erneuerung:** Auf console.anthropic.com/settings/keys werden bestehende Keys aus Sicherheitsgründen nur maskiert angezeigt (z. B. `sk-ant-api03-nTPPgB...XAAA`). Ein Kopieren dieser maskierten Anzeige ergibt einen ungültigen, buchstäblich abgeschnittenen Key. Der volle Key wird nur einmalig direkt bei „Create Key" angezeigt.
- **Fix:**
  1. Neuen API-Key in der Anthropic-Console erzeugt und sofort vollständig kopiert.
  2. `.env` (`~/Library/Application Support/ki-digest/.env`) mit dem vollständigen Key aktualisiert.
  3. Plist bereinigt: `ANTHROPIC_API_KEY` aus den `EnvironmentVariables` im LaunchAgent-Plist entfernt, damit der Key nur noch an einer Stelle (`.env`) gepflegt werden muss.
  4. LaunchAgent neu geladen (`launchctl unload/load`), Testlauf bestätigte `✓ Digest gesendet an aiformedia@br.de`.
- **Lehre:** Gmail/IMAP-Login war die ganze Zeit über in Ordnung — nur der Anthropic-Key war das Problem. Bei zukünftigen Backup-Ausfällen zuerst `digest.log` auf die letzte Fehlermeldung prüfen, statt Dateistruktur zu vermuten.

## Zusätzliche Verbesserung: Manueller Start
Ein Alias `ki-digest` wurde in `~/.zshrc` angelegt:
```
alias ki-digest='/usr/bin/python3 "/Users/berndoswald/Library/Application Support/ki-digest/digest.py"'
```
Damit lässt sich der Digest jederzeit manuell vom Terminal aus auslösen (sendet eine echte E-Mail, kein Testmodus).

## Erweiterung: Feste Quellenliste hinzugefügt
Im Anschluss an die Reparatur wurde `digest.py` um sieben zusätzliche, fest definierte Quellen erweitert (AI for Newsroom, The AI Journalist/Steady, News Product Alliance Blog, Lars Adrian Giske/Substack, JournalismAI, RJI, Nieman Lab). Details zu Methode (RSS vs. HTML-Scrape) und Konfiguration stehen in `README.md` unter „Additional Fixed Sources".

- **Warum feste Quellenliste statt Claude-Websuche:** Eine Web-Search-Tool-Integration hätte pro Lauf zusätzliche API-Kosten (Anthropic berechnet Websuchen separat) und eine agentische Mehrfach-Turn-Schleife bedeutet — deutlich mehr Tokens als der direkte HTTP-Abruf fester Quellen im bestehenden Single-Call-Design.
- **Poynter ausgeschlossen:** Cloudflare-Bot-Schutz (403, JS-Challenge) blockiert direkten Abruf; die einzige erreichbare Feed-URL ist ein 20 Jahre alter, toter FeedBurner-Feed. Nicht eingebaut, da ein Umgehen von Bot-Schutz nicht zulässig ist.
- **Reuters Institute geprüft, aber nicht eingebaut:** Der öffentliche RSS-Feed enthält nur ~10 Einträge, größtenteils Umfrage-Unterseiten statt echter Artikel — im Test kein einziger KI-Treffer trotz Keyword-Filter.
- **Datenqualität von Drittfeeds:** Nieman Labs eigener „artificial-intelligence"-Tag-Feed ist zu lückenhaft (Monate ohne Eintrag trotz täglich neuer KI-Artikel auf der Site) — stattdessen wird der allgemeine Feed per Keyword-Regex (`\bAI\b` etc. mit Wortgrenzen, um Fehltreffer wie „said" zu vermeiden) gefiltert. Manche Feeds (z. B. Reuters Institute) liefern Items nicht chronologisch sortiert — `fetch_rss()` sortiert deshalb selbst nach `pubDate`, bevor `max_items` angewendet wird.

## Lehre: GitHub-Web-Editor für große Diffs
Ohne lokales Push-Token musste die komplette neue `digest.py` (388 Zeilen) über den GitHub-Web-Editor eingespielt werden. Zwei Fallstricke dabei:

- **Simuliertes Cmd+V funktioniert nicht:** Der Browser blockiert programmatisches Cmd+V aus Sicherheitsgründen (kein echter Zugriff auf die System-Zwischenablage). Funktionierender Workaround: ein echtes `ClipboardEvent('paste', {clipboardData: ...})` per JavaScript auf das CodeMirror-Element (`.cm-content`) dispatchen — CodeMirror liest `event.clipboardData` unabhängig vom OS-Clipboard.
- **`document.execCommand('selectAll')` selektiert in CodeMirror 6 nicht zuverlässig:** Ein Paste-Dispatch nach `execCommand('selectAll')` fügte den neuen Text nur ein, ohne den alten zu löschen (Ergebnis: alter + neuer Code doppelt im File). Vor dem Paste-Dispatch muss ein **echtes** Tastatur-Cmd+A (nicht simuliert per JS) gesendet werden, damit die Selektion tatsächlich greift.
- **Vorsicht bei leerem Paste-Text:** Ein Dispatch mit `clipboardData` ohne gesetzten Text (versehentlich `undefined`) hat den markierten Inhalt trotzdem ersetzt — die Datei war kurz komplett leer, bis der korrekte Inhalt erneut eingefügt wurde. Immer vor dem Dispatch prüfen, dass die Textvariable tatsächlich den vollen Inhalt enthält.
- **Praktische Konsequenz:** Für einzelne Zeilenänderungen bleibt Klicken+Tippen im Web-Editor am zuverlässigsten. Für große Diffs (neue Funktionen, viele Zeilen) ist ein echtes GitHub Personal Access Token der deutlich schnellere Weg — siehe offener Punkt unten.

## Offene Punkte / für später
- Lokal ist kein GitHub-Push-Token hinterlegt (`~/.git-credentials` leer, osxkeychain ohne Eintrag) — Änderungen an `digest.py` müssen entweder direkt im GitHub-Web-Editor gemacht werden, oder es wird vorher ein Personal Access Token besorgt (Scopes `repo` + `workflow`). Bei größeren Änderungen lohnt sich das Token, um den fehleranfälligen Web-Editor-Workaround zu vermeiden.
- Empfehlenswert: gelegentlich einen trivialen Commit ins Repo pushen (z. B. README-Tippfehler), um die 60-Tage-Inaktivitätssperre von GitHub Actions präventiv zu vermeiden.
- Reuters Institute und Poynter könnten bei Bedarf später erneut geprüft werden, falls sich deren Feed-Situation ändert (z. B. neue offizielle RSS-URL).

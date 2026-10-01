# Bitget Momentum-Bot (Demo)

Vollautomatischer Bot, der jede Woche in die 5 stärksten Coins umschichtet und bei Bärenmarkt alles in USDT parkt.
Nur Python nötig, keine Zusatzpakete. **Kein echtes Geld**: entweder Simulation oder dein Bitget-Demo-Konto.

## Strategie
1. **Jede Woche** (zum Tagesschluss um 18:00 Uhr MESZ): die 5 Coins mit dem höchsten Kursanstieg der letzten 30 Tage kaufen, je 19,6 % vom Konto (2 % bleiben als Puffer in USDT). Coins mit negativem Momentum werden nicht gekauft.
2. **Schutzschalter (täglich):** Schließt BTC unter seinem 200-Tage-Durchschnitt, wird alles verkaufen und USDT gehalten. Wiedereinstieg erst am nächsten Umschicht-Tag.

Alle Werte stehen in `momentum_config.json`.

## Backtest (April 2021 – September 2026, Spot, 0,1 % Gebühr + Slippage)
| | pro Jahr | größter Absturz |
|---|---|---|
| BTC halten | +6 % | −76 % |
| Alle Coins gleich gewichtet halten | +17 % | −75 % |
| **Momentum-Bot (23 Coins, Spot)** | **+21 bis +22 %** | −53 bis −68 % |
| Momentum-Bot (15 Coins, Bitget-Demo/Futures inkl. Funding) | ca. +13 % | ca. −58 % |

Schwach war 2025 (−13 bis −48 %). Den größten Vorteil bringt der Schutzschalter (2022: 0 % statt −65 %). Keine Garantie für die Zukunft, keine Anlageberatung.

## Zwei Modi

| | Simulation | Bitget-Demo-Konto |
|---|---|---|
| Starten | `MOMENTUM_SIMULATION_STARTEN.bat` | `MOMENTUM_BITGET_DEMO_STARTEN.bat` |
| API-Key nötig | nein | ja, Demo-API-Key |
| Was passiert | simuliert ein Spot-Konto mit 1.000 USDT und Live-Kursen auf deinem PC | handelt auf deinem Bitget-Demo-Konto, sichtbar in der Bitget-App |
| Coins | alle 23 | 15 (mehr gibt es im Bitget-Demo nicht) |
| Technik | wie echter Spot-Handel | Futures mit 1x Hebel, nur Long (Bitgets Spot-Demo hat nur ca. 7 Coins) |
| Dashboard | `Dashboard_Simulation.html` | `Dashboard_Bitget_Demo.html` |

Beide Modi können gleichzeitig laufen.

### Bitget-Demo-Konto verbinden
1. Auf bitget.com einloggen → oben auf **Demo-Trading** umschalten
2. Profil → **API-Key-Verwaltung** → **Demo-API-Key erstellen**
3. Rechte: **Futures Lesen + Handeln**, **keine** Auszahlungsrechte
4. `.env.example` kopieren, die Kopie in `.env` umbenennen und Key, Secret und Passphrase eintragen
5. `MOMENTUM_BITGET_DEMO_STARTEN.bat` doppelklicken
6. In der Bitget-App: **Demo-Modus → Futures → Positionen** zeigt, was der Bot gekauft hat

## Den Stand verfolgen
- **Dashboard:** öffnet sich beim Start automatisch im Browser und lädt sich jede Minute neu. Es zeigt Kontowert, Ergebnis seit Start, Vergleich mit BTC, Verlauf, aktuelle Coins, Momentum-Rangliste, Schutzschalter und alle Trades.
- **Ohne laufenden Bot:** `python momentum_bot.py status --mode paper` (bzw. `--mode bitget_demo`) zeigt den Stand und aktualisiert das Dashboard
- **Dateien:** `momentum_data/` enthält Log (`bot.log`), Trades als CSV und den Zustand

## In der Cloud laufen lassen (PC kann aus sein)
GitHub Actions startet den Bot **stündlich kostenlos** in der Cloud (`.github/workflows/momentum-bot.yml`). Das Dashboard ist dann als Webseite erreichbar, auch auf dem Handy.

1. **GitHub-Konto** anlegen: https://github.com/signup
2. **Neues Repository** erstellen: https://github.com/new → Name z.B. `momentum-bot`, **Public**, *ohne* README → „Create repository“
3. **Hochladen:** im Ordner `git remote add origin https://github.com/DEIN-NAME/momentum-bot.git` und `git push -u origin main` (beim ersten Mal öffnet sich ein GitHub-Login im Browser)
4. **Dashboard einschalten:** Repository → Settings → Pages → Source „Deploy from a branch“ → Branch `main`, Ordner `/docs` → Save.
   Adresse: `https://DEIN-NAME.github.io/momentum-bot/`
5. **Optional, Bitget-Demo-Konto:** Settings → Secrets and variables → Actions → „New repository secret“, dreimal:
   `BITGET_API_KEY`, `BITGET_API_SECRET`, `BITGET_PASSPHRASE` (Werte deines Demo-API-Keys). Ohne Secrets läuft nur die Simulation.
6. **Testen:** Reiter „Actions“ → „Momentum-Bot“ → „Run workflow“. Nach ca. 1–2 Minuten ist das Dashboard aktualisiert (Pages braucht danach noch ca. 1 Minute).

Hinweise:
- Keys liegen nur verschlüsselt als Secret bei GitHub, nie in den Dateien (`.env` wird nicht hochgeladen).
- **Den Demo-Modus nicht gleichzeitig lokal und in der Cloud laufen lassen**, sonst handeln zwei Bots auf demselben Konto.
- Schlägt ein Lauf fehl, schickt GitHub eine E-Mail. Die Details stehen im Reiter „Actions“.
- Das Projekt ist öffentlich: Code, Demo-Kontostand und Trades sind einsehbar, die Keys nicht.

## Wichtig
- **Lokal muss der Bot laufen**, damit er umschichten und den Schutzschalter auslösen kann. Er prüft alle 5 Minuten, gehandelt wird nur zum Tagesschluss (18:00 Uhr MESZ). Läuft er zu dieser Zeit nicht, holt er es beim nächsten Start nach.
- **Fenster schließen oder Strg+C** stoppt den Bot. Gekaufte Coins bleiben im Konto, der Stand bleibt gespeichert.
- **Für eine echte Aussage** braucht es mehrere Monate. In einzelnen Wochen kann alles passieren.
- **Neu starten** (Simulation zurücksetzen): `momentum_data/state_paper.json` löschen.

---

## Alter Futures-Day-Trading-Bot
Noch im Ordner (`bot.py`, `backtest.py`, `config.json`, Batch-Dateien `1_`–`3_`), aber **nicht profitabel** im Backtest: Kurze Zeitrahmen verlieren durch Gebühren (−33 bis −55 %), auf 1H mit Limit-Orders etwa ±0 %. Nicht empfohlen.

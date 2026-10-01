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

## Wichtig
- **Der Bot muss laufen**, damit er umschichten und den Schutzschalter auslösen kann. Er prüft alle 5 Minuten, gehandelt wird nur zum Tagesschluss (18:00 Uhr MESZ). Läuft er zu dieser Zeit nicht, holt er es beim nächsten Start nach.
- **Fenster schließen oder Strg+C** stoppt den Bot. Gekaufte Coins bleiben im Konto, der Stand bleibt gespeichert.
- **Für eine echte Aussage** braucht es mehrere Monate. In einzelnen Wochen kann alles passieren.
- **Neu starten** (Simulation zurücksetzen): `momentum_data/state_paper.json` löschen.

---

## Alter Futures-Day-Trading-Bot
Noch im Ordner (`bot.py`, `backtest.py`, `config.json`, Batch-Dateien `1_`–`3_`), aber **nicht profitabel** im Backtest: Kurze Zeitrahmen verlieren durch Gebühren (−33 bis −55 %), auf 1H mit Limit-Orders etwa ±0 %. Nicht empfohlen.

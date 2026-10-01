"""Momentum-Rotation-Bot (Tageskerzen, nur Long).

Regeln:
  - Alle rebalance_days Tage: die top_n Coins mit der hoechsten Rendite der letzten
    lookback_days Tage kaufen (gleich gewichtet, nur Coins mit positivem Momentum).
  - Schutzschalter (taeglich): BTC schliesst unter seinem regime_sma_days-Durchschnitt
    -> alles verkaufen, USDT halten. Wiedereinstieg erst am naechsten Umschicht-Tag.

    python momentum_bot.py run       -> Bot starten (laeuft bis Strg+C)
    python momentum_bot.py status    -> Stand anzeigen + Dashboard aktualisieren
    python momentum_bot.py once      -> genau ein Durchlauf (fuer GitHub Actions / Zeitplaner)
"""
import argparse
import csv
import json
import logging
import os
import sys
import time
import urllib.error
import webbrowser
from datetime import datetime

from bitget import Bitget, BitgetError, load_env
from dashboard import write_dashboard
from exchanges import BitgetDemoExchange, PaperExchange

DATA_DIR = "momentum_data"
DASHBOARD = {"paper": "Dashboard_Simulation.html", "bitget_demo": "Dashboard_Bitget_Demo.html"}
DAY_MS = 86_400_000
log = logging.getLogger("momentum")


# ---------------------------------------------------------------------- helpers
def setup_logging():
    os.makedirs(DATA_DIR, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    for h in (logging.StreamHandler(sys.stdout),
              logging.FileHandler(os.path.join(DATA_DIR, "bot.log"), encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    log.setLevel(logging.INFO)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def fmt_day(ms):
    return datetime.fromtimestamp(ms / 1000).strftime("%d.%m.%Y %H:%M")


class MomentumBot:
    def __init__(self, cfg, out_dir=".", check_note=None):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.cfg = cfg
        self.out_dir = out_dir
        self.check_note = check_note or f"alle {cfg['check_minutes']} Minuten"
        self.dashboard_path = os.path.join(out_dir, DASHBOARD[cfg["mode"]])
        self.mode = cfg["mode"]
        self.state_path = os.path.join(DATA_DIR, f"state_{self.mode}.json")
        self.state = load_json(self.state_path, {})
        self.market = Bitget(demo=False)  # Tageskerzen immer vom echten Markt
        if self.mode == "bitget_demo":
            load_env()
            client = Bitget(os.environ.get("BITGET_API_KEY", ""), os.environ.get("BITGET_API_SECRET", ""),
                            os.environ.get("BITGET_PASSPHRASE", ""), demo=True)
            self.ex = BitgetDemoExchange(self.state, cfg, client)
            self.universe = cfg["universe_bitget_demo"]
        elif self.mode == "paper":
            self.ex = PaperExchange(self.state, cfg)
            self.universe = cfg["universe_paper"]
        else:
            sys.exit(f"Unbekannter mode '{self.mode}' (paper oder bitget_demo)")
        for key, default in (("equity_curve", []), ("trades", []), ("signal", {})):
            self.state.setdefault(key, default)

    def save(self):
        save_json(self.state_path, self.state)

    # ------------------------------------------------------------------ main loop
    def check_connection(self):
        if self.mode != "bitget_demo":
            return
        try:
            self.ex.snapshot(self.ex.prices())
        except BitgetError as e:
            sys.exit(f"\nVerbindung zum Bitget-Demo-Konto fehlgeschlagen: {e}\n"
                     "-> .env pruefen: BITGET_API_KEY, BITGET_API_SECRET, BITGET_PASSPHRASE eines DEMO-API-Keys\n"
                     "   (Bitget: auf Demo-Trading umschalten -> API-Key-Verwaltung -> Demo-API-Key, Rechte: Futures Handeln)")

    def run(self):
        self.market.sync_time()
        self.ex.setup(self.universe)
        self.check_connection()
        log.info("Momentum-Bot laeuft | %s | %s Coins | Top %s | Strg+C zum Beenden",
                 self.ex.label, len(self.universe), self.cfg["top_n"])
        first = True
        while True:
            try:
                self.cycle()
                if first:
                    webbrowser.open("file:///" + os.path.abspath(self.dashboard_path).replace("\\", "/"))
                    first = False
            except (BitgetError, urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                log.error("Fehler: %s (neuer Versuch in 60 s)", e)
                time.sleep(60)
                continue
            time.sleep(self.cfg["check_minutes"] * 60)

    def cycle(self):
        prices = self.ex.prices()
        snap = self.ex.snapshot(prices)
        now = self.market._now_ms()
        self.state.setdefault("start", {"ts": now, "equity": snap["equity"], "btc": prices.get("BTCUSDT")})

        last_closed = self.last_closed_day(now)
        if last_closed and last_closed != self.state.get("last_day_ts"):
            self.on_new_day(last_closed, prices, snap)
            snap = self.ex.snapshot(prices)

        self.record_equity(now, snap["equity"], prices.get("BTCUSDT"))
        self.save()
        write_dashboard(self.dashboard_data(snap, prices, now), self.dashboard_path)

    def last_closed_day(self, now):
        candles = self.market.candles("BTCUSDT", "USDT-FUTURES", "1D", 5)
        closed = [c for c in candles if c["ts"] + DAY_MS <= now]
        return closed[-1]["ts"] if closed else None

    # ------------------------------------------------------------------ strategy
    def compute_signal(self):
        cfg = self.cfg
        need = max(cfg["regime_sma_days"], cfg["lookback_days"]) + 15
        now = self.market._now_ms()
        closes = {}
        for s in sorted(set(self.universe) | {"BTCUSDT"}):
            try:
                cs = self.market.history_candles(s, "USDT-FUTURES", "1D", need)
            except BitgetError as e:
                log.warning("%s: keine Kerzen (%s)", s, e)
                continue
            closes[s] = [c["close"] for c in cs if c["ts"] + DAY_MS <= now]
        btc = closes["BTCUSDT"]
        n = cfg["regime_sma_days"]
        sma = sum(btc[-n:]) / n
        lb = cfg["lookback_days"]
        ranking = sorted(((x[-1] / x[-1 - lb] - 1, s) for s, x in closes.items()
                          if s in self.universe and len(x) > lb), reverse=True)
        picks = [s for m, s in ranking[:cfg["top_n"]] if m > 0]
        return {
            "risk_on": btc[-1] > sma, "btc": btc[-1], "btc_sma": sma,
            "ranking": [[s, round(m * 100, 2)] for m, s in ranking], "picks": picks,
        }

    def on_new_day(self, day_ts, prices, snap):
        sig = self.compute_signal()
        sig["day_ts"] = day_ts
        prev = self.state.get("signal") or {}
        self.state["signal"] = sig
        self.state["last_day_ts"] = day_ts
        log.info("Neuer Tag %s | BTC %.0f | SMA%s %.0f | Schutzschalter: %s | Top: %s",
                 fmt_day(day_ts + DAY_MS), sig["btc"], self.cfg["regime_sma_days"], sig["btc_sma"],
                 "INVESTIEREN" if sig["risk_on"] else "USDT HALTEN",
                 ", ".join(f"{s} {m:+.1f}%" for s, m in sig["ranking"][:self.cfg["top_n"]]))

        last_reb = self.state.get("last_rebalance_ts")
        due = last_reb is None or day_ts - last_reb >= self.cfg["rebalance_days"] * DAY_MS - DAY_MS // 2
        if not sig["risk_on"]:
            if snap["holdings"]:
                log.warning("Schutzschalter: BTC unter %s-Tage-Durchschnitt -> alles verkaufen",
                            self.cfg["regime_sma_days"])
                self.rebalance({}, prices, "Schutzschalter")
            if due:
                self.state["last_rebalance_ts"] = day_ts
        elif due:
            weight = (1 - self.cfg["cash_buffer_pct"] / 100) / self.cfg["top_n"]
            self.rebalance({s: weight for s in sig["picks"]}, prices, "Wochen-Umschichtung")
            self.state["last_rebalance_ts"] = day_ts
        elif prev and not prev.get("risk_on"):
            log.info("Schutzschalter wieder gruen - Einstieg am naechsten Umschicht-Tag")

    def rebalance(self, targets, prices, reason):
        snap = self.ex.snapshot(prices)
        equity = snap["equity"]
        tol = max(self.cfg["min_trade_usdt"], equity * self.cfg["rebalance_tolerance_pct"] / 100)
        held = {h["symbol"]: h for h in snap["holdings"]}

        for s, h in held.items():  # erst verkaufen
            target_val = targets.get(s, 0) * equity
            excess = h["value"] - target_val
            if target_val == 0 or excess > tol:
                self.trade("sell", s, excess, prices[s], reason, close_all=(target_val == 0), qty=h["qty"])

        snap = self.ex.snapshot(prices)
        cash = snap["available"]
        held = {h["symbol"]: h for h in snap["holdings"]}
        for s, w in targets.items():  # dann kaufen
            if s not in prices:
                log.warning("%s: kein Preis verfuegbar - uebersprungen", s)
                continue
            need = w * equity - held.get(s, {}).get("value", 0)
            amount = min(need, cash * 0.98)
            if need > tol and amount >= self.cfg["min_trade_usdt"]:
                if self.trade("buy", s, amount, prices[s], reason):
                    cash -= amount
        if not targets:
            log.info("Alles in USDT.")

    def trade(self, side, symbol, usdt, price, reason, close_all=False, qty=None):
        try:
            if side == "buy":
                r = self.ex.buy(symbol, usdt, price)
            else:
                r = self.ex.sell(symbol, qty if close_all else usdt / price, price, close_all=close_all)
        except BitgetError as e:
            log.error("%s %s fehlgeschlagen: %s", "Kauf" if side == "buy" else "Verkauf", symbol, e)
            return False
        row = {"zeit": datetime.now().strftime("%Y-%m-%d %H:%M"), "aktion": "KAUF" if side == "buy" else "VERKAUF",
               "coin": symbol.replace("USDT", ""), "menge": round(r["qty"], 6), "preis": round(r["price"], 6),
               "usdt": round(r["usdt"], 2), "gebuehr": round(r["fee"], 4), "grund": reason}
        log.info("%-7s %-6s %12.6f @ %-12.6g = %9.2f USDT  (%s)",
                 row["aktion"], row["coin"], r["qty"], r["price"], r["usdt"], reason)
        self.state["trades"].append(row)
        self.state["trades"] = self.state["trades"][-500:]
        path = os.path.join(DATA_DIR, f"trades_{self.mode}.csv")
        new = not os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(row), delimiter=";")
            if new:
                w.writeheader()
            w.writerow(row)
        return True

    # ------------------------------------------------------------------ reporting
    def record_equity(self, now, equity, btc):
        curve = self.state["equity_curve"]
        if not curve or now - curve[-1][0] >= 3_600_000:  # ein Punkt pro Stunde
            curve.append([now, round(equity, 2), btc])
        else:
            curve[-1] = [curve[-1][0], round(equity, 2), btc]

    def dashboard_data(self, snap, prices, now):
        sig = self.state.get("signal") or {}
        last_reb = self.state.get("last_rebalance_ts")
        rank = {s: m for s, m in sig.get("ranking", [])}
        return {
            "mode_label": self.ex.label, "mode": self.mode, "updated": now,
            "start": self.state["start"], "equity": snap["equity"], "available": snap["available"],
            "btc_price": prices.get("BTCUSDT"),
            "holdings": [dict(h, momentum=rank.get(h["symbol"])) for h in snap["holdings"]],
            "signal": sig, "top_n": self.cfg["top_n"], "sma_days": self.cfg["regime_sma_days"],
            "lookback": self.cfg["lookback_days"],
            "next_rebalance": (last_reb + self.cfg["rebalance_days"] * DAY_MS + DAY_MS) if last_reb else None,
            "curve": self.state["equity_curve"], "trades": self.state["trades"][-40:][::-1],
            "check_note": self.check_note,
        }


def main():
    ap = argparse.ArgumentParser(description="Momentum-Rotation-Bot")
    ap.add_argument("command", choices=["run", "status", "once"])
    ap.add_argument("--mode", choices=["paper", "bitget_demo"], help="ueberschreibt mode aus der Config")
    ap.add_argument("--out", default=".", help="Ordner fuer das Dashboard (GitHub Pages: docs)")
    args = ap.parse_args()
    cfg = load_json("momentum_config.json", None)
    if args.mode:
        cfg["mode"] = args.mode
    setup_logging()
    note = "stuendlich in der Cloud (GitHub Actions)" if args.command == "once" else None
    bot = MomentumBot(cfg, out_dir=args.out, check_note=note)
    if args.command == "status":
        bot.market.sync_time()
        bot.ex.setup(bot.universe)
        prices = bot.ex.prices()
        snap = bot.ex.snapshot(prices)
        start = bot.state.get("start")
        print(f"Modus     : {bot.ex.label}")
        print(f"Kontowert : {snap['equity']:.2f} USDT" +
              (f"  ({(snap['equity'] / start['equity'] - 1) * 100:+.2f} % seit Start)" if start else ""))
        for h in snap["holdings"]:
            print(f"  {h['symbol']:10s} {h['value']:9.2f} USDT  ({(h['price'] / h['entry'] - 1) * 100:+.1f} %)"
                  if h["entry"] else f"  {h['symbol']:10s} {h['value']:9.2f} USDT")
        if start:
            write_dashboard(bot.dashboard_data(snap, prices, bot.market._now_ms()), bot.dashboard_path)
            print(f"Dashboard aktualisiert: {bot.dashboard_path}")
        return
    if args.command == "once":
        bot.market.sync_time()
        bot.ex.setup(bot.universe)
        bot.check_connection()
        bot.cycle()
        log.info("Durchlauf fertig | Dashboard: %s", bot.dashboard_path)
        return
    try:
        bot.run()
    except KeyboardInterrupt:
        bot.save()
        log.info("Bot gestoppt. Gekaufte Coins bleiben im Konto.")


if __name__ == "__main__":
    main()

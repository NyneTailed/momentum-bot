"""Vollautomatischer Bitget Trading-Bot (USDT-Futures, mehrere Coins).

    python bot.py check     -> Verbindung, Demo-Kontostand und aktuelle Signale pruefen
    python bot.py run       -> Bot starten (laeuft bis Strg+C)

Standard ist DEMO-Modus (BITGET_DEMO=true in .env). Echtgeld nur mit
BITGET_DEMO=false UND "python bot.py run --live" plus Bestaetigung.

Ablauf pro Coin (order_type "limit"):
  Signal auf geschlossener Kerze -> Limit-Order (post_only = Maker-Gebuehr) mit Stop-Loss
  -> nicht gefuellt nach limit_valid_candles Kerzen: Order loeschen
  -> gefuellt: Take-Profit als reduce-only Limit-Order (Maker) setzen
  -> Position zu (SL oder TP): Ergebnis aus den Fills protokollieren, Rest-Orders loeschen
"""
import argparse
import csv
import json
import logging
import os
import sys
import time
import urllib.error
from datetime import datetime, timezone

from bitget import TIMEFRAME_MS, Bitget, BitgetError, floor_size, load_env, round_price
from strategy import compute_indicators, position_size, signal_at, stops

STATE_FILE = os.path.join("logs", "state.json")
TRADES_FILE = os.path.join("logs", "trades.csv")
log = logging.getLogger("bot")


def setup_logging():
    os.makedirs("logs", exist_ok=True)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    for handler in (logging.StreamHandler(sys.stdout),
                    logging.FileHandler(os.path.join("logs", "bot.log"), encoding="utf-8")):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    log.setLevel(logging.INFO)


def load_config():
    with open("config.json", encoding="utf-8") as f:
        return json.load(f)


def make_client():
    load_env()
    demo = os.environ.get("BITGET_DEMO", "true").strip().lower() != "false"
    return Bitget(
        api_key=os.environ.get("BITGET_API_KEY", ""),
        api_secret=os.environ.get("BITGET_API_SECRET", ""),
        passphrase=os.environ.get("BITGET_PASSPHRASE", ""),
        demo=demo,
    )


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    state.setdefault("symbols", {})
    return state


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def record_trade(row):
    new = not os.path.exists(TRADES_FILE)
    with open(TRADES_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()), delimiter=";")
        if new:
            w.writeheader()
        w.writerow(row)


def utc_str(ms=None):
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc) if ms else datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def equity_of(account):
    return float(account.get("accountEquity") or account.get("usdtEquity") or 0)


class TradingBot:
    def __init__(self, client, cfg):
        self.c = client
        self.cfg = cfg
        self.pt, self.coin = cfg["product_type"], cfg["margin_coin"]
        self.symbols = cfg["symbols"]
        self.tf_ms = TIMEFRAME_MS[cfg["timeframe"]]
        self.state = load_state()
        self.contracts = {}

    # ------------------------------------------------------------------ setup
    def setup(self):
        self.c.sync_time()
        self._try("Positionsmodus one-way", lambda: self.c.set_position_mode(self.pt))
        for sym in self.symbols:
            self.contracts[sym] = self.c.contract(sym, self.pt)
            self._try(f"{sym} Margin-Modus {self.cfg['margin_mode']}",
                      lambda: self.c.set_margin_mode(sym, self.pt, self.coin, self.cfg["margin_mode"]))
            self._try(f"{sym} Hebel {self.cfg['leverage']}x",
                      lambda: self.c.set_leverage(sym, self.pt, self.coin, self.cfg["leverage"]))

    def _try(self, name, call):
        try:
            call()
            log.info("Eingestellt: %s", name)
        except BitgetError as e:
            # z.B. schon gesetzt oder offene Position - nicht kritisch
            log.warning("Konnte %s nicht setzen: %s", name, e)

    # ------------------------------------------------------------------ main loop
    def run(self):
        self.setup()
        log.info("Bot laeuft: %s | %s | %s-Orders | %s | Abfrage alle %ss | Strg+C zum Beenden",
                 ", ".join(self.symbols), self.cfg["timeframe"], self.cfg["order_type"],
                 "DEMO" if self.c.demo else "ECHTGELD", self.cfg["poll_seconds"])
        errors = 0
        while True:
            try:
                self.tick()
                errors = 0
            except (BitgetError, urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                errors += 1
                log.error("Fehler (%s in Folge): %s", errors, e)
                if errors >= 3:
                    try:
                        self.c.sync_time()
                    except Exception:
                        pass
                time.sleep(min(60 * errors, 300))
                continue
            time.sleep(self.cfg["poll_seconds"])

    def tick(self):
        account = self.c.account(self.symbols[0], self.pt, self.coin)
        equity = equity_of(account)
        self.roll_day(equity)
        for sym in self.symbols:
            try:
                self.tick_symbol(sym, account, equity)
            except BitgetError as e:
                log.error("%s: %s", sym, e)
        save_state(self.state)

    def tick_symbol(self, sym, account, equity):
        st = self.state["symbols"].setdefault(sym, {})
        now = self.c._now_ms()
        candles = [c for c in self.c.candles(sym, self.pt, self.cfg["timeframe"], 500)
                   if c["ts"] + self.tf_ms <= now]  # nur geschlossene Kerzen
        last_ts = candles[-1]["ts"] if candles else 0

        if st.get("entry_order"):
            self.check_entry_order(sym, st, last_ts)

        positions = self.c.positions(sym, self.pt, self.coin)
        pos = positions[0] if positions else None
        trade = st.get("open_trade")

        if trade and not pos and not st.get("entry_order"):
            self.on_closed(sym, st, equity)
        elif pos and not trade:
            log.warning("%s: fremde/alte Position gefunden (%s %s) - wird uebernommen, ohne TP",
                        sym, pos.get("holdSide"), pos.get("total"))
            st["open_trade"] = {"time": utc_str(), "start_ms": now, "side": pos.get("holdSide"),
                                "size": pos.get("total"), "entry": pos.get("openPriceAvg"),
                                "sl": "", "tp": "", "tp_id": "adopted"}
        elif pos and trade and not st.get("entry_order") and not trade.get("tp_id"):
            self.place_take_profit(sym, trade, pos)

        # Neue Kerze? -> Signal auswerten
        if not candles or last_ts == st.get("last_candle_ts"):
            return
        st["last_candle_ts"] = last_ts
        p = self.cfg["strategy"]
        ind = compute_indicators(candles, p)
        i = len(candles) - 1
        sig, cross = signal_at(candles, ind, i, p)
        log.info("%-10s Kerze %s | Kurs %s | RSI %.1f | Signal: %s",
                 sym, datetime.fromtimestamp(last_ts / 1000).strftime("%d.%m %H:%M"),
                 candles[i]["close"], ind["rsi"][i] or 0, sig or "-")

        if pos:
            side = pos.get("holdSide")
            against = (side == "long" and cross == "cross_down") or (side == "short" and cross == "cross_up")
            if self.cfg["exit_on_opposite_cross"] and against:
                log.info("%s: Gegensignal -> schliesse %s-Position", sym, side)
                self.c.close_position(sym, self.pt)
            return
        if sig and not st.get("entry_order") and not st.get("open_trade"):
            self.try_open(sym, st, sig, candles[i], ind["atr"][i], account, equity)

    # ------------------------------------------------------------------ orders
    def check_entry_order(self, sym, st, last_ts):
        eo = st["entry_order"]
        d = self.c.order_detail(sym, self.pt, eo["id"])
        status = d.get("state") or d.get("status")
        filled = float(d.get("baseVolume") or 0)
        expired = last_ts >= eo["candle_ts"] + self.cfg["limit_valid_candles"] * self.tf_ms

        if status in ("live", "new", "partially_filled") and expired:
            try:
                self.c.cancel_order(sym, self.pt, self.coin, eo["id"])
            except BitgetError as e:
                log.warning("%s: Loeschen der Limit-Order fehlgeschlagen: %s", sym, e)
                return
            d = self.c.order_detail(sym, self.pt, eo["id"])
            filled = float(d.get("baseVolume") or 0)
            status = "canceled"

        if status in ("live", "new", "partially_filled"):
            return  # wartet noch

        st["entry_order"] = None
        if filled > 0:
            avg = d.get("priceAvg") or eo["price"]
            log.info("%s: Limit-Order gefuellt | %s @ %s", sym, filled, avg)
            st["open_trade"].update({"size": f"{filled}", "entry": avg})
        else:
            log.info("%s: Limit-Order nicht gefuellt -> verfallen", sym)
            st["open_trade"] = None

    def place_take_profit(self, sym, trade, pos):
        if not self.cfg.get("tp_as_limit"):
            trade["tp_id"] = "preset"
            return
        side = "sell" if pos.get("holdSide") == "long" else "buy"
        size = floor_size(float(pos.get("total")), self.contracts[sym])
        for force in ("post_only", "gtc"):
            try:
                res = self.c.place_order(sym, self.pt, self.coin, self.cfg["margin_mode"], side, size,
                                         "limit", trade["tp"], force, reduce_only=True)
                trade["tp_id"] = res.get("orderId")
                log.info("%s: Take-Profit Limit-Order gesetzt @ %s (%s)", sym, trade["tp"], force)
                return
            except BitgetError as e:
                log.warning("%s: TP-Order (%s) abgelehnt: %s", sym, force, e)
        trade["tp_id"] = "failed"  # SL bleibt aktiv; nicht endlos wiederholen

    def on_closed(self, sym, st, equity):
        trade = st["open_trade"]
        pnl = ""
        try:  # Ergebnis = Summe realisierter Gewinne + Gebuehren aller Fills seit Einstieg
            fills = self.c.fills(sym, self.pt, trade["start_ms"])
            pnl = round(sum(float(f.get("profit") or 0) + sum(float(x.get("totalFee") or 0)
                            for x in f.get("feeDetail") or []) for f in fills), 4)
        except BitgetError as e:
            log.warning("%s: Fills nicht abrufbar: %s", sym, e)
        for o in self.c.pending_orders(sym, self.pt):  # uebrig gebliebene TP-Order loeschen
            try:
                self.c.cancel_order(sym, self.pt, self.coin, o["orderId"])
            except BitgetError:
                pass
        log.info("%s: Position geschlossen | Ergebnis %s %s | Equity %.2f", sym, pnl, self.coin, equity)
        record_trade({
            "open_time": trade["time"], "close_time": utc_str(), "symbol": sym,
            "side": trade["side"], "size": trade["size"], "entry": trade["entry"],
            "stop_loss": trade["sl"], "take_profit": trade["tp"], "pnl": pnl,
            "equity": round(equity, 2), "mode": "demo" if self.c.demo else "live",
        })
        st["open_trade"] = None

    def try_open(self, sym, st, side, candle, atr_value, account, equity):
        cfg = self.cfg
        start_eq = self.state.get("day_start_equity", equity)
        if equity <= start_eq * (1 - cfg["max_daily_loss_pct"] / 100):
            log.warning("Tagesverlust-Limit erreicht (%.2f -> %.2f) - keine neuen Trades heute", start_eq, equity)
            return
        if self.state.get("trades_today", 0) >= cfg["max_trades_per_day"]:
            log.warning("Max. Trades pro Tag erreicht - keine neuen Trades heute")
            return
        busy = sum(1 for s in self.state["symbols"].values() if s.get("open_trade") or s.get("entry_order"))
        if busy >= cfg["max_open_positions"]:
            log.info("%s: Signal ignoriert, schon %s Positionen/Orders offen", sym, busy)
            return

        contract = self.contracts[sym]
        use_limit = cfg["order_type"] == "limit"
        off = atr_value * cfg.get("limit_offset_atr", 0.0) if use_limit else 0.0
        entry = candle["close"] - off if side == "long" else candle["close"] + off
        sl, tp = stops(side, entry, atr_value, cfg["strategy"])
        usable = min(equity, float(account.get("available") or equity))
        size = floor_size(position_size(usable, entry, sl, cfg["leverage"], cfg["risk_per_trade_pct"]), contract)
        min_size = float(contract.get("minTradeNum") or 0)
        min_usdt = float(contract.get("minTradeUSDT") or 0)
        if float(size) <= 0 or float(size) < min_size or float(size) * entry < min_usdt:
            log.warning("%s: Positionsgroesse %s zu klein (min %s / %s USDT) - Signal ignoriert",
                        sym, size, min_size, min_usdt)
            return

        entry_s, sl_s, tp_s = (round_price(x, contract) for x in (entry, sl, tp))
        order_side = "buy" if side == "long" else "sell"
        log.info(">>> %s %s | %s | Groesse %s | Preis %s | SL %s | TP %s",
                 sym, side.upper(), "LIMIT" if use_limit else "MARKET", size, entry_s, sl_s, tp_s)
        if use_limit:
            res = self.c.place_order(sym, self.pt, self.coin, cfg["margin_mode"], order_side, size,
                                     "limit", entry_s, "post_only", stop_loss=sl_s)
            st["entry_order"] = {"id": res.get("orderId"), "candle_ts": candle["ts"], "price": entry_s}
        else:
            res = self.c.place_order(sym, self.pt, self.coin, cfg["margin_mode"], order_side, size,
                                     stop_loss=sl_s, take_profit=None if cfg.get("tp_as_limit") else tp_s)
        log.info("%s: Order angenommen: %s", sym, res)
        self.state["trades_today"] = self.state.get("trades_today", 0) + 1
        st["open_trade"] = {"time": utc_str(), "start_ms": self.c._now_ms() - 60_000, "side": side,
                            "size": size, "entry": entry_s, "sl": sl_s, "tp": tp_s, "tp_id": None}

    def roll_day(self, equity):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.state.get("day") != today:
            self.state.update({"day": today, "day_start_equity": equity, "trades_today": 0})
            log.info("Neuer Handelstag (UTC) %s | Start-Equity %.2f", today, equity)


def cmd_check(client, cfg):
    print(f"Modus        : {'DEMO (paptrading)' if client.demo else 'ECHTGELD !!!'}")
    server = client.sync_time()
    print(f"Serverzeit   : {datetime.fromtimestamp(server / 1000)}  (Abweichung {client.time_offset_ms} ms)")
    print(f"Strategie    : {cfg['timeframe']}, {cfg['order_type']}-Orders, Hebel {cfg['leverage']}x\n")
    p = cfg["strategy"]
    for sym in cfg["symbols"]:
        contract = client.contract(sym, cfg["product_type"])
        candles = client.candles(sym, cfg["product_type"], cfg["timeframe"], 500)[:-1]
        ind = compute_indicators(candles, p)
        i = len(candles) - 1
        sig, _ = signal_at(candles, ind, i, p)
        trend = "ueber" if candles[i]["close"] > ind["trend"][i] else "unter"
        print(f"{sym:10s} Kurs {candles[i]['close']:<12} RSI {ind['rsi'][i]:5.1f}  {trend} EMA{p['ema_trend']}  "
              f"min. {contract.get('minTradeNum')}  Signal: {sig or 'keins'}")

    try:
        acc = client.account(cfg["symbols"][0], cfg["product_type"], cfg["margin_coin"])
        print(f"\nKontostand   : Equity {equity_of(acc):.2f} {cfg['margin_coin']}  "
              f"| verfuegbar {float(acc.get('available') or 0):.2f}")
        for sym in cfg["symbols"]:
            pos = client.positions(sym, cfg["product_type"], cfg["margin_coin"])
            if pos:
                print(f"Position     : {sym} {pos[0].get('holdSide')} {pos[0].get('total')}")
        print("\nAlles OK - der Bot kann mit 'python bot.py run' gestartet werden.")
    except BitgetError as e:
        print(f"\nAPI-Key-Test FEHLGESCHLAGEN: {e}")
        print("-> Pruefe Key/Secret/Passphrase in .env und ob es ein DEMO-API-Key ist.")


def main():
    ap = argparse.ArgumentParser(description="Bitget Trading-Bot")
    ap.add_argument("command", choices=["check", "run"])
    ap.add_argument("--live", action="store_true", help="Echtgeld erlauben (nur mit BITGET_DEMO=false)")
    args = ap.parse_args()

    cfg = load_config()
    client = make_client()

    if args.command == "check":
        cmd_check(client, cfg)
        return

    if not client.demo:
        if not args.live:
            sys.exit("BITGET_DEMO=false, aber --live fehlt. Abbruch zur Sicherheit.")
        if input("ECHTGELD-Modus! Tippe 'JA ECHTGELD' zum Fortfahren: ").strip() != "JA ECHTGELD":
            sys.exit("Abgebrochen.")

    setup_logging()
    bot = TradingBot(client, cfg)
    try:
        bot.run()
    except KeyboardInterrupt:
        save_state(bot.state)
        log.info("Bot gestoppt. Offene Positionen behalten ihren SL; offene Orders bleiben bei Bitget bestehen.")


if __name__ == "__main__":
    main()

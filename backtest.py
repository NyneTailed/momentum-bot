"""Backtest: testet die Strategie auf echten historischen Bitget-Kursen.

Braucht KEINE API-Keys. Aufruf:
    python backtest.py              (Tage aus config.json)
    python backtest.py --days 60
    python backtest.py --symbol ETHUSDT --timeframe 4H
    python backtest.py --order-type market
"""
import argparse
import csv
import json
import os
from datetime import datetime, timezone

from bitget import Bitget
from strategy import compute_indicators, position_size, signal_at, stops


def day_of(ts):
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date()


def fmt_ts(ts):
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def run_backtest(candles, cfg):
    """Simuliert die Strategie Kerze fuer Kerze.

    order_type "market": Einstieg zum Eroeffnungskurs der naechsten Kerze (Taker-Gebuehr + Slippage).
    order_type "limit":  Limit-Order zum Schlusskurs (optional limit_offset_atr besser), gilt
                         limit_valid_candles Kerzen. Gefuellt nur, wenn der Kurs den Preis
                         DURCHBRICHT (konservativ). Maker-Gebuehr, keine Slippage.
    tp_as_limit: Take-Profit als Limit-Order (Maker). Stop-Loss ist immer Market (Taker + Slippage).
    """
    p, bt = cfg["strategy"], cfg["backtest"]
    taker, maker = bt["fee_rate"], bt.get("maker_fee_rate", bt["fee_rate"])
    slip = bt["slippage_pct"] / 100
    use_limit = cfg.get("order_type", "market") == "limit"
    tp_fee = maker if cfg.get("tp_as_limit", False) else taker
    ind = compute_indicators(candles, p)

    equity = float(bt["start_equity"])
    peak, max_dd = equity, 0.0
    trades, pos, pending, order = [], None, None, None
    cur_day, day_start_equity, trades_today = None, equity, 0

    def open_pos(side, entry, atr_value, ts, fee_rate):
        nonlocal pos, trades_today
        sl, tp = stops(side, entry, atr_value, p)
        size = position_size(equity, entry, sl, cfg["leverage"], cfg["risk_per_trade_pct"])
        if size > 0:
            pos = {"side": side, "entry": entry, "sl": sl, "tp": tp, "size": size,
                   "ts": ts, "entry_fee": fee_rate}
            trades_today += 1

    def close(exit_price, ts, reason, exit_fee):
        nonlocal equity, pos
        d = 1 if pos["side"] == "long" else -1
        gross = (exit_price - pos["entry"]) * pos["size"] * d
        fees = (pos["entry"] * pos["entry_fee"] + exit_price * exit_fee) * pos["size"]
        pnl = gross - fees
        equity += pnl
        trades.append({
            "entry_time": fmt_ts(pos["ts"]), "exit_time": fmt_ts(ts), "side": pos["side"],
            "entry": round(pos["entry"], 4), "exit": round(exit_price, 4),
            "size": round(pos["size"], 6), "pnl": round(pnl, 4),
            "equity": round(equity, 2), "reason": reason,
        })
        pos = None

    def check_stop_loss(c):
        if pos["side"] == "long" and c["low"] <= pos["sl"]:
            close(min(pos["sl"], c["open"]) * (1 - slip), c["ts"], "stop_loss", taker)
        elif pos["side"] == "short" and c["high"] >= pos["sl"]:
            close(max(pos["sl"], c["open"]) * (1 + slip), c["ts"], "stop_loss", taker)

    for i, c in enumerate(candles):
        day = day_of(c["ts"])
        if day != cur_day:
            cur_day, day_start_equity, trades_today = day, equity, 0

        # 1) Aktionen vom Schluss der vorherigen Kerze zum Eroeffnungskurs ausfuehren
        if pending == "exit" and pos:
            px = c["open"] * (1 - slip if pos["side"] == "long" else 1 + slip)
            close(px, c["ts"], "gegensignal", taker)
        elif pending in ("long", "short") and not pos:
            entry = c["open"] * (1 + slip if pending == "long" else 1 - slip)
            open_pos(pending, entry, ind["atr"][i - 1], c["ts"], taker)
        pending = None

        # 1b) Offene Limit-Order: gefuellt, wenn der Kurs den Preis durchbricht
        just_filled = False
        if order and not pos:
            if i > order["expires"]:
                order = None
            elif order["side"] == "long" and c["low"] < order["price"]:
                open_pos("long", min(order["price"], c["open"]), order["atr"], c["ts"], maker)
                order, just_filled = None, True
            elif order["side"] == "short" and c["high"] > order["price"]:
                open_pos("short", max(order["price"], c["open"]), order["atr"], c["ts"], maker)
                order, just_filled = None, True

        # 2) Stop-Loss / Take-Profit innerhalb der Kerze (beide getroffen -> SL, konservativ).
        #    In der Fuell-Kerze einer Limit-Order zaehlt nur der SL (Reihenfolge unbekannt).
        if pos:
            check_stop_loss(c)
        if pos and not just_filled:
            if pos["side"] == "long" and c["high"] >= pos["tp"]:
                close(pos["tp"], c["ts"], "take_profit", tp_fee)
            elif pos["side"] == "short" and c["low"] <= pos["tp"]:
                close(pos["tp"], c["ts"], "take_profit", tp_fee)

        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100)

        # 3) Signal auf der geschlossenen Kerze auswerten -> naechste Kerze handeln
        if i == len(candles) - 1:
            break
        sig, cross = signal_at(candles, ind, i, p)
        if pos:
            against = (pos["side"] == "long" and cross == "cross_down") or \
                      (pos["side"] == "short" and cross == "cross_up")
            if cfg["exit_on_opposite_cross"] and against:
                pending = "exit"
        elif sig:
            daily_loss_hit = equity <= day_start_equity * (1 - cfg["max_daily_loss_pct"] / 100)
            if not daily_loss_hit and trades_today < cfg["max_trades_per_day"]:
                if use_limit:
                    off = ind["atr"][i] * cfg.get("limit_offset_atr", 0.0)
                    price = c["close"] - off if sig == "long" else c["close"] + off
                    order = {"side": sig, "price": price, "atr": ind["atr"][i],
                             "expires": i + cfg.get("limit_valid_candles", 2)}
                else:
                    pending = sig

    if pos:
        close(candles[-1]["close"], candles[-1]["ts"], "backtest_ende", taker)
    return trades, equity, max_dd


def summarize(candles, trades, equity, max_dd, cfg):
    start = cfg["backtest"]["start_equity"]
    gross_win = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gross_loss = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    return {
        "trades": len(trades),
        "winrate": len([t for t in trades if t["pnl"] > 0]) / len(trades) * 100 if trades else 0,
        "pf": gross_win / gross_loss if gross_loss else 0,
        "ret": (equity / start - 1) * 100,
        "dd": max_dd,
        "hold": (candles[-1]["close"] / candles[0]["open"] - 1) * 100,
    }


def main():
    with open("config.json", encoding="utf-8") as f:
        cfg = json.load(f)
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=cfg["backtest"]["days"])
    ap.add_argument("--symbol", help="nur diesen Coin testen, z.B. ETHUSDT")
    ap.add_argument("--timeframe", default=cfg["timeframe"])
    ap.add_argument("--order-type", choices=["limit", "market"], default=cfg.get("order_type", "market"))
    args = ap.parse_args()
    cfg["timeframe"], cfg["order_type"] = args.timeframe, args.order_type
    symbols = [args.symbol.upper()] if args.symbol else cfg["symbols"]

    client = Bitget(demo=False)  # oeffentliche Kursdaten, kein Key noetig
    client.sync_time()
    os.makedirs("results", exist_ok=True)
    rows = []
    for sym in symbols:
        print(f"Lade {args.days} Tage {sym} {cfg['timeframe']}-Kerzen ...")
        candles = client.history_candles(sym, cfg["product_type"], cfg["timeframe"], args.days)
        if len(candles) < cfg["strategy"]["ema_trend"] + 50:
            print(f"  zu wenige Kerzen ({len(candles)}), uebersprungen")
            continue
        trades, equity, max_dd = run_backtest(candles, cfg)
        rows.append((sym, summarize(candles, trades, equity, max_dd, cfg)))
        out = os.path.join("results", f"backtest_{sym}_{cfg['timeframe']}.csv")
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(trades[0].keys()) if trades else ["info"], delimiter=";")
            w.writeheader()
            w.writerows(trades or [{"info": "keine Trades"}])

    if not rows:
        return
    print()
    print("=" * 74)
    print(f" BACKTEST  {cfg['timeframe']}  {cfg['order_type']}-Orders  {args.days} Tage  "
          f"Start {cfg['backtest']['start_equity']} USDT je Coin")
    print("=" * 74)
    print(f" {'Coin':12s}{'Trades':>7s}{'Gewinnr.':>10s}{'Profit-F.':>11s}{'Rendite':>10s}{'Max-DD':>9s}{'Halten':>10s}")
    for sym, s in rows:
        print(f" {sym:12s}{s['trades']:7d}{s['winrate']:9.1f}%{s['pf']:11.2f}{s['ret']:+9.1f}%"
              f"{s['dd']:8.1f}%{s['hold']:+9.1f}%")
    avg = lambda k: sum(s[k] for _, s in rows) / len(rows)
    print("-" * 74)
    print(f" {'Durchschnitt':12s}{avg('trades'):7.0f}{avg('winrate'):9.1f}%{avg('pf'):11.2f}{avg('ret'):+9.1f}%"
          f"{avg('dd'):8.1f}%{avg('hold'):+9.1f}%")
    print("=" * 74)
    print(" Faustregel: Profit-Faktor > 1.3 ueber mehrere Zeitraeume und Coins,")
    print(" sonst ist die Strategie vermutlich Zufall. Trades je Coin in results\\")
    print()


if __name__ == "__main__":
    main()

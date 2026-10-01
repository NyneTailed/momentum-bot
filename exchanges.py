"""Ausfuehrung fuer den Momentum-Bot.

PaperExchange      : simuliert ein Spot-Konto lokal mit Live-Kursen von Bitget (kein Key noetig)
BitgetDemoExchange : handelt auf deinem Bitget-Demo-Konto (USDT-Futures, 1x Hebel, nur Long = wie Spot)
"""
import time

from bitget import Bitget, BitgetError, floor_size

PT, COIN = "USDT-FUTURES", "USDT"


class PaperExchange:
    name = "paper"
    label = "Simulation (Spot)"

    def __init__(self, state, cfg):
        self.market = Bitget(demo=False)
        self.fee = cfg["paper"]["fee_rate"]
        self.slip = cfg["paper"]["slippage_pct"] / 100
        self.acc = state.setdefault("paper", {"cash": float(cfg["paper"]["start_usdt"]), "units": {}, "entry": {}})

    def setup(self, symbols):
        self.market.sync_time()

    def prices(self):
        return self.market.tickers(PT)

    def snapshot(self, prices):
        holdings = [{"symbol": s, "qty": q, "price": prices.get(s, 0), "value": q * prices.get(s, 0),
                     "entry": self.acc["entry"].get(s, 0)} for s, q in self.acc["units"].items() if q > 0]
        equity = self.acc["cash"] + sum(h["value"] for h in holdings)
        return {"equity": equity, "available": self.acc["cash"], "holdings": holdings}

    def buy(self, symbol, usdt, price):
        px = price * (1 + self.slip)
        fee = usdt * self.fee
        qty = (usdt - fee) / px
        old = self.acc["units"].get(symbol, 0)
        self.acc["entry"][symbol] = (old * self.acc["entry"].get(symbol, 0) + qty * px) / (old + qty)
        self.acc["units"][symbol] = old + qty
        self.acc["cash"] -= usdt
        return {"qty": qty, "price": px, "usdt": usdt, "fee": fee}

    def sell(self, symbol, qty, price, close_all=False):
        held = self.acc["units"].get(symbol, 0)
        qty = held if close_all else min(qty, held)
        px = price * (1 - self.slip)
        gross = qty * px
        fee = gross * self.fee
        self.acc["units"][symbol] = held - qty
        if self.acc["units"][symbol] <= 1e-12:
            self.acc["units"].pop(symbol, None)
            self.acc["entry"].pop(symbol, None)
        self.acc["cash"] += gross - fee
        return {"qty": qty, "price": px, "usdt": gross - fee, "fee": fee}


class BitgetDemoExchange:
    name = "bitget_demo"
    label = "Bitget Demo-Konto (Futures 1x, nur Long)"

    def __init__(self, state, cfg, client):
        self.c = client
        self.contracts = {}

    def setup(self, symbols):
        self.c.sync_time()
        try:
            self.c.set_position_mode(PT)
        except BitgetError:
            pass  # schon gesetzt
        for s in symbols:
            self.contracts[s] = self.c.contract(s, PT)
            for call in (lambda: self.c.set_margin_mode(s, PT, COIN, "isolated"),
                         lambda: self.c.set_leverage(s, PT, COIN, 1)):
                try:
                    call()
                except BitgetError:
                    pass  # schon gesetzt oder Position offen

    def prices(self):
        return self.c.tickers(PT)

    def snapshot(self, prices):
        acc = self.c.account("BTCUSDT", PT, COIN)
        holdings = []
        for p in self.c.all_positions(PT, COIN):
            if p.get("holdSide") != "long":
                continue
            s, q = p["symbol"], float(p["total"])
            holdings.append({"symbol": s, "qty": q, "price": prices.get(s, 0), "value": q * prices.get(s, 0),
                             "entry": float(p.get("openPriceAvg") or 0)})
        return {"equity": float(acc.get("accountEquity") or 0),
                "available": float(acc.get("available") or 0), "holdings": holdings}

    def _fill(self, symbol, order_id, fallback_qty, fallback_price):
        time.sleep(1.5)
        try:
            d = self.c.order_detail(symbol, PT, order_id)
            qty = float(d.get("baseVolume") or fallback_qty)
            px = float(d.get("priceAvg") or fallback_price)
            return qty, px, abs(float(d.get("fee") or 0))
        except BitgetError:
            return fallback_qty, fallback_price, 0.0

    def buy(self, symbol, usdt, price):
        contract = self.contracts.get(symbol) or self.c.contract(symbol, PT)
        size = floor_size(usdt / price, contract)
        if float(size) <= 0 or float(size) * price < float(contract.get("minTradeUSDT") or 5):
            raise BitgetError("SMALL", f"{symbol}: Kaufbetrag {usdt:.2f} USDT zu klein")
        res = self.c.place_order(symbol, PT, COIN, "isolated", "buy", size)
        qty, px, fee = self._fill(symbol, res.get("orderId"), float(size), price)
        return {"qty": qty, "price": px, "usdt": qty * px, "fee": fee}

    def sell(self, symbol, qty, price, close_all=False):
        if close_all:
            self.c.close_position(symbol, PT)
            return {"qty": qty, "price": price, "usdt": qty * price, "fee": 0.0}
        contract = self.contracts.get(symbol) or self.c.contract(symbol, PT)
        size = floor_size(qty, contract)
        if float(size) <= 0:
            raise BitgetError("SMALL", f"{symbol}: Verkaufsmenge zu klein")
        res = self.c.place_order(symbol, PT, COIN, "isolated", "sell", size, reduce_only=True)
        q, px, fee = self._fill(symbol, res.get("orderId"), float(size), price)
        return {"qty": q, "price": px, "usdt": q * px, "fee": fee}

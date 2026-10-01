"""Minimaler Bitget REST-Client (API v2, USDT-Futures) - nur Python-Standardbibliothek.

Demo-Modus: Demo-API-Key + Header "paptrading: 1" (siehe Bitget API-Doku "Demo Trading").
"""
import base64
import hashlib
import hmac
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://api.bitget.com"

TIMEFRAME_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1H": 3_600_000, "4H": 14_400_000, "6H": 21_600_000, "12H": 43_200_000, "1D": 86_400_000,
}


class BitgetError(Exception):
    def __init__(self, code, msg):
        super().__init__(f"Bitget-Fehler {code}: {msg}")
        self.code = code
        self.msg = msg


def load_env(path=".env"):
    """Liest KEY=VALUE Zeilen aus einer .env-Datei in os.environ (ohne Zusatzpaket)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class Bitget:
    def __init__(self, api_key="", api_secret="", passphrase="", demo=True, timeout=15):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.demo = demo
        self.timeout = timeout
        self.time_offset_ms = 0

    # ------------------------------------------------------------------ low level
    def _now_ms(self):
        return int(time.time() * 1000) + self.time_offset_ms

    def _sign(self, timestamp, method, path_with_query, body):
        message = f"{timestamp}{method}{path_with_query}{body}"
        mac = hmac.new(self.api_secret.encode(), message.encode(), hashlib.sha256)
        return base64.b64encode(mac.digest()).decode()

    def _request(self, method, path, params=None, body=None, auth=False, demo_header=True):
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
        path_with_query = path + ("?" + query if query else "")
        body_str = json.dumps(body, separators=(",", ":")) if body is not None else ""

        headers = {"Content-Type": "application/json", "locale": "en-US"}
        if self.demo and demo_header:
            headers["paptrading"] = "1"
        if auth:
            if not (self.api_key and self.api_secret and self.passphrase):
                raise BitgetError("NO_KEYS", "API-Key, Secret oder Passphrase fehlt (.env pruefen)")
            ts = str(self._now_ms())
            headers.update({
                "ACCESS-KEY": self.api_key,
                "ACCESS-SIGN": self._sign(ts, method, path_with_query, body_str),
                "ACCESS-TIMESTAMP": ts,
                "ACCESS-PASSPHRASE": self.passphrase,
            })

        req = urllib.request.Request(
            BASE_URL + path_with_query,
            data=body_str.encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            try:
                payload = json.loads(raw)
            except ValueError:
                raise BitgetError(e.code, raw[:300]) from None
        if str(payload.get("code")) != "00000":
            raise BitgetError(payload.get("code"), payload.get("msg"))
        return payload.get("data")

    # ------------------------------------------------------------------ public
    def sync_time(self):
        # Im Demo-Modus gibt es diesen Endpunkt nicht (40404) -> ohne paptrading-Header abfragen
        data = self._request("GET", "/api/v2/public/time", demo_header=False)
        self.time_offset_ms = int(data["serverTime"]) - int(time.time() * 1000)
        return int(data["serverTime"])

    def contract(self, symbol, product_type):
        data = self._request("GET", "/api/v2/mix/market/contracts",
                             {"productType": product_type, "symbol": symbol})
        if not data:
            raise BitgetError("NO_CONTRACT", f"Symbol {symbol} nicht gefunden")
        return data[0]

    def candles(self, symbol, product_type, timeframe, limit=500):
        """Neueste Kerzen, aufsteigend sortiert. Die letzte Kerze ist meist noch offen."""
        data = self._request("GET", "/api/v2/mix/market/candles", {
            "symbol": symbol, "productType": product_type,
            "granularity": timeframe, "limit": str(min(limit, 1000)),
        })
        return _parse_candles(data)

    def history_candles(self, symbol, product_type, timeframe, days):
        """Historische Kerzen fuer Backtests (blaetter rueckwaerts in 200er-Schritten)."""
        tf_ms = TIMEFRAME_MS[timeframe]
        end = self._now_ms()
        start = end - days * 86_400_000
        out = {}
        while end > start:
            for attempt in range(6):
                try:
                    data = self._request("GET", "/api/v2/mix/market/history-candles", {
                        "symbol": symbol, "productType": product_type, "granularity": timeframe,
                        "endTime": str(end), "limit": "200",
                    })
                    break
                except BitgetError as e:
                    if str(e.code) != "429" or attempt == 5:
                        raise
                    time.sleep(1 + attempt)  # Rate-Limit: kurz warten und erneut versuchen
            batch = _parse_candles(data)
            if not batch:
                break
            for c in batch:
                if c["ts"] >= start:
                    out[c["ts"]] = c
            oldest = batch[0]["ts"]
            if oldest >= end:
                break
            end = oldest - tf_ms // 2
            time.sleep(0.12)  # Rate-Limit schonen
        return [out[k] for k in sorted(out)]

    def tickers(self, product_type):
        """Letzte Preise aller Kontrakte: {symbol: preis}."""
        data = self._request("GET", "/api/v2/mix/market/tickers", {"productType": product_type}) or []
        return {t["symbol"]: float(t["lastPr"]) for t in data if t.get("lastPr")}

    # ------------------------------------------------------------------ private
    def all_positions(self, product_type, margin_coin):
        data = self._request("GET", "/api/v2/mix/position/all-position", {
            "productType": product_type, "marginCoin": margin_coin,
        }, auth=True) or []
        return [p for p in data if float(p.get("total") or 0) > 0]

    def account(self, symbol, product_type, margin_coin):
        return self._request("GET", "/api/v2/mix/account/account", {
            "symbol": symbol, "productType": product_type, "marginCoin": margin_coin,
        }, auth=True)

    def positions(self, symbol, product_type, margin_coin):
        data = self._request("GET", "/api/v2/mix/position/single-position", {
            "symbol": symbol, "productType": product_type, "marginCoin": margin_coin,
        }, auth=True) or []
        return [p for p in data if float(p.get("total") or 0) > 0]

    def set_position_mode(self, product_type, mode="one_way_mode"):
        return self._request("POST", "/api/v2/mix/account/set-position-mode",
                             body={"productType": product_type, "posMode": mode}, auth=True)

    def set_margin_mode(self, symbol, product_type, margin_coin, margin_mode):
        return self._request("POST", "/api/v2/mix/account/set-margin-mode", body={
            "symbol": symbol, "productType": product_type,
            "marginCoin": margin_coin, "marginMode": margin_mode,
        }, auth=True)

    def set_leverage(self, symbol, product_type, margin_coin, leverage):
        return self._request("POST", "/api/v2/mix/account/set-leverage", body={
            "symbol": symbol, "productType": product_type,
            "marginCoin": margin_coin, "leverage": str(leverage),
        }, auth=True)

    def place_order(self, symbol, product_type, margin_coin, margin_mode, side, size,
                    order_type="market", price=None, force="gtc",
                    stop_loss=None, take_profit=None, reduce_only=False):
        """side: buy/sell (one-way-Modus). force: gtc oder post_only (garantiert Maker-Gebuehr)."""
        body = {
            "symbol": symbol, "productType": product_type, "marginMode": margin_mode,
            "marginCoin": margin_coin, "size": size, "side": side, "orderType": order_type,
            "clientOid": f"bot{self._now_ms()}",
        }
        if order_type == "limit":
            body["price"] = price
            body["force"] = force
        if reduce_only:
            body["reduceOnly"] = "YES"
        if stop_loss:
            body["presetStopLossPrice"] = stop_loss
        if take_profit:
            body["presetStopSurplusPrice"] = take_profit
        return self._request("POST", "/api/v2/mix/order/place-order", body=body, auth=True)

    def order_detail(self, symbol, product_type, order_id):
        return self._request("GET", "/api/v2/mix/order/detail", {
            "symbol": symbol, "productType": product_type, "orderId": order_id,
        }, auth=True)

    def pending_orders(self, symbol, product_type):
        data = self._request("GET", "/api/v2/mix/order/orders-pending", {
            "symbol": symbol, "productType": product_type,
        }, auth=True) or {}
        return data.get("entrustedList") or []

    def cancel_order(self, symbol, product_type, margin_coin, order_id):
        return self._request("POST", "/api/v2/mix/order/cancel-order", body={
            "symbol": symbol, "productType": product_type,
            "marginCoin": margin_coin, "orderId": order_id,
        }, auth=True)

    def fills(self, symbol, product_type, start_ms):
        data = self._request("GET", "/api/v2/mix/order/fills", {
            "symbol": symbol, "productType": product_type,
            "startTime": str(start_ms), "limit": "100",
        }, auth=True) or {}
        return data.get("fillList") or []

    def close_position(self, symbol, product_type):
        return self._request("POST", "/api/v2/mix/order/close-positions",
                             body={"symbol": symbol, "productType": product_type}, auth=True)


def _parse_candles(data):
    candles = [{
        "ts": int(r[0]), "open": float(r[1]), "high": float(r[2]),
        "low": float(r[3]), "close": float(r[4]), "volume": float(r[5]),
    } for r in (data or [])]
    candles.sort(key=lambda c: c["ts"])
    return candles


# ---------------------------------------------------------------------- rounding
def round_price(price, contract):
    """Rundet auf den erlaubten Preis-Tick (pricePlace + priceEndStep)."""
    places = int(contract["pricePlace"])
    tick = float(contract.get("priceEndStep") or 1) * 10 ** -places
    return f"{round(price / tick) * tick:.{places}f}"


def floor_size(size, contract):
    """Rundet die Positionsgroesse nach unten auf die erlaubte Schrittweite."""
    places = int(contract["volumePlace"])
    step = float(contract.get("sizeMultiplier") or 10 ** -places)
    floored = math.floor(size / step + 1e-9) * step
    return f"{floored:.{places}f}"

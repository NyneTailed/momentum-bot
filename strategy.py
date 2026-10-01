"""Handelsstrategie: EMA-Crossover mit Trendfilter, RSI-Filter und ATR-Stops.

Wird identisch vom Backtest und vom Live-Bot benutzt, damit beide gleich handeln.

LONG  wenn: schnelle EMA kreuzt langsame EMA nach oben, Kurs ueber Trend-EMA,
            RSI zwischen rsi_long_min und rsi_long_max
SHORT wenn: spiegelverkehrt
Stop-Loss / Take-Profit: Vielfache der ATR (Volatilitaet) vom Einstiegskurs.
"""


def ema(values, period):
    out = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values, period):
    out = [None] * len(values)
    if len(values) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        diff = values[i] - values[i - 1]
        gains += max(diff, 0)
        losses += max(-diff, 0)
    avg_gain, avg_loss = gains / period, losses / period
    out[period] = 100 - 100 / (1 + avg_gain / avg_loss) if avg_loss else 100.0
    for i in range(period + 1, len(values)):
        diff = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(diff, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-diff, 0)) / period
        out[i] = 100 - 100 / (1 + avg_gain / avg_loss) if avg_loss else 100.0
    return out


def atr(candles, period):
    out = [None] * len(candles)
    if len(candles) <= period:
        return out
    trs = [candles[0]["high"] - candles[0]["low"]]
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    prev = sum(trs[1:period + 1]) / period
    out[period] = prev
    for i in range(period + 1, len(candles)):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def compute_indicators(candles, p):
    closes = [c["close"] for c in candles]
    return {
        "fast": ema(closes, p["ema_fast"]),
        "slow": ema(closes, p["ema_slow"]),
        "trend": ema(closes, p["ema_trend"]),
        "rsi": rsi(closes, p["rsi_period"]),
        "atr": atr(candles, p["atr_period"]),
    }


def signal_at(candles, ind, i, p):
    """Signal auf Basis der GESCHLOSSENEN Kerze i: 'long', 'short' oder None.

    Zusaetzlich 'cross_up' / 'cross_down' fuer den Ausstieg bei Gegensignal.
    """
    f, s, t, r, a = ind["fast"], ind["slow"], ind["trend"], ind["rsi"], ind["atr"]
    if i < 1 or None in (f[i], s[i], f[i - 1], s[i - 1], t[i], r[i], a[i]):
        return None, None
    close = candles[i]["close"]
    cross_up = f[i - 1] <= s[i - 1] and f[i] > s[i]
    cross_down = f[i - 1] >= s[i - 1] and f[i] < s[i]
    cross = "cross_up" if cross_up else "cross_down" if cross_down else None

    if cross_up and close > t[i] and p["rsi_long_min"] <= r[i] <= p["rsi_long_max"]:
        return "long", cross
    if cross_down and close < t[i] and p["rsi_short_min"] <= r[i] <= p["rsi_short_max"]:
        return "short", cross
    return None, cross


def stops(side, entry, atr_value, p):
    sl_dist = atr_value * p["sl_atr_mult"]
    tp_dist = atr_value * p["tp_atr_mult"]
    if side == "long":
        return entry - sl_dist, entry + tp_dist
    return entry + sl_dist, entry - tp_dist


def position_size(equity, entry, stop_loss, leverage, risk_pct):
    """Groesse so, dass ein Stop-Loss-Treffer ca. risk_pct % des Kontos kostet.

    Begrenzt durch das verfuegbare Hebel-Volumen (95 % Puffer).
    """
    risk_amount = equity * risk_pct / 100
    dist = abs(entry - stop_loss)
    if dist <= 0:
        return 0.0
    size = risk_amount / dist
    max_size = equity * leverage * 0.95 / entry
    return min(size, max_size)

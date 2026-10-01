"""Erzeugt dashboard.html: eigenstaendige Seite mit Kontostand, Verlauf, Coins und Trades.

Die Seite laedt sich jede Minute selbst neu; der Bot schreibt sie bei jeder Pruefung neu.
"""
import json
import os

OUT = "dashboard.html"

TEMPLATE = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>Momentum-Bot</title>
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --ring: rgba(11,11,11,0.10);
  --series-1: #2a78d6; --series-2: #eb6834;
  --good: #006300; --good-mark: #0ca30c; --bad: #d03b3b;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --ring: rgba(255,255,255,0.10);
    --series-1: #3987e5; --series-2: #d95926;
    --good: #0ca30c; --good-mark: #0ca30c; --bad: #e66767;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --ring: rgba(255,255,255,0.10);
  --series-1: #3987e5; --series-2: #d95926;
  --good: #0ca30c; --good-mark: #0ca30c; --bad: #e66767;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink);
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1080px; margin: 0 auto; padding: 24px 16px 48px; }
header { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 16px; margin-bottom: 20px; }
h1 { font-size: 22px; margin: 0; }
h2 { font-size: 15px; margin: 0 0 12px; }
.badge { font-size: 12px; padding: 2px 10px; border-radius: 999px; box-shadow: inset 0 0 0 1px var(--ring); color: var(--ink-2); }
.updated { color: var(--muted); font-size: 12px; margin-left: auto; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 16px; }
.card { background: var(--surface); border-radius: 12px; box-shadow: 0 0 0 1px var(--ring); padding: 16px; }
.tile .label { color: var(--ink-2); font-size: 12px; }
.tile .value { font-size: 24px; font-weight: 600; margin-top: 4px; }
.tile .sub { color: var(--muted); font-size: 12px; margin-top: 2px; }
.up { color: var(--good); } .down { color: var(--bad); }
.status { display: inline-flex; align-items: center; gap: 6px; }
.status svg { flex: none; }
.grid2 { display: grid; grid-template-columns: 3fr 2fr; gap: 12px; margin-top: 12px; }
.grid2 > * { min-width: 0; }
@media (max-width: 760px) { .grid2 { grid-template-columns: 1fr; } }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
th { text-align: left; color: var(--muted); font-weight: 500; font-size: 12px; padding: 6px 8px; border-bottom: 1px solid var(--grid); }
td { padding: 7px 8px; border-bottom: 1px solid var(--grid); }
td.num, th.num { text-align: right; }
tr:last-child td { border-bottom: none; }
.empty { color: var(--muted); padding: 12px 0; }
.legend { display: flex; gap: 16px; font-size: 12px; color: var(--ink-2); margin-bottom: 8px; }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.sw { width: 12px; height: 3px; border-radius: 2px; display: inline-block; }
#chart { position: relative; }
#chart svg { display: block; width: 100%; height: auto; }
.tip { position: absolute; pointer-events: none; background: var(--surface); box-shadow: 0 0 0 1px var(--ring), 0 4px 12px rgba(0,0,0,.12);
  border-radius: 8px; padding: 8px 10px; font-size: 12px; white-space: nowrap; display: none; }
.tip .row { display: flex; align-items: center; gap: 6px; }
.tablewrap { overflow-x: auto; }
footer { color: var(--muted); font-size: 12px; margin-top: 20px; }
.pick { font-size: 11px; color: var(--ink-2); box-shadow: inset 0 0 0 1px var(--ring); border-radius: 4px; padding: 0 5px; margin-left: 4px; }
</style>
</head>
<body>
<main>
  <header>
    <h1>Momentum-Bot</h1>
    <span class="badge" id="mode"></span>
    <span class="updated" id="updated"></span>
  </header>
  <section class="tiles" id="tiles"></section>
  <section class="card">
    <h2 id="chart-title">Kontowert</h2>
    <div class="legend">
      <span><i class="sw" style="background:var(--series-1)"></i>Bot</span>
      <span><i class="sw" style="background:var(--series-2)"></i>Nur BTC gehalten (zum Vergleich)</span>
    </div>
    <div id="chart"></div>
  </section>
  <div class="grid2">
    <section class="card"><h2>Aktuelle Coins</h2><div class="tablewrap" id="holdings"></div></section>
    <section class="card"><h2 id="rank-title">Momentum-Rangliste</h2><div class="tablewrap" id="ranking"></div></section>
  </div>
  <section class="card" style="margin-top:12px"><h2>Letzte Trades</h2><div class="tablewrap" id="trades"></div></section>
  <footer id="footer"></footer>
</main>
<script>
const D = __DATA__;
const $ = id => document.getElementById(id);
const usd = (v, d = 2) => v == null ? "–" : v.toLocaleString("de-DE", {minimumFractionDigits: d, maximumFractionDigits: d});
const pct = v => v == null ? "–" : (v > 0 ? "+" : "") + v.toLocaleString("de-DE", {minimumFractionDigits: 1, maximumFractionDigits: 1}) + " %";
const cls = v => v > 0 ? "up" : v < 0 ? "down" : "";
const dt = ms => new Date(ms).toLocaleString("de-DE", {day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit"});
const dshort = ms => new Date(ms).toLocaleDateString("de-DE", {day: "2-digit", month: "2-digit"});
const coin = s => s.replace("USDT", "");
const ICON_OK = '<svg width="16" height="16" viewBox="0 0 16 16"><circle cx="8" cy="8" r="7" fill="var(--good-mark)"/><path d="M4.5 8.2l2.2 2.2 4.8-4.8" stroke="#fff" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const ICON_STOP = '<svg width="16" height="16" viewBox="0 0 16 16"><circle cx="8" cy="8" r="7" fill="var(--bad)"/><rect x="4.5" y="7" width="7" height="2" rx="1" fill="#fff"/></svg>';

$("mode").textContent = D.mode_label;
$("updated").textContent = "Stand " + dt(D.updated) + " · lädt jede Minute neu";

// ---- Kacheln
const start = D.start, sig = D.signal || {};
const gain = D.equity - start.equity, gainPct = gain / start.equity * 100;
const btcPct = start.btc ? (D.btc_price / start.btc - 1) * 100 : null;
const smaDist = sig.btc_sma ? (sig.btc / sig.btc_sma - 1) * 100 : null;
const tiles = [
  {label: "Kontowert", value: usd(D.equity) + " USDT", sub: "davon frei: " + usd(D.available) + " USDT"},
  {label: "Ergebnis seit Start", value: `<span class="${cls(gain)}">${gain >= 0 ? "+" : ""}${usd(gain)} USDT</span>`,
   sub: `<span class="${cls(gainPct)}">${pct(gainPct)}</span> seit ${dt(start.ts)}`},
  {label: "BTC im selben Zeitraum", value: `<span class="${cls(btcPct)}">${pct(btcPct)}</span>`,
   sub: "BTC jetzt " + usd(D.btc_price, 0) + " $"},
  {label: "Schutzschalter", value: sig.risk_on == null ? "–" :
     `<span class="status">${sig.risk_on ? ICON_OK + "Investiert" : ICON_STOP + "USDT halten"}</span>`,
   sub: smaDist == null ? "wartet auf ersten Tagesschluss" :
     `BTC ${pct(smaDist)} zum ${D.sma_days}-Tage-Schnitt`},
  {label: "Nächste Umschichtung", value: D.next_rebalance ? dshort(D.next_rebalance) : "beim nächsten Tagesschluss",
   sub: "jeweils zum Tagesschluss (18:00 Uhr MESZ)"},
];
$("tiles").innerHTML = tiles.map(t => `<div class="card tile"><div class="label">${t.label}</div>
  <div class="value">${t.value}</div><div class="sub">${t.sub}</div></div>`).join("");

// ---- Chart: Bot vs. BTC (auf Startkapital normiert)
(function chart() {
  const el = $("chart");
  const pts = D.curve.filter(p => p[2]);
  $("chart-title").textContent = `Kontowert in USDT (Start ${usd(start.equity, 0)} USDT)`;
  if (pts.length < 2) { el.innerHTML = '<div class="empty">Noch zu wenig Daten – der Verlauf erscheint nach ein paar Stunden Laufzeit.</div>'; return; }
  const btc0 = start.btc || pts[0][2];
  const S = [pts.map(p => [p[0], p[1]]), pts.map(p => [p[0], p[2] / btc0 * start.equity])];
  const W = 1000, H = 300, m = {l: 64, r: 92, t: 12, b: 28};
  const xs = pts.map(p => p[0]), all = S.flat().map(p => p[1]);
  let lo = Math.min(...all), hi = Math.max(...all); const pad = (hi - lo) * 0.08 || hi * 0.02; lo -= pad; hi += pad;
  const x = t => m.l + (t - xs[0]) / ((xs[xs.length - 1] - xs[0]) || 1) * (W - m.l - m.r);
  const y = v => m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b);
  const step = niceStep((hi - lo) / 4); let g = "";
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step)
    g += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}" stroke="var(--grid)"/>
          <text x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="var(--muted)">${usd(v, 0)}</text>`;
  const nx = 5; for (let i = 0; i < nx; i++) { const t = xs[0] + (xs[xs.length - 1] - xs[0]) * i / (nx - 1);
    g += `<text x="${x(t)}" y="${H - 8}" text-anchor="middle" font-size="11" fill="var(--muted)">${dshort(t)}</text>`; }
  const path = s => s.map((p, i) => (i ? "L" : "M") + x(p[0]).toFixed(1) + "," + y(p[1]).toFixed(1)).join("");
  const names = ["Bot", "BTC"], cols = ["var(--series-1)", "var(--series-2)"];
  let lines = "", labels = [];
  S.forEach((s, k) => { lines += `<path d="${path(s)}" fill="none" stroke="${cols[k]}" stroke-width="2" stroke-linejoin="round"/>`;
    labels.push({k, y: y(s[s.length - 1][1]), v: s[s.length - 1][1]}); });
  labels.sort((a, b) => a.y - b.y); if (labels[1].y - labels[0].y < 16) labels[1].y = labels[0].y + 16;
  const lab = labels.map(l => `<circle cx="${W - m.r + 8}" cy="${l.y}" r="4" fill="${cols[l.k]}"/>
     <text x="${W - m.r + 16}" y="${l.y + 4}" font-size="11" fill="var(--ink-2)">${names[l.k]} ${usd(l.v, 0)}</text>`).join("");
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Kontowert Bot im Vergleich zu BTC">
    ${g}<line x1="${m.l}" x2="${W - m.r}" y1="${H - m.b}" y2="${H - m.b}" stroke="var(--axis)"/>${lines}${lab}
    <line id="xh" y1="${m.t}" y2="${H - m.b}" stroke="var(--axis)" visibility="hidden"/>
    <circle id="d0" r="5" fill="${cols[0]}" stroke="var(--surface)" stroke-width="2" visibility="hidden"/>
    <circle id="d1" r="5" fill="${cols[1]}" stroke="var(--surface)" stroke-width="2" visibility="hidden"/>
    <rect id="hit" x="${m.l}" y="0" width="${W - m.l - m.r}" height="${H}" fill="transparent"/></svg><div class="tip" id="tip"></div>`;
  const svg = el.querySelector("svg"), tip = $("tip");
  const show = v => ["xh", "d0", "d1"].forEach(id => svg.getElementById(id).setAttribute("visibility", v));
  svg.getElementById("hit").addEventListener("mousemove", e => {
    const r = svg.getBoundingClientRect(), mx = (e.clientX - r.left) / r.width * W;
    let i = 0, best = Infinity; xs.forEach((t, j) => { const d = Math.abs(x(t) - mx); if (d < best) { best = d; i = j; } });
    const px = x(xs[i]); show("visible");
    svg.getElementById("xh").setAttribute("x1", px); svg.getElementById("xh").setAttribute("x2", px);
    [0, 1].forEach(k => { const c = svg.getElementById("d" + k); c.setAttribute("cx", px); c.setAttribute("cy", y(S[k][i][1])); });
    tip.innerHTML = `<div style="color:var(--muted);margin-bottom:4px">${dt(xs[i])}</div>` +
      [0, 1].map(k => `<div class="row"><i class="sw" style="background:${cols[k]}"></i>${names[k]}: <b>${usd(S[k][i][1])} USDT</b></div>`).join("");
    tip.style.display = "block";
    const left = px / W * r.width; tip.style.left = Math.min(left + 12, r.width - tip.offsetWidth - 4) + "px"; tip.style.top = "8px";
  });
  svg.getElementById("hit").addEventListener("mouseleave", () => { show("hidden"); tip.style.display = "none"; });
  function niceStep(s) { const p = Math.pow(10, Math.floor(Math.log10(s))); const n = s / p; return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * p; }
})();

// ---- Tabellen
const table = (head, rows, empty) => rows.length ? `<table><thead><tr>${head.map(h =>
  `<th class="${h[1] || ""}">${h[0]}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table>` : `<div class="empty">${empty}</div>`;

const hs = D.holdings.slice().sort((a, b) => b.value - a.value);
$("holdings").innerHTML = table([["Coin"], ["Wert USDT", "num"], ["Anteil", "num"], ["Einstand", "num"], ["Kurs", "num"], ["+/-", "num"]],
  hs.map(h => { const ch = h.entry ? (h.price / h.entry - 1) * 100 : null;
    return `<tr><td><b>${coin(h.symbol)}</b></td><td class="num">${usd(h.value)}</td><td class="num">${pct(h.value / D.equity * 100)}</td>
      <td class="num">${h.entry ? h.entry.toPrecision(6) : "–"}</td><td class="num">${h.price.toPrecision(6)}</td><td class="num ${cls(ch)}">${pct(ch)}</td></tr>`; }),
  sig.risk_on === false ? "Keine Coins – der Schutzschalter hält alles in USDT." : "Noch keine Coins gekauft.");

$("rank-title").textContent = `Momentum-Rangliste (${D.lookback} Tage)`;
const picks = new Set(sig.picks || []);
$("ranking").innerHTML = table([["#"], ["Coin"], [`${D.lookback}-Tage`, "num"]],
  (sig.ranking || []).slice(0, 12).map((r, i) => `<tr><td>${i + 1}</td><td><b>${coin(r[0])}</b>${picks.has(r[0]) ? '<span class="pick">Auswahl</span>' : ""}</td>
    <td class="num ${cls(r[1])}">${pct(r[1])}</td></tr>`), "Wird beim ersten Tagesschluss berechnet.");

$("trades").innerHTML = table([["Zeit"], ["Aktion"], ["Coin"], ["Menge", "num"], ["Preis", "num"], ["USDT", "num"], ["Grund"]],
  D.trades.map(t => `<tr><td>${t.zeit}</td><td>${t.aktion}</td><td><b>${t.coin}</b></td><td class="num">${t.menge}</td>
    <td class="num">${t.preis}</td><td class="num">${usd(t.usdt)}</td><td>${t.grund}</td></tr>`), "Noch keine Trades.");

$("footer").textContent = `${D.mode === "paper" ? "Simulation mit Live-Kursen – kein echtes Geld." : "Bitget Demo-Konto – kein echtes Geld."}
  Der Bot prüft ${D.check_note} und schreibt diese Seite neu. Keine Anlageberatung.`;
</script>
</body>
</html>
"""


def write_dashboard(data, path=OUT):
    html = TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(tmp, path)

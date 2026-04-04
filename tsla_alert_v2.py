#!/usr/bin/env python3
"""
TSLA Alert v2 — HTML generator
Fetches live TSLA data, runs analysis, generates standalone HTML decision engine.
earnings_week is always False here — toggle is built into the HTML itself.
"""

from __future__ import annotations
import json, sys, traceback
from datetime import datetime
from pathlib import Path

import pytz, yfinance as yf, pandas as pd

SCRIPT_DIR  = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "config.json"
OUTPUT_FILE = SCRIPT_DIR / "tsla_decision_engine.html"

TICKER       = "TSLA"
ET           = pytz.timezone("America/New_York")
RSI_PERIOD   = 14
VOL_LOOKBACK = 20
SHARES_HELD  = 460


def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
    else:
        cfg = {}
    cfg["earnings_week"] = False   # always False — toggled in HTML
    return cfg


def fetch_intraday() -> pd.DataFrame:
    ticker = yf.Ticker(TICKER)
    df = ticker.history(period="1d", interval="5m", auto_adjust=True)
    if df.empty:
        raise ValueError("No intraday data returned from yfinance.")
    if df.index.tzinfo is None:
        df.index = df.index.tz_localize("UTC").tz_convert(ET)
    else:
        df.index = df.index.tz_convert(ET)
    return df


def calc_rsi(closes: pd.Series, period: int = RSI_PERIOD) -> float:
    if len(closes) < period + 1:
        return float("nan")
    delta    = closes.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.iloc[1:period+1].mean()
    avg_loss = loss.iloc[1:period+1].mean()
    for i in range(period + 1, len(closes)):
        avg_gain = (avg_gain * (period - 1) + gain.iloc[i]) / period
        avg_loss = (avg_loss * (period - 1) + loss.iloc[i]) / period
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


def calc_vwap(df: pd.DataFrame) -> float:
    typical = (df["High"] + df["Low"] + df["Close"]) / 3
    return float((typical * df["Volume"]).cumsum().iloc[-1] / df["Volume"].cumsum().iloc[-1])


def evaluate_signals(df: pd.DataFrame, cfg: dict) -> dict:
    current_price = float(df.iloc[-1]["Close"])
    current_vol   = float(df.iloc[-1]["Volume"])
    open_price    = float(df.iloc[0]["Open"])
    rsi           = calc_rsi(df["Close"])
    vwap          = calc_vwap(df)
    vwap_gap_pct  = (current_price - vwap) / vwap * 100
    pct_from_open = (current_price - open_price) / open_price * 100
    recent_vols   = df["Volume"].iloc[-VOL_LOOKBACK-1:-1]
    avg_vol       = float(recent_vols.mean()) if len(recent_vols) >= 5 else current_vol
    vol_ratio     = current_vol / avg_vol if avg_vol > 0 else 1.0
    vol_label     = "low" if vol_ratio < 0.5 else ("high" if vol_ratio > 1.5 else "normal")

    sc1 = pct_from_open >= 3.0
    sc2 = (not pd.isna(rsi)) and rsi > 65
    sc3 = vwap_gap_pct >= 2.0
    sc4 = vol_ratio >= 0.5
    sc5 = True   # earnings_week handled in HTML
    sell_score = int(sum([sc1, sc2, sc3, sc4, sc5]) / 5 * 100)

    rc1 = (not pd.isna(rsi)) and rsi < 55
    rc2 = abs(vwap_gap_pct) <= 0.5
    rc3 = vol_ratio < 1.5
    rc4 = current_price > open_price
    rebuy_score = int(sum([rc1, rc2, rc3, rc4]) / 4 * 100)

    return {
        "current_price": round(current_price, 2),
        "open_price":    round(open_price, 2),
        "pct_from_open": round(pct_from_open, 2),
        "rsi":           round(rsi, 1) if not pd.isna(rsi) else 0,
        "vwap":          round(vwap, 2),
        "vwap_gap_pct":  round(vwap_gap_pct, 2),
        "vol_ratio":     round(vol_ratio, 2),
        "vol_label":     vol_label,
        "sell_score":    sell_score,
        "rebuy_score":   rebuy_score,
        "sell_conds":    [sc1, sc2, sc3, sc4, sc5],
        "rebuy_conds":   [rc1, rc2, rc3, rc4],
    }


def generate_html(s: dict, generated_at: str) -> str:
    shares    = SHARES_HELD
    op        = s["open_price"]
    price     = s["current_price"]
    vwap      = s["vwap"]
    rsi       = s["rsi"]
    vol       = s["vol_label"]
    pct       = s["pct_from_open"]
    vwap_gap  = s["vwap_gap_pct"]
    sc        = s["sell_conds"]
    rc        = s["rebuy_conds"]

    vol_he = "נמוך" if vol == "low" else "גבוה" if vol == "high" else "רגיל"

    t4      = round(op * 1.04, 2)
    t6      = round(op * 1.06, 2)
    rb1     = round(price * 0.98, 2)
    rb15    = round(price * 0.985, 2)
    rb2     = round(price * 0.97, 2)
    profit4 = round((t4 - op) * 212, 0)
    profit6 = round((t6 - op) * 106, 0)
    extra   = max(0, int((212 * price) / rb1) - 212)

    # Pass raw data as JS variables so the toggle can recalculate scores
    js_data = json.dumps({
        "pct_from_open": pct,
        "rsi":           rsi,
        "vwap_gap_pct":  vwap_gap,
        "vol_ratio":     s["vol_ratio"],
        "current_price": price,
        "open_price":    op,
        "vwap":          vwap,
        "vol_label":     vol,
        "sell_conds_base": [bool(sc[0]), bool(sc[1]), bool(sc[2]), bool(sc[3])],
        "rebuy_conds":   [bool(rc[0]), bool(rc[1]), bool(rc[2]), bool(rc[3])],
        "t4": t4, "t6": t6, "rb1": rb1, "rb15": rb15, "rb2": rb2,
        "profit4": profit4, "profit6": profit6, "extra": extra,
        "shares": shares,
    })

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>TSLA Decision Engine</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#f0f2f5;font-family:system-ui,-apple-system,sans-serif;color:#111;padding-bottom:40px}}
.card{{background:#fff;border:1px solid #e0e0e0;border-radius:12px;padding:16px;margin-bottom:14px}}
.section-card{{background:#fff;border-radius:14px;overflow:hidden;margin-bottom:14px;border:2px solid #e0e0e0}}
.metric{{flex:1 1 70px;min-width:70px;background:#f8f9fa;border:1px solid #e0e0e0;border-radius:10px;padding:10px 8px;text-align:center}}
.metric-label{{font-size:10px;color:#888;margin-bottom:4px;letter-spacing:.05em}}
.metric-value{{font-size:15px;font-weight:800;font-family:monospace;color:#111}}
.cond-row{{display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid #f0f0f0}}
.section-title{{font-size:18px;font-weight:800;color:#111;padding:14px 18px 2px}}
.order-row{{background:#0d0d1a;border-radius:10px;padding:12px 14px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;gap:12px}}
.score-bar-bg{{background:#e5e7eb;border-radius:4px;height:8px;width:100%;overflow:hidden}}
.score-bar-fill{{height:100%;border-radius:4px;transition:width .8s ease}}
</style>
</head>
<body>

<script>
const RAW = {js_data};
let earningsWeek = false;

function sellColor(s){{return s>=80?"#ff4444":s>=60?"#ffaa00":s>=40?"#ff6600":"#666"}}
function rebuyColor(s){{return s>=75?"#00ff88":s>=50?"#00aaff":"#ff6600"}}

function calcScores(ew){{
  const sc = [...RAW.sell_conds_base, !ew];
  const rc = RAW.rebuy_conds;
  return {{
    sellScore:  Math.round(sc.filter(Boolean).length / 5 * 100),
    rebuyScore: Math.round(rc.filter(Boolean).length / 4 * 100),
    sc, rc
  }};
}}

function getSellAction(score){{
  if(score>=80) return {{emoji:"🔴",text:"מכור עכשיו",sub:"כל התנאים מתקיימים",color:"#ff4444"}};
  if(score>=60) return {{emoji:"🟡",text:"מכירה חלקית",sub:"שקול מכירת 25–33%",color:"#ffaa00"}};
  if(score>=40) return {{emoji:"🟠",text:"המתן קצת",sub:"עקוב אחרי RSI ו-VWAP",color:"#ff6600"}};
  return {{emoji:"⚪",text:"אל תמכור",sub:"התנאים לא בשלים",color:"#888"}};
}}

function getRebuyAction(score){{
  if(score>=75) return {{emoji:"🟢",text:"קנה חזרה",sub:"תנאים אופטימליים",color:"#00ff88"}};
  if(score>=50) return {{emoji:"🔵",text:"קנה — סביר",sub:"כניסה הגיונית",color:"#00aaff"}};
  return {{emoji:"⏳",text:"המתן — גבוה",sub:"המניה עדיין גבוהה",color:"#ff6600"}};
}}

function condDot(ok){{
  return `<div style="width:22px;height:22px;border-radius:50%;flex-shrink:0;
    background:${{ok?"#0d2818":"#2a0a0a"}};
    border:2px solid ${{ok?"#00ff88":"#ff4444"}};
    display:flex;align-items:center;justify-content:center;
    font-size:11px;color:${{ok?"#00ff88":"#ff4444"}};font-weight:800">${{ok?"✓":"✗"}}</div>`;
}}

function condRow(label, value, ok, detail){{
  return `<div class="cond-row">
    ${{condDot(ok)}}
    <div style="flex:1">
      <div style="font-size:13px;font-weight:600;color:#111">${{label}}</div>
      <div style="font-size:11px;color:#888;margin-top:1px">${{detail}}</div>
    </div>
    <div style="font-size:13px;font-weight:700;font-family:monospace;
      color:${{ok?"#16a34a":"#dc2626"}};min-width:60px;text-align:left">${{value}}</div>
  </div>`;
}}

function orderRow(label, price, note, color){{
  return `<div class="order-row" style="border:1px solid ${{color}}44">
    <div>
      <div style="font-size:12px;font-weight:600;color:#333;margin-bottom:3px">${{label}}</div>
      <div style="font-size:11px;color:#888">${{note}}</div>
    </div>
    <div style="font-size:18px;font-weight:800;font-family:monospace;color:${{color}}">${{price}}</div>
  </div>`;
}}

function render(){{
  const {{sellScore, rebuyScore, sc, rc}} = calcScores(earningsWeek);
  const sell  = getSellAction(sellScore);
  const rebuy = getRebuyAction(rebuyScore);
  const d     = RAW;
  const pct   = d.pct_from_open;
  const vg    = d.vwap_gap_pct;
  const volHe = d.vol_label==="low"?"נמוך":d.vol_label==="high"?"גבוה":"רגיל";

  // Earnings toggle button
  document.getElementById("earnings-btn").style.background    = earningsWeek ? "#fff7ed" : "#f0fdf4";
  document.getElementById("earnings-btn").style.borderColor   = earningsWeek ? "#f97316" : "#16a34a";
  document.getElementById("earnings-btn").style.color         = earningsWeek ? "#ea580c" : "#15803d";
  document.getElementById("earnings-btn").textContent         = earningsWeek ? "⚠️ שבוע דוחות — פעיל" : "📅 שבוע דוחות — כבוי";

  // SELL section
  document.getElementById("sell-wrap").style.borderColor = sell.color+"44";
  document.getElementById("sell-header").style.background = `linear-gradient(135deg,${{sell.color}}11,#fff)`;
  document.getElementById("sell-action").style.color = sell.color;
  document.getElementById("sell-action").textContent = sell.emoji+" "+sell.text;
  document.getElementById("sell-sub").textContent = sell.sub;
  document.getElementById("sell-score-val").style.color = sell.color;
  document.getElementById("sell-score-val").textContent = sellScore+"/100";
  document.getElementById("sell-bar").style.width = Math.min(sellScore,100)+"%";
  document.getElementById("sell-bar").style.background = sellColor(sellScore);
  document.getElementById("sell-conds").innerHTML =
    condRow("עלייה מהפתיחה", (pct>0?"+":"")+pct+"%", sc[0], "נדרש ≥3% | פתיחה $"+d.open_price) +
    condRow("RSI", String(d.rsi), sc[1], "נדרש ≥65") +
    condRow("מחיר מעל VWAP", (vg>0?"+":"")+vg+"%", sc[2], "נדרש ≥2% | VWAP $"+d.vwap) +
    condRow("נפח מסחר", volHe, sc[3], d.vol_label==="low"?"נפח נמוך = חולשה":"נפח תומך") +
    condRow("ללא דוחות", earningsWeek?"⚠️ שבוע דוחות":"✓ בטוח", sc[4], earningsWeek?"מסוכן למכור לפני דוחות":"אין אירוע קרוב");

  // REBUY section
  document.getElementById("rebuy-wrap").style.borderColor = rebuy.color+"44";
  document.getElementById("rebuy-header").style.background = `linear-gradient(135deg,${{rebuy.color}}11,#fff)`;
  document.getElementById("rebuy-action").style.color = rebuy.color;
  document.getElementById("rebuy-action").textContent = rebuy.emoji+" "+rebuy.text;
  document.getElementById("rebuy-sub").textContent = rebuy.sub;
  document.getElementById("rebuy-score-val").style.color = rebuy.color;
  document.getElementById("rebuy-score-val").textContent = rebuyScore+"/100";
  document.getElementById("rebuy-bar").style.width = Math.min(rebuyScore,100)+"%";
  document.getElementById("rebuy-bar").style.background = rebuyColor(rebuyScore);
  document.getElementById("rebuy-conds").innerHTML =
    condRow("RSI ירד מתחת ל-55", String(d.rsi), rc[0], "כרגע "+d.rsi+" — "+(rc[0]?"התקרר ✓":"עדיין חם")) +
    condRow("מחיר קרוב ל-VWAP", (vg>0?"+":"")+vg+"%", rc[1], "נדרש ≤0.5% מעל VWAP") +
    condRow("נפח לא מואץ", rc[2]?"✓":"גבוה ⚠️", rc[2], "נפח גבוה = מומנטום ממשיך") +
    condRow("יום עדיין חיובי", (pct>0?"+":"")+pct+"%", rc[3], "מחיר מעל פתיחה $"+d.open_price);
}}

function toggleEarnings(){{
  earningsWeek = !earningsWeek;
  render();
}}

window.onload = render;
</script>

<!-- HEADER -->
<div style="background:linear-gradient(135deg,#1a1a2e,#16213e);border-bottom:3px solid #e0e0e0;padding:20px 20px 16px;display:flex;justify-content:space-between;align-items:flex-start">
  <div>
    <div style="font-size:10px;color:#aaa;letter-spacing:.2em;font-family:monospace;margin-bottom:6px">TSLA · NASDAQ</div>
    <div style="font-size:22px;font-weight:800;color:#fff;letter-spacing:-.02em">Trade Decision Engine</div>
    <div style="font-size:11px;color:#aaa;margin-top:4px">&#128344; {generated_at}</div>
  </div>
  <div style="display:flex;flex-direction:column;align-items:center;gap:8px">
    <div style="background:#0d2818;border:1px solid #00cc5544;border-radius:10px;padding:8px 14px;text-align:center">
      <div style="font-size:9px;color:#00aa55;letter-spacing:.15em;margin-bottom:2px">HOLDING</div>
      <div style="font-size:22px;font-weight:800;color:#00cc66;font-family:monospace">{shares}</div>
      <div style="font-size:9px;color:#00aa55">מניות</div>
    </div>
  </div>
</div>

<div style="padding:16px 16px 0">

  <!-- EARNINGS TOGGLE + TIMESTAMP -->
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px">
    <button id="earnings-btn" onclick="toggleEarnings()"
      style="flex:1;padding:12px;border-radius:12px;border:2px solid;font-size:14px;font-weight:700;cursor:pointer;transition:all .2s">
    </button>
    <div style="background:#f0f4ff;border:1px solid #c7d7fe;border-radius:10px;padding:8px 12px;text-align:center;flex-shrink:0;min-width:80px">
      <div style="font-size:9px;color:#6366f1;letter-spacing:.1em;margin-bottom:2px;font-weight:700">עודכן</div>
      <div style="font-size:14px;font-weight:800;color:#4338ca;font-family:monospace">{generated_at}</div>
    </div>
  </div>

  <!-- MARKET DATA -->
  <div class="card">
    <div style="font-size:11px;color:#888;margin-bottom:12px;font-weight:600;letter-spacing:.1em">&#128202; נתוני שוק</div>
    <div style="display:flex;gap:6px;flex-wrap:wrap">
      <div class="metric" style="background:#eff6ff;border-color:#bfdbfe">
        <div class="metric-label">מחיר נוכחי</div>
        <div class="metric-value" style="color:#1d4ed8">${price:.2f}</div>
      </div>
      <div class="metric"><div class="metric-label">פתיחה</div><div class="metric-value">${op:.2f}</div></div>
      <div class="metric" style="background:{'#f0fdf4' if pct>=3 else '#f8f9fa'};border-color:{'#86efac' if pct>=3 else '#e0e0e0'}">
        <div class="metric-label">מהפתיחה</div>
        <div class="metric-value" style="color:{'#16a34a' if pct>=3 else '#111'}">{pct:+.2f}%</div>
      </div>
      <div class="metric"><div class="metric-label">VWAP</div><div class="metric-value">${vwap:.2f}</div></div>
      <div class="metric" style="background:{'#fffbeb' if rsi>=65 else '#f8f9fa'};border-color:{'#fcd34d' if rsi>=65 else '#e0e0e0'}">
        <div class="metric-label">RSI</div>
        <div class="metric-value" style="color:{'#d97706' if rsi>=65 else '#111'}">{rsi}</div>
      </div>
      <div class="metric"><div class="metric-label">נפח</div><div class="metric-value">{vol_he}</div></div>
    </div>
  </div>

  <!-- SELL SECTION -->
  <div class="section-title">🔴 מכירה</div>
  <div id="sell-wrap" class="section-card">
    <div id="sell-header" style="padding:18px 18px 14px;border-bottom:1px solid #f0f0f0">
      <div id="sell-action" style="font-size:22px;font-weight:800;margin-bottom:4px"></div>
      <div id="sell-sub" style="font-size:13px;color:#666;margin-bottom:12px"></div>
      <div style="display:flex;justify-content:space-between;margin-bottom:8px">
        <span style="font-size:11px;color:#888">ציון ביטחון</span>
        <span id="sell-score-val" style="font-size:13px;font-weight:800;font-family:monospace"></span>
      </div>
      <div class="score-bar-bg"><div id="sell-bar" class="score-bar-fill" style="width:0%"></div></div>
    </div>
    <div id="sell-conds" style="padding:4px 18px 0"></div>
    <div style="padding:14px 18px;border-top:1px solid #e0e0e0;background:#fff">
      <div style="font-size:11px;font-weight:700;color:#888;margin-bottom:10px">&#128203; פקודות Limit מוצעות</div>
      {f'<div class="order-row" style="background:#fff;border:1px solid #fca5a5"><div><div style="font-size:12px;font-weight:600;color:#333;margin-bottom:3px">Limit Sell — 212 מניות (50%)</div><div style="font-size:11px;color:#888">+4% מהפתיחה | רווח משוער: ${profit4:.0f}</div></div><div style="font-size:18px;font-weight:800;font-family:monospace;color:#dc2626">${t4}</div></div>'}
      {f'<div class="order-row" style="background:#fff;border:1px solid #fca5a5"><div><div style="font-size:12px;font-weight:600;color:#333;margin-bottom:3px">Limit Sell — 106 מניות (25%)</div><div style="font-size:11px;color:#888">+6% מהפתיחה | רווח משוער: ${profit6:.0f}</div></div><div style="font-size:18px;font-weight:800;font-family:monospace;color:#ef4444">${t6}</div></div>'}
    </div>
  </div>

  <!-- REBUY SECTION -->
  <div class="section-title">🟢 קנייה חוזרת</div>
  <div id="rebuy-wrap" class="section-card">
    <div id="rebuy-header" style="padding:18px 18px 14px;border-bottom:1px solid #f0f0f0">
      <div id="rebuy-action" style="font-size:22px;font-weight:800;margin-bottom:4px"></div>
      <div id="rebuy-sub" style="font-size:13px;color:#666;margin-bottom:12px"></div>
      <div style="display:flex;justify-content:space-between;margin-bottom:8px">
        <span style="font-size:11px;color:#888">ציון ביטחון</span>
        <span id="rebuy-score-val" style="font-size:13px;font-weight:800;font-family:monospace"></span>
      </div>
      <div class="score-bar-bg"><div id="rebuy-bar" class="score-bar-fill" style="width:0%"></div></div>
    </div>
    <div id="rebuy-conds" style="padding:4px 18px 0"></div>
    <div style="padding:14px 18px;border-top:1px solid #e0e0e0;background:#fff">
      <div style="font-size:11px;font-weight:700;color:#888;margin-bottom:10px">&#128203; פקודות Limit מוצעות</div>
      {f'<div class="order-row" style="background:#fff;border:1px solid #86efac"><div><div style="font-size:12px;font-weight:600;color:#333;margin-bottom:3px">Limit Buy אגרסיבי (תוך-יומי)</div><div style="font-size:11px;color:#888">2% מתחת למחיר | +{extra} מניות בונוס</div></div><div style="font-size:18px;font-weight:800;font-family:monospace;color:#16a34a">${rb1}</div></div>'}
      {f'<div class="order-row" style="background:#fff;border:1px solid #86efac"><div><div style="font-size:12px;font-weight:600;color:#333;margin-bottom:3px">Limit Buy מאוזן (תוך-יומי)</div><div style="font-size:11px;color:#888">1.5% מתחת | איזון בין מחיר למילוי</div></div><div style="font-size:18px;font-weight:800;font-family:monospace;color:#22c55e">${rb15}</div></div>'}
      {f'<div class="order-row" style="background:#fff;border:1px solid #86efac"><div><div style="font-size:12px;font-weight:600;color:#333;margin-bottom:3px">Limit Buy שמרני (יום המחרת)</div><div style="font-size:11px;color:#888">3% מתחת | סיכוי מילוי נמוך אך מחיר טוב</div></div><div style="font-size:18px;font-weight:800;font-family:monospace;color:#4ade80">${rb2}</div></div>'}
    </div>
  </div>

</div>
</body>
</html>"""


def main():
    cfg = load_config()
    now_il       = datetime.now(pytz.timezone("Asia/Jerusalem"))
    generated_at = now_il.strftime("%d/%m/%Y %H:%M") + " (IL)"
    print(f"\nTSLA Alert v2 — {generated_at}\n")
    try:
        print("Fetching TSLA data...")
        df = fetch_intraday()
        signals = evaluate_signals(df, cfg)
        print(f"  Price: ${signals['current_price']:.2f} | Open: ${signals['open_price']:.2f} | Change: {signals['pct_from_open']:+.2f}%")
        print(f"  RSI: {signals['rsi']} | VWAP: ${signals['vwap']:.2f}")
        print(f"  SELL: {signals['sell_score']}/100 | REBUY: {signals['rebuy_score']}/100\n")
        html = generate_html(signals, generated_at)
        OUTPUT_FILE.write_text(html, encoding="utf-8")
        print(f"Saved: {OUTPUT_FILE}")
    except Exception:
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()

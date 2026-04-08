#!/usr/bin/env python3
"""
TSLA Alert v3 — HTML generator
Updated strategy based on quantitative research document:
- Scale-out selling: 25-33% at +3%, 50%+ at +4-5%, 75% at +5-7%, ~all at +7%+
- Rebuy based on distance from DAILY HIGH (not current price)
- earnings_week toggled in HTML
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
    cfg["earnings_week"] = False
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


def calc_rsi(closes: pd.Series, period: int = RSI_PERIOD) -> float | None:
    """Returns float RSI, None if too early (<5 bars), or -1 as sentinel for Wait (5-6 bars)."""
    closes = closes.dropna()
    n = len(closes)
    if n < 5:
        print(f"  [RSI] Too early: only {n} bars — Wait")
        return None
    if n <= 6:
        print(f"  [RSI] Early session ({n} bars) — Wait")
        return -1  # sentinel for "Wait"
    if n < period + 1:
        period = n - 1
        print(f"  [RSI] Partial session: {n} bars, using period={period}")
    else:
        print(f"  [RSI] Computing on {n} bars with period={period}")
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
    open_price    = float(df.iloc[0]["Open"])
    daily_high    = float(df["High"].max())
    rsi           = calc_rsi(df["Close"])
    vwap          = calc_vwap(df)
    vwap_gap_pct  = (current_price - vwap) / vwap * 100
    pct_from_open = (current_price - open_price) / open_price * 100
    pct_from_high = (current_price - daily_high) / daily_high * 100

    recent_vols = df["Volume"].iloc[-VOL_LOOKBACK-1:-1]
    avg_vol     = float(recent_vols.mean()) if len(recent_vols) >= 5 else float(df.iloc[-1]["Volume"])
    vol_ratio   = float(df.iloc[-1]["Volume"]) / avg_vol if avg_vol > 0 else 1.0
    vol_label   = "low" if vol_ratio < 0.5 else ("high" if vol_ratio > 1.5 else "normal")

    # Upper wick analysis — last 3 candles
    last3 = df.iloc[-3:]
    wicks = ((last3["High"] - last3[["Open","Close"]].max(axis=1)) / last3["Close"] * 100)
    avg_wick_pct = float(wicks.mean())
    sc6 = avg_wick_pct >= 0.5  # avg upper wick >= 0.5% = selling pressure

    # Sell conditions
    sc1 = pct_from_open >= 3.0
    sc2 = isinstance(rsi, float) and rsi > 0 and rsi > 65
    sc3 = vwap_gap_pct >= 2.0
    sc4 = vol_ratio >= 0.5
    sc5 = True  # earnings_week handled in HTML
    sell_score = int(sum([sc1, sc2, sc3, sc4, sc5, sc6]) / 6 * 100)

    # Rebuy conditions — based on distance from daily high
    rc1 = isinstance(rsi, float) and rsi > 0 and rsi < 55
    rc2 = abs(vwap_gap_pct) <= 0.5
    rc3 = vol_ratio < 1.5
    rc4 = current_price > open_price
    rebuy_score = int(sum([rc1, rc2, rc3, rc4]) / 4 * 100)

    return {
        "current_price": round(current_price, 2),
        "open_price":    round(open_price, 2),
        "daily_high":    round(daily_high, 2),
        "pct_from_open": round(pct_from_open, 2),
        "pct_from_high": round(pct_from_high, 2),
        "rsi":           "Wait" if rsi is None or rsi == -1 else round(rsi, 1),
        "vwap":          round(vwap, 2),
        "vwap_gap_pct":  round(vwap_gap_pct, 2),
        "vol_ratio":     round(vol_ratio, 2),
        "vol_label":     vol_label,
        "sell_score":    sell_score,
        "rebuy_score":   rebuy_score,
        "avg_wick_pct":  round(avg_wick_pct, 2),
        "sell_conds":    [sc1, sc2, sc3, sc4, sc5, sc6],
        "rebuy_conds":   [rc1, rc2, rc3, rc4],
    }


def generate_html(s: dict, generated_at: str) -> str:
    shares   = SHARES_HELD
    op       = s["open_price"]
    price    = s["current_price"]
    high     = s["daily_high"]
    vwap     = s["vwap"]
    rsi      = s["rsi"]
    vol          = s["vol_label"]
    pct          = s["pct_from_open"]
    pct_h        = s["pct_from_high"]
    vwap_gap     = s["vwap_gap_pct"]
    avg_wick_pct = s["avg_wick_pct"]
    sc           = s["sell_conds"]
    rc           = s["rebuy_conds"]

    vol_he = "נמוך" if vol == "low" else "גבוה" if vol == "high" else "רגיל"

    # SELL limit orders — based on open price, % of holding
    t3  = round(op * 1.03, 2)   # +3%  → sell 25-33%
    t45 = round(op * 1.045, 2)  # +4-5% → sell 50%+
    t6  = round(op * 1.06, 2)   # +6%  → sell 75%
    t7  = round(op * 1.07, 2)   # +7%+ → sell ~all

    # REBUY limit orders — based on DAILY HIGH
    rb_agg  = round(high * 0.98, 2)    # 2% מתחת לשיא — אגרסיבי
    rb_bal  = round(high * 0.975, 2)   # 2.5% מתחת לשיא — מאוזן
    rb_cons = round(high * 0.96, 2)    # 4% מתחת לשיא — שמרני
    rb_next = round(price * 0.985, 2)  # 1.5% מתחת לסגירה — יום המחרת

    js_data = json.dumps({
        "pct_from_open": pct,
        "pct_from_high": pct_h,
        "rsi":           rsi,
        "vwap_gap_pct":  vwap_gap,
        "vol_ratio":     s["vol_ratio"],
        "current_price": price,
        "open_price":    op,
        "daily_high":    high,
        "vwap":          vwap,
        "vol_label":     vol,
        "avg_wick_pct":    round(avg_wick_pct, 2),
        "sell_conds_base": [bool(sc[0]), bool(sc[1]), bool(sc[2]), bool(sc[3]), bool(sc[4])],
        "rebuy_conds":   [bool(rc[0]), bool(rc[1]), bool(rc[2]), bool(rc[3])],
        "t3": t3, "t45": t45, "t6": t6, "t7": t7,
        "rb_agg": rb_agg, "rb_bal": rb_bal, "rb_cons": rb_cons, "rb_next": rb_next,
        "shares": shares,
    })

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>TSLA Decision Engine v3</title>
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
.order-row{{background:#fff;border-radius:10px;padding:12px 14px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;gap:12px}}
.score-bar-bg{{background:#e5e7eb;border-radius:4px;height:8px;width:100%;overflow:hidden}}
.score-bar-fill{{height:100%;border-radius:4px;transition:width .8s ease}}
</style>
</head>
<body>

<script>
const RAW = {js_data};
let earningsWeek = false;

function sellColor(s){{return s>=80?"#dc2626":s>=60?"#d97706":s>=40?"#ea580c":"#6b7280"}}
function rebuyColor(s){{return s>=75?"#16a34a":s>=50?"#2563eb":"#ea580c"}}

function calcScores(ew){{
  const sc = [...RAW.sell_conds_base, !ew];
  const rc = RAW.rebuy_conds;
  return {{
    sellScore:  Math.round(sc.filter(Boolean).length / 6 * 100),
    rebuyScore: Math.round(rc.filter(Boolean).length / 4 * 100),
    sc, rc
  }};
}}

function getSellAction(score, ew){{
  if(ew) return {{emoji:"⚠️", text:"שבוע דוחות — הימנע", sub:"מומנטום פוסט-דוחות עלול להימשך ימים", color:"#ea580c"}};
  if(score>=80) return {{emoji:"🔴", text:"מכור עכשיו — תנאים מלאים", sub:"כל האינדיקטורים תומכים במכירה", color:"#dc2626"}};
  if(score>=60) return {{emoji:"🟡", text:"מכירה חלקית מומלצת", sub:"רוב התנאים מתקיימים", color:"#d97706"}};
  if(score>=40) return {{emoji:"🟠", text:"המתן — עוד קצת", sub:"עקוב אחרי RSI וה-VWAP", color:"#ea580c"}};
  return {{emoji:"⚪", text:"אל תמכור", sub:"התנאים לא בשלים", color:"#6b7280"}};
}}

function getRebuyAction(score){{
  if(score>=75) return {{emoji:"🟢", text:"קנה חזרה — תנאים אופטימליים", sub:"RSI התקרר, מחיר קרוב ל-VWAP", color:"#16a34a"}};
  if(score>=50) return {{emoji:"🔵", text:"קנה חזרה — סביר", sub:"רוב האותות תומכים", color:"#2563eb"}};
  return {{emoji:"⏳", text:"המתן — עדיין גבוה", sub:"המניה עדיין רחוקה מנקודת כניסה", color:"#ea580c"}};
}}

function condDot(ok){{
  return `<div style="width:22px;height:22px;border-radius:50%;flex-shrink:0;background:${{ok?"#f0fdf4":"#fef2f2"}};border:2px solid ${{ok?"#16a34a":"#dc2626"}};display:flex;align-items:center;justify-content:center;font-size:11px;color:${{ok?"#16a34a":"#dc2626"}};font-weight:800">${{ok?"✓":"✗"}}</div>`;
}}

function condRow(label, value, ok, detail){{
  return `<div class="cond-row">
    ${{condDot(ok)}}
    <div style="flex:1">
      <div style="font-size:13px;font-weight:600;color:#111">${{label}}</div>
      <div style="font-size:11px;color:#888;margin-top:1px">${{detail}}</div>
    </div>
    <div style="font-size:13px;font-weight:700;font-family:monospace;color:${{ok?"#16a34a":"#dc2626"}};min-width:70px;text-align:left">${{value}}</div>
  </div>`;
}}

function orderRow(label, price, note, pct_note, color){{
  return `<div class="order-row" style="border:1px solid ${{color}}44">
    <div style="flex:1">
      <div style="font-size:12px;font-weight:700;color:#333;margin-bottom:2px">${{label}}</div>
      <div style="font-size:11px;color:#888">${{note}}</div>
      <div style="font-size:10px;color:#aaa;margin-top:1px">${{pct_note}}</div>
    </div>
    <div style="font-size:20px;font-weight:800;font-family:monospace;color:${{color}};white-space:nowrap">${{price}}</div>
  </div>`;
}}

function render(){{
  const {{sellScore, rebuyScore, sc, rc}} = calcScores(earningsWeek);
  const sell  = getSellAction(sellScore, earningsWeek);
  const rebuy = getRebuyAction(rebuyScore);
  const d     = RAW;
  const pct   = d.pct_from_open;
  const pctH  = d.pct_from_high;
  const vg    = d.vwap_gap_pct;
  const volHe = d.vol_label==="low"?"נמוך":d.vol_label==="high"?"גבוה":"רגיל";

  // Earnings toggle
  document.getElementById("earnings-btn").style.background  = earningsWeek ? "#fff7ed" : "#f0fdf4";
  document.getElementById("earnings-btn").style.borderColor = earningsWeek ? "#f97316" : "#16a34a";
  document.getElementById("earnings-btn").style.color       = earningsWeek ? "#ea580c" : "#15803d";
  document.getElementById("earnings-btn").textContent       = earningsWeek ? "⚠️ שבוע דוחות — פעיל" : "📅 שבוע דוחות — כבוי";

  // SELL
  document.getElementById("sell-wrap").style.borderColor = sell.color+"55";
  document.getElementById("sell-header").style.background = `linear-gradient(135deg,${{sell.color}}0d,#fff)`;
  document.getElementById("sell-action").style.color = sell.color;
  document.getElementById("sell-action").textContent = sell.emoji+" "+sell.text;
  document.getElementById("sell-sub").textContent = sell.sub;
  document.getElementById("sell-score-val").style.color = sell.color;
  document.getElementById("sell-score-val").textContent = sellScore+"/100";
  document.getElementById("sell-bar").style.width = Math.min(sellScore,100)+"%";
  document.getElementById("sell-bar").style.background = sellColor(sellScore);
  document.getElementById("sell-conds").innerHTML =
    condRow("עלייה מהפתיחה", (pct>0?"+":"")+pct+"%", sc[0], "נדרש ≥3% | פתיחה $"+d.open_price) +
    condRow("RSI (גרף 5 דקות)", d.rsi !== null ? String(d.rsi) : "Wait", sc[1], "נדרש >65 | overbought") +
    condRow("מחיר מעל VWAP", (vg>0?"+":"")+vg+"%", sc[2], "נדרש ≥2% | VWAP $"+d.vwap) +
    condRow("נפח מסחר", volHe, sc[3], d.vol_label==="low"?"נפח נמוך = חולשה":"נפח תומך בתנועה") +
    condRow("ללא דוחות", earningsWeek?"⚠️ שבוע דוחות":"✓ בטוח", sc[4], earningsWeek?"הימנע ממכירה":"אין אירוע קרוב") +
    condRow("זנבות עליונים (Upper Wicks)", (d.avg_wick_pct||0).toFixed(2)+"%", sc[5], "ממוצע 3 נרות אחרונים ≥0.5% = לחץ מכירה");

  // SELL orders
  document.getElementById("sell-orders").innerHTML =
    orderRow("Limit Sell — 25-33% מהאחזקה", "$"+d.t3,  "עלייה +3% מהפתיחה", "כניסה ראשונה — תדירה, סיכוי נסיגה 60-65%", "#dc2626") +
    orderRow("Limit Sell — 50%+ מהאחזקה",   "$"+d.t45, "עלייה +4-5% מהפתיחה", "טריגר הליבה — מעל 1.5x ATR יומי", "#dc2626") +
    orderRow("Limit Sell — 75% מהאחזקה",    "$"+d.t6,  "עלייה +6% מהפתיחה", "סיכוי נסיגה 70-75%", "#b91c1c") +
    orderRow("Limit Sell — כמעט הכל",        "$"+d.t7,  "עלייה +7%+ מהפתיחה", "אירוע >2.5 סיגמא — סיכוי נסיגה 80-85%", "#991b1b");

  // REBUY
  document.getElementById("rebuy-wrap").style.borderColor = rebuy.color+"55";
  document.getElementById("rebuy-header").style.background = `linear-gradient(135deg,${{rebuy.color}}0d,#fff)`;
  document.getElementById("rebuy-action").style.color = rebuy.color;
  document.getElementById("rebuy-action").textContent = rebuy.emoji+" "+rebuy.text;
  document.getElementById("rebuy-sub").textContent = rebuy.sub;
  document.getElementById("rebuy-score-val").style.color = rebuy.color;
  document.getElementById("rebuy-score-val").textContent = rebuyScore+"/100";
  document.getElementById("rebuy-bar").style.width = Math.min(rebuyScore,100)+"%";
  document.getElementById("rebuy-bar").style.background = rebuyColor(rebuyScore);
  document.getElementById("rebuy-conds").innerHTML =
    condRow("RSI ירד מתחת ל-55", d.rsi !== null ? String(d.rsi) : "Wait", rc[0], rc[0]?"התקרר — אות כניסה טוב":"עדיין חם — המתן") +
    condRow("מחיר קרוב ל-VWAP", (vg>0?"+":"")+vg+"%", rc[1], "נדרש ≤0.5% מעל VWAP") +
    condRow("נפח לא מואץ", rc[2]?"✓":"גבוה ⚠️", rc[2], "נפח גבוה = מומנטום ממשיך — הימנע") +
    condRow("יום עדיין חיובי", (pct>0?"+":"")+pct+"%", rc[3], "מחיר מעל פתיחה $"+d.open_price);

  const highNote = "שיא היום: $"+d.daily_high+" | כרגע "+(pctH>0?"+":"")+pctH.toFixed(1)+"% מהשיא";

  // REBUY orders — based on DAILY HIGH
  document.getElementById("rebuy-orders").innerHTML =
    `<div style="font-size:11px;color:#6366f1;font-weight:600;margin-bottom:10px;padding:6px 10px;background:#f0f4ff;border-radius:8px">📌 ${{highNote}}</div>` +
    orderRow("Limit Buy אגרסיבי — תוך יומי",  "$"+d.rb_agg,  "2% מתחת לשיא היומי", "סיכוי מילוי 60-70% | חלון: 60-90 דקות", "#16a34a") +
    orderRow("Limit Buy מאוזן — תוך יומי",     "$"+d.rb_bal,  "2.5% מתחת לשיא היומי", "סיכוי מילוי 45-55% | איזון טוב", "#16a34a") +
    orderRow("Limit Buy שמרני — תוך יומי",     "$"+d.rb_cons, "4% מתחת לשיא היומי", "סיכוי מילוי 25-35% | רק בנסיגות גדולות", "#15803d") +
    orderRow("Limit Buy — יום המחרת",           "$"+d.rb_next, "1.5% מתחת לסגירה", "סיכוי מילוי 55-60% | mean reversion", "#2563eb");
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
    <div style="font-size:10px;color:#aaa;letter-spacing:.2em;font-family:monospace;margin-bottom:6px">TSLA · NASDAQ · v3</div>
    <div style="font-size:22px;font-weight:800;color:#fff;letter-spacing:-.02em">Trade Decision Engine</div>
  </div>
  <div style="background:#0d2818;border:1px solid #00cc5544;border-radius:10px;padding:8px 14px;text-align:center">
    <div style="font-size:9px;color:#00aa55;letter-spacing:.15em;margin-bottom:2px">HOLDING</div>
    <div style="font-size:22px;font-weight:800;color:#00cc66;font-family:monospace">{shares}</div>
    <div style="font-size:9px;color:#00aa55">מניות</div>
  </div>
</div>

<div style="padding:16px 16px 0">

  <!-- EARNINGS TOGGLE + TIMESTAMP -->
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px">
    <button id="earnings-btn" onclick="toggleEarnings()"
      style="flex:1;padding:12px;border-radius:12px;border:2px solid;font-size:14px;font-weight:700;cursor:pointer;transition:all .2s">
    </button>
    <div style="background:#f0f4ff;border:1px solid #c7d7fe;border-radius:10px;padding:8px 12px;text-align:center;flex-shrink:0;min-width:90px">
      <div style="font-size:9px;color:#6366f1;letter-spacing:.1em;margin-bottom:2px;font-weight:700">עודכן</div>
      <div style="font-size:13px;font-weight:800;color:#4338ca;font-family:monospace">{generated_at}</div>
    </div>
  </div>

  <!-- MARKET DATA -->
  <div class="card">
    <div style="font-size:11px;color:#888;margin-bottom:12px;font-weight:600;letter-spacing:.1em">📊 נתוני שוק</div>
    <div style="display:flex;gap:6px;flex-wrap:wrap">
      <div class="metric" style="background:#eff6ff;border-color:#bfdbfe">
        <div class="metric-label">מחיר נוכחי</div>
        <div class="metric-value" style="color:#1d4ed8">${price:.2f}</div>
      </div>
      <div class="metric">
        <div class="metric-label">פתיחה</div>
        <div class="metric-value">${op:.2f}</div>
      </div>
      <div class="metric" style="background:{'#f0fdf4' if pct>=3 else '#fef2f2' if pct<0 else '#f8f9fa'};border-color:{'#86efac' if pct>=3 else '#fca5a5' if pct<0 else '#e0e0e0'}">
        <div class="metric-label">מהפתיחה</div>
        <div class="metric-value" style="color:{'#16a34a' if pct>=3 else '#dc2626' if pct<0 else '#111'}">{pct:+.2f}%</div>
      </div>
      <div class="metric" style="background:#faf5ff;border-color:#d8b4fe">
        <div class="metric-label">שיא יומי</div>
        <div class="metric-value" style="color:#7c3aed">${high:.2f}</div>
      </div>
      <div class="metric">
        <div class="metric-label">VWAP</div>
        <div class="metric-value">${vwap:.2f}</div>
      </div>
      <div class="metric" style="background:{'#fffbeb' if isinstance(rsi, float) and rsi>=65 else '#f8f9fa'};border-color:{'#fcd34d' if isinstance(rsi, float) and rsi>=65 else '#e0e0e0'}">
        <div class="metric-label">RSI</div>
        <div class="metric-value" style="color:{'#d97706' if isinstance(rsi, float) and rsi>=65 else '#111'}">{rsi}</div>
      </div>
      <div class="metric">
        <div class="metric-label">נפח</div>
        <div class="metric-value">{vol_he}</div>
      </div>
    </div>
  </div>

  <!-- SELL SECTION -->
  <div class="section-title">🔴 מכירה</div>
  <div id="sell-wrap" class="section-card">
    <div id="sell-header" style="padding:18px 18px 14px;border-bottom:1px solid #f0f0f0">
      <div id="sell-action" style="font-size:20px;font-weight:800;margin-bottom:4px"></div>
      <div id="sell-sub" style="font-size:13px;color:#666;margin-bottom:12px"></div>
      <div style="display:flex;justify-content:space-between;margin-bottom:8px">
        <span style="font-size:11px;color:#888">ציון ביטחון</span>
        <span id="sell-score-val" style="font-size:13px;font-weight:800;font-family:monospace"></span>
      </div>
      <div class="score-bar-bg"><div id="sell-bar" class="score-bar-fill" style="width:0%"></div></div>
    </div>
    <div id="sell-conds" style="padding:4px 18px 0"></div>
    <div style="padding:14px 18px;border-top:1px solid #f0f0f0;background:#fafafa">
      <div style="font-size:11px;font-weight:700;color:#888;margin-bottom:10px">📋 פקודות Limit — יציאה הדרגתית (Scale Out)</div>
      <div id="sell-orders"></div>
    </div>
  </div>

  <!-- REBUY SECTION -->
  <div class="section-title">🟢 קנייה חוזרת</div>
  <div id="rebuy-wrap" class="section-card">
    <div id="rebuy-header" style="padding:18px 18px 14px;border-bottom:1px solid #f0f0f0">
      <div id="rebuy-action" style="font-size:20px;font-weight:800;margin-bottom:4px"></div>
      <div id="rebuy-sub" style="font-size:13px;color:#666;margin-bottom:12px"></div>
      <div style="display:flex;justify-content:space-between;margin-bottom:8px">
        <span style="font-size:11px;color:#888">ציון ביטחון</span>
        <span id="rebuy-score-val" style="font-size:13px;font-weight:800;font-family:monospace"></span>
      </div>
      <div class="score-bar-bg"><div id="rebuy-bar" class="score-bar-fill" style="width:0%"></div></div>
    </div>
    <div id="rebuy-conds" style="padding:4px 18px 0"></div>
    <div style="padding:14px 18px;border-top:1px solid #f0f0f0;background:#fafafa">
      <div style="font-size:11px;font-weight:700;color:#888;margin-bottom:10px">📋 פקודות Limit — לפי מרחק מהשיא היומי</div>
      <div id="rebuy-orders"></div>
    </div>
  </div>

</div>
</body>
</html>"""


def main():
    cfg = load_config()
    now_il       = datetime.now(pytz.timezone("Asia/Jerusalem"))
    generated_at = now_il.strftime("%d/%m %H:%M")
    print(f"\nTSLA Alert v3 — {generated_at}\n")
    try:
        print("Fetching TSLA data...")
        df      = fetch_intraday()
        signals = evaluate_signals(df, cfg)
        print(f"  Price:      ${signals['current_price']:.2f}")
        print(f"  Open:       ${signals['open_price']:.2f}")
        print(f"  Daily High: ${signals['daily_high']:.2f}")
        print(f"  Change:     {signals['pct_from_open']:+.2f}%")
        print(f"  RSI:        {signals['rsi']}")
        print(f"  VWAP:       ${signals['vwap']:.2f}")
        print(f"  Avg Wick:   {signals['avg_wick_pct']:.2f}%")
        print(f"  SELL:       {signals['sell_score']}/100")
        print(f"  REBUY:      {signals['rebuy_score']}/100\n")
        html = generate_html(signals, generated_at)
        OUTPUT_FILE.write_text(html, encoding="utf-8")
        print(f"Saved: {OUTPUT_FILE}")
    except Exception:
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()

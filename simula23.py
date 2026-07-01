#!/usr/bin/env python3
"""
SIMULA23 — IL CAMPO GRAVITAZIONALE PRE-ENTRATA

Per ogni trade in curva_nascita, ricostruisce le kline Binance
dei 5 minuti PRIMA dell'entrata. Confronta maschi vs non-maschi.

METRICHE PRE-ENTRATA (5 kline al minuto):
  1. trend_5m:    close[-1] - close[0]  (movimento netto 5 min)
  2. mom_last:    close[-1] - close[-2]  (ultimo minuto)
  3. vol_range:   media di (high-low) sulle 5 kline  (volatilità)
  4. vol_trend:   volume[-1] / volume[0]  (volume cresce o cala?)
  5. body_ratio:  media |close-open|/range  (candele decise vs ombre)
  6. bull_count:  quante kline chiudono in verde (0-5)
  7. trend_accel: (close[-1]-close[-2]) - (close[-3]-close[-4])  (accelera?)

MASCHIO  = peak_nascita >= 0.5 (lordo >= 2.5) AND pnl_finale > 0
NON-MASC = tutto il resto

Lancio: python3 simula23.py
"""
import sqlite3, json, time, urllib.request, urllib.parse

DB  = "/var/data/trading_data.db"
SYM = "BTCUSDC"
N_KLINES = 6   # prendiamo 6 così abbiamo 5 gap tra chiusure

# ── legge trades ─────────────────────────────────────────────────────────────
con  = sqlite3.connect(DB)

# rileva nome colonna timestamp
cols = [r[1] for r in con.execute("PRAGMA table_info(curva_nascita)").fetchall()]
print(f"Colonne curva_nascita: {cols}\n")

ts_col = None
for c in ["trade_ts", "ts", "timestamp", "created_at", "open_time"]:
    if c in cols:
        ts_col = c
        break
if ts_col is None:
    # usa la prima colonna numerica che sembra un epoch
    for c in cols:
        sample = con.execute(f"SELECT {c} FROM curva_nascita LIMIT 1").fetchone()
        if sample and sample[0] and str(sample[0]).isdigit() and len(str(int(sample[0]))) >= 10:
            ts_col = c
            break

print(f"Colonna timestamp usata: {ts_col}\n")

rows = con.execute(
    f"SELECT {ts_col}, peak_nascita, pnl_finale "
    f"FROM curva_nascita WHERE pnl_finale IS NOT NULL"
).fetchall()
con.close()

# ── fetch kline Binance ───────────────────────────────────────────────────────
def fetch_klines(ts_epoch, n=N_KLINES):
    """Ritorna le n kline al minuto che terminano PRIMA di ts_epoch."""
    # ts in ms per Binance
    ts_ms = int(ts_epoch) * 1000 if int(ts_epoch) < 1e12 else int(ts_epoch)
    params = urllib.parse.urlencode({
        "symbol":    SYM,
        "interval":  "1m",
        "endTime":   ts_ms,
        "limit":     n,
    })
    url = f"https://api.binance.com/api/v3/klines?{params}"
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            return json.loads(r.read())
    except Exception as e:
        return None

def analizza_klines(klines):
    """Estrae metriche dal campo pre-entrata."""
    if not klines or len(klines) < 4:
        return None
    opens  = [float(k[1]) for k in klines]
    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]
    closes = [float(k[4]) for k in klines]
    vols   = [float(k[5]) for k in klines]

    ranges = [h - l for h, l in zip(highs, lows)]
    bodies = [abs(c - o) for c, o in zip(closes, opens)]

    trend_5m  = closes[-1] - closes[0]
    mom_last  = closes[-1] - closes[-2]
    vol_range = sum(ranges) / len(ranges)
    vol_trend = vols[-1] / vols[0] if vols[0] > 0 else 1.0
    body_ratio = sum(b / r if r > 0 else 0 for b, r in zip(bodies, ranges)) / len(bodies)
    bull_count = sum(1 for c, o in zip(closes, opens) if c > o)
    # accelerazione: ultimi 2 gap vs penultimi 2 gap
    if len(closes) >= 5:
        mom_now  = closes[-1] - closes[-2]
        mom_prev = closes[-3] - closes[-4]
        trend_accel = mom_now - mom_prev
    else:
        trend_accel = 0.0

    return {
        "trend_5m":    trend_5m,
        "mom_last":    mom_last,
        "vol_range":   vol_range,
        "vol_trend":   vol_trend,
        "body_ratio":  body_ratio,
        "bull_count":  bull_count,
        "trend_accel": trend_accel,
    }

# ── loop principale ───────────────────────────────────────────────────────────
print(f"Fetch kline per {len(rows)} trade... (può richiedere 1-2 min)")
print("=" * 72)

records = []
errori  = 0
for i, (ts, peak, pnlf) in enumerate(rows):
    if i % 50 == 0:
        print(f"  {i}/{len(rows)}...")
    klines = fetch_klines(ts)
    if klines is None:
        errori += 1
        time.sleep(0.2)
        continue
    meta = analizza_klines(klines)
    if meta is None:
        errori += 1
        continue
    meta["peak"]    = float(peak or 0)
    meta["pnl_fin"] = float(pnlf)
    records.append(meta)
    time.sleep(0.05)   # ~20 req/s, ben sotto il limite Binance

print(f"\nCompletato: {len(records)} trade analizzati, {errori} errori fetch")
print()

# ── split maschi / non-maschi ─────────────────────────────────────────────────
maschi  = [r for r in records if r["peak"] >= 0.5 and r["pnl_fin"] > 0]
nonmasc = [r for r in records if not (r["peak"] >= 0.5 and r["pnl_fin"] > 0)]

N_m, N_n = len(maschi), len(nonmasc)
print(f"MASCHI  (peak>=0.5 AND pnl>0): {N_m}")
print(f"NON-MASCHI:                     {N_n}")
print()

def avg(lst, key):
    v = [r[key] for r in lst if r.get(key) is not None]
    return sum(v) / len(v) if v else 0.0

# ── confronto metriche ────────────────────────────────────────────────────────
print("=" * 72)
print("CAMPO GRAVITAZIONALE PRE-ENTRATA — MASCHI vs NON-MASCHI")
print("=" * 72)
print(f"  {'METRICA':<28}  {'MASCHI':>10}  {'NON-MASCHI':>11}  {'DIFF':>8}  SEGNALE")
print(f"  {'-'*72}")

metriche = [
    ("Trend 5m ($)",        "trend_5m"),
    ("Momentum ultimo 1m ($)", "mom_last"),
    ("Volatilità range medio ($)", "vol_range"),
    ("Volume trend (ratio)", "vol_trend"),
    ("Body ratio (0-1)",    "body_ratio"),
    ("Candele bullish (0-5)", "bull_count"),
    ("Accelerazione trend ($)", "trend_accel"),
]

for nome, key in metriche:
    vm = avg(maschi,  key)
    vn = avg(nonmasc, key)
    diff = vm - vn
    if abs(diff) > 0.5 * (abs(vm) + abs(vn) + 0.001):
        segnale = "  ◄◄ FORTE"
    elif abs(diff) > 0.2 * (abs(vm) + abs(vn) + 0.001):
        segnale = "  ◄ MEDIO"
    else:
        segnale = ""
    print(f"  {nome:<28}  {vm:>+10.4f}  {vn:>+11.4f}  {diff:>+8.4f}{segnale}")

print()

# ── distribuzione trend_5m ────────────────────────────────────────────────────
print("─" * 72)
print("DISTRIBUZIONE TREND 5m PRE-ENTRATA ($ movimento BTC nel minuto)")
print("─" * 72)
fasce = [
    (-9999, -200, "< -200$"),
    (-200,  -50,  "-200→-50$"),
    (-50,    0,   "-50→0$"),
    (0,     50,   "0→+50$"),
    (50,   200,   "+50→+200$"),
    (200, 9999,   "> +200$"),
]
print(f"  {'fascia':<14}  {'MASCHI%':>8}  {'NONM%':>8}  {'M/(M+N)':>9}")
for lo, hi, lab in fasce:
    nm_ = sum(1 for r in maschi  if lo <= r["trend_5m"] < hi)
    nn_ = sum(1 for r in nonmasc if lo <= r["trend_5m"] < hi)
    pm_ = 100 * nm_ / N_m if N_m else 0
    pn_ = 100 * nn_ / N_n if N_n else 0
    ratio = 100 * nm_ / (nm_ + nn_) if (nm_ + nn_) > 0 else 0
    flag = "  ← FORTE" if abs(pm_ - pn_) >= 10 else ""
    print(f"  {lab:<14}  {pm_:>7.0f}%  {pn_:>7.0f}%  {ratio:>8.0f}%{flag}")

print()

# ── distribuzione bull_count ──────────────────────────────────────────────────
print("─" * 72)
print("BULL COUNT (candele bullish su 5 o 6): 0=tutto rosso, 5/6=tutto verde")
print("─" * 72)
print(f"  {'bull':>5}  {'MASCHI%':>8}  {'NONM%':>8}")
for b in range(7):
    pm_ = 100 * sum(1 for r in maschi  if r["bull_count"] == b) / N_m if N_m else 0
    pn_ = 100 * sum(1 for r in nonmasc if r["bull_count"] == b) / N_n if N_n else 0
    flag = "  ◄" if abs(pm_ - pn_) >= 10 else ""
    print(f"  {b:>5}  {pm_:>7.0f}%  {pn_:>7.0f}%{flag}")

print()

# ── filtri combinati ──────────────────────────────────────────────────────────
print("=" * 72)
print("FILTRI CAMPO — se non entra quando campo sfavorevole")
print("(baseline: pnl_fin reale di tutti i trade analizzati)")
print("=" * 72)
print()

tot_reale = sum(r["pnl_fin"] for r in records)
N_tot = len(records)

filtri_campo = [
    ("trend_5m > 0",       lambda r: r["trend_5m"] > 0),
    ("trend_5m > 50",      lambda r: r["trend_5m"] > 50),
    ("trend_5m > 100",     lambda r: r["trend_5m"] > 100),
    ("mom_last > 0",       lambda r: r["mom_last"] > 0),
    ("bull_count >= 3",    lambda r: r["bull_count"] >= 3),
    ("bull_count >= 4",    lambda r: r["bull_count"] >= 4),
    ("trend_5m>0 AND bull>=3", lambda r: r["trend_5m"] > 0 and r["bull_count"] >= 3),
    ("trend_accel > 0",    lambda r: r["trend_accel"] > 0),
]

print(f"  {'FILTRO (entra SE)':<30}  {'N_ent':>6}  {'tot_ent':>9}  {'avg':>7}  {'vs baseline':>11}")
print(f"  {'-'*72}")

for label, cond in filtri_campo:
    ent = [r for r in records if cond(r)]
    if not ent:
        continue
    n = len(ent)
    tot = sum(r["pnl_fin"] for r in ent)
    avg_e = tot / n
    # baseline proporzionale (se filtra il X% dei trade)
    baseline_prop = tot_reale * n / N_tot
    delta = tot - baseline_prop
    flag = "  ◄ MEGLIO" if delta > 0 else ""
    print(f"  {label:<30}  {n:>6}  {tot:>+9.1f}  {avg_e:>+7.2f}  {delta:>+10.1f}{flag}")

print()
print(f"  Baseline REALE (tutti): ${tot_reale:+.1f}  avg={tot_reale/N_tot:+.2f}$/trade")
print()

# ── verdetto ──────────────────────────────────────────────────────────────────
print("=" * 72)
print("VERDETTO — IL CAMPO GRAVITAZIONALE CONTA?")
print("=" * 72)
print()

# metrica più forte: quale ha la diff relativa maggiore?
diffs = []
for nome, key in metriche:
    vm = avg(maschi,  key)
    vn = avg(nonmasc, key)
    mag = abs(vm) + abs(vn)
    if mag > 0.001:
        diffs.append((abs(vm - vn) / mag, nome, vm - vn))
diffs.sort(reverse=True)

if diffs and diffs[0][0] > 0.2:
    print(f"  ✓ Campo gravitazionale CONTA: '{diffs[0][1]}' differisce del {100*diffs[0][0]:.0f}%")
    print(f"    maschi vs non-maschi — campo pre-entrata non è neutro.")
    for _, nome, diff in diffs[:3]:
        print(f"      {nome}: diff={diff:+.4f}")
else:
    print(f"  ~ Campo pre-entrata NON mostra differenze forti tra maschi e non-maschi.")
    print(f"    Il maschio nasce dal nulla, il campo non lo predice.")
print()

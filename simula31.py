#!/usr/bin/env python3
"""
SIMULA31 — RICCHEZZA DEL TERRENO (campo graduato vs campo binario)

Ipotesi: non conta SE il campo è acceso, conta QUANTO è ricco il terreno.
Un'accelerazione intensa + lunga + voluminosa spinge il candidato a diventare
maschio grosso. Un campo tecnicamente acceso ma magro: pareggio.

METRICA RICCHEZZA (composita, 0-100):
  1. accel_intensity:  (trend_accel - 20) / 100     — quanto sopra soglia
  2. duration_push:    minuti consecutivi con mom>0  — durata della spinta
  3. vol_score:        volume accumulato vs baseline  — partecipazione
  4. density:          accel_intensity / duration    — energia per unità tempo

  ricchezza = media pesata dei 4 fattori (normalizzati 0-1, poi x100)

VERIFICA:
  A. Correlazione ricchezza ↔ pnl_fin (Pearson + permutation)
  B. Split in quartili: i trade in Q4 (terreno ricco) fanno picco/netto molto più alto?
  C. 3 test (walk-forward + OOS + permutation Bonferroni) su:
     - campo_binario (baseline: accel>20 AND mom>0 AND t5m>200)
     - campo_ricchezza > Q50, > Q75 (filtri graduati)
     - campo_ricchezza score come peso (top 25% per pnl)
  D. Confronto diretto: campo graduato SUPERA il binario sui 3 test?

Lancio: python3 simula31.py
"""
import sqlite3, json, os, random, math, time, datetime
import urllib.request, urllib.parse

DB          = "/var/data/trading_data.db"
CACHE_RICH  = "/tmp/campo_rich_cache.json"
SYM         = "BTCUSDC"
N_PERM      = 2000
N_KLINES    = 16   # 15 minuti pre-entrata per misurare durata

# ── fetch ─────────────────────────────────────────────────────────────────────
def fetch_klines(ts_epoch, n=N_KLINES):
    ts_ms = int(ts_epoch)*1000 if int(ts_epoch)<1e12 else int(ts_epoch)
    params = urllib.parse.urlencode({"symbol":SYM,"interval":"1m","endTime":ts_ms,"limit":n})
    try:
        with urllib.request.urlopen(
                f"https://api.binance.com/api/v3/klines?{params}", timeout=8) as r:
            return json.loads(r.read())
    except Exception: return None

def fetch_vol_baseline(ts_epoch, n=30):
    ts_ms = int(ts_epoch)*1000 if int(ts_epoch)<1e12 else int(ts_epoch)
    ts_pre = ts_ms - N_KLINES*60*1000 - 60*1000
    params = urllib.parse.urlencode({"symbol":SYM,"interval":"1m","endTime":ts_pre,"limit":n})
    try:
        with urllib.request.urlopen(
                f"https://api.binance.com/api/v3/klines?{params}", timeout=8) as r:
            kl = json.loads(r.read())
        if kl: return sum(float(k[5]) for k in kl)/len(kl)
    except Exception: pass
    return None

def calcola_ricchezza(klines, vol_baseline=None):
    """
    Ritorna dict con tutte le metriche di ricchezza del terreno.
    klines = ultimi N_KLINES minuti prima dell'entrata.
    """
    if not klines or len(klines) < 6: return None
    closes = [float(k[4]) for k in klines]
    opens  = [float(k[1]) for k in klines]
    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]
    vols   = [float(k[5]) for k in klines]

    # ── metriche base (ultime 5 kline) ────────────────────────────────────
    c5 = closes[-6:]   # 6 close per calcolare 5 gap
    trend_5m   = c5[-1] - c5[0]
    mom_last   = c5[-1] - c5[-2]
    mom_prev   = c5[-3] - c5[-4]
    trend_accel = mom_last - mom_prev

    # ── DURATA: quanti minuti consecutivi FINALI con mom > 0 ──────────────
    gaps = [closes[i]-closes[i-1] for i in range(1, len(closes))]
    duration_push = 0
    for g in reversed(gaps):
        if g > 0: duration_push += 1
        else: break

    # ── VOLUME ACCUMULATO durante la spinta ────────────────────────────────
    # volume negli ultimi duration_push minuti
    vol_spinta = sum(vols[-duration_push:]) if duration_push > 0 else vols[-1]

    # volume relativo alla baseline
    vol_rel = (vol_spinta / duration_push) / vol_baseline \
        if vol_baseline and vol_baseline > 0 and duration_push > 0 else 1.0

    # ── INTENSITÀ: quanto sopra la soglia 20 ──────────────────────────────
    accel_intensity = max(0, trend_accel - 20)   # lordo sopra soglia

    # ── DENSITÀ: energia per minuto ───────────────────────────────────────
    density = accel_intensity / max(duration_push, 1)

    # ── RICCHEZZA COMPOSITA (0-100) ────────────────────────────────────────
    # Normalizza ogni componente (valori tipici da dati storici):
    #   accel_intensity: tipicamente 0-200, usiamo /100 → 0-2 → cap a 1
    #   duration_push:   0-15 min → /10 → 0-1.5 → cap a 1
    #   vol_rel:         1.0 = media, 2.0 = doppio → (vol_rel-1)/2 → 0-0.5 → cap 1
    #   density:         0-50 → /20 → 0-2.5 → cap a 1
    norm_accel    = min(1.0, accel_intensity / 100)
    norm_duration = min(1.0, duration_push   / 10)
    norm_vol      = min(1.0, max(0, (vol_rel - 1) / 2))
    norm_density  = min(1.0, density / 20)

    # pesi: accel 35%, duration 30%, vol 20%, density 15%
    ricchezza = 100 * (0.35*norm_accel + 0.30*norm_duration +
                        0.20*norm_vol   + 0.15*norm_density)

    return {
        # base (per filtro binario)
        "trend_5m":      trend_5m,
        "mom_last":      mom_last,
        "trend_accel":   trend_accel,
        # quarta dimensione
        "accel_intensity": accel_intensity,
        "duration_push":   duration_push,
        "vol_spinta":      vol_spinta,
        "vol_rel":         vol_rel,
        "density":         density,
        # composita
        "ricchezza":       ricchezza,
        "norm_accel":      norm_accel,
        "norm_duration":   norm_duration,
        "norm_vol":        norm_vol,
        "norm_density":    norm_density,
    }

# ── carica DB ─────────────────────────────────────────────────────────────────
con  = sqlite3.connect(DB)
cols = [r[1] for r in con.execute("PRAGMA table_info(curva_nascita)").fetchall()]
ts_col = next((c for c in ["trade_ts","ts","timestamp","created_at","open_time"]
               if c in cols), None)
rows = con.execute(
    f"SELECT {ts_col}, peak_nascita, pnl_finale "
    f"FROM curva_nascita WHERE pnl_finale IS NOT NULL ORDER BY {ts_col} ASC"
).fetchall()
con.close()
print(f"Trade totali: {len(rows)}")

# ── cache ─────────────────────────────────────────────────────────────────────
rich_cache = {}
if os.path.exists(CACHE_RICH):
    with open(CACHE_RICH) as f:
        for item in json.load(f):
            rich_cache[str(item["ts"])] = item
    print(f"Cache ricchezza: {len(rich_cache)} voci")

records = []; to_save = []
for i, (ts, peak, pnlf) in enumerate(rows):
    ts_str = str(ts)
    if ts_str in rich_cache:
        m = rich_cache[ts_str].get("metrics")
    else:
        if i % 50 == 0: print(f"  fetch {i}/{len(rows)}...")
        kl = fetch_klines(ts, n=N_KLINES)
        vb = fetch_vol_baseline(ts)
        m  = calcola_ricchezza(kl, vb) if kl else None
        rich_cache[ts_str] = {"ts": ts, "metrics": m}
        to_save.append({"ts": ts, "metrics": m})
        time.sleep(0.12)
    if m is None: continue
    rec = dict(m)
    rec.update({"ts": ts, "peak": float(peak or 0), "pnl_fin": float(pnlf)})
    records.append(rec)

if to_save:
    existing = []
    if os.path.exists(CACHE_RICH):
        with open(CACHE_RICH) as f: existing = json.load(f)
    with open(CACHE_RICH, "w") as f: json.dump(existing + to_save, f)

N = len(records)
tot_real = sum(r["pnl_fin"] for r in records)
print(f"Record: {N}  |  Netto reale: ${tot_real:+.1f}\n")

def filtro_binario(r):
    return r["trend_accel"] > 20 and r["mom_last"] > 0 and r["trend_5m"] > 200

# ── A. CORRELAZIONE ricchezza ↔ pnl_fin ──────────────────────────────────────
print("="*72)
print("A. CORRELAZIONE: ricchezza del terreno ↔ pnl_fin")
print("="*72)
print()

def pearson(xs, ys):
    n = len(xs)
    if n < 3: return 0.0
    mx = sum(xs)/n; my = sum(ys)/n
    num = sum((x-mx)*(y-my) for x,y in zip(xs,ys))
    dx  = math.sqrt(sum((x-mx)**2 for x in xs))
    dy  = math.sqrt(sum((y-my)**2 for y in ys))
    return num/(dx*dy) if dx*dy > 0 else 0.0

random.seed(42)

def perm_corr(xs, ys, n_perm=1000):
    obs = pearson(xs, ys)
    ys_mut = list(ys)
    count = sum(1 for _ in range(n_perm)
                if (random.shuffle(ys_mut) or True) and abs(pearson(xs, ys_mut)) >= abs(obs))
    return obs, count/n_perm

all_r    = [r["ricchezza"] for r in records]
all_pnl  = [r["pnl_fin"]  for r in records]
all_peak = [r["peak"]      for r in records]

r_pnl,  p_pnl  = perm_corr(all_r, all_pnl)
r_peak, p_peak  = perm_corr(all_r, all_peak)

print(f"  ricchezza ↔ pnl_fin:      r={r_pnl:+.3f}  p={p_pnl:.3f}  "
      f"{'✓ SIGN.' if p_pnl<=0.05 else '✗ non sign.'}")
print(f"  ricchezza ↔ peak_nascita: r={r_peak:+.3f}  p={p_peak:.3f}  "
      f"{'✓ SIGN.' if p_peak<=0.05 else '✗ non sign.'}")
print()

# correlazioni delle singole componenti
for key in ["accel_intensity","duration_push","vol_rel","density"]:
    xs = [r[key] for r in records]
    rc, pc = perm_corr(xs, all_pnl, n_perm=500)
    flag = "  ◄◄" if pc<=0.05 and abs(rc)>0.1 else ("  ◄" if pc<=0.10 else "")
    print(f"  {key:<22} ↔ pnl_fin: r={rc:+.3f}  p={pc:.3f}{flag}")
print()

# ── B. SPLIT IN QUARTILI ───────────────────────────────────────────────────────
print("="*72)
print("B. SPLIT QUARTILI — terreno povero vs ricco")
print("="*72)
print()

rich_sorted = sorted(records, key=lambda r: r["ricchezza"])
q_size = N // 4
quartili = [rich_sorted[i*q_size:(i+1)*q_size] for i in range(4)]
if len(quartili[3]) < q_size: quartili[3] = rich_sorted[3*q_size:]

def avg(lst, key): v=[r[key] for r in lst]; return sum(v)/len(v) if v else 0.0

print(f"  {'QUARTILE':<12}  {'N':>4}  {'rich_avg':>9}  {'pnl_avg':>9}  {'peak_avg':>9}  {'tot_netto':>10}")
print(f"  {'-'*64}")
for i, q in enumerate(quartili):
    r_avg = avg(q, "ricchezza"); p_avg = avg(q, "pnl_fin"); pk_avg = avg(q, "peak")
    tot = sum(r["pnl_fin"] for r in q)
    print(f"  Q{i+1} ({'povero' if i==0 else 'ricco' if i==3 else 'medio':<6})  "
          f"{len(q):>4}  {r_avg:>9.1f}  {p_avg:>+9.3f}  {pk_avg:>9.3f}  {tot:>+10.2f}")
print()

# percentili chiave della ricchezza
pcts = [25, 50, 75, 90]
thresholds = {}
for p in pcts:
    thresholds[p] = sorted(all_r)[int(p*N/100)]
    print(f"  P{p:02d} ricchezza = {thresholds[p]:.2f}")
print()

# ── C. 3 TEST SU FILTRI GRADUATI ─────────────────────────────────────────────
print("="*72)
print("C. 3 TEST — CAMPO BINARIO vs CAMPO GRADUATO")
print("="*72)
print()

random.seed(42)

def netto_filt(subset, cond):
    return sum(r["pnl_fin"] for r in subset if cond(r))

def tre_test_full(recs, cond, n_perm=N_PERM):
    n_f  = sum(1 for r in recs if cond(r))
    obs  = netto_filt(recs, cond)
    # walk-forward 3 blocchi
    bs   = len(recs)//3
    blks = [recs[:bs], recs[bs:2*bs], recs[2*bs:]]
    wf   = [netto_filt(b, cond) for b in blks]
    wf_ok = all(x >= -20 for x in wf)
    # OOS
    mid  = len(recs)//2
    oos  = netto_filt(recs[mid:], cond)
    oos_ok = oos >= -30
    # permutation
    pnls  = [r["pnl_fin"] for r in recs]
    flags = [cond(r) for r in recs]
    count = 0
    for _ in range(n_perm):
        random.shuffle(flags)
        if sum(p for p,f in zip(pnls,flags) if f) >= obs: count += 1
    p_val = count/n_perm
    return {
        "n": n_f, "netto": obs, "wf": wf, "wf_ok": wf_ok,
        "oos": oos, "oos_ok": oos_ok, "p_val": p_val,
        "all_ok": obs>0 and wf_ok and oos_ok and p_val<=0.05
    }

# filtri da testare
filtri = []
filtri.append(("campo_binario (base)",
    lambda r: filtro_binario(r)))

for p in [50, 75, 90]:
    th = thresholds[p]
    filtri.append((f"ricchezza > P{p} ({th:.1f})",
        lambda r, t=th: r["ricchezza"] > t))

for p in [50, 75]:
    th = thresholds[p]
    filtri.append((f"binario + ricchezza>P{p} ({th:.1f})",
        lambda r, t=th: filtro_binario(r) and r["ricchezza"] > t))

# soglie assolute
for th_abs in [10, 20, 30, 40, 50]:
    filtri.append((f"ricchezza > {th_abs}",
        lambda r, t=th_abs: r["ricchezza"] > t))

# binario + componenti specifiche
filtri.append(("binario + duration>=3",
    lambda r: filtro_binario(r) and r["duration_push"] >= 3))
filtri.append(("binario + duration>=5",
    lambda r: filtro_binario(r) and r["duration_push"] >= 5))
filtri.append(("binario + vol_rel>=1.5",
    lambda r: filtro_binario(r) and r["vol_rel"] >= 1.5))
filtri.append(("binario + accel_int>=50",
    lambda r: filtro_binario(r) and r["accel_intensity"] >= 50))
filtri.append(("binario + density>=10",
    lambda r: filtro_binario(r) and r["density"] >= 10))

BONF = 0.05 / len(filtri)
print(f"  Filtri testati: {len(filtri)}  |  Bonferroni p < {BONF:.4f}")
print()
print(f"  {'FILTRO':<40}  {'N':>4}  {'netto':>7}  {'WF(1,2,3)':>16}  {'OOS':>7}  {'p':>6}  ESITO")
print(f"  {'-'*96}")

migliori = []
for label, cond in filtri:
    n_f = sum(1 for r in records if cond(r))
    if n_f < 10:
        print(f"  {label:<40}  {n_f:>4}  {'(pochi)':>50}")
        continue
    res = tre_test_full(records, cond)
    wf_str = f"({res['wf'][0]:+.0f},{res['wf'][1]:+.0f},{res['wf'][2]:+.0f})"
    bon    = "✓BON" if res["p_val"] <= BONF else ""
    esito  = ("✓✓ PASSA!" if res["all_ok"] else
              "◐ 2/3"    if sum([res["wf_ok"], res["oos_ok"], res["p_val"]<=0.05, res["netto"]>0])>=3 else
              "✗")
    print(f"  {label:<40}  {n_f:>4}  {res['netto']:>+7.1f}  {wf_str:>16}  "
          f"{res['oos']:>+7.1f}  {res['p_val']:>6.3f}  {esito} {bon}")
    if res["all_ok"]:
        migliori.append({"label": label, "n": n_f, **res})

print()

# ── D. CONFRONTO BINARIO vs GRADUATO ─────────────────────────────────────────
print("="*72)
print("D. VERDETTO — CAMPO GRADUATO SUPERA IL BINARIO?")
print("="*72)
print()

bin_res = tre_test_full(records, filtro_binario)
print(f"  CAMPO BINARIO (baseline):")
print(f"    N={bin_res['n']}  netto=${bin_res['netto']:+.1f}  "
      f"OOS=${bin_res['oos']:+.1f}  p={bin_res['p_val']:.3f}")
print(f"    WF: {bin_res['wf'][0]:+.1f} / {bin_res['wf'][1]:+.1f} / {bin_res['wf'][2]:+.1f}")
print()

if migliori:
    migliori.sort(key=lambda x: x["netto"], reverse=True)
    print(f"  ✓ CAMPO GRADUATO SUPERA IL BINARIO:")
    for m in migliori:
        delta = m["netto"] - bin_res["netto"]
        print(f"    '{m['label']}'")
        print(f"    N={m['n']}  netto=${m['netto']:+.1f}  "
              f"(vs binario: {delta:+.1f}$)  OOS=${m['oos']:+.1f}  p={m['p_val']:.3f}")
    print()
    print(f"  PROSSIMO PASSO: aggiungere la metrica ricchezza al gate CAMPO_ESTERNO.")
    print(f"  La 'ricchezza del terreno' conta, non solo il campo binario.")
else:
    print(f"  Nessun filtro graduato supera tutti e 3 i test meglio del binario.")
    print()
    print(f"  INTERPRETAZIONE:")
    if bin_res["all_ok"]:
        print(f"  Il campo BINARIO già regge i 3 test — graduare non aggiunge potere predittivo.")
        print(f"  Il 4 giugno era un outlier di ricchezza, non una dimensione sistematica.")
        print(f"  Conclusione: mantieni il campo binario. La ricchezza del terreno")
        print(f"  non è misurabile in modo affidabile dalle kline al minuto.")
    else:
        print(f"  Né il binario né il graduato reggono i 3 test stabilmente.")
        print(f"  Il segnale è ancora debole per N={N} trade.")
print()

# ── DISTRIBUZIONE RICCHEZZA per giorno ────────────────────────────────────────
print("="*72)
print("RICCHEZZA DEL TERRENO PER GIORNO (tutti i trade, non solo filtrati)")
print("="*72)
print()
from collections import defaultdict
giorni_r   = defaultdict(list)
giorni_pnl = defaultdict(list)
for r in records:
    s = int(r["ts"]) if int(r["ts"])<1e12 else int(r["ts"])//1000
    d = datetime.datetime.utcfromtimestamp(s).strftime("%Y-%m-%d")
    giorni_r[d].append(r["ricchezza"])
    giorni_pnl[d].append(r["pnl_fin"])

print(f"  {'DATA':<12}  {'N':>4}  {'rich_med':>9}  {'rich_max':>9}  {'pnl_tot':>9}  NOTE")
print(f"  {'-'*60}")
for d in sorted(giorni_r.keys()):
    rs = giorni_r[d]; ps = giorni_pnl[d]
    r_med = sum(rs)/len(rs); r_max = max(rs)
    p_tot = sum(ps)
    note = "  ← FERTILE" if r_max >= thresholds.get(90, 999) else ""
    print(f"  {d:<12}  {len(rs):>4}  {r_med:>9.1f}  {r_max:>9.1f}  {p_tot:>+9.2f}{note}")
print()

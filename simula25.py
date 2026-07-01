#!/usr/bin/env python3
"""
SIMULA25 — PROVA DEL NOVE: IL CAMPO REGGE O È OVERFITTING?

Tre test rigorosi sul filtro (accel>0 AND mom>0 AND t5m>100):

1. WALK-FORWARD (3 blocchi temporali): il filtro funziona in tutti e 3, o solo in uno?
2. OUT-OF-SAMPLE: parametri scelti sulla prima metà, applicati alla seconda mai vista.
3. PERMUTATION TEST: mescola i dati 1000 volte → qual è la probabilità che -1.6$ sia caso?

VERDETTO ONESTO:
  - Se il filtro regge in tutti e 3 i blocchi → segnale reale
  - Se crolla in 1-2 blocchi → overfitting, NON deployare
  - Se p-value permutation > 0.05 → potrebbe essere fortuna

Lancio: python3 simula25.py
"""
import sqlite3, json, os, random, time, urllib.request, urllib.parse

DB         = "/var/data/trading_data.db"
CACHE_FILE = "/tmp/campo_cache.json"
SYM        = "BTCUSDC"

# ── carica DB ─────────────────────────────────────────────────────────────────
con  = sqlite3.connect(DB)
cols = [r[1] for r in con.execute("PRAGMA table_info(curva_nascita)").fetchall()]
ts_col = next((c for c in ["trade_ts","ts","timestamp","created_at","open_time"]
               if c in cols), None)
if ts_col is None:
    for c in cols:
        s = con.execute(f"SELECT {c} FROM curva_nascita LIMIT 1").fetchone()
        if s and s[0] and str(s[0]).isdigit() and len(str(int(s[0]))) >= 10:
            ts_col = c; break

rows = con.execute(
    f"SELECT {ts_col}, peak_nascita, pnl_finale, curva_json "
    f"FROM curva_nascita WHERE pnl_finale IS NOT NULL ORDER BY {ts_col} ASC"
).fetchall()
con.close()
print(f"Trade totali (ordinati per tempo): {len(rows)}")

# ── carica cache campo ────────────────────────────────────────────────────────
def fetch_klines(ts_epoch, n=6):
    ts_ms = int(ts_epoch)*1000 if int(ts_epoch) < 1e12 else int(ts_epoch)
    params = urllib.parse.urlencode({"symbol":SYM,"interval":"1m","endTime":ts_ms,"limit":n})
    try:
        with urllib.request.urlopen(
                f"https://api.binance.com/api/v3/klines?{params}", timeout=8) as r:
            return json.loads(r.read())
    except Exception:
        return None

def klines_to_metrics(klines):
    if not klines or len(klines) < 4: return None
    opens  = [float(k[1]) for k in klines]
    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]
    closes = [float(k[4]) for k in klines]
    vols   = [float(k[5]) for k in klines]
    ranges = [h-l for h,l in zip(highs,lows)]
    bodies = [abs(c-o) for c,o in zip(closes,opens)]
    mom_now  = closes[-1] - closes[-2]
    mom_prev = closes[-3] - closes[-4] if len(closes) >= 5 else 0
    return {
        "trend_5m":    closes[-1] - closes[0],
        "mom_last":    mom_now,
        "vol_range":   sum(ranges)/len(ranges),
        "vol_trend":   vols[-1]/vols[0] if vols[0]>0 else 1.0,
        "body_ratio":  sum(b/r if r>0 else 0 for b,r in zip(bodies,ranges))/len(bodies),
        "bull_count":  sum(1 for c,o in zip(closes,opens) if c>o),
        "trend_accel": mom_now - mom_prev,
    }

cache_map = {}
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE) as f:
        for item in json.load(f):
            cache_map[str(item["ts"])] = item["metrics"]
    print(f"Cache caricata: {len(cache_map)} voci")

# ── costruisce records ────────────────────────────────────────────────────────
records  = []
to_save  = []
for ts, peak, pnlf, cj in rows:
    ts_str = str(ts)
    if ts_str in cache_map:
        m = cache_map[ts_str]
    else:
        kl = fetch_klines(ts)
        m  = klines_to_metrics(kl) if kl else None
        cache_map[ts_str] = m
        to_save.append({"ts": ts, "metrics": m})
        time.sleep(0.05)
    if m is None:
        continue
    g1s = None
    try:
        pts   = json.loads(cj)
        micro = [(float(p[0]), float(p[1])) for p in pts
                 if len(p) >= 2 and float(p[0]) <= 2.0]
        if len(micro) >= 2:
            g1s = min(micro, key=lambda p: abs(p[0]-1.0))[1]
    except Exception:
        pass
    rec = dict(m)
    rec.update({"ts": ts, "peak": float(peak or 0),
                "pnl_fin": float(pnlf), "g1s": g1s})
    records.append(rec)

if to_save:
    existing = []
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f: existing = json.load(f)
    with open(CACHE_FILE, "w") as f: json.dump(existing + to_save, f)

N        = len(records)
tot_real = sum(r["pnl_fin"] for r in records)
print(f"Record con campo: {N}  |  Netto reale: ${tot_real:+.1f}\n")

# ── filtro candidato (il migliore trovato in simula24) ────────────────────────
FILTRO_LABEL = "accel>0 AND mom>0 AND t5m>100"
def filtro_campo(r):
    return r["trend_accel"] > 0 and r["mom_last"] > 0 and r["trend_5m"] > 100

def netto_col_filtro(subset):
    """Netto se entriamo solo quando filtro_campo(r) è True, 0 altrimenti."""
    return sum(r["pnl_fin"] for r in subset if filtro_campo(r))

def netto_reale(subset):
    return sum(r["pnl_fin"] for r in subset)

# ── TEST 1: WALK-FORWARD (3 blocchi temporali) ───────────────────────────────
print("=" * 72)
print("TEST 1 — WALK-FORWARD (3 blocchi temporali, stessa dimensione)")
print(f"  Filtro testato: {FILTRO_LABEL}")
print("=" * 72)
print()

block_size = N // 3
blocks = [
    records[0           : block_size],
    records[block_size  : 2*block_size],
    records[2*block_size: N],
]

print(f"  {'BLOCCO':<10}  {'N':>5}  {'N_entr':>7}  {'netto_filt':>11}  {'netto_real':>11}  {'delta':>8}  STATUS")
print(f"  {'-'*72}")

all_ok = True
for i, blk in enumerate(blocks):
    n_blk     = len(blk)
    n_ent     = sum(1 for r in blk if filtro_campo(r))
    nf        = netto_col_filtro(blk)
    nr        = netto_reale(blk)
    delta     = nf - nr
    # ts range
    ts_min = blk[0]["ts"]  if blk else 0
    ts_max = blk[-1]["ts"] if blk else 0
    status = "✓ OK" if nf >= 0 else ("~ quasi" if nf >= -30 else "✗ CROLLA")
    if nf < -30: all_ok = False
    print(f"  Blocco {i+1:<4}  {n_blk:>5}  {n_ent:>7}  {nf:>+11.1f}  {nr:>+11.1f}  {delta:>+8.1f}  {status}")

print()
if all_ok:
    print("  ✓ Il filtro regge in TUTTI e 3 i blocchi → segnale consistente")
else:
    print("  ✗ Il filtro NON regge uniformemente → rischio overfitting ALTO")
print()

# ── TEST 2: OUT-OF-SAMPLE (prima metà → scelta, seconda metà → verifica) ────
print("=" * 72)
print("TEST 2 — OUT-OF-SAMPLE")
print("  Scelta parametri su PRIMA METÀ, verifica su SECONDA METÀ mai vista")
print("=" * 72)
print()

mid   = N // 2
train = records[:mid]
test  = records[mid:]

# sulla prima metà: qual è il miglior filtro?
# testiamo una griglia di combinazioni e scegliamo il migliore
candidati = [
    ("accel>0",                   lambda r: r["trend_accel"] > 0),
    ("mom>0",                     lambda r: r["mom_last"] > 0),
    ("t5m>100",                   lambda r: r["trend_5m"] > 100),
    ("accel>0 AND mom>0",         lambda r: r["trend_accel"] > 0 and r["mom_last"] > 0),
    ("accel>0 AND t5m>100",       lambda r: r["trend_accel"] > 0 and r["trend_5m"] > 100),
    ("mom>0 AND t5m>100",         lambda r: r["mom_last"] > 0 and r["trend_5m"] > 100),
    ("accel>0 AND mom>0 AND t5m>100",
                                  lambda r: r["trend_accel"] > 0 and r["mom_last"] > 0 and r["trend_5m"] > 100),
    ("accel>0 AND mom>0 AND t5m>50",
                                  lambda r: r["trend_accel"] > 0 and r["mom_last"] > 0 and r["trend_5m"] > 50),
    ("accel>10 AND mom>0",        lambda r: r["trend_accel"] > 10 and r["mom_last"] > 0),
    ("accel>10 AND mom>0 AND t5m>100",
                                  lambda r: r["trend_accel"] > 10 and r["mom_last"] > 0 and r["trend_5m"] > 100),
    ("bull>=3",                   lambda r: r["bull_count"] >= 3),
    ("accel>0 AND bull>=3",       lambda r: r["trend_accel"] > 0 and r["bull_count"] >= 3),
]

print(f"  PRIMA METÀ ({len(train)} trade) — selezione filtro:")
netto_train_real = netto_reale(train)
best_train = {"netto": netto_train_real, "label": "baseline", "cond": None}
for label, cond in candidati:
    n_t = sum(r["pnl_fin"] for r in train if cond(r))
    if n_t > best_train["netto"]:
        best_train = {"netto": n_t, "label": label, "cond": cond}
    print(f"    {label:<40}  netto_train={n_t:>+8.1f}")

print()
print(f"  → Miglior filtro sulla prima metà: '{best_train['label']}'")
print(f"    Netto train: ${best_train['netto']:+.1f}  (baseline: ${netto_train_real:+.1f})")
print()

# applica quel filtro sulla seconda metà (mai vista)
netto_test_real = netto_reale(test)
if best_train["cond"]:
    netto_test_filt = sum(r["pnl_fin"] for r in test if best_train["cond"](r))
    n_ent_test      = sum(1 for r in test if best_train["cond"](r))
else:
    netto_test_filt = netto_test_real
    n_ent_test      = len(test)

print(f"  SECONDA METÀ ({len(test)} trade) — VERIFICA (mai vista):")
print(f"    Netto REALE seconda metà:    ${netto_test_real:+.1f}")
print(f"    Netto COL FILTRO (N={n_ent_test}): ${netto_test_filt:+.1f}")
print(f"    Delta rispetto al baseline:  ${netto_test_filt - netto_test_real:+.1f}")
print()

if netto_test_filt >= 0:
    print("  ✓ OUT-OF-SAMPLE: il filtro regge sulla seconda metà → SEGNALE REALE")
elif netto_test_filt >= -50:
    print("  ~ OUT-OF-SAMPLE: degradazione lieve, ma vicino a zero → accettabile")
else:
    print("  ✗ OUT-OF-SAMPLE: crolla sulla seconda metà → OVERFITTING CONFERMATO")
print()

# ── TEST 3: PERMUTATION TEST ──────────────────────────────────────────────────
print("=" * 72)
print("TEST 3 — PERMUTATION TEST (1000 shuffle)")
print("  Domanda: quante volte il caso produce un risultato buono quanto il tuo?")
print("=" * 72)
print()

N_PERM  = 1000
pnls    = [r["pnl_fin"] for r in records]
flags   = [filtro_campo(r) for r in records]   # quali trade passano il filtro
netto_osservato = sum(p for p, f in zip(pnls, flags) if f)

random.seed(42)
perm_results = []
for _ in range(N_PERM):
    random.shuffle(flags)
    perm_results.append(sum(p for p, f in zip(pnls, flags) if f))

# p-value: quante permutazioni >= netto osservato?
p_value  = sum(1 for x in perm_results if x >= netto_osservato) / N_PERM
perm_avg = sum(perm_results) / N_PERM
perm_std = (sum((x - perm_avg)**2 for x in perm_results) / N_PERM) ** 0.5
perm_p5  = sorted(perm_results)[int(0.95 * N_PERM)]   # 95° percentile

print(f"  Netto OSSERVATO (filtro reale): ${netto_osservato:+.1f}")
print(f"  Media permutazioni random:      ${perm_avg:+.1f}")
print(f"  Std permutazioni:               ${perm_std:.1f}")
print(f"  95° percentile permutazioni:    ${perm_p5:+.1f}")
print(f"  p-value (P(random >= osservato)): {p_value:.3f}  ({int(p_value*100)}%)")
print()

if p_value <= 0.01:
    print("  ✓✓ p-value ≤ 1%: MOLTO IMPROBABILE per caso. Segnale quasi certamente reale.")
elif p_value <= 0.05:
    print("  ✓ p-value ≤ 5%: statisticamente significativo. Segnale probabilmente reale.")
elif p_value <= 0.10:
    print("  ~ p-value ≤ 10%: borderline. Con 80 filtri provati, potrebbe essere fortuna.")
else:
    print(f"  ✗ p-value = {p_value:.0%}: NON significativo. Questo risultato si ottiene per caso.")
    print("  Probabilmente overfitting da selezione su 80 filtri.")
print()

# ── VERDETTO FINALE ───────────────────────────────────────────────────────────
print("=" * 72)
print("VERDETTO FINALE — IL CAMPO È REALE O OVERFITTING?")
print("=" * 72)
print()
print(f"  Test 1 (walk-forward 3 blocchi): {'✓ REGGE' if all_ok else '✗ CROLLA in qualche blocco'}")
print(f"  Test 2 (out-of-sample):          "
      f"{'✓ REGGE' if netto_test_filt >= -50 else '✗ CROLLA'} "
      f"(${netto_test_filt:+.1f} sulla metà mai vista)")
print(f"  Test 3 (permutation test):       "
      f"{'✓ SIGNIFICATIVO' if p_value <= 0.05 else '✗ NON SIGNIFICATIVO'} "
      f"(p={p_value:.3f})")
print()

n_ok = sum([all_ok, netto_test_filt >= -50, p_value <= 0.05])
if n_ok == 3:
    print("  ✓✓✓ TUTTI E 3 I TEST POSITIVI → il campo è un segnale REALE.")
    print("  Si può considerare l'implementazione in produzione con cautela.")
elif n_ok == 2:
    print("  ◐ 2/3 test positivi → segnale PARZIALMENTE reale.")
    print("  Raccomandato: altri 2-3 mesi di dati prima di deployare.")
elif n_ok == 1:
    print("  ✗ 1/3 test positivi → segnale DEBOLE, rischio overfitting ALTO.")
    print("  NON deployare. Servono dati fuori-campione reali.")
else:
    print("  ✗✗ NESSUN test positivo → quasi certamente OVERFITTING.")
    print("  Il pareggio su -1.6$ era fortuna da 80 combinazioni testate.")
print()

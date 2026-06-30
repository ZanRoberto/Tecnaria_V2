#!/usr/bin/env python3
"""
SIMULA24 — CAMPO + FIRMA: IL SISTEMA ATTRAVERSA LO ZERO?

Due domande:
1. FILTRI CAMPO COMBINATI: trend_accel>0 AND mom_last>0 AND trend_5m>100 ecc.
   Il netto dei soli trade con campo favorevole arriva a positivo?

2. CAMPO + FIRMA COMBINATI: per i trade che passano il campo, applico la firma
   del maschio (g@1s da curva_json). Se g@1s <= soglia → esco a 1s.
   Il sistema attraversa lo zero?

MODELLO ECONOMICO CORRETTO:
  - Campo fails  → pnl = 0          (non entro, risparmio la fee $2)
  - Campo ok, firma fails → pnl = g@1s  (esco a 1s, fee già pagata)
  - Campo ok, firma ok   → pnl = pnl_fin (tengo fino alla fine)

CACHE: salva /tmp/campo_cache.json per evitare 518 fetch ogni volta.

Lancio: python3 simula24.py
"""
import sqlite3, json, time, os, urllib.request, urllib.parse

DB         = "/var/data/trading_data.db"
CACHE_FILE = "/tmp/campo_cache.json"
SYM        = "BTCUSDC"
N_KLINES   = 6

# ── FETCH CAMPO (con cache) ───────────────────────────────────────────────────
def fetch_klines(ts_epoch, n=N_KLINES):
    ts_ms = int(ts_epoch) * 1000 if int(ts_epoch) < 1e12 else int(ts_epoch)
    params = urllib.parse.urlencode({"symbol": SYM, "interval": "1m",
                                     "endTime": ts_ms, "limit": n})
    try:
        with urllib.request.urlopen(
                f"https://api.binance.com/api/v3/klines?{params}", timeout=8) as r:
            return json.loads(r.read())
    except Exception:
        return None

def klines_to_metrics(klines):
    if not klines or len(klines) < 4:
        return None
    opens  = [float(k[1]) for k in klines]
    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]
    closes = [float(k[4]) for k in klines]
    vols   = [float(k[5]) for k in klines]
    ranges = [h - l for h, l in zip(highs, lows)]
    bodies = [abs(c - o) for c, o in zip(closes, opens)]
    mom_now  = closes[-1] - closes[-2]
    mom_prev = closes[-3] - closes[-4] if len(closes) >= 5 else 0
    return {
        "trend_5m":    closes[-1] - closes[0],
        "mom_last":    mom_now,
        "vol_range":   sum(ranges) / len(ranges),
        "vol_trend":   vols[-1] / vols[0] if vols[0] > 0 else 1.0,
        "body_ratio":  sum(b/r if r>0 else 0 for b,r in zip(bodies,ranges))/len(bodies),
        "bull_count":  sum(1 for c,o in zip(closes,opens) if c > o),
        "trend_accel": mom_now - mom_prev,
    }

# ── legge DB ──────────────────────────────────────────────────────────────────
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
    f"FROM curva_nascita WHERE pnl_finale IS NOT NULL"
).fetchall()
con.close()

# ── carica o costruisce cache ─────────────────────────────────────────────────
if os.path.exists(CACHE_FILE):
    print(f"Carico cache da {CACHE_FILE}...")
    with open(CACHE_FILE) as f:
        cached = json.load(f)   # list of {ts, metrics or null}
    cache_map = {str(item["ts"]): item["metrics"] for item in cached}
else:
    print(f"Nessuna cache trovata — fetch Binance per {len(rows)} trade...")
    cache_map = {}

# ── costruisce records ─────────────────────────────────────────────────────────
records = []
need_fetch = []
for ts, peak, pnlf, cj in rows:
    need_fetch.append((ts, peak, pnlf, cj))

to_save = []
fetched = 0
for i, (ts, peak, pnlf, cj) in enumerate(need_fetch):
    ts_str = str(ts)
    if ts_str in cache_map:
        metrics = cache_map[ts_str]
    else:
        if i % 50 == 0 and fetched == 0:
            print(f"  Fetch {i}/{len(need_fetch)}...")
        kl = fetch_klines(ts)
        metrics = klines_to_metrics(kl) if kl else None
        cache_map[ts_str] = metrics
        to_save.append({"ts": ts, "metrics": metrics})
        fetched += 1
        if fetched % 50 == 0:
            print(f"  Fetch {fetched}/{len(need_fetch) - len([x for x in need_fetch if str(x[0]) in cache_map])}...")
        time.sleep(0.05)

    if metrics is None:
        continue

    # estrai g@1s da curva_json (firma post-entrata)
    g1s = None
    try:
        pts   = json.loads(cj)
        micro = [(float(p[0]), float(p[1])) for p in pts
                 if len(p) >= 2 and float(p[0]) <= 2.0]
        if len(micro) >= 2:
            g1s = min(micro, key=lambda p: abs(p[0] - 1.0))[1]
    except Exception:
        pass

    rec = dict(metrics)
    rec["ts"]      = ts
    rec["peak"]    = float(peak or 0)
    rec["pnl_fin"] = float(pnlf)
    rec["g1s"]     = g1s
    records.append(rec)

# salva cache aggiornata
if to_save or not os.path.exists(CACHE_FILE):
    existing = []
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            existing = json.load(f)
    all_cache = existing + to_save
    with open(CACHE_FILE, "w") as f:
        json.dump(all_cache, f)
    print(f"Cache salvata: {len(all_cache)} voci")

N_tot    = len(records)
tot_real = sum(r["pnl_fin"] for r in records)
print(f"\nRecord analizzati: {N_tot}  |  Netto REALE: ${tot_real:+.1f}")
print()

# ── DOMANDA 1: FILTRI CAMPO COMBINATI ────────────────────────────────────────
print("=" * 76)
print("DOMANDA 1 — FILTRI CAMPO COMBINATI (non entro se campo sfavorevole)")
print("  Netto = sum(pnl_fin per trade entrati) + 0 per trade saltati")
print("=" * 76)
print()
print(f"  {'FILTRO CAMPO':<40}  {'N_ent':>6}  {'skip':>5}  {'netto_ent':>10}  {'vs_real':>8}")
print(f"  {'-'*76}")

filtri_campo = [
    # singoli
    ("trend_accel > 0",
        lambda r: r["trend_accel"] > 0),
    ("trend_accel > 10",
        lambda r: r["trend_accel"] > 10),
    ("mom_last > 0",
        lambda r: r["mom_last"] > 0),
    ("trend_5m > 0",
        lambda r: r["trend_5m"] > 0),
    ("trend_5m > 100",
        lambda r: r["trend_5m"] > 100),
    # doppi
    ("trend_accel>0 AND mom_last>0",
        lambda r: r["trend_accel"] > 0 and r["mom_last"] > 0),
    ("trend_accel>0 AND trend_5m>0",
        lambda r: r["trend_accel"] > 0 and r["trend_5m"] > 0),
    ("trend_accel>10 AND mom_last>0",
        lambda r: r["trend_accel"] > 10 and r["mom_last"] > 0),
    ("mom_last>0 AND trend_5m>0",
        lambda r: r["mom_last"] > 0 and r["trend_5m"] > 0),
    # tripli
    ("accel>0 AND mom>0 AND t5m>0",
        lambda r: r["trend_accel"] > 0 and r["mom_last"] > 0 and r["trend_5m"] > 0),
    ("accel>0 AND mom>0 AND t5m>100",
        lambda r: r["trend_accel"] > 0 and r["mom_last"] > 0 and r["trend_5m"] > 100),
    ("accel>10 AND mom>0 AND t5m>0",
        lambda r: r["trend_accel"] > 10 and r["mom_last"] > 0 and r["trend_5m"] > 0),
    ("accel>10 AND mom>0 AND t5m>50",
        lambda r: r["trend_accel"] > 10 and r["mom_last"] > 0 and r["trend_5m"] > 50),
    ("accel>10 AND mom>0 AND t5m>100",
        lambda r: r["trend_accel"] > 10 and r["mom_last"] > 0 and r["trend_5m"] > 100),
    # bull count
    ("bull_count >= 3",
        lambda r: r["bull_count"] >= 3),
    ("accel>0 AND bull>=3",
        lambda r: r["trend_accel"] > 0 and r["bull_count"] >= 3),
    ("accel>0 AND mom>0 AND bull>=3",
        lambda r: r["trend_accel"] > 0 and r["mom_last"] > 0 and r["bull_count"] >= 3),
    ("accel>10 AND mom>0 AND bull>=3",
        lambda r: r["trend_accel"] > 10 and r["mom_last"] > 0 and r["bull_count"] >= 3),
    ("accel>10 AND mom>0 AND bull>=3 AND t5m>0",
        lambda r: r["trend_accel"] > 10 and r["mom_last"] > 0
                  and r["bull_count"] >= 3 and r["trend_5m"] > 0),
]

best_campo = {"netto": tot_real, "label": "baseline", "cond": None}

for label, cond in filtri_campo:
    ent  = [r for r in records if cond(r)]
    skip = N_tot - len(ent)
    if not ent:
        continue
    netto_ent = sum(r["pnl_fin"] for r in ent)
    vs_real   = netto_ent - tot_real
    flag = "  ◄◄ POSITIVO!" if netto_ent > 0 else ("  ◄ MEGLIO" if vs_real > 0 else "")
    print(f"  {label:<40}  {len(ent):>6}  {skip:>5}  {netto_ent:>+10.1f}  {vs_real:>+8.1f}{flag}")
    if netto_ent > best_campo["netto"]:
        best_campo = {"netto": netto_ent, "label": label, "cond": cond, "ent": ent}

print()
print(f"  BASELINE (tutti): netto=${tot_real:+.1f}  N={N_tot}")
print()

# ── DOMANDA 2: CAMPO + FIRMA ──────────────────────────────────────────────────
print("=" * 76)
print("DOMANDA 2 — CAMPO + FIRMA DEL MASCHIO (g@1s)")
print("  Logica: campo fails → pnl=0 | campo ok + firma fails → pnl=g@1s | ok+ok → pnl_fin")
print("=" * 76)
print()

# filtri di firma (post-entrata, g@1s)
soglie_firma = [0.0, 0.1, 0.2, 0.3, 0.5]

print(f"  {'CAMPO':<36}  {'g@1s>':>6}  {'N_ok':>5}  {'netto_tot':>10}  {'vs_real':>8}")
print(f"  {'-'*76}")

migliore = {"netto": tot_real, "label": "—", "soglia": None, "campo_label": "—"}

for campo_label, campo_cond in filtri_campo:
    # trade che passano il campo
    passa_campo    = [r for r in records if campo_cond(r)]
    non_passa_campo = [r for r in records if not campo_cond(r)]

    for soglia_g1s in soglie_firma:
        pnl_totale = 0.0

        # trade che NON passano il campo → pnl = 0 (non entra)
        # pnl_totale += 0 per ognuno

        # trade che passano il campo
        n_firma_ok = 0
        for r in passa_campo:
            g1s = r.get("g1s")
            if g1s is None:
                # nessun dato g1s: usa pnl_fin (non possiamo applicare firma)
                pnl_totale += r["pnl_fin"]
                n_firma_ok += 1
            elif g1s > soglia_g1s:
                # campo OK + firma OK → tieni
                pnl_totale += r["pnl_fin"]
                n_firma_ok += 1
            else:
                # campo OK + firma FAIL → esci a 1s
                pnl_totale += g1s

        vs_real = pnl_totale - tot_real
        flag = "  ◄◄◄ POSITIVO!" if pnl_totale > 0 else ("  ◄ MEGLIO" if vs_real > 0 else "")
        if pnl_totale > migliore["netto"]:
            migliore = {
                "netto": pnl_totale,
                "label": f"{campo_label}  g@1s>{soglia_g1s}",
                "soglia": soglia_g1s,
                "campo_label": campo_label,
                "n_firma_ok": n_firma_ok,
                "vs_real": vs_real,
            }
        if vs_real > 0 or pnl_totale > 0:
            print(f"  {campo_label:<36}  {soglia_g1s:>6.1f}  {n_firma_ok:>5}  "
                  f"{pnl_totale:>+10.1f}  {vs_real:>+8.1f}{flag}")

print()

# ── VERDETTO FINALE ───────────────────────────────────────────────────────────
print("=" * 76)
print("VERDETTO")
print("=" * 76)
print()
print(f"  Netto REALE (baseline): ${tot_real:+.1f}")
print()

if best_campo["netto"] > 0:
    print(f"  ✓ CAMPO DA SOLO porta a POSITIVO!")
    print(f"    Filtro: '{best_campo['label']}'")
    print(f"    Netto: ${best_campo['netto']:+.1f}")
elif best_campo["netto"] > tot_real:
    print(f"  ◐ CAMPO da solo: miglioramento ma ancora negativo.")
    print(f"    Miglior filtro: '{best_campo['label']}'  → ${best_campo['netto']:+.1f}")
print()

if migliore["netto"] > 0:
    print(f"  ✓✓ CAMPO + FIRMA porta a POSITIVO!")
    print(f"    Combo: {migliore['label']}")
    print(f"    Netto: ${migliore['netto']:+.1f}  (vs baseline {migliore['vs_real']:+.1f})")
elif migliore["netto"] > tot_real:
    print(f"  ◐ CAMPO + FIRMA: miglioramento ma ancora negativo.")
    print(f"    Miglior combo: {migliore['label']}  → ${migliore['netto']:+.1f}")
else:
    print(f"  ✗ Nessuna combinazione campo+firma supera il baseline.")
print()

# ── decomposizione del guadagno ───────────────────────────────────────────────
if migliore.get("campo_label"):
    print("─" * 76)
    print("DECOMPOSIZIONE — dove viene il miglioramento?")
    print("─" * 76)
    cl = migliore["campo_label"]
    sg = migliore["soglia"]
    campo_cond_best = next(c for l,c in filtri_campo if l == cl)
    passa   = [r for r in records if campo_cond_best(r)]
    no_pass = [r for r in records if not campo_cond_best(r)]
    print(f"  Trade che passano il campo  : {len(passa)}")
    print(f"  Trade saltati (pnl=0 vs {sum(r['pnl_fin'] for r in no_pass):+.1f}$): {len(no_pass)}")
    if sg is not None:
        firma_ok   = [r for r in passa if r.get("g1s") is not None and r["g1s"] > sg]
        firma_fail = [r for r in passa if r.get("g1s") is not None and r["g1s"] <= sg]
        firma_na   = [r for r in passa if r.get("g1s") is None]
        g_exit     = sum(r["g1s"] for r in firma_fail)
        print(f"  Campo OK + firma OK (pnl_fin): {len(firma_ok)}")
        print(f"  Campo OK + firma FAIL (exit@1s, tot g@1s={g_exit:+.1f}$): {len(firma_fail)}")
        print(f"  Campo OK, g1s N/A: {firma_na}")
print()

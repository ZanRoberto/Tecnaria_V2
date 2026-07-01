#!/usr/bin/env python3
"""
SIMULA26 — DAL PAREGGIO AL POSITIVO (con 3 test obbligatori su ogni filtro)

Due domande:
1. STRINGERE IL CAMPO: soglie più alte portano a netto positivo reggendo i 3 test?
   ATTENZIONE MULTIPLI CONFRONTI: se testo 20 filtri a p<0.05, mi aspetto 1 falso
   positivo per caso. Il p-value corretto (Bonferroni) è p < 0.05/N_filtri.

2. ANALISI USCITA: nei trade che passano il campo, i maschi fanno più grasso?
   C'è margine per catturare più profitto sui filtrati?

Lancio: python3 simula26.py
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
print(f"Trade totali: {len(rows)}")

# ── fetch / cache campo ───────────────────────────────────────────────────────
def fetch_klines(ts_epoch, n=6):
    ts_ms = int(ts_epoch)*1000 if int(ts_epoch)<1e12 else int(ts_epoch)
    params = urllib.parse.urlencode({"symbol":SYM,"interval":"1m","endTime":ts_ms,"limit":n})
    try:
        with urllib.request.urlopen(
                f"https://api.binance.com/api/v3/klines?{params}", timeout=8) as r:
            return json.loads(r.read())
    except Exception:
        return None

def klines_to_metrics(klines):
    if not klines or len(klines)<4: return None
    closes = [float(k[4]) for k in klines]
    opens  = [float(k[1]) for k in klines]
    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]
    vols   = [float(k[5]) for k in klines]
    ranges = [h-l for h,l in zip(highs,lows)]
    bodies = [abs(c-o) for c,o in zip(closes,opens)]
    mom_now  = closes[-1]-closes[-2]
    mom_prev = closes[-3]-closes[-4] if len(closes)>=5 else 0
    return {
        "trend_5m":    closes[-1]-closes[0],
        "mom_last":    mom_now,
        "vol_range":   sum(ranges)/len(ranges),
        "vol_trend":   vols[-1]/vols[0] if vols[0]>0 else 1.0,
        "body_ratio":  sum(b/r if r>0 else 0 for b,r in zip(bodies,ranges))/len(bodies),
        "bull_count":  sum(1 for c,o in zip(closes,opens) if c>o),
        "trend_accel": mom_now-mom_prev,
    }

cache_map = {}
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE) as f:
        for item in json.load(f):
            cache_map[str(item["ts"])] = item["metrics"]
    print(f"Cache: {len(cache_map)} voci")

records = []
to_save = []
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
    if m is None: continue
    g1s = None
    try:
        pts   = json.loads(cj)
        micro = [(float(p[0]),float(p[1])) for p in pts
                 if len(p)>=2 and float(p[0])<=2.0]
        if len(micro)>=2:
            g1s = min(micro, key=lambda p: abs(p[0]-1.0))[1]
    except Exception:
        pass
    # peak massimo nella curva
    peak_curva = None
    try:
        pts = json.loads(cj)
        vals = [float(p[1]) for p in pts if len(p)>=2]
        if vals: peak_curva = max(vals)
    except Exception:
        pass

    rec = dict(m)
    rec.update({"ts":ts, "peak":float(peak or 0), "pnl_fin":float(pnlf),
                "g1s":g1s, "peak_curva":peak_curva})
    records.append(rec)

if to_save:
    existing = []
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f: existing = json.load(f)
    with open(CACHE_FILE,"w") as f: json.dump(existing+to_save, f)

N        = len(records)
tot_real = sum(r["pnl_fin"] for r in records)
print(f"Record con campo: {N}  |  Netto reale: ${tot_real:+.1f}\n")

# ── funzioni di test ───────────────────────────────────────────────────────────
random.seed(42)
N_PERM = 2000

def netto_filtrato(subset, cond):
    return sum(r["pnl_fin"] for r in subset if cond(r))

def walk_forward_3(recs, cond):
    """Ritorna (netti dei 3 blocchi, True se tutti >= -20$)"""
    bs = len(recs)//3
    blks = [recs[:bs], recs[bs:2*bs], recs[2*bs:]]
    nettos = [netto_filtrato(b, cond) for b in blks]
    ok = all(n >= -20 for n in nettos)
    return nettos, ok

def out_of_sample(recs, cond):
    """Prima metà: selezione. Seconda: verifica."""
    mid  = len(recs)//2
    nf   = netto_filtrato(recs[mid:], cond)
    nr   = sum(r["pnl_fin"] for r in recs[mid:])
    return nf, nr

def permutation_p(recs, cond, n_perm=N_PERM):
    pnls  = [r["pnl_fin"] for r in recs]
    flags = [cond(r) for r in recs]
    obs   = sum(p for p,f in zip(pnls,flags) if f)
    count = 0
    for _ in range(n_perm):
        random.shuffle(flags)
        if sum(p for p,f in zip(pnls,flags) if f) >= obs:
            count += 1
    return obs, count/n_perm

def tre_test(recs, cond, label, n_filt):
    """Esegue i 3 test e ritorna dict con risultati."""
    nettos_wf, wf_ok = walk_forward_3(recs, cond)
    oos_filt, oos_real = out_of_sample(recs, cond)
    obs, p_val = permutation_p(recs, cond)
    return {
        "label": label,
        "n_filt": n_filt,
        "netto_tot": obs,
        "wf_nettos": nettos_wf,
        "wf_ok": wf_ok,
        "oos_filt": oos_filt,
        "oos_real": oos_real,
        "oos_ok": oos_filt >= -30,
        "p_val": p_val,
        "all_ok": wf_ok and oos_filt >= -30 and p_val <= 0.05,
    }

# ── PARTE 1: GRIGLIA DI FILTRI STRETTI ────────────────────────────────────────
print("=" * 78)
print("PARTE 1 — GRIGLIA FILTRI STRETTI (ogni filtro con 3 test obbligatori)")
print("=" * 78)
print()

# definisco i filtri da testare
filtri = []

# baseline confermato
filtri.append(("accel>0 AND mom>0 AND t5m>100",
    lambda r: r["trend_accel"]>0 and r["mom_last"]>0 and r["trend_5m"]>100))

# variano t5m
for t5m in [150, 200, 300]:
    label = f"accel>0 AND mom>0 AND t5m>{t5m}"
    t = t5m  # closure
    filtri.append((label, lambda r, t=t: r["trend_accel"]>0 and r["mom_last"]>0 and r["trend_5m"]>t))

# variano accel
for ac in [10, 20, 30]:
    label = f"accel>{ac} AND mom>0 AND t5m>100"
    a = ac
    filtri.append((label, lambda r, a=a: r["trend_accel"]>a and r["mom_last"]>0 and r["trend_5m"]>100))

# variano entrambi accel e t5m
for ac in [10, 20]:
    for t5m in [150, 200]:
        label = f"accel>{ac} AND mom>0 AND t5m>{t5m}"
        a, t = ac, t5m
        filtri.append((label, lambda r, a=a, t=t: r["trend_accel"]>a and r["mom_last"]>0 and r["trend_5m"]>t))

# aggiunge bull_count
for ac in [0, 10]:
    for bc in [3, 4]:
        label = f"accel>{ac} AND mom>0 AND t5m>100 AND bull>={bc}"
        a, b = ac, bc
        filtri.append((label, lambda r, a=a, b=b:
            r["trend_accel"]>a and r["mom_last"]>0 and r["trend_5m"]>100 and r["bull_count"]>=b))

N_FILTRI = len(filtri)
BONFERRONI = 0.05 / N_FILTRI
print(f"  Filtri testati: {N_FILTRI}")
print(f"  p-value Bonferroni corretto: {BONFERRONI:.4f}  (= 0.05 / {N_FILTRI})")
print(f"  (Con {N_FILTRI} filtri, ci aspettiamo ~{0.05*N_FILTRI:.1f} falso positivo a p<0.05)")
print()
print(f"  {'FILTRO':<42}  {'N':>4}  {'netto':>7}  {'WF(1,2,3)':>16}  {'OOS':>7}  {'p-val':>7}  TEST")
print(f"  {'-'*100}")

positivi = []
for label, cond in filtri:
    n_filt = sum(1 for r in records if cond(r))
    if n_filt < 15:
        print(f"  {label:<42}  {n_filt:>4}  {'(troppo pochi)':>35}")
        continue
    res = tre_test(records, cond, label, n_filt)
    wf_str = f"({res['wf_nettos'][0]:+.0f},{res['wf_nettos'][1]:+.0f},{res['wf_nettos'][2]:+.0f})"
    p_flag = "✓" if res["p_val"] <= 0.05 else ("~" if res["p_val"] <= 0.10 else "✗")
    wf_flag = "✓" if res["wf_ok"] else "✗"
    oos_flag = "✓" if res["oos_ok"] else "✗"
    all_flag = "✓✓ PASSA" if res["all_ok"] else ("◐ 2/3" if sum([res["wf_ok"], res["oos_ok"], res["p_val"]<=0.05])==2 else "✗")
    bon_flag = "✓BON" if res["p_val"] <= BONFERRONI else ""
    print(f"  {label:<42}  {n_filt:>4}  {res['netto_tot']:>+7.1f}  {wf_str:>16}  "
          f"{res['oos_filt']:>+7.1f}  {res['p_val']:>7.3f}{p_flag}  {all_flag} {bon_flag}")
    if res["all_ok"]:
        positivi.append(res)

print()
if positivi:
    positivi.sort(key=lambda x: x["netto_tot"], reverse=True)
    print(f"  ✓ {len(positivi)} filtro/i passa TUTTI e 3 i test:")
    for r in positivi:
        bon = " ← Bonferroni OK" if r["p_val"] <= BONFERRONI else " ← solo p<0.05 (non Bonferroni)"
        print(f"    '{r['label']}'  netto=${r['netto_tot']:+.1f}  p={r['p_val']:.3f}{bon}")
else:
    print("  ✗ Nessun filtro stretto passa tutti e 3 i test con netto positivo.")
    print("  Il pareggio a -1.6$ rimane il massimo ottenibile da solo il campo.")
print()

# ── PARTE 2: ANALISI USCITA SUI TRADE FILTRATI ────────────────────────────────
print("=" * 78)
print("PARTE 2 — ANALISI USCITA: nei trade con campo fertile, come finiscono?")
print("=" * 78)
print()

# usa il filtro baseline confermato
BASE_COND = filtri[0][1]
filtrati   = [r for r in records if BASE_COND(r)]
non_filt   = [r for r in records if not BASE_COND(r)]
N_f, N_nf  = len(filtrati), len(non_filt)

def avg(lst, key):
    v = [r[key] for r in lst if r.get(key) is not None]
    return sum(v)/len(v) if v else 0.0

def pct(lst, fn):
    v = [fn(r) for r in lst]
    return 100*sum(v)/len(v) if v else 0.0

print(f"  Trade con campo fertile (accel>0 AND mom>0 AND t5m>100): {N_f}")
print(f"  Trade senza campo fertile:                                 {N_nf}")
print()
print(f"  {'METRICA':<32}  {'CAMPO OK':>10}  {'CAMPO NO':>10}  {'DIFF':>8}")
print(f"  {'-'*64}")

metriche_uscita = [
    ("pnl_fin medio ($)",     "pnl_fin"),
    ("peak_nascita medio ($)", "peak"),
    ("peak_curva medio ($)",  "peak_curva"),
    ("g@1s medio ($)",        "g1s"),
]
for nome, key in metriche_uscita:
    vf = avg(filtrati, key)
    vn = avg(non_filt, key)
    flag = "  ◄◄" if abs(vf-vn) > 0.3 else ("  ◄" if abs(vf-vn) > 0.1 else "")
    print(f"  {nome:<32}  {vf:>+10.3f}  {vn:>+10.3f}  {vf-vn:>+8.3f}{flag}")

# win rate
wr_f = pct(filtrati, lambda r: r["pnl_fin"]>0)
wr_n = pct(non_filt, lambda r: r["pnl_fin"]>0)
print(f"  {'win rate (pnl>0) (%)':32}  {wr_f:>+10.1f}  {wr_n:>+10.1f}  {wr_f-wr_n:>+8.1f}")

# % maschi
m_f = pct(filtrati, lambda r: r["peak"]>=0.5 and r["pnl_fin"]>0)
m_n = pct(non_filt, lambda r: r["peak"]>=0.5 and r["pnl_fin"]>0)
print(f"  {'% maschi veri (peak>=0.5+pnl>0)':32}  {m_f:>+10.1f}  {m_n:>+10.1f}  {m_f-m_n:>+8.1f}")

print()

# distribuzione pnl_fin nei filtrati
print("─" * 60)
print("  DISTRIBUZIONE pnl_fin — CAMPO OK vs CAMPO NO:")
print(f"  {'fascia':>12}  {'CAMPO OK%':>10}  {'CAMPO NO%':>10}")
fasce = [(-99,-2,"< -2$"),(-2,-1,"-2→-1$"),(-1,0,"-1→0$"),
         (0,1,"0→+1$"),(1,2,"+1→+2$"),(2,99,"> +2$")]
for lo,hi,lab in fasce:
    pf_ = 100*sum(1 for r in filtrati if lo<=r["pnl_fin"]<hi)/N_f if N_f else 0
    pn_ = 100*sum(1 for r in non_filt if lo<=r["pnl_fin"]<hi)/N_nf if N_nf else 0
    flag = "  ◄" if abs(pf_-pn_)>=10 else ""
    print(f"  {lab:>12}  {pf_:>9.0f}%  {pn_:>9.0f}%{flag}")
print()

# peak_nascita nei filtrati vs non
print("─" * 60)
print("  DISTRIBUZIONE peak_nascita — CAMPO OK vs CAMPO NO:")
print(f"  {'fascia':>12}  {'CAMPO OK%':>10}  {'CAMPO NO%':>10}")
fasce_pk = [(-99,0,"<0"),( 0,0.5,"0→0.5$"),(0.5,1,"0.5→1$"),(1,2,"1→2$"),(2,99,">2$")]
for lo,hi,lab in fasce_pk:
    pf_ = 100*sum(1 for r in filtrati if lo<=r["peak"]<hi)/N_f if N_f else 0
    pn_ = 100*sum(1 for r in non_filt if lo<=r["peak"]<hi)/N_nf if N_nf else 0
    flag = "  ◄◄" if abs(pf_-pn_)>=15 else ("  ◄" if abs(pf_-pn_)>=8 else "")
    print(f"  {lab:>12}  {pf_:>9.0f}%  {pn_:>9.0f}%{flag}")
print()

# ── SIMULAZIONE EXIT POTENZIATA SUI FILTRATI ─────────────────────────────────
print("─" * 60)
print("  EXIT POTENZIATA sui soli filtrati: esco a 1s se g@1s <= soglia")
print(f"  {'soglia_g1s':>12}  {'N_exit_early':>14}  {'netto_tot':>10}  {'vs_solo_campo':>14}")
print(f"  {'-'*56}")

netto_solo_campo = sum(r["pnl_fin"] for r in filtrati)
for soglia in [-0.5, -0.3, -0.1, 0.0, 0.1, 0.2, 0.3]:
    n_exit = sum(1 for r in filtrati if r.get("g1s") is not None and r["g1s"]<=soglia)
    nt = sum(
        r["g1s"] if (r.get("g1s") is not None and r["g1s"]<=soglia) else r["pnl_fin"]
        for r in filtrati
    )
    delta = nt - netto_solo_campo
    flag  = "  ◄ MEGLIO" if delta > 10 else ""
    print(f"  {soglia:>12.1f}  {n_exit:>14}  {nt:>+10.1f}  {delta:>+14.1f}{flag}")
print()

print("=" * 78)
print("SINTESI")
print("=" * 78)
print()
print(f"  Il campo fertile (accel>0 AND mom>0 AND t5m>100):")
print(f"    Seleziona {N_f} trade su {N}  ({100*N_f/N:.0f}%)")
print(f"    Netto su quei trade: ${netto_solo_campo:+.1f}")
print(f"    % maschi veri in quel gruppo: {m_f:.0f}%  (vs {m_n:.0f}% nel resto)")
print(f"    pnl medio: ${avg(filtrati,'pnl_fin'):+.3f}  vs ${avg(non_filt,'pnl_fin'):+.3f} nel resto")
print()
if positivi:
    print(f"  ✓ Esiste un filtro più stretto con netto POSITIVO che regge i 3 test:")
    print(f"    → '{positivi[0]['label']}'  netto=${positivi[0]['netto_tot']:+.1f}")
else:
    print(f"  Il pareggio (-1.6$) è il massimo del solo campo.")
    print(f"  Per andare in positivo serve combinare campo + firma (vedi simula24).")
print()

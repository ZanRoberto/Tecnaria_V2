#!/usr/bin/env python3
"""
SIMULA33 — ALZARE LA SOGLIA MASCHIO: DA 0.5$ A 1$ O 2$?

I maschi piccoli (peak 0.5-1$) hanno cattura -27.8% e 31% negativi.
Cosa succede se alziamo la soglia minima del maschio?

TESTA:
  - soglia 0.5$ (baseline attuale)
  - soglia 1.0$
  - soglia 1.5$
  - soglia 2.0$
  - soglia 2.5$ (solo grandi)

Per ogni soglia mostra:
  - N maschi inclusi
  - netto totale
  - cattura% media
  - % negativi
  - confronto con baseline

IMPORTANTE: alzare la soglia non significa "non entrare" —
significa che il bot ENTRA ma con un'uscita più aggressiva.
Oppure significa NON ENTRARE se il candidato non supera la soglia
in osservazione pre-entrata.

Lancio: python3 simula33.py
"""
import sqlite3, json

DB = "/var/data/trading_data.db"

con  = sqlite3.connect(DB)
rows = con.execute(
    "SELECT peak_nascita, pnl_finale, curva_json "
    "FROM curva_nascita WHERE pnl_finale IS NOT NULL"
).fetchall()
con.close()
print(f"Trade totali nel DB: {len(rows)}")

# costruisce record completi
tutti = []
for peak, pnlf, cj in rows:
    pk = float(peak or 0)
    pf = float(pnlf)
    t_picco = None; grasso_picco_curva = None
    # grasso al picco e tempo al picco dalla curva
    try:
        pts = json.loads(cj)
        pts = [(float(p[0]), float(p[1])) for p in pts if len(p) >= 2]
        if pts:
            idx_pk = max(range(len(pts)), key=lambda i: pts[i][1])
            t_picco = pts[idx_pk][0]
            grasso_picco_curva = pts[idx_pk][1]
    except Exception:
        pass
    tutti.append({
        "peak":   pk,
        "pnl_fin": pf,
        "t_picco": t_picco,
        "g_picco": grasso_picco_curva,
    })

def avg(lst, key):
    v = [r[key] for r in lst if r.get(key) is not None]
    return sum(v)/len(v) if v else 0.0

N_tot    = len(tutti)
tot_real = sum(r["pnl_fin"] for r in tutti)
print(f"Netto REALE (tutti i trade): ${tot_real:+.2f}")
print()

# ── PARTE 1: IMPATTO SOGLIA SUL NETTO ────────────────────────────────────────
print("="*72)
print("PARTE 1 — SE ENTRAMO SOLO CON peak_osservato >= SOGLIA")
print("  (Il bot osserva il candidato PRIMA di entrare: entra solo se")
print("   il grasso osservato supera la soglia. Chi non supera → pnl=0)")
print("="*72)
print()
print(f"  {'SOGLIA':>8}  {'N_entr':>7}  {'N_skip':>7}  {'netto_ent':>11}  "
      f"{'netto_tot':>11}  {'cattura%':>9}  {'neg%':>6}  DELTA")
print(f"  {'-'*80}")

soglie = [0.0, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0, 2.5, 3.0]
baseline_tot = tot_real

for s in soglie:
    entr  = [r for r in tutti if r["peak"] >= s]
    skip  = [r for r in tutti if r["peak"] <  s]
    if not entr: continue
    n_e   = len(entr); n_s = len(skip)
    nt_e  = sum(r["pnl_fin"] for r in entr)
    # netto totale: entr prendono pnl_fin, skip prendono 0
    nt_tot = nt_e   # (skip = 0, già non contano)
    cat    = avg(entr, "peak")
    cat_pf = avg(entr, "pnl_fin")
    cat_pct = avg([{"c": r["pnl_fin"]/r["peak"]*100}
                   for r in entr if r["peak"]>0], "c")
    neg    = 100*sum(1 for r in entr if r["pnl_fin"]<0)/n_e
    delta  = nt_tot - baseline_tot
    flag   = "  ◄◄ MEGLIO" if delta > 0 else ""
    print(f"  {s:>8.1f}  {n_e:>7}  {n_s:>7}  {nt_e:>+11.2f}  "
          f"{nt_tot:>+11.2f}  {cat_pct:>8.1f}%  {neg:>5.0f}%  {delta:>+8.2f}{flag}")

print()
print(f"  BASELINE (tutti, soglia=0): ${baseline_tot:+.2f}")
print()

# ── PARTE 2: CONFRONTO CATEGORIE ──────────────────────────────────────────────
print("="*72)
print("PARTE 2 — CONTRIBUTO AL NETTO PER CATEGORIA DI PEAK")
print("="*72)
print()
print(f"  {'CATEGORIA':<22}  {'N':>4}  {'netto_tot':>11}  {'avg/trade':>10}  "
      f"{'cattura%':>9}  {'neg%':>6}  {'t_picco':>8}")
print(f"  {'-'*76}")

categorie = [
    ("non-maschio (<0.5)",  lambda r: r["peak"] < 0.5),
    ("piccolo  0.5-1.0$",  lambda r: 0.5 <= r["peak"] < 1.0),
    ("medio    1.0-2.0$",  lambda r: 1.0 <= r["peak"] < 2.0),
    ("grande   2.0-5.0$",  lambda r: 2.0 <= r["peak"] < 5.0),
    ("enorme   >5.0$",     lambda r: r["peak"] >= 5.0),
]
for lab, cond in categorie:
    grp = [r for r in tutti if cond(r)]
    if not grp: continue
    nt    = sum(r["pnl_fin"] for r in grp)
    av    = nt/len(grp)
    cat_pct = avg([{"c": r["pnl_fin"]/r["peak"]*100}
                   for r in grp if r["peak"]>0], "c")
    neg   = 100*sum(1 for r in grp if r["pnl_fin"]<0)/len(grp)
    tp    = avg([r for r in grp if r["t_picco"]], "t_picco")
    print(f"  {lab:<22}  {len(grp):>4}  {nt:>+11.2f}  {av:>+10.3f}  "
          f"{cat_pct:>8.1f}%  {neg:>5.0f}%  {tp:>7.1f}s")

print()
print(f"  TOTALE: ${tot_real:+.2f}")
print()

# ── PARTE 3: SE ALZIAMO PRESA_TRANS (soglia minima grasso in osservazione) ───
print("="*72)
print("PARTE 3 — IMPATTO REALE NEL BOT: CANCELLO_GRASSO_MIN")
print("  Il bot già ha CANCELLO_APERTURA_MIN (default 0.0).")
print("  Alzandolo a 0.5/1.0/1.5$, il bot NON APRE chi non ha raggiunto")
print("  quella soglia in osservazione. Chi non apre → pnl=0 (risparmia fee).")
print("="*72)
print()

# simulazione: se il bot non entra sotto soglia, risparmia la fee ($2)
# pnl = 0 per chi non entra (risparmia la fee che avrebbe pagato)
# confronto con: pnl_fin per chi entra (positivo o negativo)
FEE = 2.0
print(f"  {'SOGLIA_MIN':>10}  {'N_entr':>7}  {'N_skip':>7}  "
      f"{'netto_ent':>11}  {'fee_risparm':>12}  {'netto_tot':>11}  DELTA")
print(f"  {'-'*76}")

for s in [0.0, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0, 2.5]:
    entr = [r for r in tutti if r["peak"] >= s]
    skip = [r for r in tutti if r["peak"] <  s]
    nt_e = sum(r["pnl_fin"] for r in entr)
    # chi non entra: risparmia FEE ma anche perde il guadagno potenziale
    # fee risparmiata = N_skip * FEE (non pagata)
    # ma perde anche il pnl positivo dei skip che avrebbero guadagnato
    fee_risp = len(skip) * FEE
    # netto totale corretto: nt_e + 0 per skip
    # confronto con baseline che era nt_e + pnl_skip
    pnl_skip = sum(r["pnl_fin"] for r in skip)
    netto_tot = nt_e  # skip = 0
    delta = netto_tot - tot_real
    flag  = "  ◄◄" if delta > 0 else ""
    print(f"  {s:>10.1f}  {len(entr):>7}  {len(skip):>7}  "
          f"{nt_e:>+11.2f}  {fee_risp:>+12.2f}  {netto_tot:>+11.2f}  {delta:>+8.2f}{flag}")

print()

# ── PARTE 4: SOGLIA OTTIMALE CON 3 TEST ──────────────────────────────────────
print("="*72)
print("PARTE 4 — LA SOGLIA OTTIMALE REGGE I 3 TEST?")
print("  Walk-forward + OOS + permutation su ogni soglia promettente")
print("="*72)
print()

import random
random.seed(42)
N_PERM = 1000

def tre_test(recs, cond, n_perm=N_PERM):
    n_f  = sum(1 for r in recs if cond(r))
    obs  = sum(r["pnl_fin"] for r in recs if cond(r))
    bs   = len(recs)//3
    blks = [recs[:bs], recs[bs:2*bs], recs[2*bs:]]
    wf   = [sum(r["pnl_fin"] for r in b if cond(r)) for b in blks]
    wf_ok = all(x >= -20 for x in wf)
    mid  = len(recs)//2
    oos  = sum(r["pnl_fin"] for r in recs[mid:] if cond(r))
    # permutation
    pnls  = [r["pnl_fin"] for r in recs]; flags = [cond(r) for r in recs]
    count = 0
    for _ in range(n_perm):
        random.shuffle(flags)
        if sum(p for p,f in zip(pnls,flags) if f) >= obs: count += 1
    return {"n":n_f,"netto":obs,"wf":wf,"wf_ok":wf_ok,"oos":oos,"p":count/n_perm}

soglie_test = [0.5, 1.0, 1.5, 2.0, 2.5]
BONF = 0.05 / len(soglie_test)
print(f"  Bonferroni: p < {BONF:.3f}")
print()
print(f"  {'SOGLIA':>7}  {'N':>5}  {'netto':>9}  {'WF':>18}  {'OOS':>8}  {'p':>7}  ESITO")
print(f"  {'-'*70}")
for s in soglie_test:
    s_ = s
    cond = lambda r, s=s_: r["peak"] >= s
    res = tre_test(tutti, cond)
    wf_str = f"({res['wf'][0]:+.0f},{res['wf'][1]:+.0f},{res['wf'][2]:+.0f})"
    ok = res["netto"]>0 and res["wf_ok"] and res["oos"]>=-30 and res["p"]<=0.05
    bon= "✓BON" if res["p"]<=BONF else ""
    esito = "✓✓ PASSA" if ok else "✗"
    print(f"  {s:>7.1f}  {res['n']:>5}  {res['netto']:>+9.2f}  {wf_str:>18}  "
          f"{res['oos']:>+8.2f}  {res['p']:>7.3f}  {esito} {bon}")

print()

# ── VERDETTO ──────────────────────────────────────────────────────────────────
print("="*72)
print("VERDETTO")
print("="*72)
print()
print(f"  Netto attuale (tutti i trade, soglia 0): ${tot_real:+.2f}")
print()

# trova il delta massimo
best_s = None; best_delta = 0
for s in [0.5, 1.0, 1.5, 2.0, 2.5]:
    entr = [r for r in tutti if r["peak"] >= s]
    nt   = sum(r["pnl_fin"] for r in entr)
    if nt - tot_real > best_delta:
        best_delta = nt - tot_real; best_s = s

if best_s:
    entr_best = [r for r in tutti if r["peak"] >= best_s]
    print(f"  Soglia ottimale: {best_s}$")
    print(f"  Trade inclusi: {len(entr_best)} / {N_tot}")
    print(f"  Netto con soglia: ${sum(r['pnl_fin'] for r in entr_best):+.2f}")
    print(f"  Miglioramento: ${best_delta:+.2f}")
    print()
    print(f"  IMPLEMENTAZIONE: alzare CANCELLO_APERTURA_MIN={best_s} in Render → Env Vars.")
    print(f"  Nessun deploy necessario — solo variabile d'ambiente.")
else:
    print(f"  Nessuna soglia migliora il netto totale.")
    print(f"  I maschi piccoli perdono ma i non-maschi perdono di più:")
    print(f"  escluderli migliora i maschi ma non compensa il resto.")
print()

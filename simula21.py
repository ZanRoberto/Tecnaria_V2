#!/usr/bin/env python3
"""
SIMULA21 — FIRMA DEL MASCHIO NEI PRIMISSIMI TICK

129 maschi veri (peak>=0.5 grasso E pnl_finale>0) vs 389 non-maschi.
Analisi dei primi 3 tick (t~0, t~0.5s, t~1s): c'è una firma PRE-scatto?

METRICHE NEI PRIMI 2-3 TICK:
  1. Grasso al tick 0, 0.5s, 1s, 1.5s (snapshot assoluto)
  2. Minimo nei primi 1s (il maschio torna mai sotto 0 prima di partire?)
  3. Monotonia: quante inversioni nei primi 1s (dritto=maschio, strappi=non-maschio?)
  4. Segno del primo tick: il maschio parte subito positivo?
  5. "Tempo al verde": quanti secondi prima di toccare grasso>0 per la prima volta

CRITICA ONESTA:
  Questi tick sono POST-ENTRATA (fee pagata). La firma pre-entrata
  richiederebbe dati durante RITARDO_INGRESSO — non disponibili in curva_nascita.
  Se la firma appare al tick 0 (t<0.2s): forse recuperabile con uscita ultra-rapida.
  Se appare solo al tick 2-3 (t>1s): troppo tardi, il danno è fatto.

Lancio: python3 simula21.py
"""
import sqlite3, json
from collections import Counter

DB = "/var/data/trading_data.db"

con  = sqlite3.connect(DB)
rows = con.execute(
    "SELECT peak_nascita, pnl_finale, curva_json FROM curva_nascita WHERE pnl_finale IS NOT NULL"
).fetchall()
con.close()

def estrai_micro(cj, t_max=3.0):
    pts = json.loads(cj)
    micro = [(float(p[0]), float(p[1])) for p in pts if len(p)>=2 and float(p[0]) <= t_max]
    return micro

def snap(micro, t_tgt):
    if not micro: return None
    return min(micro, key=lambda p: abs(p[0]-t_tgt))[1]

records = []
for peak, pnlf, cj in rows:
    try:
        micro = estrai_micro(cj, t_max=3.0)
        if len(micro) < 2: continue
        times  = [p[0] for p in micro]
        grassi = [p[1] for p in micro]

        # inversioni di segno nel delta
        deltas = [grassi[i]-grassi[i-1] for i in range(1,len(grassi))]
        inv = sum(1 for i in range(1,len(deltas)) if deltas[i]*deltas[i-1]<0)

        # tempo al primo valore positivo
        t_pos = next((t for t,g in micro if g > 0), None)

        # minimo nei primi 1.5s
        early = [g for t,g in micro if t <= 1.5]
        min_early = min(early) if early else None

        records.append({
            "peak":     float(peak or 0),
            "pnl_fin":  float(pnlf),
            "g_t0":     grassi[0],                  # primo tick registrato
            "g_05":     snap(micro, 0.5),
            "g_1s":     snap(micro, 1.0),
            "g_15":     snap(micro, 1.5),
            "min_early":min_early,
            "inv_3s":   inv,
            "t_pos":    t_pos,
            "tick0_pos":1 if grassi[0] > 0 else 0,
        })
    except Exception:
        pass

SOGLIA = 0.5
maschi   = [r for r in records if r["peak"] >= SOGLIA and r["pnl_fin"] > 0]
nonmasc  = [r for r in records if not (r["peak"] >= SOGLIA and r["pnl_fin"] > 0)]

N_m, N_n = len(maschi), len(nonmasc)

print("=" * 72)
print("SIMULA21 — FIRMA DEL MASCHIO NEI PRIMISSIMI TICK")
print("=" * 72)
print(f"MASCHI  (peak>=2.5 lordo E pnl>0): {N_m}")
print(f"NON-MASCHI (tutto il resto):         {N_n}")
print()

def avg(lst, key):
    v = [r[key] for r in lst if r.get(key) is not None]
    return sum(v)/len(v) if v else 0.0

def pct(lst, fn):
    v = [fn(r) for r in lst]
    return 100*sum(v)/len(v) if v else 0.0

print(f"  {'METRICA':<32}  {'MASCHI':>10}  {'NON-MASCHI':>11}  {'DIFF':>8}")
print(f"  {'-'*66}")

metriche = [
    ("Grasso tick-0 ($)",         "g_t0"),
    ("Grasso a 0.5s ($)",         "g_05"),
    ("Grasso a 1.0s ($)",         "g_1s"),
    ("Grasso a 1.5s ($)",         "g_15"),
    ("Minimo in primi 1.5s ($)",  "min_early"),
    ("Inversioni in 3s (#)",      "inv_3s"),
]
for nome, key in metriche:
    vm = avg(maschi,  key)
    vn = avg(nonmasc, key)
    diff = vm - vn
    flag = "  ◄ FORTE" if abs(diff) > 0.15 else ("  ◄ MEDIO" if abs(diff) > 0.05 else "")
    print(f"  {nome:<32}  {vm:>+10.3f}  {vn:>+11.3f}  {diff:>+8.3f}{flag}")

# tick-0 positivo
pm = pct(maschi,  lambda r: r["tick0_pos"])
pn = pct(nonmasc, lambda r: r["tick0_pos"])
print(f"  {'Tick-0 positivo (%)':32}  {pm:>+10.0f}%  {pn:>+10.0f}%  {pm-pn:>+7.0f}pp")

# tempo al verde
tm = avg([r for r in maschi  if r["t_pos"] is not None], "t_pos")
tn = avg([r for r in nonmasc if r["t_pos"] is not None], "t_pos")
nm_nv = sum(1 for r in nonmasc if r["t_pos"] is None)
print(f"  {'Tempo al primo g>0 (s)':32}  {tm:>+10.2f}   {tn:>+10.2f}  {tm-tn:>+8.2f}")
print(f"  {'Mai in verde in 3s':32}  {'':>10}   {nm_nv:>9} ({100*nm_nv/N_n:.0f}%)")
print()

# ── distribuzione grasso a 0.5s ─────────────────────────────────────────────
print("─" * 72)
print("DISTRIBUZIONE GRASSO A 0.5s — dove sono maschi e non-maschi?")
print("─" * 72)
fasce = [(-99,-0.5,"<-0.5$"),(-0.5,-0.1,"-0.5→-0.1$"),(-0.1,0.0,"-0.1→0$"),
         (0.0,0.3,"0→0.3$"),(0.3,0.7,"0.3→0.7$"),(0.7,1.5,"0.7→1.5$"),(1.5,99,">1.5$")]
print(f"  {'grasso@0.5s':14}  {'MASCHI%':>8}  {'NONM%':>8}  {'M/(M+N)':>9}  note")
for lo,hi,lab in fasce:
    nm_ = sum(1 for r in maschi  if r["g_05"] is not None and lo<=r["g_05"]<hi)
    nn_ = sum(1 for r in nonmasc if r["g_05"] is not None and lo<=r["g_05"]<hi)
    pm_ = 100*nm_/N_m if N_m else 0
    pn_ = 100*nn_/N_n if N_n else 0
    ratio = 100*nm_/(nm_+nn_) if (nm_+nn_)>0 else 0
    flag = "  ← SOLO MASCHI" if nn_==0 and nm_>0 else ("  ← pochi maschi" if ratio<20 and nm_>0 else "")
    print(f"  {lab:<14}  {pm_:>7.0f}%  {pn_:>7.0f}%  {ratio:>8.0f}%{flag}")
print()

# ── distribuzione inversioni ──────────────────────────────────────────────
print("─" * 72)
print("INVERSIONI IN 3s — maschi salgono dritti o a strappi?")
print("─" * 72)
print(f"  {'inv':>4}  {'MASCHI%':>8}  {'NONM%':>8}")
for inv_n in range(7):
    pm_ = 100*sum(1 for r in maschi  if r["inv_3s"]==inv_n)/N_m if N_m else 0
    pn_ = 100*sum(1 for r in nonmasc if r["inv_3s"]==inv_n)/N_n if N_n else 0
    flag = "  ◄" if abs(pm_-pn_)>=10 else ""
    print(f"  {inv_n:>4}  {pm_:>7.0f}%  {pn_:>7.0f}%{flag}")
print()

# ── la domanda chiave: a che TEMPO appare la firma? ─────────────────────────
print("=" * 72)
print("TIMING DELLA FIRMA — a che secondo si separa il maschio?")
print("=" * 72)
print()
for t_chk, key in [(0.0,"g_t0"),(0.5,"g_05"),(1.0,"g_1s"),(1.5,"g_15")]:
    # soglia: grasso > 0 separa bene?
    for soglia_g in [0.0, 0.3]:
        nm_ = sum(1 for r in maschi  if r.get(key) is not None and r[key]>soglia_g)
        nn_ = sum(1 for r in nonmasc if r.get(key) is not None and r[key]>soglia_g)
        prec_m = 100*nm_/N_m if N_m else 0      # maschi catturati
        prec_n = 100*nn_/N_n if N_n else 0      # non-maschi inclusi (falsi positivi)
        print(f"  t={t_chk:.1f}s  g>{soglia_g:.1f}:  "
              f"cattura {prec_m:.0f}% maschi,  include {prec_n:.0f}% non-maschi")
print()
print("  INTERPRETAZIONE:")
print("  Se t=0.0s separa già bene → firma nel PRIMO tick (ultra-precoce)")
print("  Se serve t=1.0s → firma appare dopo 1s (tardi, movimento già avvenuto)")
print()

# ── verdetto ────────────────────────────────────────────────────────────────
print("=" * 72)
print("VERDETTO — esiste la firma pre-scatto?")
print("=" * 72)
print()

g05_maschi_pos = sum(1 for r in maschi  if r.get("g_05") and r["g_05"]>0.3)
g05_nonm_zero  = sum(1 for r in nonmasc if r.get("g_05") and r["g_05"]<=0.3)
pct_m_catturati = 100*g05_maschi_pos/N_m if N_m else 0
pct_n_esclusi   = 100*g05_nonm_zero/N_n if N_n else 0

print(f"  Filtro g@0.5s > 0.3:")
print(f"    Cattura {pct_m_catturati:.0f}% dei maschi")
print(f"    Esclude {pct_n_esclusi:.0f}% dei non-maschi")
print()
if pct_m_catturati > 70 and pct_n_esclusi > 50:
    print("  ✓ FIRMA ESISTE e si manifesta entro 0.5s dall'entrata.")
    print("  CAVEAT: 0.5s POST-entrata (fee già pagata).")
    print("  Applicazione pratica: uscita ultra-rapida se g@0.5s <= 0.3.")
    print("  NON è pre-entrata — ma è la firma più precoce possibile sul dato.")
else:
    print("  ~ La firma non emerge con forza nei primissimi tick.")
    print("  Il maschio si distingue più tardi (dopo 1-2s).")
    print("  A quell'ora lo scatto è già avvenuto — troppo tardi per filtrare.")

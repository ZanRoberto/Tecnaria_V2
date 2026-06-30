#!/usr/bin/env python3
"""
SIMULA15 — USCITA ASIMMETRICA MFE (maschio vs femmina)

SCOPERTA (simula14): l'MFE predice la ripresa con gap di 53pp.
  MFE < 1.5$: risale 43%  → FEMMINA → chiudi appena scende (VERDE_FLOOR)
  MFE > 3.0$: risale 96%  → MASCHIO → NON chiudere, lascia respirare

REGOLA ASIMMETRICA (tick per tick, no lookahead):
  - Traccia max_grasso_visto in tempo reale
  - Se max_grasso_visto < FEMMINA_SOGLIA (1.5) e il trade scende sotto il floor:
      chiudi subito (è femmina, non risale)
  - Se max_grasso_visto >= FEMMINA_SOGLIA:
      nessun floor — lascia correre (è maschio/medio, risale)

Confronta vs:
  A) Netto REALE (baseline)
  B) VERDE_FLOOR semplice (simula13: -$10 — tagliava anche i maschi)
  C) USCITA ASIMMETRICA MFE (questo script)

Lancio: python3 simula15.py
"""
import sqlite3, json

DB  = "/var/data/trading_data.db"
FEE = 2.0   # grasso = lordo - $2

VERDE_MIN_GRASSO   = 0.5   # min grasso per "era davvero verde" (lordo $2.5)
VERDE_FLOOR_GRASSO = 0.0   # pavimento (lordo $2.0 = breakeven netto)

# ── carica dati ────────────────────────────────────────────────────────────
con  = sqlite3.connect(DB)
rows = con.execute(
    "SELECT peak_nascita, pnl_finale, curva_json FROM curva_nascita WHERE pnl_finale IS NOT NULL"
).fetchall()
con.close()

trades_raw = []
for peak, pnlf, cj in rows:
    try:
        pts    = json.loads(cj)
        grassi = [float(p[1]) for p in pts if len(p) >= 2]
        if len(grassi) < 2:
            continue
        trades_raw.append({
            "grassi":   grassi,
            "pnl_real": float(pnlf),
        })
    except Exception:
        pass

N          = len(trades_raw)
tot_reale  = sum(t["pnl_real"] for t in trades_raw)
nwin_reale = sum(1 for t in trades_raw if t["pnl_real"] > 0)

print("=" * 72)
print("SIMULA15 — USCITA ASIMMETRICA MFE")
print("=" * 72)
print(f"Trade totali: {N}")
print(f"Netto REALE:  ${tot_reale:+.1f}  ({nwin_reale}/{N} win = {100*nwin_reale/N:.0f}%)")
print()

# ── simulazione asimmetrica (tick per tick) ───────────────────────────────
def simula_asimmetrica(trades, femmina_soglia, verde_min=VERDE_MIN_GRASSO,
                        verde_floor=VERDE_FLOOR_GRASSO):
    """
    Applica VERDE_FLOOR solo ai trade con max_grasso_visto < femmina_soglia.
    Trade con max_grasso_visto >= femmina_soglia: nessun intervento.
    Scan tick per tick — nessun lookahead.
    """
    risultati = []
    for t in trades:
        grassi   = t["grassi"]
        pnl_real = t["pnl_real"]

        max_g    = -9999.0
        pnl_sim  = pnl_real   # default: nessun intervento
        scattato = False

        for g in grassi:
            max_g = max(max_g, g)

            # ha raggiunto verde genuino?
            if max_g < verde_min:
                continue

            # è ancora classificato come femmina?
            if max_g < femmina_soglia:
                # applica floor: chiudi se scende sotto il pavimento
                if g < verde_floor:
                    pnl_sim  = g
                    scattato = True
                    break
            # se max_g >= femmina_soglia: nessun floor (maschio o medio)

        risultati.append({
            "pnl_real": pnl_real,
            "pnl_sim":  pnl_sim,
            "scattato": scattato,
            "max_g":    max_g,
        })
    return risultati

# ── VERDE_FLOOR semplice (per confronto interno, replica simula13) ─────────
def simula_verde_floor_semplice(trades, verde_min_lordo=2.5, verde_floor_lordo=2.0):
    vmg = verde_min_lordo  - FEE
    vfg = verde_floor_lordo - FEE
    risultati = []
    for t in trades:
        grassi   = t["grassi"]
        pnl_real = t["pnl_real"]
        max_g    = -9999.0
        pnl_sim  = pnl_real
        for g in grassi:
            max_g = max(max_g, g)
            if max_g >= vmg and g < vfg:
                pnl_sim = g
                break
        risultati.append({"pnl_real": pnl_real, "pnl_sim": pnl_sim, "max_g": max_g})
    return risultati

ris_vf = simula_verde_floor_semplice(trades_raw)
tot_vf  = sum(r["pnl_sim"] for r in ris_vf)
delta_vf = tot_vf - tot_reale

# ── test configurazioni asimmetriche ─────────────────────────────────────
configs = [
    (1.0, "ASIMMETRICA stretta  fem<1.0$"),
    (1.5, "ASIMMETRICA base     fem<1.5$"),
    (2.0, "ASIMMETRICA larga    fem<2.0$"),
    (2.5, "ASIMMETRICA xlarga   fem<2.5$"),
    (3.0, "ASIMMETRICA=FLOOR    fem<3.0$ (solo maschi liberi)"),
]

print(f"  {'CONFIGURAZIONE':<42}  {'delta':>8}  {'tot_sim':>10}  {'WR%':>6}  {'floor_n':>7}")
print(f"  {'-'*76}")

migliore_delta = None
migliore_cfg   = None
migliore_ris   = None
migliore_label = None

for fs, label in configs:
    r = simula_asimmetrica(trades_raw, femmina_soglia=fs)
    ts  = sum(x["pnl_sim"] for x in r)
    d   = ts - tot_reale
    wr  = 100 * sum(1 for x in r if x["pnl_sim"] > 0) / N
    nsc = sum(1 for x in r if x["scattato"])
    mk  = ""
    if migliore_delta is None or d > migliore_delta:
        migliore_delta = d
        migliore_cfg   = fs
        migliore_ris   = r
        migliore_label = label
        mk = " ◄ BEST"
    print(f"  {label:<42}  {d:>+8.1f}  {ts:>+10.1f}  {wr:>5.0f}%{mk}  {nsc:>7}")

print(f"  {'VERDE_FLOOR semplice (simula13 replica)':<42}  {delta_vf:>+8.1f}  {tot_vf:>+10.1f}  "
      f"{100*sum(1 for r in ris_vf if r['pnl_sim']>0)/N:>5.0f}%        ---")
print(f"  {'REALE (baseline)':<42}  {'0':>8}  {tot_reale:>+10.1f}  "
      f"{100*nwin_reale/N:>5.0f}%        ---")

# ── dettaglio config migliore ─────────────────────────────────────────────
print()
print("=" * 72)
print(f"DETTAGLIO — config migliore: {migliore_label}")
print("=" * 72)
print()

scattati     = [r for r in migliore_ris if r["scattato"]]
non_scattati = [r for r in migliore_ris if not r["scattato"]]
salvati      = [r for r in scattati if r["pnl_sim"] > r["pnl_real"]]
tagliati     = [r for r in scattati if r["pnl_sim"] < r["pnl_real"]]

print(f"  Floor scattato su:   {len(scattati)} trade ({100*len(scattati)/N:.0f}%)")
print(f"  Non toccati:         {len(non_scattati)} trade")
print()
if salvati:
    tot_sav = sum(r["pnl_sim"] - r["pnl_real"] for r in salvati)
    print(f"  SALVATI (evitato perdita):   {len(salvati):>4}  +${tot_sav:.1f}")
if tagliati:
    tot_tag = sum(r["pnl_real"] - r["pnl_sim"] for r in tagliati)
    print(f"  TAGLIATI PRESTO (lasciato):  {len(tagliati):>4}  -${tot_tag:.1f}")
    if salvati:
        saldo = sum(r["pnl_sim"] - r["pnl_real"] for r in salvati) - tot_tag
        print(f"  SALDO NETTO:                       {saldo:+.1f}$")

# ── breakdown per fascia MFE ──────────────────────────────────────────────
print()
print("─" * 72)
print("BREAKDOWN PER FASCIA MFE (max grasso raggiunto):")
print("─" * 72)
fasce = [
    ("NEUTRO  (mai verde, mfe<0.5)", lambda r: r["max_g"] < 0.5),
    ("FEMMINA (0.5 → 1.5$)",         lambda r: 0.5 <= r["max_g"] < 1.5),
    ("MEDIO   (1.5 → 3.0$)",         lambda r: 1.5 <= r["max_g"] < 3.0),
    ("MASCHIO (3.0$+)",               lambda r: r["max_g"] >= 3.0),
]
print(f"  {'FASCIA':<28}  {'N':>4}  {'real_tot':>9}  {'sim_tot':>9}  {'delta':>7}  {'WR_r':>6}  {'WR_s':>6}")
print(f"  {'-'*76}")
for nome, cond in fasce:
    gr = [r for r in migliore_ris if cond(r)]
    if not gr:
        continue
    tr   = sum(r["pnl_real"] for r in gr)
    ts   = sum(r["pnl_sim"]  for r in gr)
    wr_r = 100 * sum(1 for r in gr if r["pnl_real"] > 0) / len(gr)
    wr_s = 100 * sum(1 for r in gr if r["pnl_sim"]  > 0) / len(gr)
    print(f"  {nome:<28}  {len(gr):>4}  {tr:>+9.1f}  {ts:>+9.1f}  {ts-tr:>+7.1f}  {wr_r:>5.0f}%  {wr_s:>5.0f}%")

# ── verdetto ──────────────────────────────────────────────────────────────
print()
print("=" * 72)
print("VERDETTO")
print("=" * 72)
print()
tot_best = tot_reale + migliore_delta
print(f"  Netto REALE:                    ${tot_reale:+.1f}")
print(f"  VERDE_FLOOR semplice:           ${tot_vf:+.1f}  ({delta_vf:+.1f} vs reale)")
print(f"  ASIMMETRICA MFE (best):         ${tot_best:+.1f}  ({migliore_delta:+.1f} vs reale)")
print()

if migliore_delta > delta_vf:
    print(f"  ✓ L'uscita ASIMMETRICA batte il VERDE_FLOOR semplice")
    print(f"    di ${migliore_delta - delta_vf:.1f} (non taglia i maschi buoni)")
else:
    print(f"  ✗ L'uscita ASIMMETRICA NON batte il VERDE_FLOOR semplice")

if migliore_delta > 0:
    print(f"  ✓ MIGLIORA il netto di ${migliore_delta:+.1f} sul totale storico")
    if tot_best > 0:
        print(f"  ✓ Il sistema diventa POSITIVO con l'uscita asimmetrica")
    else:
        print(f"  ~ Il sistema resta negativo (${tot_best:+.1f}) ma si avvicina al pareggio")
else:
    print(f"  ✗ Anche l'uscita asimmetrica NON migliora il netto")
    print(f"    Il problema è altrove (entrata, regime, altro)")
print()

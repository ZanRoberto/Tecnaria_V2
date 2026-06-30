#!/usr/bin/env python3
"""
SIMULA14 — MFE PREDICE LA RIPRESA? (maschio vs femmina all'uscita)

Domanda: i trade con MFE alto (picco verde > X$) risalgono piu' spesso
         dei trade con MFE basso dopo aver ceduto dal picco?

METODO:
  - Prende tutti i trade che erano in verde (MFE > 0)
  - Filtra solo quelli che poi sono SCESI dal picco (pnl_finale < MFE)
  - Li divide per livello di MFE raggiunto
  - Per ogni gruppo: quanti hanno chiuso POSITIVO (risaliti) vs NEGATIVO (franati)?

  Se MFE alto → WR_recovery molto piu' alta di MFE basso:
      l'MFE distingue maschio (tiene) da femmina (frana)
  Se simili:
      l'MFE non serve, bisogna guardare altro

GRUPPI:
  MFE_BASSO:  picco grasso  0.0  a  1.5$
  MFE_MEDIO:  picco grasso  1.5$ a  3.0$
  MFE_ALTO:   picco grasso  3.0$+

Lancio: python3 simula14.py
"""
import sqlite3, json
from collections import defaultdict

DB = "/var/data/trading_data.db"

con  = sqlite3.connect(DB)
rows = con.execute(
    "SELECT peak_nascita, pnl_finale, curva_json FROM curva_nascita WHERE pnl_finale IS NOT NULL"
).fetchall()
con.close()

trades = []
for peak, pnlf, cj in rows:
    try:
        pts    = json.loads(cj)
        grassi = [float(p[1]) for p in pts if len(p) >= 2]
        if not grassi:
            continue
        mfe      = float(peak or max(grassi))
        pnl_fin  = float(pnlf)
        trades.append({"mfe": mfe, "pnl": pnl_fin, "grassi": grassi})
    except Exception:
        pass

N = len(trades)
print("=" * 72)
print("SIMULA14 — MFE PREDICE LA RIPRESA?")
print("=" * 72)
print(f"Trade totali in curva_nascita: {N}")
print()

# ── filtra: erano in verde E poi sono scesi dal picco ─────────────────────
# "in verde" = MFE > 0 (grasso positivo = covered fee + profit)
# "poi scesi" = pnl_finale < MFE (non hanno chiuso al picco)
scesi = [t for t in trades if t["mfe"] > 0 and t["pnl"] < t["mfe"]]
print(f"Erano in verde (MFE>0) e poi sono scesi dal picco: {len(scesi)}")
print()

# ── analisi per fasce MFE ─────────────────────────────────────────────────
FASCE = [
    ("MFE_BASSO  (0.0 → 1.5$)", lambda t: 0.0 <= t["mfe"] < 1.5),
    ("MFE_MEDIO  (1.5 → 3.0$)", lambda t: 1.5 <= t["mfe"] < 3.0),
    ("MFE_ALTO   (3.0$+)      ", lambda t: t["mfe"] >= 3.0),
    ("MFE_MOLTO_ALTO (5.0$+)  ", lambda t: t["mfe"] >= 5.0),
]

print(f"  {'FASCIA':<27}  {'N':>5}  {'risaliti':>9}  {'franati':>8}  "
      f"{'WR%_rec':>8}  {'avg_pnl':>8}  {'avg_mfe':>8}")
print(f"  {'-'*76}")

risultati_fasce = {}
for nome, cond in FASCE:
    gruppo = [t for t in scesi if cond(t)]
    if not gruppo:
        print(f"  {nome:<27}  (nessun trade)")
        continue
    risaliti = [t for t in gruppo if t["pnl"] > 0]
    franati  = [t for t in gruppo if t["pnl"] <= 0]
    wr       = 100 * len(risaliti) / len(gruppo)
    avg_pnl  = sum(t["pnl"]  for t in gruppo) / len(gruppo)
    avg_mfe  = sum(t["mfe"]  for t in gruppo) / len(gruppo)
    risultati_fasce[nome] = {"wr": wr, "N": len(gruppo)}
    print(f"  {nome:<27}  {len(gruppo):>5}  {len(risaliti):>9}  {len(franati):>8}  "
          f"{wr:>7.0f}%  {avg_pnl:>+8.2f}  {avg_mfe:>+8.2f}")

print()

# ── analisi granulare: soglie MFE da 0.5 a 5.0 ───────────────────────────
print("=" * 72)
print("GRANULARE — WR recovery per ogni soglia MFE (0.5$ step)")
print("=" * 72)
print()
print(f"  {'MFE >':>7}  {'N':>5}  {'risaliti':>9}  {'WR%_rec':>8}  {'avg_pnl':>9}  {'tot_pnl':>9}")
print(f"  {'-'*58}")

prev_wr = None
for soglia in [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]:
    g = [t for t in scesi if t["mfe"] >= soglia]
    if not g:
        print(f"  {soglia:>7.1f}  (nessun trade)")
        continue
    ris  = [t for t in g if t["pnl"] > 0]
    wr   = 100 * len(ris) / len(g)
    avg  = sum(t["pnl"] for t in g) / len(g)
    tot  = sum(t["pnl"] for t in g)
    trend = ""
    if prev_wr is not None:
        diff = wr - prev_wr
        trend = f"  ↑+{diff:.0f}%" if diff > 3 else (f"  ↓{diff:.0f}%" if diff < -3 else "")
    prev_wr = wr
    print(f"  {soglia:>7.1f}  {len(g):>5}  {len(ris):>9}  {wr:>7.0f}%  {avg:>+9.2f}  {tot:>+9.1f}{trend}")

print()

# ── analisi comportamento DOPO il picco (tick per tick) ───────────────────
print("=" * 72)
print("ANALISI DINAMICA — cosa fa il grasso DOPO il picco?")
print("Confronta: trade MFE<1.5 vs MFE>3: dopo il picco scendono allo stesso modo?")
print("=" * 72)
print()

def dopo_picco_stats(gruppo_trades):
    """Per ogni trade, trova il picco nel grasso e analizza il tratto dopo."""
    discese_max = []   # quanto scende al massimo dopo il picco
    riprese     = []   # se risale sopra 50% del picco dopo aver ceduto
    for t in gruppo_trades:
        gs  = t["grassi"]
        mfe = t["mfe"]
        # trova posizione del picco
        pk_idx = 0
        pk_val = gs[0]
        for i, g in enumerate(gs):
            if g >= pk_val:
                pk_val = g
                pk_idx = i
        # analizza tratto dopo il picco
        dopo = gs[pk_idx:]
        if len(dopo) < 2:
            continue
        min_dopo = min(dopo)
        discesa_dal_picco = pk_val - min_dopo
        discese_max.append(discesa_dal_picco)
        # risale sopra il 50% del picco dopo aver ceduto?
        soglia_ripresa = pk_val * 0.5
        ha_ceduto   = any(g < soglia_ripresa for g in dopo)
        ha_ripreso  = ha_ceduto and any(g >= soglia_ripresa for g in dopo[1:])
        riprese.append(ha_ripreso)

    n = len(discese_max)
    if n == 0:
        return None
    avg_disc = sum(discese_max) / n
    pct_ripr = 100 * sum(riprese) / len(riprese) if riprese else 0
    return {"n": n, "avg_discesa": avg_disc, "pct_ripresa": pct_ripr}

basso_g = [t for t in scesi if t["mfe"] < 1.5]
alto_g  = [t for t in scesi if t["mfe"] >= 3.0]

s_basso = dopo_picco_stats(basso_g)
s_alto  = dopo_picco_stats(alto_g)

if s_basso and s_alto:
    print(f"  {'':30}  {'MFE BASSO (<1.5$)':>18}  {'MFE ALTO (>3$)':>16}")
    print(f"  {'-'*66}")
    print(f"  {'Trade analizzati':30}  {s_basso['n']:>18}  {s_alto['n']:>16}")
    print(f"  {'Discesa media dal picco ($)':30}  {s_basso['avg_discesa']:>+18.2f}  {s_alto['avg_discesa']:>+16.2f}")
    print(f"  {'% che rip. sopra 50% del picco':30}  {s_basso['pct_ripresa']:>17.0f}%  {s_alto['pct_ripresa']:>15.0f}%")
    print()

# ── verdetto ──────────────────────────────────────────────────────────────
print("=" * 72)
print("VERDETTO")
print("=" * 72)
print()

g_basso = [t for t in scesi if t["mfe"] < 1.5]
g_alto  = [t for t in scesi if t["mfe"] >= 3.0]

if g_basso and g_alto:
    wr_b = 100 * sum(1 for t in g_basso if t["pnl"] > 0) / len(g_basso)
    wr_a = 100 * sum(1 for t in g_alto  if t["pnl"] > 0) / len(g_alto)
    gap  = wr_a - wr_b

    print(f"  WR recovery MFE BASSO (<1.5$): {wr_b:.0f}%  ({len(g_basso)} trade)")
    print(f"  WR recovery MFE ALTO  (>3.0$): {wr_a:.0f}%  ({len(g_alto)} trade)")
    print(f"  GAP: {gap:+.0f} punti percentuali")
    print()

    if gap >= 20:
        print(f"  RISPOSTA: SI — l'MFE predice la ripresa.")
        print(f"  Gap di {gap:.0f}pp: il MFE alto risale molto piu' spesso.")
        print(f"  IMPLICAZIONE: nel VERDE_FLOOR, trattare diversamente:")
        print(f"    MFE basso (<1.5$): chiudi subito quando scende (femmina)")
        print(f"    MFE alto (>3$):    lascia respirare, probabilmente risale (maschio)")
    elif gap >= 10:
        print(f"  RISPOSTA: FORSE — gap di {gap:.0f}pp, segnale moderato.")
        print(f"  L'MFE aiuta ma non e' sufficiente da solo.")
        print(f"  Utile come fattore aggiuntivo ma non come discriminatore primario.")
    else:
        print(f"  RISPOSTA: NO — gap di {gap:.0f}pp, troppo piccolo per distinguere.")
        print(f"  L'MFE raggiunto NON predice se un verde risale o frana.")
        print(f"  La distinzione maschio/femmina va cercata altrove (velocita', MAE, etc.).")
print()

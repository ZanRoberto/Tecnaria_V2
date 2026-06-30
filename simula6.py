#!/usr/bin/env python3
"""
SIMULA6 — ANALISI PER REGIME (RANGING vs EXPLOSIVE vs TRENDING)

Domanda: in RANGING, esiste una soglia MFE che copre le fee e lascia margine?
         O il RANGING e' sempre perdente indipendentemente dalla soglia?

Usa la colonna 'firma' di curva_nascita che contiene:
    momentum|volatility|trend|regime|direction
    es: DEBOLE|BASSA|SIDEWAYS|RANGING|LONG

Per ogni regime mostra la tabella soglia → totale$ con entrata ritardata
(formula: pnl_maschio = pnl_finale - grasso_al_primo_tocco_soglia)

Lancio: python3 simula6.py
"""
import sqlite3, json
from collections import defaultdict

DB = "/var/data/trading_data.db"
FEE = 2.0
SOGLIE = [0.0, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0]

con = sqlite3.connect(DB)
rows = con.execute(
    "SELECT firma, peak_nascita, pnl_finale, curva_json FROM curva_nascita WHERE pnl_finale IS NOT NULL"
).fetchall()
con.close()

# raggruppa per regime
per_regime = defaultdict(list)
for firma, peak, pnlf, cj in rows:
    try:
        parti = (firma or "").split("|")
        regime = parti[3] if len(parti) >= 4 else "SCONOSCIUTO"
        pts = json.loads(cj)
        grassi = [float(p[1]) for p in pts if len(p) >= 2]
        if not grassi:
            continue
        per_regime[regime].append({
            "pnl":    float(pnlf),
            "mfe":    float(peak or max(grassi)),
            "grassi": grassi,
        })
    except Exception:
        pass

print("=" * 72)
print("ANALISI PER REGIME — entrata ritardata (MASCHIO_DIRETTO timing)")
print("=" * 72)

for regime in sorted(per_regime.keys()):
    trades = per_regime[regime]
    N = len(trades)
    tot_reale = sum(t["pnl"] for t in trades)
    nwin_reale = sum(1 for t in trades if t["pnl"] > 0)
    print(f"\n{'─'*72}")
    print(f"REGIME: {regime}  |  trade totali: {N}  |  win reali: {nwin_reale}  |  totale reale: {tot_reale:+.1f}")
    print(f"{'─'*72}")
    print(f"  {'soglia':>7} {'entrati':>8} {'win':>5} {'WR%':>5} {'win_persi':>10} {'totale$':>9} {'media$/t':>9}")
    print(f"  {'-'*62}")

    for soglia in SOGLIE:
        entrati_pnl = []
        win_cat = 0
        win_persi = 0
        for t in trades:
            grasso_entry = next((g for g in t["grassi"] if g >= soglia), None)
            if grasso_entry is None:
                if t["pnl"] > 0:
                    win_persi += 1
                continue
            pnl_md = t["pnl"] - grasso_entry
            entrati_pnl.append(pnl_md)
            if pnl_md > 0:
                win_cat += 1

        n_ent = len(entrati_pnl)
        if n_ent == 0:
            print(f"  {soglia:>7.1f}  nessun trade entra")
            continue
        tot = sum(entrati_pnl)
        wr = 100 * win_cat / n_ent
        media = tot / n_ent
        print(f"  {soglia:>7.1f} {n_ent:>8} {win_cat:>5} {wr:>5.0f}% {win_persi:>10} {tot:>+9.1f} {media:>+9.2f}")

print(f"\n{'='*72}")
print("LEGENDA:")
print("  totale$  = somma pnl se entri quando grasso tocca la soglia")
print("  media$/t = profitto medio per trade (deve coprire fee $2)")
print("  win_persi= win storici che la soglia non cattura")
print()
print("RISPOSTA ALLA DOMANDA:")
print("  Se RANGING ha totale$ POSITIVO a qualche soglia -> si puo' tradare")
print("  Se RANGING e' SEMPRE negativo -> veto totale, aspetta EXPLOSIVE")

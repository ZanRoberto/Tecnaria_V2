#!/usr/bin/env python3
"""
SIMULA36 — REGOLA EXIT A 10s: SE IL PICCO ERA PRIMA DI 3s E SIAMO IN ROSSO, ESCI
Read-only. Nessuna modifica al DB o ad app.py.

Filtro: trade_ts >= 1782604800 (24-giu-2026)
Regola: al primo punto con t>=10s,
        se pnl_netto < 0 E picco_pnl raggiunto prima di t=3s → CHIUDI lì
        altrimenti → lascia correre fino a pnl_finale
"""
import sqlite3, json

DB = "/var/data/trading_data.db"

con = sqlite3.connect(DB)
rows = con.execute(
    "SELECT pnl_finale, curva_json FROM curva_nascita "
    "WHERE trade_ts >= 1782604800 AND pnl_finale IS NOT NULL"
).fetchall()
con.close()

print(f"Righe lette dal DB: {len(rows)}")

n_trade         = 0
n_chiusi        = 0
win_uccisi      = 0   # pnl_finale > 0 ma regola li ha chiusi prima
loss_evitati    = 0   # pnl_finale < 0 e regola li ha salvati
netto_reale     = 0.0
netto_simulato  = 0.0

for pnl_finale, cj in rows:
    pf = float(pnl_finale)
    try:
        pts = json.loads(cj)
        parsed = []
        for p in pts:
            if len(p) >= 2:
                parsed.append((float(p[0]), float(p[1])))
    except Exception:
        continue
    if not parsed:
        continue

    n_trade += 1
    netto_reale += pf

    # picco di pnl_netto e quando è avvenuto
    picco_val = max(p[1] for p in parsed)
    picco_t   = next(p[0] for p in parsed if p[1] >= picco_val)

    # primo punto con t >= 10s
    pt_10 = next(((t, g) for t, g in parsed if t >= 10.0), None)

    if pt_10 is not None and pt_10[1] < 0 and picco_t < 3.0:
        # regola scatta: chiudi a quel pnl_netto
        pnl_sim = pt_10[1]
        n_chiusi += 1
        if pf > 0:
            win_uccisi += 1
        else:
            loss_evitati += 1
    else:
        pnl_sim = pf

    netto_simulato += pnl_sim

print()
print(f"{'':30} {'VALORE':>12}")
print("-" * 44)
print(f"{'Trade analizzati':<30} {n_trade:>12}")
print(f"{'Chiusi dalla regola':<30} {n_chiusi:>12}")
print(f"{'  di cui WIN reali uccisi':<30} {win_uccisi:>12}")
print(f"{'  di cui LOSS reali evitati':<30} {loss_evitati:>12}")
print(f"{'Netto reale ($)':<30} {netto_reale:>+12.2f}")
print(f"{'Netto simulato ($)':<30} {netto_simulato:>+12.2f}")
print(f"{'Delta ($)':<30} {netto_simulato - netto_reale:>+12.2f}")

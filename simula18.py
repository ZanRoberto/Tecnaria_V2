#!/usr/bin/env python3
"""
SIMULA18 — SE GIOCO SOLO I MASCHI (peak >= 2.5 lordo), IL NETTO E' POSITIVO?
"""
import sqlite3

con  = sqlite3.connect("/var/data/trading_data.db")
rows = con.execute(
    "SELECT peak_nascita, pnl_finale FROM curva_nascita WHERE pnl_finale IS NOT NULL"
).fetchall()
con.close()

N_tot  = len(rows)
tot_all = sum(f for _, f in rows)

print(f"TUTTI I TRADE: {N_tot}  netto={tot_all:+.1f}$")
print()
print(f"  {'FILTRO':<30}  {'N':>5}  {'netto':>9}  {'WR%':>6}  {'media/trade':>12}")
print(f"  {'-'*66}")

for lordo_min in [2.0, 2.5, 3.0, 3.5, 4.0, 5.0]:
    grasso_min = lordo_min - 2.0
    g = [(p, f) for p, f in rows if (p or 0) >= grasso_min]
    if not g:
        print(f"  peak >= {lordo_min:.1f}$ lordo: nessun trade")
        continue
    tot  = sum(f for _, f in g)
    win  = sum(1 for _, f in g if f > 0)
    avg  = tot / len(g)
    print(f"  peak >= {lordo_min:.1f}$ lordo        {len(g):>5}  {tot:>+9.1f}  "
          f"{100*win/len(g):>5.0f}%  {avg:>+12.2f}$/trade")

print()
print("NOTA: questi sono trade che hanno GIA' raggiunto quel picco (oracle).")
print("In produzione non sai prima se lo raggiungera'.")

#!/usr/bin/env python3
import sqlite3, json

con  = sqlite3.connect("/var/data/trading_data.db")
rows = con.execute(
    "SELECT peak_nascita, pnl_finale, curva_json FROM curva_nascita WHERE pnl_finale IS NOT NULL"
).fetchall()
con.close()

ris = []
for peak, pnlf, cj in rows:
    try:
        pts  = json.loads(cj)
        micro = [(float(p[0]), float(p[1])) for p in pts if len(p) >= 2 and float(p[0]) <= 2]
        if len(micro) < 2:
            continue
        g1s  = min(micro, key=lambda p: abs(p[0] - 1.0))[1]
        pk   = float(peak or 0)
        pf   = float(pnlf)
        if pk >= 0.5 and pf > 0 and g1s > 0.3:
            ris.append({"g1s": g1s, "pnl_fin": pf, "net": pf - g1s - 2.0})
    except Exception:
        pass

N = len(ris)
print(f"Maschi con g@1s > 0.3:  {N}")
print(f"avg g@1s:               {sum(r['g1s']     for r in ris)/N:+.3f}$")
print(f"avg pnl_finale:         {sum(r['pnl_fin'] for r in ris)/N:+.3f}$")
print(f"avg netto CORRETTO:     {sum(r['net']     for r in ris)/N:+.3f}$  (= pnl_fin - g1s - 2$)")
print(f"totale netto CORRETTO:  {sum(r['net']     for r in ris):+.2f}$")
print(f"positivi:               {sum(1 for r in ris if r['net']>0)}/{N}")

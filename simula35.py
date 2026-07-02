#!/usr/bin/env python3
"""
SIMULA35 — PICCO LORDO NEI PRIMI 3s: WIN vs LOSS
Read-only. Nessuna modifica al DB o ad app.py.
"""
import sqlite3, json

DB = "/var/data/trading_data.db"

con = sqlite3.connect(DB)
rows = con.execute(
    "SELECT pnl_finale, curva_json FROM curva_nascita WHERE pnl_finale IS NOT NULL"
).fetchall()
con.close()

FEE = 2.0
risultati = {"WIN": [], "LOSS": []}

for pnl_finale, cj in rows:
    pf = float(pnl_finale)
    try:
        pts = json.loads(cj)
        # ogni punto = [t, pnl_netto, mfe] — ignora terzo campo
        parsed = []
        for p in pts:
            if len(p) >= 2:
                t   = float(p[0])
                pnl = float(p[1])
                parsed.append((t, pnl))
    except Exception:
        continue
    if not parsed:
        continue

    lordo_pts = [(t, pnl + FEE) for t, pnl in parsed]

    picco_3s    = max((l for t, l in lordo_pts if t <= 3.0), default=None)
    picco_tot   = max(l for _, l in lordo_pts)

    if picco_3s is None:
        picco_3s = lordo_pts[0][1]  # se nessun punto <= 3s, prendo il primo

    label = "WIN" if pf > 0 else "LOSS"
    risultati[label].append({
        "picco_3s":  picco_3s,
        "picco_tot": picco_tot,
    })

print(f"{'CLASSE':<6}  {'N':>5}  {'pk3s_avg':>10}  {'pktot_avg':>11}  "
      f"{'pk3s>1$':>9}  {'pk3s>2$':>9}")
print("-" * 60)

for label in ["WIN", "LOSS"]:
    grp = risultati[label]
    if not grp:
        print(f"{label:<6}  {'0':>5}")
        continue
    n          = len(grp)
    avg_3s     = sum(r["picco_3s"]  for r in grp) / n
    avg_tot    = sum(r["picco_tot"] for r in grp) / n
    gt1        = sum(1 for r in grp if r["picco_3s"] > 1.0)
    gt2        = sum(1 for r in grp if r["picco_3s"] > 2.0)
    print(f"{label:<6}  {n:>5}  {avg_3s:>+10.3f}  {avg_tot:>+11.3f}  "
          f"{gt1:>6} ({100*gt1//n:>2}%)  {gt2:>6} ({100*gt2//n:>2}%)")

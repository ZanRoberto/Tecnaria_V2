#!/usr/bin/env python3
"""
SIMULA5 — ENTRATA IN RITARDO (MASCHIO_DIRETTO timing reale)

Domanda: se MASCHIO_DIRETTO entra QUANDO il grasso tocca X$,
quanto PnL resta da guadagnare dopo quell'entrata?

Formula:
  pnl_maschio = pnl_finale - grasso_al_momento_entrata

Perche': il pnl_finale e' calcolato dal prezzo di nascita.
Se entri quando il grasso e' gia' a X$, hai gia' "consumato"
quella parte del movimento — non la prendi tu.

Confronta soglie diverse per trovare il punto ottimale:
  - soglia bassa (0.3) = entri presto, prendi piu' del movimento
  - soglia alta (1.5)  = entri tardi, il movimento e' gia' accaduto

Lancio: python3 simula5.py
"""
import sqlite3, json

DB = "/var/data/trading_data.db"
FEE = 2.0

con = sqlite3.connect(DB)
rows = con.execute(
    "SELECT peak_nascita, pnl_finale, curva_json FROM curva_nascita WHERE pnl_finale IS NOT NULL"
).fetchall()
con.close()

trades = []
for peak, pnlf, cj in rows:
    try:
        pts = json.loads(cj)
        grassi = [float(p[1]) for p in pts if len(p) >= 2]
        if not grassi:
            continue
        trades.append({
            "mfe":       float(peak or max(grassi)),
            "pnl":       float(pnlf),
            "win":       float(pnlf) > 0,
            "grassi":    grassi,
        })
    except Exception:
        pass

N = len(trades)
tot_reale = sum(t["pnl"] for t in trades)
nwin_reale = sum(1 for t in trades if t["win"])
print(f"Trade in DB (con pnl_finale): {N}")
print(f"BASELINE reale (entra tutto al segnale): WIN={nwin_reale} TOTALE={tot_reale:+.1f}")
print()
print("SIMULAZIONE ENTRATA RITARDATA (MASCHIO_DIRETTO):")
print("Se entri QUANDO grasso >= soglia, quanto pnl ti resta?")
print("="*72)
print(f"{'soglia':>8} {'entrati':>8} {'win':>6} {'WR%':>6} {'win_persi':>10} {'totale$':>10} {'vs_reale':>10}")
print("-"*72)

for soglia in [0.0, 0.3, 0.5, 0.7, 1.0, 1.2, 1.5, 2.0]:
    entrati_pnl = []
    win_catturati = 0
    for t in trades:
        # trova il PRIMO momento in cui il grasso tocca la soglia
        grasso_entry = None
        for g in t["grassi"]:
            if g >= soglia:
                grasso_entry = g
                break
        if grasso_entry is None:
            continue  # non ha mai toccato la soglia: MASCHIO_DIRETTO non entra
        # pnl che MASCHIO_DIRETTO ottiene = pnl_finale - grasso_gia_consumato
        pnl_md = t["pnl"] - grasso_entry
        entrati_pnl.append(pnl_md)
        if pnl_md > 0:
            win_catturati += 1
    n_entrati = len(entrati_pnl)
    if n_entrati == 0:
        print(f"{soglia:>8.1f}  nessun trade entra")
        continue
    tot = sum(entrati_pnl)
    wr = 100 * win_catturati / n_entrati
    win_persi = nwin_reale - sum(1 for t in trades
                                  if any(g >= soglia for g in t["grassi"]) and t["win"])
    print(f"{soglia:>8.1f} {n_entrati:>8} {win_catturati:>6} {wr:>6.0f}% {win_persi:>10} {tot:>+10.1f} {tot-tot_reale:>+10.1f}")

print("="*72)
print()
print("LETTURA:")
print("  totale$ = somma dei pnl che MASCHIO_DIRETTO porta a casa con quella soglia")
print("  vs_reale = quanto fa meglio/peggio del sistema senza filtro")
print("  win_persi = win storici che quella soglia NON cattura")
print()
print("REGOLA: cerca la soglia con totale$ piu' ALTO e win_persi BASSI.")
print("  soglia troppo alta = il movimento e' gia' accaduto -> entri in cima -> perdi")
print("  soglia troppo bassa = entri presto ma prendi anche femmine -> perdi")

#!/usr/bin/env python3
"""
SIMULA7 — SOGLIA OTTIMALE MD_MFE_MIN (MASCHIO_DIRETTO)

Domanda: quale valore di MD_MFE_MIN massimizza il profitto reale?

Metodo:
  Per ogni soglia S, simula MASCHIO_DIRETTO cosi':
    - il trade ENTRA quando grasso tocca S per la prima volta
    - pnl_maschio = pnl_finale - grasso_al_primo_tocco_S
      (il grasso gia' consumato quando entri non lo prendi tu)
    - se il trade non ha mai raggiunto S -> NON entra (filtrato)

Mostra per ogni soglia:
  entrati   = trade che superano S (hanno fatto abbastanza grasso)
  win       = quanti pnl_maschio > 0
  WR%       = win rate
  totale$   = somma pnl_maschio (il vero P&L del bot con quella soglia)
  PF        = profit factor = sum_wins / abs(sum_losses)   (>1 = edge reale)
  media$/t  = media per trade (deve coprire fee $2)
  win_persi = trade storicamente win ma NON raggiungono S (persi dal filtro)

Lancio: python3 simula7.py
"""
import sqlite3, json

DB    = "/var/data/trading_data.db"
FEE   = 2.0
SOGLIE = [0.0, 0.3, 0.5, 0.7, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0]

# ── carica dati ─────────────────────────────────────────────────────────────
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
        trades.append({
            "mfe":    float(peak or max(grassi)),
            "pnl":    float(pnlf),
            "win":    float(pnlf) > 0,
            "grassi": grassi,
        })
    except Exception:
        pass

N          = len(trades)
tot_reale  = sum(t["pnl"] for t in trades)
nwin_reale = sum(1 for t in trades if t["win"])

print("=" * 80)
print("SIMULA7 — SOGLIA OTTIMALE MD_MFE_MIN (entrata ritardata MASCHIO_DIRETTO)")
print("=" * 80)
print(f"Trade totali in DB: {N}  |  win reali: {nwin_reale}  |  totale reale: {tot_reale:+.1f}$")
print()
print(f"  {'soglia':>6}  {'entrati':>8}  {'win':>5}  {'WR%':>5}  "
      f"{'win_persi':>9}  {'totale$':>9}  {'media$/t':>9}  {'PF':>6}")
print(f"  {'-'*72}")

best_soglia = None
best_tot    = None

for soglia in SOGLIE:
    entrati_pnl = []
    win_cat     = 0
    sum_win     = 0.0
    sum_loss    = 0.0
    win_persi   = 0

    for t in trades:
        # primo momento in cui il grasso tocca la soglia
        grasso_entry = next((g for g in t["grassi"] if g >= soglia), None)

        if grasso_entry is None:
            # trade non raggiunge soglia → non entra
            if t["win"]:
                win_persi += 1
            continue

        pnl_md = t["pnl"] - grasso_entry
        entrati_pnl.append(pnl_md)
        if pnl_md > 0:
            win_cat  += 1
            sum_win  += pnl_md
        else:
            sum_loss += pnl_md

    n_ent = len(entrati_pnl)
    if n_ent == 0:
        print(f"  {soglia:>6.1f}  (nessun trade entra)")
        continue

    tot   = sum(entrati_pnl)
    wr    = 100 * win_cat / n_ent
    media = tot / n_ent
    pf    = (sum_win / abs(sum_loss)) if sum_loss != 0 else float("inf")

    marker = ""
    if best_tot is None or tot > best_tot:
        best_tot    = tot
        best_soglia = soglia
        marker      = " ◄ BEST"

    print(f"  {soglia:>6.1f}  {n_ent:>8}  {win_cat:>5}  {wr:>5.0f}%  "
          f"{win_persi:>9}  {tot:>+9.1f}  {media:>+9.2f}  {pf:>6.2f}{marker}")

print(f"  {'-'*72}")
print()
print("LEGENDA:")
print("  totale$   = somma pnl se entri quando grasso tocca la soglia")
print("  media$/t  = profitto medio per trade (serve > fee $2 per avere edge netto)")
print("  PF        = profit factor: sum_wins / |sum_losses|  (>1 = edge, >1.5 = solido)")
print("  win_persi = win storici che la soglia NON cattura (filtrati via)")
print()
print(f"RISPOSTA: soglia ottimale sui dati reali = {best_soglia}$  (totale {best_tot:+.1f}$)")
print()
print("NOTE:")
print("  - soglia 0.0 = baseline (entra tutto subito, senza ritardo)")
print("  - soglia alta = meno trade ma piu' selettivi; rischio win_persi alti")
print("  - sceglie la soglia dove  media$/t > 2.0 E PF > 1.0")
print()

# ── tabella win/loss per soglia (analisi distribuzione) ─────────────────────
print("=" * 80)
print("DISTRIBUZIONE DETTAGLIATA — solo soglie interessanti (media$/t > 0)")
print("=" * 80)

for soglia in SOGLIE:
    entrati_pnl = []
    for t in trades:
        grasso_entry = next((g for g in t["grassi"] if g >= soglia), None)
        if grasso_entry is None:
            continue
        entrati_pnl.append(t["pnl"] - grasso_entry)

    if not entrati_pnl:
        continue

    tot   = sum(entrati_pnl)
    media = tot / len(entrati_pnl)
    if media <= 0:
        continue

    wins  = [p for p in entrati_pnl if p > 0]
    losss = [p for p in entrati_pnl if p <= 0]
    avg_w = sum(wins)  / len(wins)  if wins  else 0
    avg_l = sum(losss) / len(losss) if losss else 0

    print(f"\n  SOGLIA {soglia}$ — {len(entrati_pnl)} trade  totale {tot:+.1f}$  media {media:+.2f}$/t")
    print(f"    Win:  {len(wins):>4} trade  avg_win  {avg_w:>+7.2f}$")
    print(f"    Loss: {len(losss):>4} trade  avg_loss {avg_l:>+7.2f}$")
    if losss:
        pf = sum(wins) / abs(sum(losss)) if losss else float("inf")
        print(f"    Profit Factor: {pf:.2f}  (edge {'REALE' if pf > 1 else 'ASSENTE'})")

print()

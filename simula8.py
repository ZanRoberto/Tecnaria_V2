#!/usr/bin/env python3
"""
SIMULA8 — IL MOTORE RESPIRA NEL TREND? (RANGING vs TRENDING, fee-adjusted)

Domanda: il problema è il RANGING, o il motore è rotto a priori?

Separa i trade storici per regime (dalla colonna 'firma' di curva_nascita):
  firma = "momentum|volatility|trend|regime|direction"
  regime = campo [3] → RANGING / TRENDING_BULL / TRENDING_BEAR / EXPLOSIVE

Per ogni regime, calcola il P&L NETTO di MASCHIO_DIRETTO a varie soglie:
  pnl_netto = (pnl_finale - grasso_al_primo_tocco_soglia) - FEE

FEE: maker=$2 (paper trade attuale) e taker=$5 (market order in LIVE).

Lancio: python3 simula8.py
"""
import sqlite3, json
from collections import defaultdict

DB         = "/var/data/trading_data.db"
FEE_MAKER  = 2.0
FEE_TAKER  = 5.0
SOGLIE_KEY = [0.0, 0.5, 1.0, 2.0]   # soglie rilevanti per la tabella

# ── carica dati ─────────────────────────────────────────────────────────────
con  = sqlite3.connect(DB)
rows = con.execute(
    "SELECT firma, peak_nascita, pnl_finale, curva_json FROM curva_nascita WHERE pnl_finale IS NOT NULL"
).fetchall()
con.close()

# raggruppa per regime
per_regime = defaultdict(list)
for firma, peak, pnlf, cj in rows:
    try:
        parti   = (firma or "").split("|")
        regime  = parti[3].strip() if len(parti) >= 4 else "SCONOSCIUTO"
        pts     = json.loads(cj)
        grassi  = [float(p[1]) for p in pts if len(p) >= 2]
        if not grassi:
            continue
        per_regime[regime].append({
            "peak":   float(peak or max(grassi)),
            "pnl":    float(pnlf),
            "grassi": grassi,
        })
    except Exception:
        pass

# ── funzione analisi per un regime ─────────────────────────────────────────
def analizza(trades, fee, label_fee):
    N      = len(trades)
    tot_r  = sum(t["pnl"] for t in trades)
    nwin_r = sum(1 for t in trades if t["pnl"] > 0)
    wr_r   = 100 * nwin_r / N if N else 0

    print(f"  [{label_fee}]  N={N}  win_reali={nwin_r}  WR_reale={wr_r:.0f}%  tot_reale={tot_r:+.1f}$  fee/t=${fee:.2f}")
    print(f"  {'soglia':>6}  {'entrati':>8}  {'win':>5}  {'WR%':>5}  {'lordo$':>8}  {'NETTO$':>9}  {'media_n/t':>10}  {'PF_n':>6}")
    print(f"  {'-'*68}")

    for soglia in SOGLIE_KEY:
        lordi  = []
        netti  = []
        win_n  = 0
        sum_w  = 0.0
        sum_l  = 0.0

        for t in trades:
            g = next((g for g in t["grassi"] if g >= soglia), None)
            if g is None:
                continue
            pnl_l = t["pnl"] - g
            pnl_n = pnl_l - fee
            lordi.append(pnl_l)
            netti.append(pnl_n)
            if pnl_n > 0:
                win_n += 1
                sum_w += pnl_n
            else:
                sum_l += pnl_n

        n_e = len(netti)
        if n_e == 0:
            print(f"  {soglia:>6.1f}  (nessun trade raggiunge la soglia)")
            continue

        tot_l  = sum(lordi)
        tot_n  = sum(netti)
        media  = tot_n / n_e
        wr     = 100 * win_n / n_e
        pf_n   = (sum_w / abs(sum_l)) if sum_l != 0 else float("inf")
        tick   = " ✓" if tot_n > 0 else ""

        print(f"  {soglia:>6.1f}  {n_e:>8}  {win_n:>5}  {wr:>5.0f}%  "
              f"{tot_l:>+8.1f}  {tot_n:>+9.1f}  {media:>+10.2f}  {pf_n:>6.2f}{tick}")
    print()

# ── ordine di stampa: prima i TRENDING, poi RANGING, poi resto ──────────────
ORDINE = ["TRENDING_BULL", "TRENDING_BEAR", "EXPLOSIVE", "RANGING", "SCONOSCIUTO"]
regimi_presenti = list(per_regime.keys())
ordinati = [r for r in ORDINE if r in regimi_presenti]
ordinati += [r for r in regimi_presenti if r not in ORDINE]

print("=" * 80)
print("SIMULA8 — MOTORE vs REGIME  (P&L netto MASCHIO_DIRETTO per regime)")
print("=" * 80)
print()

for regime in ordinati:
    trades = per_regime[regime]
    print("═" * 80)
    print(f"  REGIME: {regime}  ({len(trades)} trade storici)")
    print("═" * 80)
    analizza(trades, FEE_MAKER, "MAKER $2")
    analizza(trades, FEE_TAKER, "TAKER $5")

# ── VERDETTO COMPARATIVO ─────────────────────────────────────────────────────
print("=" * 80)
print("VERDETTO COMPARATIVO — totale netto a soglia 0.5$ (soglia bassa, massimo volume)")
print("=" * 80)
print()
print(f"  {'REGIME':<18}  {'N':>5}  {'NETTO(maker$2)':>15}  {'media_n/t':>10}  {'NETTO(taker$5)':>15}  {'media_n/t':>10}")
print(f"  {'-'*78}")

SOGLIA_VD = 0.5
for regime in ordinati:
    trades = per_regime[regime]
    for fee, col in [(FEE_MAKER, None), (FEE_TAKER, None)]:
        pass  # pre-loop vuoto, calcoliamo sotto

    risultati = {}
    for fee_val, fee_lbl in [(FEE_MAKER, "maker"), (FEE_TAKER, "taker")]:
        netti = []
        for t in trades:
            g = next((g for g in t["grassi"] if g >= SOGLIA_VD), None)
            if g is None: continue
            netti.append((t["pnl"] - g) - fee_val)
        risultati[fee_lbl] = (netti, sum(netti) if netti else 0, sum(netti)/len(netti) if netti else 0)

    nm, tot_m, med_m = risultati["maker"]
    nt, tot_t, med_t = risultati["taker"]
    N_ent = len(nm)

    segno_m = "✓" if tot_m > 0 else "✗"
    segno_t = "✓" if tot_t > 0 else "✗"
    print(f"  {regime:<18}  {N_ent:>5}  {segno_m} {tot_m:>+12.1f}  {med_m:>+10.2f}  {segno_t} {tot_t:>+12.1f}  {med_t:>+10.2f}")

print()
print("LEGENDA:")
print("  ✓ = netto positivo dopo fee  |  ✗ = netto negativo")
print("  soglia usata per il verdetto: 0.5$  (trade che toccano almeno 50 centesimi di grasso)")
print()
print("INTERPRETAZIONE:")
print("  Se RANGING è ✗ e TRENDING è ✓ → il motore funziona, il problema è il regime")
print("  Se entrambi ✗ → il problema è strutturale (fee > catturato in qualunque regime)")
print()

#!/usr/bin/env python3
"""
SIMULA7 — SOGLIA, FEE REALE (MAKER vs TAKER), FILTRO PEAK

DOMANDA: a quale soglia e con quale tipo di ordine il sistema è positivo al netto?
          Filtrando solo i movimenti grossi (peak > 3$, > 5$), copre la fee?

FEE:
  MAKER (limit order): 5000$ × 0.0002 × 2 lati = $2.00 per trade
  TAKER (market order): 5000$ × 0.0005 × 2 lati = $5.00 per trade
  Oggi il bot è PAPER (FEE_PCT=0.0002 = maker). In LIVE, se usa market → TAKER.

FORMULA:
  pnl_lordo_maschio = pnl_finale - grasso_al_primo_tocco_soglia  (lordo catturato)
  pnl_netto         = pnl_lordo_maschio - FEE                    (MASCHIO paga la sua fee)

Lancio: python3 simula7.py
"""
import sqlite3, json
from collections import defaultdict

DB            = "/var/data/trading_data.db"
FEE_MAKER     = 2.0    # $5000 × 0.02% × 2 = $2.00
FEE_TAKER     = 5.0    # $5000 × 0.05% × 2 = $5.00
SOGLIE        = [0.0, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0, 3.0]
PEAK_FILTRI   = [0.0, 3.0, 5.0]   # 0.0 = tutti, 3.0 = peak_nascita > 3$, 5.0 > 5$

# ── carica dati ─────────────────────────────────────────────────────────────
con  = sqlite3.connect(DB)
rows = con.execute(
    "SELECT peak_nascita, pnl_finale, curva_json FROM curva_nascita WHERE pnl_finale IS NOT NULL"
).fetchall()
con.close()

all_trades = []
for peak, pnlf, cj in rows:
    try:
        pts    = json.loads(cj)
        grassi = [float(p[1]) for p in pts if len(p) >= 2]
        if not grassi:
            continue
        all_trades.append({
            "peak":   float(peak or max(grassi)),
            "pnl":    float(pnlf),
            "grassi": grassi,
        })
    except Exception:
        pass

# ── funzione tabella per un set di trade e una fee ──────────────────────────
def stampa_tabella(trades, fee, label_fee):
    N      = len(trades)
    tot_r  = sum(t["pnl"] for t in trades)
    nwin_r = sum(1 for t in trades if t["pnl"] > 0)

    print(f"  Trade: {N}  |  win reali: {nwin_r}  |  totale reale: {tot_r:+.1f}$  |  fee/trade: ${fee:.2f} ({label_fee})")
    print()
    print(f"    {'soglia':>6}  {'entrati':>8}  {'win_n':>6}  {'WR%':>5}  "
          f"{'lordo$':>8}  {'fee_tot':>8}  {'NETTO$':>9}  {'media_n/t':>10}  {'PF_n':>6}")
    print(f"    {'-'*80}")

    best_netto  = None
    best_soglia = None

    for soglia in SOGLIE:
        lordi_md = []
        netti_md = []
        win_netto = 0
        sum_w = 0.0
        sum_l = 0.0

        for t in trades:
            g_entry = next((g for g in t["grassi"] if g >= soglia), None)
            if g_entry is None:
                continue
            pnl_lordo = t["pnl"] - g_entry
            pnl_netto = pnl_lordo - fee

            lordi_md.append(pnl_lordo)
            netti_md.append(pnl_netto)
            if pnl_netto > 0:
                win_netto += 1
                sum_w     += pnl_netto
            else:
                sum_l     += pnl_netto

        n_ent = len(netti_md)
        if n_ent == 0:
            print(f"    {soglia:>6.1f}  (nessun trade raggiunge la soglia)")
            continue

        tot_lordo = sum(lordi_md)
        tot_netto = sum(netti_md)
        fee_tot   = n_ent * fee
        media_n   = tot_netto / n_ent
        wr        = 100 * win_netto / n_ent
        pf_n      = (sum_w / abs(sum_l)) if sum_l != 0 else float("inf")

        marker = ""
        if tot_netto > 0 and (best_netto is None or tot_netto > best_netto):
            best_netto  = tot_netto
            best_soglia = soglia
            marker = " ◄ NETTO+"

        print(f"    {soglia:>6.1f}  {n_ent:>8}  {win_netto:>6}  {wr:>5.0f}%  "
              f"{tot_lordo:>+8.1f}  {-fee_tot:>+8.1f}  {tot_netto:>+9.1f}  "
              f"{media_n:>+10.2f}  {pf_n:>6.2f}{marker}")

    print(f"    {'-'*80}")
    if best_soglia is not None:
        print(f"    ✓ NETTO POSITIVO alla soglia {best_soglia}$  (totale {best_netto:+.1f}$)")
    else:
        print(f"    ✗ Nessuna soglia è positiva al netto della fee ${fee:.2f}")
    print()

# ── CORPO PRINCIPALE ─────────────────────────────────────────────────────────
for peak_min in PEAK_FILTRI:
    if peak_min == 0.0:
        trades_sub = all_trades
        titolo_peak = "TUTTI i trade (nessun filtro peak)"
    else:
        trades_sub = [t for t in all_trades if t["peak"] >= peak_min]
        titolo_peak = f"Solo trade con peak_nascita >= {peak_min}$  (movimenti grossi)"

    print("=" * 90)
    print(f"FILTRO PEAK: {titolo_peak}")
    print("=" * 90)
    print()

    print("  ── MAKER ($2/trade — limit order) ──────────────────────────────────────────────")
    stampa_tabella(trades_sub, FEE_MAKER, "maker 0.02%")

    print("  ── TAKER ($5/trade — market order) ─────────────────────────────────────────────")
    stampa_tabella(trades_sub, FEE_TAKER, "taker 0.05%")

# ── RIEPILOGO FINALE ─────────────────────────────────────────────────────────
print("=" * 90)
print("RIEPILOGO — tratti residui medi catturati per peak_min × soglia")
print("(lordo, prima di fee — confronta con fee MAKER $2 e TAKER $5)")
print("=" * 90)
print()
print(f"  {'peak_min':>8}  {'n_trade':>8}  ", end="")
for s in [0.0, 0.7, 1.5, 3.0]:
    print(f"  {'s='+str(s):>8}", end="")
print()
print(f"  {'-'*70}")

for peak_min in [0.0, 1.0, 3.0, 5.0]:
    trades_sub = [t for t in all_trades if t["peak"] >= peak_min]
    N = len(trades_sub)
    print(f"  {peak_min:>8.1f}  {N:>8}  ", end="")
    for soglia in [0.0, 0.7, 1.5, 3.0]:
        lordi = []
        for t in trades_sub:
            g = next((g for g in t["grassi"] if g >= soglia), None)
            if g is None: continue
            lordi.append(t["pnl"] - g)
        if lordi:
            print(f"  {sum(lordi)/len(lordi):>+8.2f}", end="")
        else:
            print(f"  {'---':>8}", end="")
    print()

print()
print("  Valori = media lordo$/t catturato da MASCHIO_DIRETTO.")
print("  Deve essere > 2.0 (maker) o > 5.0 (taker) per coprire la fee.")
print()

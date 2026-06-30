#!/usr/bin/env python3
"""
SIMULA13 — VERDE_FLOOR: QUANTO RECUPERO SUI DATI REALI?

Ricalcola il pnl di ogni trade in curva_nascita come se fosse uscito
con la nuova regola VERDE_FLOOR invece di quella attuale.

LOGICA VERDE_FLOOR (tradotta in grasso, dove grasso = lordo - $2 = netto):
  - max_grasso visto durante il trade >= VERDE_MIN_GRASSO
    (il trade era davvero verde = aveva coperto la fee e dato profitto)
  - corrente_grasso scende sotto VERDE_FLOOR_GRASSO
    (il verde sta evaporando → chiudi ADESSO)
  → pnl_simulato = corrente_grasso al momento del trigger

Relazione lordo ↔ grasso:
  grasso = lordo - FEE ($2)
  VERDE_MIN_USD=2.5 lordo  → VERDE_MIN_GRASSO  = 0.5
  VERDE_FLOOR_USD=2.0 lordo → VERDE_FLOOR_GRASSO = 0.0  (breakeven netto)
  VERDE_FLOOR_USD=1.5 lordo → VERDE_FLOOR_GRASSO = -0.5 (tolera -$0.5 netto)

EFFETTO COLLATERALE:
  Quando verde_floor chiude un trade, il pnl_reale potrebbe essere MIGLIORE
  (avrebbe risalito → lo abbiamo tagliato troppo presto).
  Calcoliamo:
    - trade SALVATI: verde_floor > pnl_reale → abbiamo evitato una perdita
    - trade TAGLIATI PRESTO: verde_floor < pnl_reale → abbiamo lasciato sul tavolo
  Saldo netto = somma(salvati) - somma(tagliati_presto)

Lancio: python3 simula13.py
"""
import sqlite3, json
from collections import defaultdict

DB  = "/var/data/trading_data.db"
FEE = 2.0   # $2 = grasso + $2 = lordo

# Combinazioni da testare (VERDE_MIN_USD, VERDE_FLOOR_USD) entrambi in lordo
COMBINAZIONI = [
    (2.5, 1.5),  # "verde genuino ($0.5 net), chiudi se scendi a -$0.5 net" — più permissivo
    (2.5, 2.0),  # "verde genuino ($0.5 net), chiudi a breakeven" — proposta originale
    (2.0, 2.0),  # "qualsiasi verde, chiudi a breakeven" — più aggressivo
    (2.5, 2.5),  # "verde genuino, non cedere nemmeno al breakeven — lock $0.5 net"
]

# ── carica dati ────────────────────────────────────────────────────────────
con  = sqlite3.connect(DB)
rows = con.execute(
    "SELECT peak_nascita, pnl_finale, curva_json FROM curva_nascita WHERE pnl_finale IS NOT NULL"
).fetchall()
con.close()

trades_raw = []
for peak, pnlf, cj in rows:
    try:
        pts    = json.loads(cj)
        # grasso = p[1]: il pnl live netto (fee già dedotta) a ogni punto della curva
        grassi = [float(p[1]) for p in pts if len(p) >= 2]
        if len(grassi) < 2:
            continue
        trades_raw.append({
            "grassi":   grassi,
            "pnl_real": float(pnlf),
        })
    except Exception:
        pass

N = len(trades_raw)
tot_reale = sum(t["pnl_real"] for t in trades_raw)
nwin_reale = sum(1 for t in trades_raw if t["pnl_real"] > 0)

print("=" * 80)
print("SIMULA13 — VERDE_FLOOR: il numero vero del miglioramento")
print("=" * 80)
print(f"Trade in curva_nascita: {N}")
print(f"Totale netto REALE:     ${tot_reale:+.1f}  (come è andata davvero)")
print(f"Win reali:              {nwin_reale}/{N}  ({100*nwin_reale/N:.0f}%)")
print()

# ── funzione simulazione per una combinazione ──────────────────────────────
def simula_verde_floor(trades, verde_min_lordo, verde_floor_lordo):
    """
    Ricalcola il pnl di ogni trade applicando la logica VERDE_FLOOR.
    Restituisce lista di dict con pnl_sim e metadati.
    """
    verde_min_grasso  = verde_min_lordo  - FEE   # soglia "era verde"
    verde_floor_grasso = verde_floor_lordo - FEE  # pavimento "non tornare rosso"

    risultati = []
    for t in trades:
        grassi    = t["grassi"]
        pnl_real  = t["pnl_real"]

        max_grasso_visto = -9999.0
        pnl_sim          = pnl_real   # default: verde_floor non scatta → uscita normale
        verde_floor_scattato = False
        grasso_al_trigger    = None

        for g in grassi:
            max_grasso_visto = max(max_grasso_visto, g)

            # VERDE_FLOOR: se il trade ha mai toccato verde genuino E ora scende sotto il pavimento
            if max_grasso_visto >= verde_min_grasso and g < verde_floor_grasso:
                pnl_sim = g   # chiudi qui
                verde_floor_scattato = True
                grasso_al_trigger = g
                break

        risultati.append({
            "pnl_real":       pnl_real,
            "pnl_sim":        pnl_sim,
            "scattato":       verde_floor_scattato,
            "grasso_trigger": grasso_al_trigger,
            "max_grasso":     max_grasso_visto,
        })

    return risultati

# ── stampa risultati per ogni combinazione ─────────────────────────────────
print(f"  {'VERDE_MIN':>9}  {'VERDE_FLOOR':>11}  {'trigger':>8}  {'netto_sim':>10}  "
      f"{'DELTA':>8}  {'salvati':>8}  {'tagliati':>9}  {'saldo_netto':>11}")
print(f"  {'-'*84}")

migliore_delta = None
migliore_cfg   = None

for vm_usd, vf_usd in COMBINAZIONI:
    ris = simula_verde_floor(trades_raw, vm_usd, vf_usd)

    tot_sim    = sum(r["pnl_sim"]  for r in ris)
    delta      = tot_sim - tot_reale
    n_scattati = sum(1 for r in ris if r["scattato"])

    # trade salvati: floor > pnl_real (senza floor avrebbero perso di più)
    salvati    = [r for r in ris if r["scattato"] and r["pnl_sim"] > r["pnl_real"]]
    # trade tagliati presto: floor < pnl_real (avrebbero guadagnato di più)
    tagliati   = [r for r in ris if r["scattato"] and r["pnl_sim"] < r["pnl_real"]]

    beneficio  = sum(r["pnl_sim"] - r["pnl_real"] for r in salvati)  # positivo
    costo      = sum(r["pnl_real"] - r["pnl_sim"] for r in tagliati) # positivo
    saldo      = beneficio - costo

    mk = ""
    if migliore_delta is None or delta > migliore_delta:
        migliore_delta = delta
        migliore_cfg   = (vm_usd, vf_usd)
        mk = " ◄ BEST"

    print(f"  {vm_usd:>9.1f}  {vf_usd:>11.1f}  {n_scattati:>8}  {tot_sim:>+10.1f}  "
          f"{delta:>+8.1f}  {beneficio:>+8.1f}  {-costo:>+9.1f}  {saldo:>+11.1f}{mk}")

print(f"  {'-'*84}")
print()

# ── dettaglio combinazione migliore ───────────────────────────────────────
vm_best, vf_best = migliore_cfg
ris_best = simula_verde_floor(trades_raw, vm_best, vf_best)
tot_sim_best = sum(r["pnl_sim"] for r in ris_best)

scattati    = [r for r in ris_best if r["scattato"]]
non_scattati = [r for r in ris_best if not r["scattato"]]
salvati_b   = [r for r in scattati if r["pnl_sim"] > r["pnl_real"]]
tagliati_b  = [r for r in scattati if r["pnl_sim"] < r["pnl_real"]]
neutri_b    = [r for r in scattati if r["pnl_sim"] == r["pnl_real"]]

print("═" * 80)
print(f"DETTAGLIO — config migliore: VERDE_MIN=${vm_best:.1f} VERDE_FLOOR=${vf_best:.1f}")
print("═" * 80)
print()
print(f"  Trade totali analizzati:          {N}")
print(f"  Trade su cui VERDE_FLOOR scatta:  {len(scattati)} ({100*len(scattati)/N:.0f}%)")
print(f"  Trade NON toccati (normale exit): {len(non_scattati)} ({100*len(non_scattati)/N:.0f}%)")
print()
print(f"  Dei {len(scattati)} trade su cui scatta VERDE_FLOOR:")
print(f"    SALVATI  (evitato una perdita peggiore): {len(salvati_b):>4}")
if salvati_b:
    avg_sav = sum(r["pnl_sim"] - r["pnl_real"] for r in salvati_b) / len(salvati_b)
    tot_sav = sum(r["pnl_sim"] - r["pnl_real"] for r in salvati_b)
    print(f"      beneficio totale:  +${tot_sav:.1f}  media: +${avg_sav:.2f} per trade")
    # mostra qualche esempio
    esempi_s = sorted(salvati_b, key=lambda r: r["pnl_real"] - r["pnl_sim"])[:5]
    for r in esempi_s:
        print(f"      es: avrei chiuso a {r['pnl_sim']:+.2f}$  invece di {r['pnl_real']:+.2f}$  "
              f"→ salvato {r['pnl_sim']-r['pnl_real']:+.2f}$")
print()
print(f"    TAGLIATI PRESTO (avrebbe fatto meglio):  {len(tagliati_b):>4}")
if tagliati_b:
    avg_tag = sum(r["pnl_real"] - r["pnl_sim"] for r in tagliati_b) / len(tagliati_b)
    tot_tag = sum(r["pnl_real"] - r["pnl_sim"] for r in tagliati_b)
    print(f"      costo totale:      -${tot_tag:.1f}  media: -${avg_tag:.2f} per trade")
    esempi_t = sorted(tagliati_b, key=lambda r: r["pnl_real"] - r["pnl_sim"], reverse=True)[:5]
    for r in esempi_t:
        print(f"      es: avrei chiuso a {r['pnl_sim']:+.2f}$  ma avrebbe fatto {r['pnl_real']:+.2f}$  "
              f"→ perso {r['pnl_real']-r['pnl_sim']:+.2f}$")
print()

tot_beneficio = sum(r["pnl_sim"] - r["pnl_real"] for r in salvati_b)
tot_costo     = sum(r["pnl_real"] - r["pnl_sim"] for r in tagliati_b)
saldo_netto   = tot_beneficio - tot_costo

print(f"  SALDO NETTO: +${tot_beneficio:.1f} (salvato) - ${tot_costo:.1f} (tagliato presto) = {saldo_netto:+.1f}$")
print()

# ── confronto finale win/loss ──────────────────────────────────────────────
nwin_sim  = sum(1 for r in ris_best if r["pnl_sim"] > 0)
nloss_sim = sum(1 for r in ris_best if r["pnl_sim"] <= 0)
vr_old = sum(1 for r in ris_best if r["pnl_real"] <= 0 and r["max_grasso"] > (vm_best - FEE))
vr_new = sum(1 for r in ris_best if r["pnl_sim"]  <= 0 and r["max_grasso"] > (vm_best - FEE))

print("═" * 80)
print("CONFRONTO PRIMA/DOPO")
print("═" * 80)
print()
print(f"  {'':25}  {'REALE (attuale)':>18}  {'VERDE_FLOOR (nuovo)':>18}")
print(f"  {'-'*65}")
print(f"  {'Totale netto $':25}  {tot_reale:>+18.1f}  {tot_sim_best:>+18.1f}")
print(f"  {'DELTA':25}  {'':18}  {tot_sim_best - tot_reale:>+18.1f}")
print(f"  {'Win rate':25}  {100*nwin_reale/N:>17.0f}%  {100*nwin_sim/N:>17.0f}%")
print(f"  {'Win count':25}  {nwin_reale:>18}  {nwin_sim:>18}")
print(f"  {'Loss count':25}  {N-nwin_reale:>18}  {N-nloss_sim:>18}")
print(f"  {'Verde→rosso rimasti':25}  {vr_old:>18}  {vr_new:>18}")
print()

print("=" * 80)
print("VERDETTO")
print("=" * 80)
print()
delta_best = tot_sim_best - tot_reale
print(f"  Config migliore: VERDE_MIN=${vm_best:.1f} lordo / VERDE_FLOOR=${vf_best:.1f} lordo")
print(f"  Totale reale:     ${tot_reale:+.1f}")
print(f"  Totale simulato:  ${tot_sim_best:+.1f}")
print(f"  MIGLIORAMENTO:    ${delta_best:+.1f}  su {N} trade storici")
print()
if delta_best > 0:
    print(f"  ✓ Il VERDE_FLOOR migliora il totale di +${delta_best:.1f}$")
    print(f"  ✓ Saldo netto (salvato - tagliato presto): ${saldo_netto:+.1f}$")
    if saldo_netto > 0:
        print(f"  ✓ Vale la pena: salva più di quanto taglia.")
    else:
        print(f"  ⚠ Attenzione: il guadagno viene dal floor che chiude a valori più alti,")
        print(f"    ma il saldo netto è negativo — taglia alcune corsa buone.")
else:
    print(f"  ✗ Il VERDE_FLOOR NON migliora il netto sui dati storici.")
    print(f"     Possibile causa: i trade 'verde' avrebbero risalito in molti casi.")
print()
print("  NOTA: il fix è già applicato al bot (OVERTOP_BASSANO_V16_PRODUCTION.py).")
print("  ENV per regolarlo senza toccare il codice:")
print(f"    VERDE_MIN_USD={vm_best:.1f}    (lordo minimo per 'verde genuino')")
print(f"    VERDE_FLOOR_USD={vf_best:.1f}  (lordo sotto cui chiudi se eri verde)")
print(f"    TRAIL_OFF=true        (disabilita tutto il blocco verde se necessario)")
print()

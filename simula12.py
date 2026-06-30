#!/usr/bin/env python3
"""
SIMULA12 — ANALISI VERDE BUTTATO VIA (peak vs close reale)

Domanda: quanto profitto è stato "lasciato sul tavolo" da trade che
         erano in verde e hanno chiuso in perdita (o meno verde)?

FONTE 1 — tabella `trades` (M2_EXIT):
  data_json.peak_pnl   = picco lordo intra-trade
  data_json.pnl_netto  = finale netto
  reason               = motivo uscita (SALVA_VERDE*, HARD_STOP, energia, ...)

FONTE 2 — tabella `curva_nascita`:
  peak_nascita = max grasso in osservazione (il "verde" che ha visto il bot)
  pnl_finale   = dove ha chiuso davvero

METRICA CHIAVE:
  "verde buttato" = peak_pnl - pnl_netto   (per ogni trade che aveva picco > 0)
  "se chiudevi al picco" = sum(max(peak_pnl, 0)) per tutti i trade

Lancio: python3 simula12.py
"""
import sqlite3, json
from collections import defaultdict

DB  = "/var/data/trading_data.db"
FEE = 2.0

# ════════════════════════════════════════════════════════════════════════════
# FONTE 1 — tabella trades (M2_EXIT, dati recenti con peak_pnl in data_json)
# ════════════════════════════════════════════════════════════════════════════
con = sqlite3.connect(DB)

# verifica colonne disponibili
cols_trades = [r[1] for r in con.execute("PRAGMA table_info(trades)").fetchall()]

rows_trades = con.execute(
    "SELECT pnl, reason, data_json FROM trades WHERE event_type='M2_EXIT'"
).fetchall()

# ════════════════════════════════════════════════════════════════════════════
# FONTE 2 — tabella curva_nascita (tutti i trade con curva grasso)
# ════════════════════════════════════════════════════════════════════════════
rows_curva = con.execute(
    "SELECT peak_nascita, pnl_finale, curva_json FROM curva_nascita WHERE pnl_finale IS NOT NULL"
).fetchall()
con.close()

# ════════════════════════════════════════════════════════════════════════════
# ANALISI FONTE 1 — tabella trades
# ════════════════════════════════════════════════════════════════════════════
print("=" * 80)
print("FONTE 1 — tabella `trades` (M2_EXIT)  — peak_pnl da data_json")
print("=" * 80)
print()

trade_records = []
for pnl_final, reason, dj in rows_trades:
    try:
        d = json.loads(dj) if dj else {}
    except Exception:
        d = {}
    peak_lordo = d.get("peak_pnl", None)    # picco lordo intra-trade
    pnl_netto  = d.get("pnl_netto", pnl_final)
    pnl_lordo  = d.get("pnl_lordo", None)
    trade_records.append({
        "pnl_n":   float(pnl_netto  or 0),
        "pnl_l":   float(pnl_lordo  or 0) if pnl_lordo is not None else None,
        "peak_l":  float(peak_lordo or 0) if peak_lordo is not None else None,
        "reason":  reason or "",
    })

N_trades = len(trade_records)
# solo quelli con peak_pnl disponibile (V16 in avanti)
con_peak = [t for t in trade_records if t["peak_l"] is not None]

print(f"Trade M2_EXIT totali in DB: {N_trades}")
print(f"  Con peak_pnl in data_json: {len(con_peak)}")
print()

if con_peak:
    # suddividi per scenario
    had_verde    = [t for t in con_peak if t["peak_l"] > 0]       # erano in profitto lordo
    verde_rosso  = [t for t in had_verde  if t["pnl_n"] <= 0]     # erano verdi, chiusi in rosso
    verde_verde  = [t for t in had_verde  if t["pnl_n"] > 0]      # verdi e restati verdi
    mai_verde    = [t for t in con_peak   if t["peak_l"] <= 0]     # non sono mai stati in verde

    print(f"Dei {len(con_peak)} trade con peak_pnl tracciato:")
    print(f"  ✓ Erano in verde (peak_lordo > 0):        {len(had_verde):>5}  ({100*len(had_verde)/len(con_peak):.0f}%)")
    print(f"    → hanno chiuso POSITIVI (netto>0):      {len(verde_verde):>5}  ({100*len(verde_verde)/max(len(had_verde),1):.0f}% dei verdi)")
    print(f"    → hanno chiuso NEGATIVI (netto<=0):     {len(verde_rosso):>5}  ({100*len(verde_rosso)/max(len(had_verde),1):.0f}% dei verdi) ← VERDE BUTTATO")
    print(f"  ✗ Mai stati in verde (peak<=0):            {len(mai_verde):>5}  ({100*len(mai_verde)/len(con_peak):.0f}%)")
    print()

    if verde_rosso:
        peak_totale    = sum(t["peak_l"] for t in verde_rosso)
        pnl_totale_vr  = sum(t["pnl_n"]  for t in verde_rosso)
        verde_perduto  = peak_totale - pnl_totale_vr    # lordo perduto (picco - finale)
        avg_peak       = peak_totale / len(verde_rosso)
        avg_pnl        = pnl_totale_vr / len(verde_rosso)
        avg_buttato    = verde_perduto / len(verde_rosso)

        print(f"VERDE→ROSSO ({len(verde_rosso)} trade):")
        print(f"  Picco lordo medio:     ${avg_peak:+.2f}   (erano qui in verde)")
        print(f"  PnL netto medio:       ${avg_pnl:+.2f}   (qui hanno chiuso)")
        print(f"  Differenza media:      ${avg_buttato:.2f}  per trade")
        print()
        print(f"  Totale picco (lordo):  ${peak_totale:+.1f}")
        print(f"  Totale PnL finale:     ${pnl_totale_vr:+.1f}")
        print(f"  PROFITTO BUTTATO:      ${verde_perduto:.1f}  ← quello che avresti guadagnato in più")
        print()

    # scenario ipotetico: chiudi OGNI trade al suo picco lordo (netto = peak_lordo - FEE)
    se_chiudevi_al_picco = sum(max(t["peak_l"] - FEE, -FEE) for t in con_peak)
    reale_netto          = sum(t["pnl_n"] for t in con_peak)
    guadagno_extra       = se_chiudevi_al_picco - reale_netto

    print(f"SCENARIO: se avessi chiuso ogni trade esattamente al suo picco lordo:")
    print(f"  Totale netto reale:                   ${reale_netto:+.1f}")
    print(f"  Totale netto se chiuso al picco:       ${se_chiudevi_al_picco:+.1f}  (peak_lordo - $2 fee)")
    print(f"  DELTA (profitto extra mancato):        ${guadagno_extra:+.1f}")
    print()

    # breakdown per reason
    print("─" * 60)
    print("BREAKDOWN PER MOTIVO DI USCITA:")
    print("─" * 60)
    per_reason = defaultdict(list)
    for t in con_peak:
        # raggruppa per tipo di reason
        r = t["reason"]
        if "SALVA_VERDE" in r:
            key = "SALVA_VERDE"
        elif "HARD_STOP" in r or "hard_stop" in r.lower():
            key = "HARD_STOP"
        elif "STOP_LIVE" in r:
            key = "STOP_LIVE"
        elif "energia" in r.lower() or "EXIT_E" in r:
            key = "EXIT_ENERGIA"
        elif "STRAPPO" in r or "strappo" in r.lower() or "PRESA" in r:
            key = "STRAPPO/PRESA"
        elif "timeout" in r.lower() or "TIME" in r:
            key = "TIMEOUT"
        else:
            key = r[:30] if r else "ALTRO"
        per_reason[key].append(t)

    print(f"  {'MOTIVO':<20}  {'N':>5}  {'peak_avg':>9}  {'pnl_avg':>9}  {'buttato_avg':>12}  {'tot_netto':>10}")
    print(f"  {'-'*72}")
    for key in sorted(per_reason.keys()):
        ts = per_reason[key]
        n  = len(ts)
        has_peak = [t for t in ts if t["peak_l"] is not None]
        if not has_peak:
            print(f"  {key:<20}  {n:>5}  {'---':>9}  {'---':>9}  {'---':>12}  {'---':>10}")
            continue
        avg_pk  = sum(t["peak_l"] for t in has_peak) / len(has_peak)
        avg_pn  = sum(t["pnl_n"]  for t in has_peak) / len(has_peak)
        avg_bt  = avg_pk - avg_pn
        tot_n   = sum(t["pnl_n"]  for t in has_peak)
        mk_bt   = f"  ← evaporato!" if avg_bt > 2 else ""
        print(f"  {key:<20}  {n:>5}  {avg_pk:>+9.2f}  {avg_pn:>+9.2f}  {avg_bt:>+12.2f}  {tot_n:>+10.1f}{mk_bt}")
    print()

else:
    print("  (nessun trade con peak_pnl — probabilmente dati pre-V16)")
    print("  Uso FONTE 2 (curva_nascita) per l'analisi.")
    print()

# ════════════════════════════════════════════════════════════════════════════
# FONTE 2 — curva_nascita (copertura totale, anche trade precedenti)
# ════════════════════════════════════════════════════════════════════════════
print("=" * 80)
print("FONTE 2 — curva_nascita  (TUTTI i trade storici — copertura completa)")
print("=" * 80)
print()

curva_records = []
for peak, pnlf, cj in rows_curva:
    try:
        pts    = json.loads(cj)
        grassi = [float(p[1]) for p in pts if len(p) >= 2]
        if not grassi:
            continue
        peak_v = float(peak or max(grassi))
        pnl_v  = float(pnlf)
        # picco netto = picco lordo - fee (il "verde" al momento del picco)
        # pnl_finale = già netto delle fee (da simula7 analisi)
        curva_records.append({
            "peak":  peak_v,   # max grasso (già netto di entry fee, lordo di exit fee)
            "pnl":   pnl_v,    # finale (già include tutte le fee)
        })
    except Exception:
        pass

N_c = len(curva_records)
had_v   = [r for r in curva_records if r["peak"] > 0]
vr      = [r for r in had_v         if r["pnl"]  <= 0]   # verde→rosso
vv      = [r for r in had_v         if r["pnl"]  >  0]   # verde→verde
nv      = [r for r in curva_records  if r["peak"] <= 0]   # mai verdi

print(f"Trade totali in curva_nascita: {N_c}")
print(f"  Con picco grasso > 0 (erano in verde):  {len(had_v):>5}  ({100*len(had_v)/N_c:.0f}%)")
print(f"    → chiuso POSITIVO:                    {len(vv):>5}  ({100*len(vv)/max(len(had_v),1):.0f}% dei verdi)")
print(f"    → chiuso NEGATIVO (verde→rosso):      {len(vr):>5}  ({100*len(vr)/max(len(had_v),1):.0f}% dei verdi) ← BUTTATO")
print(f"  Mai in verde (picco<=0):                 {len(nv):>5}  ({100*len(nv)/N_c:.0f}%)")
print()

if vr:
    sum_peak_vr = sum(r["peak"] for r in vr)
    sum_pnl_vr  = sum(r["pnl"]  for r in vr)
    evaporato   = sum_peak_vr - sum_pnl_vr
    avg_pk_vr   = sum_peak_vr / len(vr)
    avg_pn_vr   = sum_pnl_vr  / len(vr)

    print(f"VERDE→ROSSO — dettaglio ({len(vr)} trade):")
    print(f"  Picco grasso medio:     ${avg_pk_vr:+.2f}")
    print(f"  PnL finale medio:       ${avg_pn_vr:+.2f}")
    print(f"  Differenza media:       ${avg_pk_vr - avg_pn_vr:.2f} per trade  ← grasso evaporato")
    print()
    print(f"  Totale picchi verdi:    ${sum_peak_vr:+.1f}")
    print(f"  Totale PnL finali:      ${sum_pnl_vr:+.1f}")
    print(f"  PROFITTO EVAPORATO:     ${evaporato:.1f}")
    print()

# scenario ORACLE: chiudi ogni trade al suo picco
reale_tot      = sum(r["pnl"]  for r in curva_records)
se_picco_tot   = sum(r["peak"] for r in curva_records)   # picco = già netto entry fee
extra_picco    = se_picco_tot - reale_tot

print(f"SCENARIO ORACLE — chiudi ogni trade ESATTAMENTE al suo picco grasso:")
print(f"  Totale reale (dove ha chiuso davvero):   ${reale_tot:+.1f}")
print(f"  Totale se chiuso al picco:               ${se_picco_tot:+.1f}")
print(f"  PROFITTO EXTRA MANCATO:                  ${extra_picco:+.1f}")
print(f"  Media extra per trade:                   ${extra_picco/N_c:+.2f}")
print()

# distribuzione picchi verdi buttati
print("─" * 60)
print("DISTRIBUZIONE: di quanto il picco supera il finale (per i verde→rosso)?")
print("─" * 60)
fasce = [(0, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 5.0), (5.0, 999)]
etichette = ["0→0.5$", "0.5→1$", "1→2$", "2→5$", ">5$"]
for (lo, hi), lab in zip(fasce, etichette):
    subset = [r for r in vr if lo <= (r["peak"] - r["pnl"]) < hi]
    if subset:
        pnl_s = sum(r["pnl"] for r in subset)
        print(f"  Picco-finale in {lab:<8}: {len(subset):>4} trade  PnL_finale={pnl_s:+.1f}$")

print()
print("=" * 80)
print("VERDETTO")
print("=" * 80)
print()
pct_vr = 100*len(vr)/max(len(had_v),1)
print(f"  {pct_vr:.0f}% dei trade che erano in verde hanno chiuso in negativo.")
print(f"  Profitto evaporato (verde→rosso, curva_nascita): ${evaporato:.1f}")
print(f"  Scenario oracle (chiudi tutti al picco): +${extra_picco:.1f} in più")
print()
if extra_picco > abs(reale_tot):
    print(f"  ⚡ Il sistema avrebbe guadagnato anziché perdere se chiudevi al picco.")
    print(f"     Il problema È l'uscita, non l'entrata.")
elif extra_picco > 100:
    print(f"  ⚠ C'è profitto significativo lasciato sul tavolo nell'uscita.")
    print(f"    Vale la pena ottimizzare il SALVA_VERDE e ridurre l'evaporazione.")
else:
    print(f"  Il problema non è principalmente nell'uscita — i picchi verdi sono piccoli.")
print()

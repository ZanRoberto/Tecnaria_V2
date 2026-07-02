#!/usr/bin/env python3
"""
SIMULA32 — STIAMO USCENDO NEL MOMENTO GIUSTO COI MASCHI?

Per ogni maschio (peak_nascita >= 0.5$), analizza:
  - picco massimo raggiunto (peak_nascita)
  - pnl effettivo alla chiusura (pnl_finale)
  - cattura% = pnl_finale / peak_nascita
  - dove esce: prima del picco (perde grasso), al picco, dopo (rimonta e crolla)

DOMANDE:
  1. In media catturiamo quanto % del picco?
  2. Quanti maschi escono PRIMA del picco (tagliati troppo presto)?
  3. Quanti maschi escono DOPO il picco (tenuti troppo, rimontano)?
  4. Dove nel tempo avviene il picco? A quanti secondi dall'entrata?
  5. C'è un pattern: i maschi grossi escono meglio o peggio dei piccoli?

Lancio: python3 simula32.py
"""
import sqlite3, json

DB = "/var/data/trading_data.db"

con  = sqlite3.connect(DB)
rows = con.execute(
    "SELECT peak_nascita, pnl_finale, curva_json "
    "FROM curva_nascita WHERE pnl_finale IS NOT NULL"
).fetchall()
con.close()
print(f"Trade totali: {len(rows)}")

records = []
for peak, pnlf, cj in rows:
    pk   = float(peak or 0)
    pf   = float(pnlf)
    if pk < 0.5: continue   # solo maschi (peak >= 0.5$)

    # analizza la curva
    t_picco = None; grasso_picco = None; durata = None
    try:
        pts = json.loads(cj)
        pts = [(float(p[0]), float(p[1])) for p in pts if len(p) >= 2]
        if pts:
            durata = pts[-1][0]
            # trova il punto di picco massimo nella curva
            idx_pk = max(range(len(pts)), key=lambda i: pts[i][1])
            t_picco      = pts[idx_pk][0]
            grasso_picco = pts[idx_pk][1]
    except Exception:
        pass

    if grasso_picco is None: grasso_picco = pk
    cattura_pct = (pf / pk * 100) if pk > 0 else 0

    records.append({
        "peak":         pk,
        "pnl_fin":      pf,
        "cattura_pct":  cattura_pct,
        "t_picco":      t_picco,
        "grasso_picco": grasso_picco,
        "durata":       durata,
        # tipo uscita
        "esce_prima":  pf < pk * 0.5,   # prende meno del 50% del picco
        "esce_bene":   pf >= pk * 0.7,  # prende >= 70% del picco
        "esce_negativo": pf < 0,         # rimonta e crolla sotto zero
    })

N = len(records)
print(f"Maschi (peak>=0.5$): {N}")
print()

def avg(lst, key):
    v = [r[key] for r in lst if r.get(key) is not None]
    return sum(v)/len(v) if v else 0.0

# ── PANORAMICA GENERALE ───────────────────────────────────────────────────────
print("="*68)
print("PANORAMICA — CATTURA DEL PICCO")
print("="*68)
print()
print(f"  Peak medio:          ${avg(records,'peak'):+.3f}")
print(f"  pnl_finale medio:    ${avg(records,'pnl_fin'):+.3f}")
print(f"  Cattura media:        {avg(records,'cattura_pct'):+.1f}%  (pnl / peak)")
print(f"  Netto totale:        ${sum(r['pnl_fin'] for r in records):+.2f}")
print()
print(f"  Esce prima del 50% del picco: {sum(r['esce_prima'] for r in records)} / {N}  ({100*sum(r['esce_prima'] for r in records)/N:.0f}%)")
print(f"  Esce con >= 70% del picco:    {sum(r['esce_bene']  for r in records)} / {N}  ({100*sum(r['esce_bene']  for r in records)/N:.0f}%)")
print(f"  Esce in negativo (rimonta):   {sum(r['esce_negativo'] for r in records)} / {N}  ({100*sum(r['esce_negativo'] for r in records)/N:.0f}%)")
print()

# ── DISTRIBUZIONE CATTURA% ────────────────────────────────────────────────────
print("─"*68)
print("DISTRIBUZIONE CATTURA% (pnl_finale / peak_nascita)")
print("─"*68)
fasce = [
    (-999,   0,  "negativo (crolla)"),
    (   0,  25,  "0-25%  (quasi perso)"),
    (  25,  50,  "25-50% (metà del picco)"),
    (  50,  75,  "50-75% (discreto)"),
    (  75, 100,  "75-100% (buono)"),
    ( 100, 999,  ">100%  (supera il picco)"),
]
print(f"  {'FASCIA':<28}  {'N':>4}  {'%':>5}  {'avg_pnl':>8}")
for lo, hi, lab in fasce:
    grp = [r for r in records if lo <= r["cattura_pct"] < hi]
    pct = 100*len(grp)/N if N else 0
    ap  = avg(grp, "pnl_fin")
    print(f"  {lab:<28}  {len(grp):>4}  {pct:>4.0f}%  {ap:>+8.3f}")
print()

# ── QUANDO AVVIENE IL PICCO? ──────────────────────────────────────────────────
print("─"*68)
print("QUANDO AVVIENE IL PICCO? (secondi dall'entrata)")
print("─"*68)
con_t = [r for r in records if r["t_picco"] is not None]
if con_t:
    print(f"  Tempo medio al picco: {avg(con_t,'t_picco'):.1f}s")
    print(f"  Durata media trade:   {avg(con_t,'durata'):.1f}s")
    print()
    fasce_t = [(0,5,"0-5s"),(5,15,"5-15s"),(15,30,"15-30s"),
               (30,60,"30-60s"),(60,999,"60s+")]
    print(f"  {'QUANDO picco':<14}  {'N':>4}  {'cattura%':>9}  {'pnl_avg':>8}")
    for lo,hi,lab in fasce_t:
        grp=[r for r in con_t if lo<=r["t_picco"]<hi]
        if not grp: continue
        print(f"  {lab:<14}  {len(grp):>4}  {avg(grp,'cattura_pct'):>8.1f}%  {avg(grp,'pnl_fin'):>+8.3f}")
print()

# ── PER FASCIA DI PEAK ────────────────────────────────────────────────────────
print("─"*68)
print("CATTURA PER DIMENSIONE DEL MASCHIO")
print("─"*68)
fasce_pk = [(0.5,1,"piccolo  0.5-1$"),(1,2,"medio    1-2$"),
            (2,5,"grande   2-5$"),(5,999,"enorme   >5$")]
print(f"  {'MASCHIO':<18}  {'N':>4}  {'peak_avg':>9}  {'pnl_avg':>9}  {'cattura%':>9}  {'neg%':>6}")
for lo,hi,lab in fasce_pk:
    grp=[r for r in records if lo<=r["peak"]<hi]
    if not grp: continue
    neg_pct=100*sum(1 for r in grp if r["pnl_fin"]<0)/len(grp)
    print(f"  {lab:<18}  {len(grp):>4}  {avg(grp,'peak'):>+9.3f}  "
          f"{avg(grp,'pnl_fin'):>+9.3f}  {avg(grp,'cattura_pct'):>8.1f}%  {neg_pct:>5.0f}%")
print()

# ── I MASCHI CHE DIVENTANO NEGATIVI ──────────────────────────────────────────
print("─"*68)
print("MASCHI CHE ESCONO NEGATIVI (peak>=0.5 ma pnl<0) — li perdiamo tutti")
print("─"*68)
negativi = [r for r in records if r["pnl_fin"] < 0]
print(f"  N: {len(negativi)} / {N}  ({100*len(negativi)/N:.0f}%)")
if negativi:
    print(f"  Peak medio:       ${avg(negativi,'peak'):+.3f}")
    print(f"  pnl_fin medio:    ${avg(negativi,'pnl_fin'):+.3f}")
    print(f"  t_picco medio:    {avg([r for r in negativi if r['t_picco']],'t_picco'):.1f}s")
    print(f"  Totale perso:     ${sum(r['pnl_fin'] for r in negativi):+.2f}")
    print()
    print(f"  Questi maschi salgono (peak>=0.5) poi crollano sotto zero.")
    print(f"  Il trailing stop non li ha presi in tempo.")
print()

# ── VERDETTO ──────────────────────────────────────────────────────────────────
print("="*68)
print("VERDETTO — STIAMO USCENDO BENE?")
print("="*68)
print()
cat = avg(records, "cattura_pct")
neg_pct = 100*len(negativi)/N

if cat >= 70:
    print(f"  ✓ USCITA BUONA: catturiamo in media {cat:.0f}% del picco.")
    print(f"  Il trailing stop funziona bene sui maschi.")
elif cat >= 50:
    print(f"  ~ USCITA MEDIA: catturiamo {cat:.0f}% del picco.")
    print(f"  C'è margine per migliorare l'uscita.")
else:
    print(f"  ✗ USCITA DEBOLE: catturiamo solo {cat:.0f}% del picco.")
    print(f"  Stiamo lasciando troppo grasso sul tavolo.")

print()
if neg_pct >= 20:
    print(f"  ⚠️  {neg_pct:.0f}% dei maschi escono in NEGATIVO — salgono poi crollano.")
    print(f"  Il problema principale non è uscire tardi, è che questi")
    print(f"  maschi rimontano dopo il picco e il trailing non li piglia.")
    print(f"  Soluzione: trailing più stretto, o exit al primo segno di discesa.")
elif neg_pct >= 10:
    print(f"  ~ {neg_pct:.0f}% dei maschi escono negativi — accettabile ma migliorabile.")
else:
    print(f"  ✓ Solo {neg_pct:.0f}% dei maschi escono negativi — trailing funziona.")

print()
tot_perduto = sum(r["peak"] - r["pnl_fin"] for r in records)
print(f"  Grasso totale MAI CATTURATO (peak - pnl_fin): ${tot_perduto:+.2f}")
print(f"  (Questo è il grasso che il bot ha visto ma non ha preso)")
print()

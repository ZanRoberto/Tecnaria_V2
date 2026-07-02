#!/usr/bin/env python3
"""
SIMULA34 — I 27 MASCHI CHE CROLLANO: AUTOPSIA

I maschi (peak>=0.5$) che escono negativi: salgono poi crollano sotto zero.
Il trailing stop non li prende in tempo.

DOMANDE:
  1. Come appare la curva di questi 27? Picco rapido poi crollo lento?
     O picco alto poi rimonta improvvisa?
  2. A che punto della curva erano ancora in profitto (potevamo uscire)?
  3. Se uscissimo quando il grasso scende del X% dal picco VISTO (trailing),
     quanti si salvano? Che netto?
  4. Il trailing ottimale è diverso per maschi piccoli vs grandi?
  5. Questo trailing (simulato su dati post-entrata) è implementabile live?

ATTENZIONE look-ahead: usiamo solo dati disponibili LIVE (grasso corrente,
picco visto finora) — nessun dato futuro. Il trailing è applicabile live.

Lancio: python3 simula34.py
"""
import sqlite3, json

DB = "/var/data/trading_data.db"

con  = sqlite3.connect(DB)
rows = con.execute(
    "SELECT peak_nascita, pnl_finale, curva_json "
    "FROM curva_nascita WHERE pnl_finale IS NOT NULL"
).fetchall()
con.close()

# costruisce record con curva completa
tutti = []
for peak, pnlf, cj in rows:
    pk = float(peak or 0)
    pf = float(pnlf)
    try:
        pts = [(float(p[0]), float(p[1]), float(p[2])) for p in json.loads(cj) if len(p)>=3]
    except Exception:
        pts = []
    tutti.append({"peak": pk, "pnl_fin": pf, "curva": pts})

maschi    = [r for r in tutti if r["peak"] >= 0.5]
negativi  = [r for r in maschi if r["pnl_fin"] < 0]
positivi  = [r for r in maschi if r["pnl_fin"] >= 0]

print(f"Trade totali: {len(tutti)}")
print(f"Maschi (peak>=0.5): {len(maschi)}")
print(f"  positivi: {len(positivi)}  negativi: {len(negativi)}")
print()

def avg(lst, key=None, fn=None):
    if fn: v=[fn(r) for r in lst]
    else:  v=[r[key] for r in lst if r.get(key) is not None]
    return sum(v)/len(v) if v else 0.0

# ── PARTE 1: AUTOPSIA — COME APPARE LA CURVA DEI 27 ─────────────────────────
print("="*72)
print("PARTE 1 — AUTOPSIA DEI 27 MASCHI NEGATIVI")
print("="*72)
print()

def analizza_curva(r):
    """Estrae metriche dalla curva (solo dati live — nessun look-ahead)."""
    pts = r["curva"]
    if not pts: return {}
    pnls    = [p[1] for p in pts]
    peaks   = [p[2] for p in pts]
    tempi   = [p[0] for p in pts]
    pk_max  = max(peaks)
    idx_pk  = peaks.index(pk_max)
    t_pk    = tempi[idx_pk]
    pnl_pk  = pnls[idx_pk]
    # grasso all'ultimo punto (chiusura)
    pnl_fin = pnls[-1]
    durata  = tempi[-1]
    # quanto tempo è rimasto sopra zero dopo il picco
    dopo_pk = [(tempi[i], pnls[i]) for i in range(idx_pk, len(pts))]
    t_sopra_zero = next((p[0]-t_pk for p in dopo_pk if p[1]<=0), durata-t_pk)
    # velocità del crollo: da picco a zero
    crollo = pk_max - 0  # drop in $
    vel_crollo = crollo / t_sopra_zero if t_sopra_zero > 0 else 0
    return {
        "pk_max":   pk_max,
        "t_pk":     t_pk,
        "pnl_fin":  pnl_fin,
        "durata":   durata,
        "dopo_pk_s": durata - t_pk,
        "t_sopra_zero": t_sopra_zero,
        "vel_crollo":   vel_crollo,
    }

neg_meta = [analizza_curva(r) for r in negativi]
pos_meta = [analizza_curva(r) for r in positivi]

print(f"  {'METRICA':<30}  {'NEGATIVI':>10}  {'POSITIVI':>10}  {'DIFF':>8}")
print(f"  {'-'*60}")
metriche = [
    ("Peak max ($)",       "pk_max"),
    ("Tempo al picco (s)", "t_pk"),
    ("Durata trade (s)",   "durata"),
    ("Dopo picco (s)",     "dopo_pk_s"),
    ("Tempo sopra 0 dopo picco (s)", "t_sopra_zero"),
    ("Velocità crollo ($/s)", "vel_crollo"),
]
for nome, key in metriche:
    vn = avg(neg_meta, key); vp = avg(pos_meta, key)
    flag = "  ◄◄" if abs(vn-vp)/(abs(vp)+0.001) > 0.3 else ""
    print(f"  {nome:<30}  {vn:>+10.3f}  {vp:>+10.3f}  {vn-vp:>+8.3f}{flag}")
print()

# mostra le prime 5 curve dei negativi (forma)
print("─"*72)
print("FORMA DELLE CURVE — 5 maschi negativi (picco→crollo):")
print("─"*72)
for i, r in enumerate(negativi[:5]):
    pts = r["curva"]
    if not pts: continue
    pk_max = max(p[2] for p in pts)
    print(f"\n  Trade {i+1}: peak=${r['peak']:.2f}  pnl_fin=${r['pnl_fin']:.2f}")
    # mostra punti chiave: ogni 5s e il punto di picco
    prev_t = -999
    for p in pts:
        t, g, pk = p[0], p[1], p[2]
        if t - prev_t >= 5 or abs(g - pk_max) < 0.01:
            marker = " ← PICCO" if abs(g - pk_max) < 0.05 else ""
            print(f"    t={t:5.1f}s  grasso={g:+6.3f}  peak_so_far={pk:+6.3f}{marker}")
            prev_t = t
print()

# ── PARTE 2: TRAILING STOP SIMULATO ──────────────────────────────────────────
print("="*72)
print("PARTE 2 — TRAILING STOP SIMULATO (dati live, no look-ahead)")
print("  Regola: esci quando grasso scende di X% dal picco VISTO FINORA")
print("  Questo è replicabile live: il bot vede peak_so_far ad ogni tick")
print("="*72)
print()

def simula_trailing(record, pct_drop):
    """
    Simula trailing stop: esci quando grasso cala di pct_drop% dal picco visto.
    Usa solo p[2] (peak_so_far) e p[1] (grasso live) — disponibili live.
    Ritorna il pnl all'uscita simulata.
    """
    pts = record["curva"]
    if not pts: return record["pnl_fin"]
    for t, g, pk in pts:
        if pk > 0 and g <= pk * (1 - pct_drop):
            return g  # uscita al trailing
    return pts[-1][1]  # non scattato: uscita naturale

print(f"  {'TRAILING%':>10}  {'n_maschi':>9}  {'netto_mas':>11}  "
      f"{'netto_neg':>11}  {'delta_neg':>11}  {'netto_tot':>11}")
print(f"  {'-'*70}")

netto_mas_base = sum(r["pnl_fin"] for r in maschi)
netto_neg_base = sum(r["pnl_fin"] for r in negativi)
netto_tot_base = sum(r["pnl_fin"] for r in tutti)

for pct in [0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]:
    # applica trailing a TUTTI i maschi
    pnl_mas = [simula_trailing(r, pct) for r in maschi]
    pnl_neg = [simula_trailing(r, pct) for r in negativi]
    nt_mas   = sum(pnl_mas)
    nt_neg   = sum(pnl_neg)
    # netto totale: maschi col trailing + non-maschi invariati
    nt_tot   = nt_mas + sum(r["pnl_fin"] for r in tutti if r["peak"] < 0.5)
    delta_neg = nt_neg - netto_neg_base
    flag     = "  ◄◄ MEGLIO" if nt_mas > netto_mas_base else ""
    print(f"  {pct*100:>9.0f}%  {len(maschi):>9}  {nt_mas:>+11.2f}  "
          f"{nt_neg:>+11.2f}  {delta_neg:>+11.2f}  {nt_tot:>+11.2f}{flag}")

print()
print(f"  BASELINE maschi: ${netto_mas_base:+.2f}  negativi: ${netto_neg_base:+.2f}  tot: ${netto_tot_base:+.2f}")
print()

# ── PARTE 3: TRAILING PER DIMENSIONE ─────────────────────────────────────────
print("="*72)
print("PARTE 3 — TRAILING OTTIMALE PER DIMENSIONE DEL MASCHIO")
print("="*72)
print()

categorie = [
    ("piccolo 0.5-1$",  lambda r: 0.5 <= r["peak"] < 1.0),
    ("medio   1-2$",    lambda r: 1.0 <= r["peak"] < 2.0),
    ("grande  2-5$",    lambda r: 2.0 <= r["peak"] < 5.0),
    ("enorme  >5$",     lambda r: r["peak"] >= 5.0),
]

for lab, cond in categorie:
    grp = [r for r in maschi if cond(r)]
    if not grp: continue
    base = sum(r["pnl_fin"] for r in grp)
    print(f"  {lab} (N={len(grp)}, netto base=${base:+.2f})")
    print(f"    {'trailing%':>10}  {'netto':>10}  {'delta':>8}")
    best_pct = None; best_nt = base
    for pct in [0.30, 0.40, 0.50, 0.60, 0.70, 0.80]:
        nt = sum(simula_trailing(r, pct) for r in grp)
        flag = "  ←" if nt > best_nt else ""
        if nt > best_nt: best_nt = nt; best_pct = pct
        print(f"    {pct*100:>9.0f}%  {nt:>+10.2f}  {nt-base:>+8.2f}{flag}")
    if best_pct:
        print(f"    → ottimale: {best_pct*100:.0f}% trailing")
    print()

# ── PARTE 4: I 27 — COSA AVREMMO CATTURATO ───────────────────────────────────
print("="*72)
print("PARTE 4 — I 27 NEGATIVI: COSA AVREMMO CATTURATO CON TRAILING 50%")
print("="*72)
print()
PCT = 0.50
print(f"  {'#':>3}  {'peak':>7}  {'pnl_fin':>9}  {'trailing_exit':>14}  {'guadagno':>9}")
print(f"  {'-'*52}")
tot_guadagno = 0
for i, r in enumerate(sorted(negativi, key=lambda x: x["pnl_fin"])):
    exit_pnl = simula_trailing(r, PCT)
    guadagno = exit_pnl - r["pnl_fin"]
    tot_guadagno += guadagno
    print(f"  {i+1:>3}  {r['peak']:>+7.3f}  {r['pnl_fin']:>+9.3f}  "
          f"{exit_pnl:>+14.3f}  {guadagno:>+9.3f}")
print(f"  {'-'*52}")
print(f"  {'TOTALE':>3}  {'':>7}  {netto_neg_base:>+9.3f}  "
      f"  {sum(simula_trailing(r,PCT) for r in negativi):>+14.3f}  {tot_guadagno:>+9.3f}")
print()

# ── VERDETTO ─────────────────────────────────────────────────────────────────
print("="*72)
print("VERDETTO — IL TRAILING MIGLIORA IL SISTEMA?")
print("="*72)
print()

# trova il trailing che massimizza il netto totale
best_pct_tot = None; best_nt_tot = netto_tot_base
for pct in [x/100 for x in range(20,95,5)]:
    nt_mas = sum(simula_trailing(r, pct) for r in maschi)
    nt_tot = nt_mas + sum(r["pnl_fin"] for r in tutti if r["peak"] < 0.5)
    if nt_tot > best_nt_tot:
        best_nt_tot = nt_tot; best_pct_tot = pct

print(f"  Netto totale baseline: ${netto_tot_base:+.2f}")
if best_pct_tot:
    best_nt_mas = sum(simula_trailing(r, best_pct_tot) for r in maschi)
    print(f"  Trailing ottimale globale: {best_pct_tot*100:.0f}%")
    print(f"  Netto maschi col trailing: ${best_nt_mas:+.2f}  (vs ${netto_mas_base:+.2f})")
    print(f"  Netto totale col trailing: ${best_nt_tot:+.2f}  (delta: ${best_nt_tot-netto_tot_base:+.2f})")
    print()
    print(f"  IMPLEMENTAZIONE (se il trailing regge i 3 test):")
    print(f"  ENV TRAILING_MASCHIO_PCT={best_pct_tot:.2f} nel bot:")
    print(f"  quando peak_so_far > 0.5$ E grasso_live < peak_so_far * {1-best_pct_tot:.2f} → esci")
    print(f"  Questo usa SOLO dati live — nessun look-ahead.")
else:
    print(f"  Nessun trailing migliora il netto totale.")
    print(f"  I 27 maschi negativi crollano troppo velocemente per il trailing.")
    print(f"  Problema strutturale: il bot non reagisce abbastanza veloce al crollo.")
print()

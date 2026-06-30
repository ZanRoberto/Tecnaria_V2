#!/usr/bin/env python3
"""
SIMULA17 — FILTRO PNL_10S: SIMULAZIONE CORRETTA + WALK-FORWARD

CHIARIMENTO ARCHITETTURALE:
  pnl_a_10s è misurato DOPO l'ingresso (fee già pagata).
  Il filtro NON è "non entrare mai" — è "esci a 10 secondi se pnl<soglia".

SIMULAZIONE CORRETTA:
  Per ogni trade:
    - Se pnl_10s < soglia → esci a 10s (pnl_sim = pnl_10s)
    - Altrimenti → lascia correre fino a pnl_finale (pnl_sim = pnl_finale)

  DELTA reale = sum(pnl_sim) - sum(pnl_reale)
    = per i trade filtrati: (pnl_10s - pnl_finale) — esce prima del danno
    = per i trade non filtrati: 0 — non cambia nulla

VERIFICA FEE:
  pnl_a_10s e pnl_finale sono già NETTI (grasso = lordo - $2 fee embedded).
  Nessuna fee aggiuntiva da sottrarre.

WALK-FORWARD:
  Divide i trade per data (trade_ts) in blocchi e testa in ogni blocco.

Lancio: python3 simula17.py
"""
import sqlite3, json
from datetime import datetime
from collections import defaultdict

DB = "/var/data/trading_data.db"

# ── carica dati ─────────────────────────────────────────────────────────────
con = sqlite3.connect(DB)
rows = con.execute(
    "SELECT trade_ts, pnl_a_10s, curva_json, pnl_finale "
    "FROM curva_nascita WHERE pnl_finale IS NOT NULL"
).fetchall()
con.close()

records = []
for trade_ts, pnl10s, cj, pnlf in rows:
    try:
        pts    = json.loads(cj)
        grassi = [float(p[1]) for p in pts if len(p) >= 2]
        if len(grassi) < 2:
            continue
        mfe = max(grassi)
        pnl_10s_val = float(pnl10s) if pnl10s is not None else grassi[min(2, len(grassi)-1)]
        ts_float = float(trade_ts) if trade_ts else 0.0
        records.append({
            "ts":       ts_float,
            "mfe":      mfe,
            "pnl_real": float(pnlf),
            "pnl_10s":  pnl_10s_val,
        })
    except Exception:
        pass

records.sort(key=lambda r: r["ts"])
N         = len(records)
tot_reale = sum(r["pnl_real"] for r in records)
nwin_r    = sum(1 for r in records if r["pnl_real"] > 0)

# date range
ts_min = records[0]["ts"]  if records else 0
ts_max = records[-1]["ts"] if records else 0
d_min  = datetime.utcfromtimestamp(ts_min).strftime("%Y-%m-%d") if ts_min else "?"
d_max  = datetime.utcfromtimestamp(ts_max).strftime("%Y-%m-%d") if ts_max else "?"

print("=" * 72)
print("SIMULA17 — FILTRO PNL_10S: SIMULAZIONE CORRETTA + WALK-FORWARD")
print("=" * 72)
print()
print(f"Trade totali: {N}  ({d_min} → {d_max})")
print(f"Netto REALE:  ${tot_reale:+.1f}  ({nwin_r}/{N} win = {100*nwin_r/N:.0f}%)")
print()
print("CHIARIMENTO FEE:")
print("  pnl_a_10s e pnl_finale sono già NETTI (fee $2 embedded nel grasso).")
print("  La simulazione è già corretta — nessuna fee aggiuntiva.")
print()
print("CHIARIMENTO MECCANISMO:")
print("  Il bot ENTRA sempre (fee pagata). A 10 secondi misura pnl_10s.")
print("  Il filtro = 'se pnl_10s < soglia, ESCI SUBITO a 10s'.")
print("  Per i trade NON filtrati: nessun cambiamento, escono normalmente.")
print()

# ── simulazione filtro a 10s ────────────────────────────────────────────────
def simula_filtro_10s(records, soglia):
    """
    Esce a 10s se pnl_10s < soglia.
    Per i trade filtrati: pnl_sim = pnl_10s (esci prima)
    Per gli altri: pnl_sim = pnl_finale (nessuna variazione)
    """
    ris = []
    for r in records:
        if r["pnl_10s"] < soglia:
            pnl_sim = r["pnl_10s"]
            filtrato = True
        else:
            pnl_sim = r["pnl_real"]
            filtrato = False
        ris.append({
            "pnl_real": r["pnl_real"],
            "pnl_sim":  pnl_sim,
            "pnl_10s":  r["pnl_10s"],
            "mfe":      r["mfe"],
            "filtrato": filtrato,
        })
    return ris

# ── test soglie ──────────────────────────────────────────────────────────────
soglie = [0.0, -0.1, -0.2, -0.3, -0.5, -0.7, -1.0]

print("=" * 72)
print("RISULTATI PER SOGLIA (su tutto lo storico)")
print("=" * 72)
print()
print(f"  {'SOGLIA':>8}  {'delta$':>8}  {'tot_sim':>9}  {'WR%':>6}  "
      f"{'filtrati':>9}  {'di cui neu%':>11}  {'di cui verd%':>12}")
print(f"  {'-'*76}")

migliore_d = None
migliore_s = None

for soglia in soglie:
    ris = simula_filtro_10s(records, soglia)
    ts  = sum(r["pnl_sim"] for r in ris)
    d   = ts - tot_reale
    wr  = 100 * sum(1 for r in ris if r["pnl_sim"] > 0) / N
    nf  = sum(1 for r in ris if r["filtrato"])
    nf_neu  = sum(1 for r in ris if r["filtrato"] and r["mfe"] < 0.5)
    nf_verd = sum(1 for r in ris if r["filtrato"] and r["mfe"] >= 0.5)
    pct_neu  = 100*nf_neu/nf  if nf else 0
    pct_verd = 100*nf_verd/nf if nf else 0
    mk = ""
    if migliore_d is None or d > migliore_d:
        migliore_d = d
        migliore_s = soglia
        mk = " ◄ BEST"
    print(f"  {soglia:>8.1f}  {d:>+8.1f}  {ts:>+9.1f}  {wr:>5.0f}%  "
          f"{nf:>9}  {pct_neu:>10.0f}%  {pct_verd:>11.0f}%{mk}")

print()

# ── dettaglio soglia migliore ────────────────────────────────────────────────
print("=" * 72)
print(f"DETTAGLIO — soglia migliore: pnl_10s < {migliore_s}")
print("=" * 72)
print()
ris_best = simula_filtro_10s(records, migliore_s)
filtrati  = [r for r in ris_best if r["filtrato"]]
rimasti   = [r for r in ris_best if not r["filtrato"]]
f_neu  = [r for r in filtrati if r["mfe"] < 0.5]
f_verd = [r for r in filtrati if r["mfe"] >= 0.5]

print(f"  Trade filtrati (esci a 10s):  {len(filtrati)}")
print(f"    di cui NEUTRI (MFE<0.5):    {len(f_neu)}  — evitato  ${-sum(r['pnl_real']-r['pnl_sim'] for r in f_neu):+.1f} di danno")
print(f"    di cui VERDI  (MFE≥0.5):    {len(f_verd)}  — pagato  ${sum(r['pnl_real']-r['pnl_sim'] for r in f_verd):+.1f} di costo")
print(f"  Trade lasciati correre:        {len(rimasti)}")
print()

ben = sum(r["pnl_sim"] - r["pnl_real"] for r in filtrati if r["pnl_sim"] > r["pnl_real"])
cos = sum(r["pnl_real"] - r["pnl_sim"] for r in filtrati if r["pnl_sim"] < r["pnl_real"])
tot_sim_b = sum(r["pnl_sim"] for r in ris_best)
print(f"  Beneficio (evitato perdita):  +${ben:.1f}")
print(f"  Costo (tagliato presto):      -${cos:.1f}")
print(f"  SALDO NETTO:                  {ben-cos:+.1f}$")
print()
print(f"  Netto REALE:    ${tot_reale:+.1f}")
print(f"  Netto FILTRATO: ${tot_sim_b:+.1f}  ({tot_sim_b-tot_reale:+.1f}$ vs reale)")

# ── WALK-FORWARD ─────────────────────────────────────────────────────────────
print()
print("=" * 72)
print("WALK-FORWARD — robustezza per periodo")
print("=" * 72)
print()

if ts_max - ts_min < 3600 * 24 * 14:
    print("  ATTENZIONE: meno di 2 settimane di dati — walk-forward non affidabile.")
    print(f"  Range dati: {d_min} → {d_max}  ({N} trade)")
    print()
else:
    # dividi in blocchi uguali per numero di trade (più robusto che per tempo)
    n_blocchi = 3 if N >= 60 else 2
    size = N // n_blocchi
    blocchi = []
    for i in range(n_blocchi):
        start = i * size
        end   = (i + 1) * size if i < n_blocchi - 1 else N
        blocchi.append(records[start:end])

    print(f"  Soglia testata: pnl_10s < {migliore_s}")
    print()
    print(f"  {'BLOCCO':<10}  {'N':>4}  {'periodo':>22}  "
          f"{'netto_r':>9}  {'netto_s':>9}  {'delta':>8}  {'esito':>8}")
    print(f"  {'-'*76}")

    tutti_pos = True
    for i, bl in enumerate(blocchi):
        r_bl = simula_filtro_10s(bl, migliore_s)
        tr   = sum(r["pnl_real"] for r in r_bl)
        ts_  = sum(r["pnl_sim"]  for r in r_bl)
        d_   = ts_ - tr
        d_i  = datetime.utcfromtimestamp(bl[0]["ts"]).strftime("%d/%m/%y")
        d_f  = datetime.utcfromtimestamp(bl[-1]["ts"]).strftime("%d/%m/%y")
        esito = "POSITIVO" if d_ > 0 else "NEGATIVO"
        if d_ <= 0:
            tutti_pos = False
        print(f"  blocco {i+1:<4}  {len(bl):>4}  {d_i} → {d_f}  "
              f"{tr:>+9.1f}  {ts_:>+9.1f}  {d_:>+8.1f}  {esito:>8}")

    print()
    if tutti_pos:
        print(f"  ✓ ROBUSTO: positivo in tutti i {n_blocchi} blocchi.")
    else:
        print(f"  ✗ NON ROBUSTO: negativo in almeno un blocco.")
        print(f"    Il risultato potrebbe essere specifico di un periodo.")

# ── verdetto finale ───────────────────────────────────────────────────────────
print()
print("=" * 72)
print("VERDETTO FINALE")
print("=" * 72)
print()
print(f"  Meccanismo: ENTRA sempre, ESCI a 10s se pnl_10s < {migliore_s}$")
print(f"  Fee: già incluse nel pnl (grasso = netto, nessuna fee aggiuntiva)")
print()
print(f"  Netto REALE:    ${tot_reale:+.1f}")
print(f"  Netto FILTRATO: ${tot_sim_b:+.1f}  (delta: {tot_sim_b-tot_reale:+.1f}$)")
print()
if tot_sim_b - tot_reale > 0:
    print(f"  ✓ Il filtro a 10s migliora il netto di ${tot_sim_b-tot_reale:.1f}")
    if tot_sim_b > 0:
        print(f"  ✓ Il sistema diventa POSITIVO (${tot_sim_b:+.1f})")
    else:
        print(f"  ~ Resta negativo ma si avvicina al pareggio")
    print()
    print(f"  IMPLEMENTAZIONE: a 10s dall'ingresso, se pnl_live < {migliore_s}$")
    print(f"  → chiudi subito (STOP_10S)")
    print(f"  → ENV: STOP_10S_USD={migliore_s}  STOP_10S_ENABLED=true")
else:
    print(f"  ✗ Il filtro a 10s NON migliora il netto (delta {tot_sim_b-tot_reale:+.1f}$)")
    print(f"    Anche uscire presto dai neutri non basta.")
print()

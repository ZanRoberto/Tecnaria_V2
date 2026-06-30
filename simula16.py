#!/usr/bin/env python3
"""
SIMULA16 — FIRMA D'INGRESSO: NEUTRO vs VERDE

Domanda: i 379 trade che non vanno MAI in verde (NEUTRO, MFE<0.5)
         hanno una firma d'ingresso diversa dai trade che invece vanno in verde?

DATI USATI:
  curva_nascita → pnl_a_10s, t_peak_s, firma, trade_ts (ora del giorno)
  trades (JOIN via trade_ts) → momentum, volatility, trend, regime, direction, sesso, score

METRICA: distribuzione di queste variabili nel gruppo NEUTRO vs VERDE
         Se differiscono → c'è un filtro all'ingresso da applicare.

Lancio: python3 simula16.py
"""
import sqlite3, json
from collections import defaultdict, Counter
from datetime import datetime

DB = "/var/data/trading_data.db"

# ── schema discovery ────────────────────────────────────────────────────────
con = sqlite3.connect(DB)
cols_cn = [r[1] for r in con.execute("PRAGMA table_info(curva_nascita)").fetchall()]
cols_tr = [r[1] for r in con.execute("PRAGMA table_info(trades)").fetchall()]
print("curva_nascita columns:", cols_cn)
print("trades columns:       ", cols_tr)
print()

# ── carica curva_nascita ────────────────────────────────────────────────────
rows = con.execute(
    "SELECT trade_ts, firma, peak_nascita, t_peak_s, pnl_a_10s, curva_json, pnl_finale "
    "FROM curva_nascita WHERE pnl_finale IS NOT NULL"
).fetchall()

# ── carica trades M2_EXIT → dict {trade_ts: data_json} ─────────────────────
# trova la colonna timestamp nelle trades
ts_col = None
for c in ["trade_ts", "ts", "timestamp", "entry_ts", "created_at"]:
    if c in cols_tr:
        ts_col = c
        break

trades_meta = {}
if ts_col:
    tr_rows = con.execute(
        f"SELECT {ts_col}, data_json FROM trades WHERE event_type='M2_EXIT'"
    ).fetchall()
    for ts, dj in tr_rows:
        try:
            trades_meta[float(ts)] = json.loads(dj) if dj else {}
        except Exception:
            pass
    print(f"Trades M2_EXIT con data_json: {len(trades_meta)}")
else:
    print("ATTENZIONE: trade_ts non trovato in trades — skip JOIN")
print()
con.close()

# ── costruisce dataset ──────────────────────────────────────────────────────
trades = []
for trade_ts, firma, peak_n, t_peak, pnl10s, cj, pnlf in rows:
    try:
        pts    = json.loads(cj)
        grassi = [float(p[1]) for p in pts if len(p) >= 2]
        if len(grassi) < 2:
            continue
        mfe = max(grassi)

        # ora del giorno dall'epoch timestamp
        try:
            ora = datetime.utcfromtimestamp(float(trade_ts)).hour if trade_ts else -1
        except Exception:
            ora = -1

        # metadati da trades (se join disponibile)
        meta = {}
        if ts_col and trade_ts:
            # cerca il match più vicino (tolleranza 2 secondi)
            ts_f = float(trade_ts)
            for k in trades_meta:
                if abs(k - ts_f) < 2.0:
                    meta = trades_meta[k]
                    break

        # pnl_a_10s dalla curva se non in colonna dedicata
        pnl_10s_val = float(pnl10s) if pnl10s is not None else (grassi[2] if len(grassi) > 2 else grassi[-1])

        # velocità iniziale: pendenza primi 3 tick
        vel_iniziale = grassi[min(2, len(grassi)-1)] - grassi[0]

        trades.append({
            "mfe":        mfe,
            "pnl_real":   float(pnlf),
            "pnl_10s":    pnl_10s_val,
            "t_peak":     float(t_peak) if t_peak else None,
            "firma":      firma or "",
            "ora":        ora,
            "vel_iniz":   vel_iniziale,
            "momentum":   meta.get("momentum", "?"),
            "volatility": meta.get("volatility", "?"),
            "trend":      meta.get("trend", "?"),
            "regime":     meta.get("regime", "?"),
            "direction":  meta.get("direction", "?"),
            "sesso":      meta.get("sesso", "?"),
            "score":      float(meta.get("score", 0) or 0),
        })
    except Exception:
        pass

N = len(trades)
neutro = [t for t in trades if t["mfe"] < 0.5]
verde  = [t for t in trades if t["mfe"] >= 0.5]

print("=" * 72)
print("SIMULA16 — FIRMA D'INGRESSO: NEUTRO vs VERDE")
print("=" * 72)
print(f"Trade totali: {N}")
print(f"  NEUTRO (MFE<0.5): {len(neutro)} — {100*len(neutro)/N:.0f}%")
print(f"  VERDE  (MFE≥0.5): {len(verde)} — {100*len(verde)/N:.0f}%")
print()

# ── helper: confronto distribuzione categorica ──────────────────────────────
def confronta_cat(nome, key, gruppi):
    vals_n = [t[key] for t in gruppi[0] if t[key] != "?"]
    vals_v = [t[key] for t in gruppi[1] if t[key] != "?"]
    if not vals_n and not vals_v:
        return
    tutte = sorted(set(vals_n + vals_v))
    print(f"  {nome}:")
    for v in tutte:
        pct_n = 100 * vals_n.count(v) / len(vals_n) if vals_n else 0
        pct_v = 100 * vals_v.count(v) / len(vals_v) if vals_v else 0
        diff  = pct_n - pct_v
        flag  = "  ◄ DIFF" if abs(diff) >= 10 else ""
        print(f"    {v:<15}  NEUTRO {pct_n:>4.0f}%  VERDE {pct_v:>4.0f}%  ({diff:>+5.0f}pp){flag}")
    print()

def confronta_num(nome, vals_n, vals_v):
    if not vals_n or not vals_v:
        return
    avg_n = sum(vals_n) / len(vals_n)
    avg_v = sum(vals_v) / len(vals_v)
    med_n = sorted(vals_n)[len(vals_n)//2]
    med_v = sorted(vals_v)[len(vals_v)//2]
    print(f"  {nome}:")
    print(f"    NEUTRO  media={avg_n:>+.2f}  mediana={med_n:>+.2f}")
    print(f"    VERDE   media={avg_v:>+.2f}  mediana={med_v:>+.2f}")
    diff = avg_n - avg_v
    flag = "  ◄ DIFF SIGNIFICATIVA" if abs(diff) > 0.3 else ""
    print(f"    DIFF    {diff:>+.2f}{flag}")
    print()

gruppi = [neutro, verde]

# ── 1. pnl a 10 secondi ────────────────────────────────────────────────────
print("─" * 72)
print("1. PNL A 10 SECONDI (cosa fa il trade nei primi 10s)")
print("─" * 72)
confronta_num("pnl_10s ($)",
    [t["pnl_10s"] for t in neutro if t["pnl_10s"] is not None],
    [t["pnl_10s"] for t in verde  if t["pnl_10s"] is not None])

# distribuzione pnl_10s in fasce
print("  Distribuzione pnl_10s:")
fasce_10s = [(-99, -1.0, "<-1$"), (-1.0, -0.5, "-1→-0.5$"), (-0.5, 0.0, "-0.5→0$"),
             (0.0, 0.5, "0→0.5$"), (0.5, 1.5, "0.5→1.5$"), (1.5, 99, ">1.5$")]
print(f"    {'fascia':<12}  {'NEUTRO':>8}  {'VERDE':>8}")
for lo, hi, lab in fasce_10s:
    n_n = sum(1 for t in neutro if t["pnl_10s"] is not None and lo <= t["pnl_10s"] < hi)
    n_v = sum(1 for t in verde  if t["pnl_10s"] is not None and lo <= t["pnl_10s"] < hi)
    pn = 100*n_n/len(neutro) if neutro else 0
    pv = 100*n_v/len(verde) if verde else 0
    flag = "  ◄" if abs(pn-pv) >= 15 else ""
    print(f"    {lab:<12}  {pn:>7.0f}%  {pv:>7.0f}%{flag}")
print()

# ── 2. velocità iniziale ───────────────────────────────────────────────────
print("─" * 72)
print("2. VELOCITÀ INIZIALE (grasso tick3 - tick1)")
print("─" * 72)
confronta_num("vel_iniziale",
    [t["vel_iniz"] for t in neutro],
    [t["vel_iniz"] for t in verde])

# ── 3. ora del giorno ──────────────────────────────────────────────────────
print("─" * 72)
print("3. ORA DEL GIORNO (UTC)")
print("─" * 72)
ore_n = Counter(t["ora"] for t in neutro if t["ora"] >= 0)
ore_v = Counter(t["ora"] for t in verde  if t["ora"] >= 0)
if ore_n or ore_v:
    # raggruppa per sessione
    sessioni = {
        "notte  (00-07 UTC)": list(range(0, 7)),
        "europa (07-15 UTC)": list(range(7, 15)),
        "usa    (15-23 UTC)": list(range(15, 24)),
    }
    tot_n = sum(ore_n.values()) or 1
    tot_v = sum(ore_v.values()) or 1
    print(f"  {'sessione':<22}  {'NEUTRO':>8}  {'VERDE':>8}")
    for nome_s, ore in sessioni.items():
        n_n = sum(ore_n.get(h, 0) for h in ore)
        n_v = sum(ore_v.get(h, 0) for h in ore)
        pn = 100*n_n/tot_n
        pv = 100*n_v/tot_v
        flag = "  ◄ DIFF" if abs(pn-pv) >= 10 else ""
        print(f"  {nome_s:<22}  {pn:>7.0f}%  {pv:>7.0f}%{flag}")
    print()

# ── 4. firma d'ingresso ────────────────────────────────────────────────────
print("─" * 72)
print("4. FIRMA D'INGRESSO (dal campo 'firma' di curva_nascita)")
print("─" * 72)
firme_n = Counter(t["firma"] for t in neutro if t["firma"])
firme_v = Counter(t["firma"] for t in verde  if t["firma"])
tutte_f = set(list(firme_n.keys()) + list(firme_v.keys()))
if tutte_f and len(tutte_f) < 30:
    tot_n = sum(firme_n.values()) or 1
    tot_v = sum(firme_v.values()) or 1
    print(f"  {'firma':<30}  {'NEUTRO':>8}  {'VERDE':>8}")
    for f in sorted(tutte_f, key=lambda x: -firme_n.get(x,0)):
        pn = 100*firme_n.get(f,0)/tot_n
        pv = 100*firme_v.get(f,0)/tot_v
        flag = "  ◄ DIFF" if abs(pn-pv) >= 10 else ""
        print(f"  {f:<30}  {pn:>7.0f}%  {pv:>7.0f}%{flag}")
    print()
elif tutte_f:
    print(f"  Firme uniche: {len(tutte_f)} (troppo variabili per lista)")
    # estrai componenti (format: A|B|C)
    print("  Componente 1 (es. momentum):")
    c1_n = Counter(f.split("|")[0] for f in (t["firma"] for t in neutro if "|" in t["firma"]))
    c1_v = Counter(f.split("|")[0] for f in (t["firma"] for t in verde  if "|" in t["firma"]))
    for v in sorted(set(list(c1_n)+list(c1_v))):
        pn = 100*c1_n.get(v,0)/max(sum(c1_n.values()),1)
        pv = 100*c1_v.get(v,0)/max(sum(c1_v.values()),1)
        flag = "  ◄ DIFF" if abs(pn-pv) >= 10 else ""
        print(f"    {v:<15}  NEUTRO {pn:>4.0f}%  VERDE {pv:>4.0f}%{flag}")
    print("  Componente 2 (es. volatility):")
    c2_n = Counter(f.split("|")[1] for f in (t["firma"] for t in neutro if len(f.split("|"))>1))
    c2_v = Counter(f.split("|")[1] for f in (t["firma"] for t in verde  if len(f.split("|"))>1))
    for v in sorted(set(list(c2_n)+list(c2_v))):
        pn = 100*c2_n.get(v,0)/max(sum(c2_n.values()),1)
        pv = 100*c2_v.get(v,0)/max(sum(c2_v.values()),1)
        flag = "  ◄ DIFF" if abs(pn-pv) >= 10 else ""
        print(f"    {v:<15}  NEUTRO {pn:>4.0f}%  VERDE {pv:>4.0f}%{flag}")
    print()

# ── 5. dati da JOIN con trades (se disponibile) ───────────────────────────
n_con_meta = sum(1 for t in trades if t["momentum"] != "?")
print("─" * 72)
print(f"5. METADATI DA TRADES (JOIN via trade_ts) — {n_con_meta}/{N} trade con dati")
print("─" * 72)
if n_con_meta > 10:
    neutro_m = [t for t in neutro if t["momentum"] != "?"]
    verde_m  = [t for t in verde  if t["momentum"] != "?"]
    if neutro_m and verde_m:
        confronta_cat("Momentum",   "momentum",   [neutro_m, verde_m])
        confronta_cat("Volatility", "volatility", [neutro_m, verde_m])
        confronta_cat("Trend",      "trend",       [neutro_m, verde_m])
        confronta_cat("Regime",     "regime",      [neutro_m, verde_m])
        confronta_cat("Direction",  "direction",   [neutro_m, verde_m])
        confronta_cat("Sesso",      "sesso",       [neutro_m, verde_m])
        confronta_num("Score",
            [t["score"] for t in neutro_m],
            [t["score"] for t in verde_m])
else:
    print("  (dati insufficienti — probabile mancanza di trade_ts in trades)")
    print()

# ── 6. t_peak: quando raggiunge il picco ──────────────────────────────────
print("─" * 72)
print("6. T_PEAK — dopo quanti secondi raggiunge il picco")
print("─" * 72)
tp_n = [t["t_peak"] for t in neutro if t["t_peak"] is not None and t["t_peak"] > 0]
tp_v = [t["t_peak"] for t in verde  if t["t_peak"] is not None and t["t_peak"] > 0]
confronta_num("t_peak (secondi)", tp_n, tp_v)

# ── verdetto ──────────────────────────────────────────────────────────────
print("=" * 72)
print("VERDETTO — NEUTRO vs VERDE: sono distinguibili all'ingresso?")
print("=" * 72)
print()
print("Cerca i ◄ DIFF sopra. Se ci sono differenze >= 10-15pp,")
print("esiste un filtro applicabile PRIMA di entrare.")
print()
print("Campi più promettenti da guardare:")
print("  - pnl_10s: se negativo già a 10s → molto probabile NEUTRO")
print("  - firma/momentum/volatility: combinazioni tossiche")
print("  - ora del giorno: sessioni con più neutri")
print()

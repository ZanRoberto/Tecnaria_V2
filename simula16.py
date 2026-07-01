#!/usr/bin/env python3
"""
SIMULA16 — CONTESTO D'INGRESSO: NEUTRO vs VERDE

379 trade neutri (MFE<0.5) perdono -$974. Esistono prima dell'apertura
segnali di contesto che li distinguono dai 128 trade che vanno in verde?

ANALISI:
  1. Regime (ranging/trend) al momento dell'apertura
  2. Volatilità all'ingresso
  3. Ora del giorno
  4. Momentum / trend / direction
  5. pnl_a_10s (proxy velocità iniziale)

PER OGNI FILTRO CANDIDATO:
  - neutri eliminati (e $ guadagnati evitando quelle perdite)
  - verdi persi (e $ persi saltando quei trade)
  - saldo netto $: se positivo il filtro vale

Lancio: python3 simula16.py
"""
import sqlite3, json
from collections import Counter
from datetime import datetime

DB = "/var/data/trading_data.db"

# ── schema ──────────────────────────────────────────────────────────────────
con = sqlite3.connect(DB)
cols_cn = [r[1] for r in con.execute("PRAGMA table_info(curva_nascita)").fetchall()]
cols_tr = [r[1] for r in con.execute("PRAGMA table_info(trades)").fetchall()]

# ── carica curva_nascita ────────────────────────────────────────────────────
rows = con.execute(
    "SELECT trade_ts, firma, peak_nascita, t_peak_s, pnl_a_10s, curva_json, pnl_finale "
    "FROM curva_nascita WHERE pnl_finale IS NOT NULL"
).fetchall()

# ── carica trades → dict {trade_ts_approx: data_json} ──────────────────────
ts_col = next((c for c in ["trade_ts","ts","timestamp","entry_ts"] if c in cols_tr), None)
trades_meta = {}
if ts_col:
    for ts, dj in con.execute(
            f"SELECT {ts_col}, data_json FROM trades WHERE event_type='M2_EXIT'"):
        try:
            trades_meta[round(float(ts), 1)] = json.loads(dj) if dj else {}
        except Exception:
            pass
con.close()

# ── costruisce dataset ──────────────────────────────────────────────────────
records = []
for trade_ts, firma, peak_n, t_peak, pnl10s, cj, pnlf in rows:
    try:
        pts    = json.loads(cj)
        grassi = [float(p[1]) for p in pts if len(p) >= 2]
        if len(grassi) < 2:
            continue
        mfe = max(grassi)

        ora = -1
        try:
            ora = datetime.utcfromtimestamp(float(trade_ts)).hour
        except Exception:
            pass

        # JOIN con trades via trade_ts (tolleranza 2s)
        meta = {}
        if ts_col and trade_ts:
            ts_f = round(float(trade_ts), 1)
            for delta in [0, 1, -1, 2, -2, 3, -3, 4, -4, 5, -5]:
                k = round(ts_f + delta * 0.1, 1)
                if k in trades_meta:
                    meta = trades_meta[k]
                    break
            if not meta:
                for k in trades_meta:
                    if abs(k - ts_f) < 3.0:
                        meta = trades_meta[k]
                        break

        pnl_10s = float(pnl10s) if pnl10s is not None else grassi[min(2,len(grassi)-1)]

        # estrai componenti firma (formato momentum|volatility|trend|regime)
        f_parts = (firma or "").split("|")

        records.append({
            "mfe":        mfe,
            "pnl_real":   float(pnlf),
            "pnl_10s":    pnl_10s,
            "ora":        ora,
            "firma":      firma or "",
            "f0":         f_parts[0] if len(f_parts) > 0 else "?",  # momentum
            "f1":         f_parts[1] if len(f_parts) > 1 else "?",  # volatility
            "f2":         f_parts[2] if len(f_parts) > 2 else "?",  # trend
            "f3":         f_parts[3] if len(f_parts) > 3 else "?",  # regime
            "momentum":   meta.get("momentum",   "?"),
            "volatility": meta.get("volatility", "?"),
            "trend":      meta.get("trend",      "?"),
            "regime":     meta.get("regime",     "?"),
            "direction":  meta.get("direction",  "?"),
            "sesso":      meta.get("sesso",      "?"),
            "score":      float(meta.get("score", 0) or 0),
        })
    except Exception:
        pass

N      = len(records)
neutro = [r for r in records if r["mfe"] < 0.5]
verde  = [r for r in records if r["mfe"] >= 0.5]

avg_pnl_n = sum(r["pnl_real"] for r in neutro) / len(neutro) if neutro else 0
avg_pnl_v = sum(r["pnl_real"] for r in verde)  / len(verde)  if verde  else 0
tot_reale  = sum(r["pnl_real"] for r in records)

print("=" * 72)
print("SIMULA16 — CONTESTO D'INGRESSO: NEUTRO vs VERDE")
print("=" * 72)
print(f"Trade totali: {N}  |  NEUTRO: {len(neutro)}  |  VERDE: {len(verde)}")
print(f"PnL medio NEUTRO: ${avg_pnl_n:+.2f}  |  PnL medio VERDE: ${avg_pnl_v:+.2f}")
print(f"JOIN con trades: {sum(1 for r in records if r['regime']!='?')} trade con regime/metadati")
print()

# ── funzione confronto ──────────────────────────────────────────────────────
def confronta(nome, fn_key, show_max=8):
    vals_n = [fn_key(r) for r in neutro if fn_key(r) not in ("?","")]
    vals_v = [fn_key(r) for r in verde  if fn_key(r) not in ("?","")]
    if not vals_n and not vals_v:
        print(f"  {nome}: (nessun dato)\n")
        return
    cnt_n = Counter(vals_n)
    cnt_v = Counter(vals_v)
    tot_n = len(vals_n) or 1
    tot_v = len(vals_v) or 1
    tutte = sorted(set(list(cnt_n)+list(cnt_v)), key=lambda x: -cnt_n.get(x,0))[:show_max]
    print(f"  {nome}:")
    print(f"    {'valore':<18}  {'NEUTRO':>8}  {'VERDE':>7}  {'DIFF':>6}")
    for v in tutte:
        pn = 100*cnt_n.get(v,0)/tot_n
        pv = 100*cnt_v.get(v,0)/tot_v
        flag = "  ◄" if abs(pn-pv) >= 10 else ""
        print(f"    {str(v):<18}  {pn:>7.0f}%  {pv:>6.0f}%  {pn-pv:>+5.0f}pp{flag}")
    print()

# ── 1-5: confronto variabili ────────────────────────────────────────────────
print("─" * 72)
print("CONFRONTO VARIABILI DI CONTESTO")
print("─" * 72)
print()

# Regime (da JOIN o da firma)
usa_join = sum(1 for r in records if r["regime"] != "?") > 20
if usa_join:
    confronta("REGIME (JOIN trades)", lambda r: r["regime"])
    confronta("VOLATILITY (JOIN)",    lambda r: r["volatility"])
    confronta("MOMENTUM (JOIN)",      lambda r: r["momentum"])
    confronta("TREND (JOIN)",         lambda r: r["trend"])
    confronta("DIRECTION (JOIN)",     lambda r: r["direction"])
    confronta("SESSO (JOIN)",         lambda r: r["sesso"])
else:
    print("  JOIN non disponibile — uso firma da curva_nascita")
    print()
    confronta("FIRMA comp.1 (momentum?)",  lambda r: r["f0"])
    confronta("FIRMA comp.2 (volatility?)",lambda r: r["f1"])
    confronta("FIRMA comp.3 (trend?)",     lambda r: r["f2"])
    confronta("FIRMA comp.4 (regime?)",    lambda r: r["f3"])

# Ora del giorno
print("  ORA DEL GIORNO (UTC):")
sessioni = [("notte 00-06",range(0,7)),("europa 07-14",range(7,15)),
            ("usa 15-21",range(15,22)),("notte 22-23",range(22,24))]
print(f"    {'sessione':<16}  {'NEUTRO':>8}  {'VERDE':>7}  {'DIFF':>6}")
tot_on = sum(1 for r in neutro if r["ora"]>=0) or 1
tot_ov = sum(1 for r in verde  if r["ora"]>=0) or 1
for nome_s, ore in sessioni:
    n = sum(1 for r in neutro if r["ora"] in ore)
    v = sum(1 for r in verde  if r["ora"] in ore)
    pn, pv = 100*n/tot_on, 100*v/tot_ov
    flag = "  ◄" if abs(pn-pv)>=10 else ""
    print(f"    {nome_s:<16}  {pn:>7.0f}%  {pv:>6.0f}%  {pn-pv:>+5.0f}pp{flag}")
print()

# pnl_10s
print("  PNL A 10s — distribuzione:")
fasce = [(-99,-1.0,"<-1$"),(-1.0,-0.3,"-1→-0.3$"),(-0.3,0.0,"-0.3→0$"),
         (0.0,0.5,"0→0.5$"),(0.5,99,">0.5$")]
print(f"    {'fascia':<12}  {'NEUTRO':>8}  {'VERDE':>7}  {'DIFF':>6}")
for lo,hi,lab in fasce:
    n = sum(1 for r in neutro if lo<=r["pnl_10s"]<hi)
    v = sum(1 for r in verde  if lo<=r["pnl_10s"]<hi)
    pn, pv = 100*n/len(neutro), 100*v/len(verde)
    flag = "  ◄" if abs(pn-pv)>=15 else ""
    print(f"    {lab:<12}  {pn:>7.0f}%  {pv:>6.0f}%  {pn-pv:>+5.0f}pp{flag}")
print()

# ── VALUTAZIONE FILTRI ──────────────────────────────────────────────────────
print("=" * 72)
print("VALUTAZIONE FILTRI — saldo netto $ se applico il filtro")
print("  logica: NON aprire se condizione vera")
print("  beneficio = $ persi evitati sui neutri filtrati")
print("  costo     = $ guadagnati saltati sui verdi filtrati")
print("  saldo     = beneficio - costo  (positivo = filtro vale)")
print("=" * 72)
print()

def valuta_filtro(nome, condizione, records, neutro, verde):
    """Valuta il saldo netto di un filtro di non-apertura."""
    n_esclusi = [r for r in neutro if condizione(r)]
    v_esclusi = [r for r in verde  if condizione(r)]
    if not n_esclusi and not v_esclusi:
        return None
    beneficio = -sum(r["pnl_real"] for r in n_esclusi)  # perdite evitate (positivo)
    costo     =  sum(r["pnl_real"] for r in v_esclusi)  # profitti saltati (positivo)
    saldo     = beneficio - costo
    pct_n     = 100*len(n_esclusi)/len(neutro) if neutro else 0
    pct_v     = 100*len(v_esclusi)/len(verde)  if verde  else 0
    return {
        "nome": nome, "saldo": saldo, "beneficio": beneficio, "costo": costo,
        "n_neu": len(n_esclusi), "pct_n": pct_n,
        "n_ver": len(v_esclusi), "pct_v": pct_v,
    }

filtri = []

# filtri su regime/volatility/momentum (se JOIN disponibile)
if usa_join:
    for val in set(r["regime"] for r in records if r["regime"]!="?"):
        f = valuta_filtro(f"regime={val}", lambda r,v=val: r["regime"]==v, records, neutro, verde)
        if f: filtri.append(f)
    for val in set(r["volatility"] for r in records if r["volatility"]!="?"):
        f = valuta_filtro(f"volatility={val}", lambda r,v=val: r["volatility"]==v, records, neutro, verde)
        if f: filtri.append(f)
    for val in set(r["momentum"] for r in records if r["momentum"]!="?"):
        f = valuta_filtro(f"momentum={val}", lambda r,v=val: r["momentum"]==v, records, neutro, verde)
        if f: filtri.append(f)
    # combinazioni
    for rv in set(r["regime"] for r in records if r["regime"]!="?"):
        for vv in set(r["volatility"] for r in records if r["volatility"]!="?"):
            f = valuta_filtro(f"regime={rv}+vol={vv}",
                lambda r,rv=rv,vv=vv: r["regime"]==rv and r["volatility"]==vv,
                records, neutro, verde)
            if f: filtri.append(f)
else:
    # filtri su firma
    for val in set(r["f0"] for r in records if r["f0"]!="?"):
        f = valuta_filtro(f"firma[0]={val}", lambda r,v=val: r["f0"]==v, records, neutro, verde)
        if f: filtri.append(f)
    for val in set(r["f1"] for r in records if r["f1"]!="?"):
        f = valuta_filtro(f"firma[1]={val}", lambda r,v=val: r["f1"]==v, records, neutro, verde)
        if f: filtri.append(f)
    for v0 in set(r["f0"] for r in records if r["f0"]!="?"):
        for v1 in set(r["f1"] for r in records if r["f1"]!="?"):
            f = valuta_filtro(f"firma[0]={v0}+[1]={v1}",
                lambda r,v0=v0,v1=v1: r["f0"]==v0 and r["f1"]==v1,
                records, neutro, verde)
            if f: filtri.append(f)

# filtri su ora
for ora_lo, ora_hi, lab in [(0,7,"notte 00-06"),(7,15,"europa 07-14"),(15,24,"usa 15-23")]:
    f = valuta_filtro(f"ora={lab}", lambda r,lo=ora_lo,hi=ora_hi: lo<=r["ora"]<hi,
                      records, neutro, verde)
    if f: filtri.append(f)

# filtri su pnl_10s
for soglia in [-0.5, -0.3, -0.1, 0.0]:
    f = valuta_filtro(f"pnl_10s<{soglia}$", lambda r,s=soglia: r["pnl_10s"]<s,
                      records, neutro, verde)
    if f: filtri.append(f)

# ordina per saldo
filtri_ok = [f for f in filtri if f is not None]
filtri_ok.sort(key=lambda x: -x["saldo"])

print(f"  {'FILTRO':<38}  {'saldo':>7}  {'neu_eli%':>8}  {'ver_eli%':>8}  {'beneficio':>10}  {'costo':>8}")
print(f"  {'-'*90}")
for f in filtri_ok[:20]:
    mk = " ◄ BEST" if f == filtri_ok[0] else ""
    print(f"  {f['nome']:<38}  {f['saldo']:>+7.1f}  "
          f"{f['pct_n']:>7.0f}%  {f['pct_v']:>7.0f}%  "
          f"{f['beneficio']:>+10.1f}  {-f['costo']:>+8.1f}{mk}")

print()

# ── verdetto ──────────────────────────────────────────────────────────────
print("=" * 72)
print("VERDETTO")
print("=" * 72)
print()
print(f"  Netto REALE (senza filtri): ${tot_reale:+.1f}")
if filtri_ok and filtri_ok[0]["saldo"] > 0:
    best = filtri_ok[0]
    print(f"  FILTRO MIGLIORE: '{best['nome']}'")
    print(f"    → elimina {best['n_neu']}/{len(neutro)} neutri ({best['pct_n']:.0f}%)")
    print(f"    → perde   {best['n_ver']}/{len(verde)} verdi ({best['pct_v']:.0f}%)")
    print(f"    → SALDO NETTO: +${best['saldo']:.1f}")
    print(f"    → Netto simulato: ${tot_reale + best['saldo']:+.1f}")
    print()
    print(f"  ✓ Esiste un filtro di contesto che migliora il sistema.")
else:
    print(f"  ✗ Nessun filtro di contesto migliora significativamente il netto.")
    print(f"    I neutri sono indistinguibili dai verdi al momento dell'apertura.")
    print(f"    Il problema non è il QUANDO si entra, ma il COME si gestisce.")
print()

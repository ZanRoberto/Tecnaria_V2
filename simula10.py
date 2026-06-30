#!/usr/bin/env python3
"""
SIMULA10 — BACKTEST ROBUSTO 6 MESI su BTCUSDC 15min

TEST 1: Config corpo>=0.8 exit=5 su 6 mesi interi
        → N trade, WR%, netto totale, media/t, profit factor, max drawdown

TEST 2: Robustezza parametri  corpo [0.70/0.75/0.80/0.85] × exit [3/4/5/6]
        → overfit? o regge su tutti i valori?

TEST 3: Walk-forward 3 blocchi da 2 mesi
        → è positivo in tutti e 3 o solo in uno fortunato?

FEE: maker $2 (paper attuale), taker $5 (live market order)
EXPOSURE: $5000 (come il bot live)

Lancio: python3 simula10.py
"""
import json, sys, time
import urllib.request as ur
from datetime import datetime, timezone, timedelta

SYMBOL    = "BTCUSDC"
INTERVAL  = "15m"
EXPOSURE  = 5000.0
FEE_MAKER = 2.0
FEE_TAKER = 5.0

MS_PER_CANDLE = 15 * 60 * 1000   # 15 min in ms
MESI          = 6
GIORNI        = MESI * 30

# ── fetch paginato ────────────────────────────────────────────────────────────
def fetch_klines_range(symbol, interval, start_ms, end_ms):
    """Scarica tutte le candele tra start_ms e end_ms (paginato, max 1000/req)."""
    all_candles = []
    cur = start_ms
    url_base = "https://fapi.binance.com/fapi/v1/klines"
    while cur < end_ms:
        url = (f"{url_base}?symbol={symbol}&interval={interval}"
               f"&startTime={cur}&endTime={end_ms}&limit=1000")
        try:
            with ur.urlopen(url, timeout=20) as r:
                batch = json.loads(r.read())
        except Exception as e:
            print(f"\n  Errore fetch: {e}")
            time.sleep(3)
            continue
        if not batch:
            break
        for row in batch:
            o,h,l,c = float(row[1]),float(row[2]),float(row[3]),float(row[4])
            all_candles.append({"t": int(row[0]), "o":o, "h":h, "l":l, "c":c})
        cur = batch[-1][0] + MS_PER_CANDLE
        time.sleep(0.15)   # rate limit gentile
        print(f"  ... {len(all_candles)} candele  "
              f"({datetime.fromtimestamp(cur/1000, tz=timezone.utc).strftime('%Y-%m-%d')})",
              end="\r", flush=True)
    print()
    return all_candles

# ── motore maschio LONG ───────────────────────────────────────────────────────
def run_backtest(candles, soglia_corpo, exit_n, fee):
    """
    Entry: chiusura candela bullish con corpo/range >= soglia_corpo
    Exit:  chiusura candela N dopo l'entry
    Ritorna lista trade con pnl_lordo, pnl_netto, ts_entry
    """
    trades = []
    N = len(candles)
    for i in range(N - exit_n):
        c   = candles[i]
        rng = c["h"] - c["l"]
        if rng < 0.5:          # candela piatta (<0.5$): skip
            continue
        corpo_ratio = (c["c"] - c["o"]) / rng
        if corpo_ratio < soglia_corpo:
            continue           # femmina o bearish: non entra
        entry = c["c"]
        exit_ = candles[i + exit_n]["c"]
        btc   = EXPOSURE / entry
        lordo = (exit_ - entry) * btc
        netto = lordo - fee
        trades.append({"ts": c["t"], "pnl_l": lordo, "pnl_n": netto})
    return trades

def stats(trades):
    """Calcola metriche complete da lista trade."""
    if not trades:
        return None
    N       = len(trades)
    netti   = [t["pnl_n"] for t in trades]
    wins    = [p for p in netti if p > 0]
    losses  = [p for p in netti if p <= 0]
    tot     = sum(netti)
    wr      = 100 * len(wins) / N if N else 0
    media   = tot / N
    sum_w   = sum(wins)
    sum_l   = abs(sum(losses)) if losses else 0
    pf      = sum_w / sum_l if sum_l > 0 else float("inf")

    # max drawdown (equity curve)
    equity  = 0.0
    peak_eq = 0.0
    max_dd  = 0.0
    for p in netti:
        equity += p
        if equity > peak_eq:
            peak_eq = equity
        dd = peak_eq - equity
        if dd > max_dd:
            max_dd = dd

    return {
        "N":     N,
        "WR":    wr,
        "tot":   tot,
        "media": media,
        "PF":    pf,
        "max_dd": max_dd,
        "wins":  len(wins),
        "losses":len(losses),
    }

# ── MAIN ──────────────────────────────────────────────────────────────────────
now_ms    = int(datetime.now(timezone.utc).timestamp() * 1000)
start_ms  = now_ms - GIORNI * 24 * 60 * 60 * 1000

print("=" * 80)
print(f"SIMULA10 — BACKTEST 6 MESI  {SYMBOL} {INTERVAL}")
print("=" * 80)
t0_str = datetime.fromtimestamp(start_ms/1000, tz=timezone.utc).strftime("%Y-%m-%d")
t1_str = datetime.fromtimestamp(now_ms/1000,   tz=timezone.utc).strftime("%Y-%m-%d")
print(f"Periodo: {t0_str} → {t1_str}  ({GIORNI} giorni)")
print(f"Fetch paginato da Binance (pausa 150ms/req per rate limit) ...")
print()

candles = fetch_klines_range(SYMBOL, INTERVAL, start_ms, now_ms)
# rimuovi ultima candela (incompleta)
candles = [c for c in candles if c["t"] < now_ms - MS_PER_CANDLE]
print(f"Candele caricate: {len(candles)}")
print()

if len(candles) < 100:
    sys.exit("Dati insufficienti. Controlla connessione e simbolo.")

# range statistiche mercato
ranges  = [c["h"] - c["l"] for c in candles]
corpi   = [abs(c["c"] - c["o"]) for c in candles]
avg_rng = sum(ranges) / len(ranges)
avg_mov = sum(corpi)  / len(corpi)
print(f"Mercato {INTERVAL} — {len(candles)} candele su 6 mesi:")
print(f"  Range medio (H-L):       ${avg_rng:.1f}")
print(f"  Corpo medio |C-O|:       ${avg_mov:.1f}")
print(f"  Fee maker $2 = {100*FEE_MAKER/avg_rng:.1f}% del range  "
      f"(vs {100*FEE_MAKER/0.53:.0f}% nello scalping)")
print(f"  Fee taker $5 = {100*FEE_TAKER/avg_rng:.1f}% del range")
print()

# ═══════════════════════════════════════════════════════════════════════════
# TEST 1 — Config principale: corpo>=0.8, exit=5, maker $2
# ═══════════════════════════════════════════════════════════════════════════
print("═" * 80)
print("TEST 1 — Config principale: corpo>=0.8, exit=5 candele, fee maker $2")
print("═" * 80)
t1_trades = run_backtest(candles, 0.80, 5, FEE_MAKER)
s1 = stats(t1_trades)
if s1:
    print(f"  Periodo:        {t0_str} → {t1_str}  ({GIORNI} giorni)")
    print(f"  Trade totali:   {s1['N']}")
    print(f"  Win/Loss:       {s1['wins']} / {s1['losses']}")
    print(f"  Win Rate:       {s1['WR']:.1f}%")
    print(f"  Profitto netto: ${s1['tot']:+.1f}")
    print(f"  Media/trade:    ${s1['media']:+.2f}  {'✓ POSITIVO' if s1['media']>0 else '✗ NEGATIVO'}")
    print(f"  Profit Factor:  {s1['PF']:.2f}  {'✓ EDGE' if s1['PF']>1 else '✗ NO EDGE'}")
    print(f"  Max Drawdown:   ${s1['max_dd']:.1f}")
    print()
    # anche con taker
    t1_taker = run_backtest(candles, 0.80, 5, FEE_TAKER)
    s1t = stats(t1_taker)
    if s1t:
        print(f"  [Con fee TAKER $5]  netto: ${s1t['tot']:+.1f}  media: ${s1t['media']:+.2f}  "
              f"{'✓' if s1t['media']>0 else '✗'}")
else:
    print("  Nessun trade generato.")
print()

# ═══════════════════════════════════════════════════════════════════════════
# TEST 2 — Robustezza parametri
# ═══════════════════════════════════════════════════════════════════════════
print("═" * 80)
print("TEST 2 — Robustezza parametri  (corpo × exit, fee maker $2)")
print("═" * 80)
print()
SOGLIE_C = [0.70, 0.75, 0.80, 0.85]
EXIT_NS  = [3, 4, 5, 6]

header = f"  {'corpo':>6}  {'exit':>4}  {'N':>6}  {'WR%':>5}  {'netto$':>9}  {'media/t':>8}  {'PF':>6}  {'MaxDD':>8}"
print(header)
print(f"  {'-'*74}")

positivi = 0
totale   = 0
for sc in SOGLIE_C:
    for ex in EXIT_NS:
        tr = run_backtest(candles, sc, ex, FEE_MAKER)
        s  = stats(tr)
        totale += 1
        if not s:
            print(f"  {sc:>6.2f}  {ex:>4}  (nessun trade)")
            continue
        pos = s["media"] > 0
        if pos:
            positivi += 1
        mk = "●" if (sc == 0.80 and ex == 5) else " "   # config principale
        sg = "✓" if pos else "✗"
        print(f"  {mk}{sc:>5.2f}  {ex:>4}  {s['N']:>6}  {s['WR']:>5.1f}%  "
              f"{s['tot']:>+9.1f}  {s['media']:>+8.2f}  {s['PF']:>6.2f}  {s['max_dd']:>8.1f}  {sg}")
    print()

print(f"  Configurazioni positive: {positivi}/{totale}")
if positivi == totale:
    print("  ✓ ROBUSTO — tutte le configurazioni sono positive")
elif positivi >= totale * 0.75:
    print(f"  ~ SEMI-ROBUSTO — {positivi}/{totale} config positive (>75%)")
elif positivi > 0:
    print(f"  ⚠ FRAGILE — solo {positivi}/{totale} config positive: rischio overfit")
else:
    print("  ✗ NO EDGE — nessuna configurazione positiva")
print()

# ═══════════════════════════════════════════════════════════════════════════
# TEST 3 — Walk-forward 3 blocchi da 2 mesi
# ═══════════════════════════════════════════════════════════════════════════
print("═" * 80)
print("TEST 3 — Walk-forward 3 blocchi da 2 mesi (corpo=0.8, exit=5, maker $2)")
print("═" * 80)
print()

# suddividi per timestamp
blocco_ms  = GIORNI * 24 * 60 * 60 * 1000 // 3
risultati_blocchi = []
blocchi_pos = 0

print(f"  {'BLOCCO':<10}  {'periodo':<23}  {'N':>5}  {'WR%':>5}  {'netto$':>9}  {'media/t':>8}  {'PF':>6}  {'esito':>6}")
print(f"  {'-'*74}")

for i in range(3):
    b_start = start_ms + i * blocco_ms
    b_end   = start_ms + (i + 1) * blocco_ms
    b_candles = [c for c in candles if b_start <= c["t"] < b_end]
    if not b_candles:
        print(f"  Blocco {i+1}: nessuna candela in range")
        continue
    b_trades = run_backtest(b_candles, 0.80, 5, FEE_MAKER)
    s = stats(b_trades)
    t_s = datetime.fromtimestamp(b_start/1000, tz=timezone.utc).strftime("%Y-%m-%d")
    t_e = datetime.fromtimestamp(b_end/1000,   tz=timezone.utc).strftime("%Y-%m-%d")
    lbl = f"Blocco {i+1}"
    if s:
        pos = s["media"] > 0
        if pos:
            blocchi_pos += 1
        sg  = "✓ OK" if pos else "✗ LOSS"
        risultati_blocchi.append(pos)
        print(f"  {lbl:<10}  {t_s} → {t_e}  {s['N']:>5}  {s['WR']:>5.1f}%  "
              f"{s['tot']:>+9.1f}  {s['media']:>+8.2f}  {s['PF']:>6.2f}  {sg:>6}")
    else:
        print(f"  {lbl:<10}  {t_s} → {t_e}  (nessun trade)")

print()
if blocchi_pos == 3:
    print("  ✓ POSITIVO IN TUTTI E 3 I BLOCCHI — consistente nel tempo")
elif blocchi_pos == 2:
    print("  ~ POSITIVO IN 2/3 BLOCCHI — abbastanza consistente ma non perfetto")
elif blocchi_pos == 1:
    print("  ✗ POSITIVO IN SOLO 1 BLOCCO — probabilmente fortuna su un periodo")
else:
    print("  ✗ NEGATIVO IN TUTTI I BLOCCHI — no edge")
print()

# ═══════════════════════════════════════════════════════════════════════════
# VERDETTO FINALE
# ═══════════════════════════════════════════════════════════════════════════
print("=" * 80)
print("VERDETTO FINALE")
print("=" * 80)
print()

criteri = {
    "T1 positivo su 6 mesi (maker)":      s1 and s1["media"] > 0,
    "T1 positivo su 6 mesi (taker)":      s1t and s1t["media"] > 0,
    "T2 robusto (≥75% config positive)":  positivi >= totale * 0.75,
    "T3 positivo in tutti i blocchi":      blocchi_pos == 3,
    "T3 positivo in 2/3 blocchi":          blocchi_pos >= 2,
}

for descr, ok in criteri.items():
    print(f"  {'✓' if ok else '✗'}  {descr}")

print()
superati = sum(1 for ok in criteri.values() if ok)
if criteri["T1 positivo su 6 mesi (maker)"] and criteri["T2 robusto (≥75% config positive)"] and criteri["T3 positivo in tutti i blocchi"]:
    print("  ★ PASSA TUTTI E 3 I TEST — il motore è solido su 15min maker.")
    print("    Puoi valutare la migrazione. Priorità: implementare limit orders (maker).")
elif criteri["T1 positivo su 6 mesi (maker)"] and criteri["T3 positivo in 2/3 blocchi"]:
    print("  ~ PASSA PARZIALMENTE — positivo su 6 mesi e 2/3 blocchi.")
    print("    Approfondisci il blocco negativo prima di migrare.")
else:
    print("  ✗ NON SUPERA I TEST — non sufficiente per migrare il sistema.")
    print("    Identifica dove cade e aggiusta prima.")
print()

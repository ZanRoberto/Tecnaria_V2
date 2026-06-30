#!/usr/bin/env python3
"""
SIMULA11 — MOTORE VERO SU 5-MIN + MULTI-ASSET (6 mesi)

Il MOTORE VERO non è "candela piena". È la DINAMICA nel tempo:
  - salita dritta:  tick consecutivi in salita (tick_salita >= SALITA_MIN)
  - tiene:          ultimi 3 tick >= 70% del picco della finestra (MD_CONFERMA)
  - mfe_min:        grasso minimo in $ prima di confermare (filtra micro-spike)

Traduzione tick→5min:
  - finestra osservazione = 12 × 5min = 1 ora  (il bot osserva ~60-120s di tick)
  - grasso_usd = (close[j] - open_finestra) × btc_qty
  - tick_salita = 5min-candle consecutive con close > prev close
  - tiene = ultimi 3 sub-candle con close >= 70% del picco grasso lordo
  - MASCHIO → ENTRA; EXIT dopo EXIT_N × 5min

Multi-asset: BTC, ETH, SOL  (Binance USDC futures)
Mostra dove il motore trova spazio.

Lancio: python3 simula11.py
"""
import json, sys, time
import urllib.request as ur
from datetime import datetime, timezone

EXPOSURE   = 5000.0
FEE_MAKER  = 2.0
FEE_TAKER  = 5.0

ASSETS     = ["BTCUSDC", "ETHUSDC", "SOLUSDC"]   # fallback automatico a USDT
INTERVAL   = "5m"
GIORNI     = 180   # 6 mesi
MS_5M      = 5 * 60 * 1000
WINDOW_SZ  = 12   # 12 × 5min = 1h observation window

# ── fetch paginato ────────────────────────────────────────────────────────────
def fetch_klines(symbol, interval, giorni, verbose=True):
    now_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = now_ms - giorni * 24 * 60 * 60 * 1000
    ms_step  = MS_5M if interval == "5m" else 15 * 60 * 1000
    url_base = "https://fapi.binance.com/fapi/v1/klines"
    candles  = []
    cur      = start_ms
    while cur < now_ms - ms_step:
        url = f"{url_base}?symbol={symbol}&interval={interval}&startTime={cur}&endTime={now_ms}&limit=1000"
        try:
            with ur.urlopen(url, timeout=20) as r:
                batch = json.loads(r.read())
        except Exception as e:
            if "400" in str(e) or "invalid" in str(e).lower():
                return None, f"Simbolo {symbol} non trovato"
            time.sleep(2)
            continue
        if not batch:
            break
        for row in batch:
            candles.append({
                "t": int(row[0]),
                "o": float(row[1]),
                "h": float(row[2]),
                "l": float(row[3]),
                "c": float(row[4]),
            })
        cur = batch[-1][0] + ms_step
        time.sleep(0.12)
        if verbose:
            dt = datetime.fromtimestamp(cur/1000, tz=timezone.utc).strftime("%Y-%m-%d")
            print(f"  {symbol} {interval}: {len(candles)} candele ({dt})  ", end="\r", flush=True)
    if verbose:
        print()
    return candles, None

# ── MOTORE VERO ───────────────────────────────────────────────────────────────
def motore_vero(candles, salita_min=3, mfe_min_usd=10.0, tiene_pct=0.70,
                tiene_n=3, exit_n=12, fee=FEE_MAKER):
    """
    Traduzione ESATTA del motore tick → 5-min sub-candle.

    Per ogni finestra non-overlapping di WINDOW_SZ candele:
    1. Traccia grasso_usd = (close - open_finestra) × qty
    2. Traccia tick_salita = 5min-candle consecutive con close crescente
    3. MASCHIO_CONFERMATO quando:
       - mfe_usd (max grasso finora) >= mfe_min_usd
       - tick_salita >= salita_min  (sale DRITTA, non spike)
       - tiene: ultimi tiene_n sub-candle tutti con close >= tiene_pct × (open + mfe_usd/qty)
         (analog di MD_CONFERMA nel bot)
    4. Entra al close della candela di conferma
    5. Esce dopo exit_n × 5min
    """
    trades   = []
    N        = len(candles)
    start    = 0

    while start + WINDOW_SZ + exit_n <= N:
        window   = candles[start:start + WINDOW_SZ]
        open_p   = window[0]["o"]
        qty      = EXPOSURE / open_p

        prev_c   = open_p
        tick_sal = 0
        mfe_usd  = -9999.0
        mfe_p    = open_p        # prezzo al picco del grasso
        history_c = []           # closes visti dentro la finestra

        entered  = False
        for j, c in enumerate(window):
            grasso     = (c["c"] - open_p) * qty
            history_c.append(c["c"])

            # aggiorna tick_salita
            if c["c"] > prev_c:
                tick_sal += 1
            else:
                tick_sal = 0
            prev_c = c["c"]

            # aggiorna MFE
            if grasso > mfe_usd:
                mfe_usd = grasso
                mfe_p   = c["c"]

            # TIENE: ultimi tiene_n close tutti >= tiene_pct del picco prezzo
            soglia_tiene = open_p + (mfe_usd / qty) * tiene_pct
            if len(history_c) >= tiene_n:
                ultimi = history_c[-tiene_n:]
                tiene  = all(p >= soglia_tiene for p in ultimi)
            else:
                tiene = False

            # MASCHIO CHECK
            if mfe_usd >= mfe_min_usd and tick_sal >= salita_min and tiene:
                entry_p   = c["c"]
                exit_idx  = start + j + exit_n
                if exit_idx >= N:
                    break
                exit_p    = candles[exit_idx]["c"]
                qty_entry = EXPOSURE / entry_p
                pnl_l     = (exit_p - entry_p) * qty_entry
                pnl_n     = pnl_l - fee
                trades.append({
                    "ts":   c["t"],
                    "pnl_l": pnl_l,
                    "pnl_n": pnl_n,
                    "mfe":  mfe_usd,
                })
                entered = True
                break    # un solo trade per finestra

        start += WINDOW_SZ   # avanza alla prossima finestra (non-overlapping)

    return trades

# ── CANDELA PIENA (benchmark) ─────────────────────────────────────────────────
def candela_piena(candles, soglia_corpo=0.80, exit_n=12, fee=FEE_MAKER):
    """Benchmark: versione grezza simula10 (corpo/range >= soglia)."""
    trades = []
    N = len(candles)
    for i in range(N - exit_n):
        c   = candles[i]
        rng = c["h"] - c["l"]
        if rng < 0.01:
            continue
        if (c["c"] - c["o"]) / rng < soglia_corpo:
            continue
        entry = c["c"]
        exit_ = candles[i + exit_n]["c"]
        qty   = EXPOSURE / entry
        pnl_l = (exit_ - entry) * qty
        pnl_n = pnl_l - fee
        trades.append({"ts": c["t"], "pnl_l": pnl_l, "pnl_n": pnl_n})
    return trades

def stats(trades):
    if not trades:
        return None
    netti  = [t["pnl_n"] for t in trades]
    wins   = [p for p in netti if p > 0]
    losses = [p for p in netti if p <= 0]
    tot    = sum(netti)
    N      = len(netti)
    wr     = 100 * len(wins) / N
    media  = tot / N
    sum_w  = sum(wins)
    sum_l  = abs(sum(losses)) if losses else 0
    pf     = sum_w / sum_l if sum_l > 0 else float("inf")
    # max drawdown
    eq = 0.0; pk = 0.0; dd = 0.0
    for p in netti:
        eq += p
        pk = max(pk, eq)
        dd = max(dd, pk - eq)
    return {"N": N, "WR": wr, "tot": tot, "media": media,
            "PF": pf, "max_dd": dd, "wins": len(wins)}

def riga(s):
    if s is None:
        return "(nessun trade)"
    sg = "✓" if s["media"] > 0 else "✗"
    return (f"N={s['N']:>5}  WR={s['WR']:>5.1f}%  netto=${s['tot']:>+8.1f}  "
            f"media=${s['media']:>+6.2f}  PF={s['PF']:>5.2f}  DD=${s['max_dd']:>7.1f}  {sg}")

# ══════════════════════════════════════════════════════════════════════════════
print("=" * 82)
print("SIMULA11 — MOTORE VERO (5-min sub-candle) vs CANDELA PIENA + MULTI-ASSET")
print("=" * 82)
print()

# ── scarica dati per ogni asset ───────────────────────────────────────────────
dati = {}
for sym in ASSETS:
    print(f"Fetch {sym} 5min 6 mesi ...")
    c, err = fetch_klines(sym, "5m", GIORNI)
    if err or not c:
        # prova fallback USDT
        sym_usdt = sym.replace("USDC", "USDT")
        print(f"  {sym} non trovato ({err}) → provo {sym_usdt}")
        c, err2 = fetch_klines(sym_usdt, "5m", GIORNI)
        if err2 or not c:
            print(f"  ✗ Anche {sym_usdt} non disponibile, skip")
            continue
        print(f"  ✓ {sym_usdt}: {len(c)} candele")
        dati[sym_usdt] = c
    else:
        print(f"  ✓ {sym}: {len(c)} candele")
        dati[sym] = c

if not dati:
    sys.exit("Nessun asset disponibile.")

print()

# ══════════════════════════════════════════════════════════════════════════════
# TEST A — MOTORE VERO vs CANDELA PIENA su BTC (config default)
# ══════════════════════════════════════════════════════════════════════════════
btc_sym  = next((s for s in dati if "BTC" in s), None)
if btc_sym:
    btc_c = dati[btc_sym]
    print("═" * 82)
    print(f"TEST A — MOTORE VERO vs CANDELA PIENA su {btc_sym}  (6 mesi, fee maker $2)")
    print("═" * 82)
    print(f"  Config motore vero: salita_min=3, mfe_min=$10, tiene=70%×3, exit=12×5min=1h")
    print(f"  Config candela piena: corpo>=0.80, exit=12×5min=1h  (stesso orizzonte)")
    print()

    mv_btc = motore_vero(btc_c, salita_min=3, mfe_min_usd=10.0, exit_n=12, fee=FEE_MAKER)
    cp_btc = candela_piena(btc_c, soglia_corpo=0.80, exit_n=12, fee=FEE_MAKER)

    s_mv = stats(mv_btc)
    s_cp = stats(cp_btc)

    print(f"  MOTORE VERO:    {riga(s_mv)}")
    print(f"  CANDELA PIENA:  {riga(s_cp)}")
    print()

    if s_mv and s_cp:
        if s_mv["PF"] > s_cp["PF"]:
            delta = s_mv["PF"] - s_cp["PF"]
            print(f"  → Motore vero MIGLIORE di +{delta:.2f} PF rispetto a candela piena")
        else:
            print(f"  → Candela piena uguale o meglio — il filtro dinamico non aiuta su BTC")
    print()

    # anche con taker $5
    mv_btc_t = motore_vero(btc_c, salita_min=3, mfe_min_usd=10.0, exit_n=12, fee=FEE_TAKER)
    s_mv_t   = stats(mv_btc_t)
    print(f"  MOTORE VERO (taker $5): {riga(s_mv_t)}")
    print()

# ══════════════════════════════════════════════════════════════════════════════
# TEST B — Griglia robustezza motore vero su BTC
# ══════════════════════════════════════════════════════════════════════════════
if btc_sym:
    print("═" * 82)
    print(f"TEST B — Robustezza motore vero su {btc_sym}  (salita_min × mfe_min, exit=12)")
    print("═" * 82)
    print()
    SALITE  = [2, 3, 4]
    MFE_MINS = [5.0, 10.0, 20.0, 30.0]

    print(f"  {'sal':>4}  {'mfe$':>6}  {'N':>5}  {'WR%':>5}  {'netto$':>8}  {'media/t':>8}  {'PF':>6}  {'DD$':>7}")
    print(f"  {'-'*64}")

    miglior_pf = 0; miglior_cfg = None
    for sal in SALITE:
        for mfe in MFE_MINS:
            tr = motore_vero(btc_c, salita_min=sal, mfe_min_usd=mfe, exit_n=12, fee=FEE_MAKER)
            s  = stats(tr)
            if not s:
                print(f"  {sal:>4}  {mfe:>6.0f}  (nessun trade)")
                continue
            sg = "✓" if s["media"] > 0 else "✗"
            mk = "●" if (sal == 3 and mfe == 10.0) else " "
            print(f"  {mk}{sal:>3}  {mfe:>6.0f}  {s['N']:>5}  {s['WR']:>5.1f}%  "
                  f"{s['tot']:>+8.1f}  {s['media']:>+8.2f}  {s['PF']:>6.2f}  {s['max_dd']:>7.1f}  {sg}")
            if s["PF"] > miglior_pf:
                miglior_pf  = s["PF"]
                miglior_cfg = (sal, mfe)
        print()
    if miglior_cfg:
        print(f"  Config migliore BTC: salita_min={miglior_cfg[0]}, mfe_min=${miglior_cfg[1]:.0f}  (PF={miglior_pf:.2f})")
    print()

# ══════════════════════════════════════════════════════════════════════════════
# TEST C — Multi-asset: dove il motore trova spazio?
# ══════════════════════════════════════════════════════════════════════════════
print("═" * 82)
print("TEST C — Multi-asset: MOTORE VERO su tutti gli asset (config salita=3 mfe=$10, exit=12)")
print("═" * 82)
print()
print(f"  {'ASSET':<12}  {'N':>5}  {'WR%':>5}  {'netto$':>9}  {'media/t':>8}  {'PF':>6}  {'MaxDD':>8}  {'esito':>6}")
print(f"  {'-'*72}")

for sym, cands in dati.items():
    # prezzo tipico
    mid_p = cands[len(cands)//2]["c"]
    # mfe_min in % (0.2% del prezzo = $5000×0.002) per essere comparabile cross-asset
    mfe_min = round(EXPOSURE * 0.002)   # 0.2% exposure = $10

    tr = motore_vero(cands, salita_min=3, mfe_min_usd=mfe_min, exit_n=12, fee=FEE_MAKER)
    s  = stats(tr)
    if not s:
        print(f"  {sym:<12}  (nessun trade con mfe_min=${mfe_min})")
        continue

    avg_rng = sum(c["h"] - c["l"] for c in cands) / len(cands)
    sg = "✓" if s["media"] > 0 else "✗"
    print(f"  {sym:<12}  {s['N']:>5}  {s['WR']:>5.1f}%  {s['tot']:>+9.1f}  "
          f"{s['media']:>+8.2f}  {s['PF']:>6.2f}  {s['max_dd']:>8.1f}  {sg}")
    print(f"  {'':12}  Range medio 5min: ${avg_rng:.1f}  Fee maker ${FEE_MAKER:.0f} = {100*FEE_MAKER/avg_rng:.1f}% del range")
print()

# ══════════════════════════════════════════════════════════════════════════════
# TEST D — Walk-forward 3 blocchi su asset migliore
# ══════════════════════════════════════════════════════════════════════════════
# trova asset con PF più alto
best_sym = None; best_pf_asset = 0
for sym, cands in dati.items():
    tr = motore_vero(cands, salita_min=3, mfe_min_usd=round(EXPOSURE*0.002),
                     exit_n=12, fee=FEE_MAKER)
    s  = stats(tr)
    if s and s["PF"] > best_pf_asset:
        best_pf_asset = s["PF"]
        best_sym = sym

if best_sym:
    best_c = dati[best_sym]
    blocco = len(best_c) // 3

    print("═" * 82)
    print(f"TEST D — Walk-forward 3×2mesi su {best_sym} (asset migliore, salita=3 mfe=$10)")
    print("═" * 82)
    print()
    print(f"  {'BLOCCO':<10}  {'periodo':<23}  {'N':>5}  {'WR%':>5}  {'netto$':>9}  {'media/t':>8}  {'PF':>6}  {'esito':>6}")
    print(f"  {'-'*74}")

    blocchi_pos = 0
    for i in range(3):
        b = best_c[i*blocco:(i+1)*blocco]
        tr = motore_vero(b, salita_min=3, mfe_min_usd=round(EXPOSURE*0.002),
                         exit_n=12, fee=FEE_MAKER)
        s  = stats(tr)
        ts = datetime.fromtimestamp(b[0]["t"]/1000, tz=timezone.utc).strftime("%Y-%m-%d")
        te = datetime.fromtimestamp(b[-1]["t"]/1000, tz=timezone.utc).strftime("%Y-%m-%d")
        if s:
            sg = "✓ OK" if s["media"] > 0 else "✗ LOSS"
            if s["media"] > 0:
                blocchi_pos += 1
            print(f"  Blocco {i+1:<4}  {ts} → {te}  {s['N']:>5}  {s['WR']:>5.1f}%  "
                  f"{s['tot']:>+9.1f}  {s['media']:>+8.2f}  {s['PF']:>6.2f}  {sg}")
        else:
            print(f"  Blocco {i+1:<4}  {ts} → {te}  (nessun trade)")
    print()

    if blocchi_pos == 3:
        print(f"  ✓ Positivo in tutti e 3 i blocchi su {best_sym}")
    elif blocchi_pos == 2:
        print(f"  ~ Positivo in 2/3 blocchi su {best_sym}")
    else:
        print(f"  ✗ Positivo in meno di 2 blocchi su {best_sym} — non consistente")
    print()

# ══════════════════════════════════════════════════════════════════════════════
# VERDETTO FINALE
# ══════════════════════════════════════════════════════════════════════════════
print("=" * 82)
print("VERDETTO FINALE")
print("=" * 82)
print()

if btc_sym:
    s_mv_f = stats(motore_vero(dati[btc_sym], 3, 10.0, 12, FEE_MAKER))
    s_cp_f = stats(candela_piena(dati[btc_sym], 0.80, 12, FEE_MAKER))
    if s_mv_f and s_cp_f:
        print(f"  BTC motore vero PF={s_mv_f['PF']:.2f}  vs  candela piena PF={s_cp_f['PF']:.2f}")
        if s_mv_f["PF"] > 1 and s_mv_f["media"] > 0:
            print(f"  ✓ Il MOTORE VERO funziona su BTC 5min/1h — meglio della candela piena")
        elif s_mv_f["PF"] > s_cp_f["PF"]:
            print(f"  ~ Il motore vero è MIGLIORE della candela piena ma non ancora positivo")
            print(f"    Gap dalla positività: ${abs(s_mv_f['media']):.2f}/trade")
        else:
            print(f"  ✗ Anche il motore vero non batte la candela piena su BTC 5min")
            print(f"    BTC è probabilmente troppo efficiente anche su 5min/1h")

print()
print(f"  La chiave è il RANGE 5min vs fee:")
for sym, cands in dati.items():
    avg_rng = sum(c["h"] - c["l"] for c in cands) / len(cands)
    ratio   = avg_rng / FEE_MAKER
    print(f"    {sym}: range medio ${avg_rng:.1f}  →  fee maker è {100/ratio:.1f}% del range  "
          f"{'(gestibile)' if ratio > 5 else '(ancora troppo pesante)'}")
print()

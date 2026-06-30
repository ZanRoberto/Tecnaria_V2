#!/usr/bin/env python3
"""
SIMULA9 — STESSO MOTORE SU CANDELE 15 MIN (dati Binance REST API)

Domanda: lo stesso riconoscimento "maschio/femmina che tiene o si svuota"
         applicato a candele 15min genera tratti abbastanza grandi da coprire la fee?

LOGICA (traduzione del motore tick → candele 15min):
  - MASCHIO 15min = candela con corpo grande (corpo > X% del range),
    chiude vicino all'alto (per long), senza svuotarsi nel progresso.
    Proxy: (close-open)/(high-low) > SOGLIA_CORPO  (candela "dritta")
  - FEMMINA 15min = candela che sale ma poi torna giù: spike con chiusura bassa.
    Proxy: (close-open)/(high-low) < SOGLIA_CORPO (corpo piccolo = svuotata)

ENTRY: alla chiusura della candela "maschio" (segnale confermato)
EXIT:  alla chiusura di N candele successive (configurable)
P&L:  (close_exit - close_entry) × btc_qty - FEE
      dove btc_qty = EXPOSURE / close_entry  (posizione $5000)

FEE: maker=$2 (paper) e taker=$5 (live market order)

Fetch: BTC/USDC 15min futures da Binance (endpoint pubblico, no auth).
       Scarica le ultime 1000 candele (~10 giorni).

Lancio: python3 simula9.py
"""
import json, sys, time
try:
    import urllib.request as ur
except ImportError:
    sys.exit("Serve urllib (standard library)")

SYMBOL        = "BTCUSDC"
INTERVAL      = "15m"
LIMIT         = 1000          # max Binance per request = 1000
EXPOSURE      = 5000.0        # $ (come il bot)
FEE_MAKER     = 2.0
FEE_TAKER     = 5.0

# Parametri del motore "maschio" su candele
SOGLIE_CORPO  = [0.5, 0.6, 0.7, 0.8]   # (close-open)/(high-low) > soglia → maschio
EXIT_CANDLES  = [1, 2, 3, 5]            # chiudi dopo N candele

# ── fetch Binance futures klines ─────────────────────────────────────────────
def fetch_klines(symbol, interval, limit):
    url = (f"https://fapi.binance.com/fapi/v1/klines"
           f"?symbol={symbol}&interval={interval}&limit={limit}")
    try:
        with ur.urlopen(url, timeout=15) as r:
            data = json.loads(r.read())
    except Exception as e:
        sys.exit(f"Errore fetch Binance: {e}\n  URL: {url}")
    # formato: [open_time, open, high, low, close, volume, ...]
    candles = []
    for row in data[:-1]:  # escludi ultima (candela incompleta)
        o, h, l, c = float(row[1]), float(row[2]), float(row[3]), float(row[4])
        candles.append({"o": o, "h": h, "l": l, "c": c, "t": int(row[0])})
    return candles

print("=" * 80)
print(f"SIMULA9 — MOTORE MASCHIO/FEMMINA su {SYMBOL} {INTERVAL} (Binance futures)")
print("=" * 80)
print(f"Fetching {LIMIT} candele {INTERVAL} da Binance ... ", end="", flush=True)
candles = fetch_klines(SYMBOL, INTERVAL, LIMIT)
print(f"OK — {len(candles)} candele caricate")

from datetime import datetime
t0 = datetime.fromtimestamp(candles[0]["t"]  / 1000).strftime("%Y-%m-%d %H:%M")
t1 = datetime.fromtimestamp(candles[-1]["t"] / 1000).strftime("%Y-%m-%d %H:%M")
print(f"  Periodo: {t0}  →  {t1}")
print()

# ── statistiche delle candele raw ───────────────────────────────────────────
ranges  = [(c["h"] - c["l"]) for c in candles]
corpi   = [(c["c"] - c["o"]) for c in candles]  # pos = bullish, neg = bearish
n_bull  = sum(1 for c in corpi if c > 0)
n_bear  = sum(1 for c in corpi if c < 0)
avg_rng = sum(ranges) / len(ranges)
avg_mov = sum(abs(c) for c in corpi) / len(corpi)

print(f"Statistiche {INTERVAL}:")
print(f"  Candele bullish: {n_bull}  bearish: {n_bear}")
print(f"  Range medio (high-low): ${avg_rng:.1f}")
print(f"  Corpo medio |close-open|: ${avg_mov:.1f}")
print(f"  Rapporto corpo/range medio: {avg_mov/avg_rng:.2f}")
print()
print(f"  FEE maker ${FEE_MAKER:.0f} = {100*FEE_MAKER/avg_mov:.1f}% del corpo medio  "
      f"→ devi catturare almeno {FEE_MAKER/avg_mov:.0%} del corpo per pareggiare")
print(f"  FEE taker ${FEE_TAKER:.0f} = {100*FEE_TAKER/avg_mov:.1f}% del corpo medio  "
      f"→ devi catturare almeno {FEE_TAKER/avg_mov:.0%} del corpo per pareggiare")
print()

# ── simulazione entrata/uscita ───────────────────────────────────────────────
def simula_maschio(candles, soglia_corpo, exit_n, fee, direzione="LONG"):
    """
    Entry: chiusura candela 'maschio' (corpo/range >= soglia_corpo, bullish per LONG)
    Exit: chiusura della candela N successiva.
    """
    trades = []
    N = len(candles)
    for i in range(N - exit_n):
        c = candles[i]
        rng = c["h"] - c["l"]
        if rng < 0.01:
            continue  # candela piatta, skip
        corpo = (c["c"] - c["o"]) / rng

        # MASCHIO LONG: bullish E corpo grande E chiude in alto
        if direzione == "LONG":
            if corpo < soglia_corpo:
                continue   # femmina (svuotata o bearish)
        else:
            if -corpo < soglia_corpo:
                continue

        # entra alla chiusura di questa candela
        entry_price = c["c"]
        exit_price  = candles[i + exit_n]["c"]
        btc_qty     = EXPOSURE / entry_price

        if direzione == "LONG":
            pnl_lordo = (exit_price - entry_price) * btc_qty
        else:
            pnl_lordo = (entry_price - exit_price) * btc_qty

        pnl_netto = pnl_lordo - fee
        trades.append({"pnl_l": pnl_lordo, "pnl_n": pnl_netto, "corpo": corpo * rng})

    return trades

print("SIMULAZIONE MASCHIO LONG (entra su candela bullish dritta):")
print()
print(f"  {'soglia_corpo':>12}  {'exit_N':>6}  {'N_trade':>7}  {'WR%':>5}  "
      f"{'lordo$':>8}  {'NETTO(maker)':>13}  {'NETTO(taker)':>13}  {'media_mk':>9}")
print(f"  {'-'*82}")

best_maker = None
best_cfg   = None

for sc in SOGLIE_CORPO:
    for ex in EXIT_CANDLES:
        trades_m = simula_maschio(candles, sc, ex, FEE_MAKER, "LONG")
        trades_t = simula_maschio(candles, sc, ex, FEE_TAKER, "LONG")
        if not trades_m:
            continue

        N_t     = len(trades_m)
        win_m   = sum(1 for t in trades_m if t["pnl_n"] > 0)
        wr      = 100 * win_m / N_t
        tot_l   = sum(t["pnl_l"] for t in trades_m)
        tot_mk  = sum(t["pnl_n"] for t in trades_m)
        tot_tk  = sum(t["pnl_n"] for t in trades_t)
        med_mk  = tot_mk / N_t

        marker = ""
        if tot_mk > 0 and (best_maker is None or tot_mk > best_maker):
            best_maker = tot_mk
            best_cfg   = (sc, ex)
            marker = " ◄"

        segno = "✓" if tot_mk > 0 else "✗"
        print(f"  {sc:>12.1f}  {ex:>6}  {N_t:>7}  {wr:>5.0f}%  "
              f"{tot_l:>+8.1f}  {segno}{tot_mk:>+12.1f}  {'?' if tot_tk>0 else '✗'}{tot_tk:>+12.1f}  {med_mk:>+9.2f}{marker}")

print()

# ── confronto vs scalping ─────────────────────────────────────────────────────
print("=" * 80)
print("CONFRONTO DIRETTO: scalping (tick) vs candele 15min")
print("=" * 80)
print()
print(f"  Scalping (tick) — da simula7 (dati storici bot):")
print(f"    media lordo catturato:  +$0.53 / trade  (soglia 0.0)")
print(f"    fee maker $2:  netto = -$1.47/trade  ← PERDITA")
print(f"    fee taker $5:  netto = -$4.47/trade  ← PERDITA GRAVE")
print()
print(f"  Candele {INTERVAL} — dati appena calcolati:")
if best_cfg:
    sc, ex = best_cfg
    trades_best = simula_maschio(candles, sc, ex, FEE_MAKER, "LONG")
    if trades_best:
        avg_l_best = sum(t["pnl_l"] for t in trades_best) / len(trades_best)
        avg_n_best = sum(t["pnl_n"] for t in trades_best) / len(trades_best)
        avg_corpo  = sum(t["corpo"] for t in trades_best) / len(trades_best)
        print(f"    Migliore config: soglia_corpo={sc}, exit_N={ex}")
        print(f"    media lordo catturato: {avg_l_best:+.2f}$/trade")
        print(f"    corpo medio delle candele maschio: ${avg_corpo:.1f}")
        print(f"    fee maker $2: netto = {avg_n_best:+.2f}$/trade  "
              f"{'← PROFITTO' if avg_n_best > 0 else '← PERDITA'}")
        trades_best_t = simula_maschio(candles, sc, ex, FEE_TAKER, "LONG")
        avg_n_best_t = sum(t["pnl_n"] for t in trades_best_t) / len(trades_best_t)
        print(f"    fee taker $5: netto = {avg_n_best_t:+.2f}$/trade  "
              f"{'← PROFITTO' if avg_n_best_t > 0 else '← PERDITA'}")
else:
    print(f"    Nessuna configurazione positiva trovata.")
    print(f"    Media range {INTERVAL}: ${avg_rng:.1f} → fee maker è {100*FEE_MAKER/avg_rng:.1f}% del range")

print()
print("=" * 80)
print("CONCLUSIONE:")
print("=" * 80)
print()
if best_maker and best_maker > 0:
    sc, ex = best_cfg
    print(f"  ✓ Su {INTERVAL}: il motore 'maschio/femmina' FUNZIONA con maker fee.")
    print(f"    Configurazione: corpo >= {sc*100:.0f}% del range, exit dopo {ex} candele.")
    print(f"    Il range medio ${avg_rng:.1f} assorbe facilmente la fee $2.")
    print()
    print(f"  → SCALPING: fee $2 = {100*FEE_MAKER/0.53:.0f}% del catturato → IMPOSSIBILE")
    print(f"  → 15 MIN:   fee $2 = {100*FEE_MAKER/avg_rng:.1f}% del range → GESTIBILE")
    print()
    print(f"  VERDETTO: il MOTORE è valido. Il problema è il TIMEFRAME, non la logica.")
    print(f"  Stessa riconoscimento maschio/femmina su scala più lenta = profittevole.")
else:
    print(f"  ✗ Anche su {INTERVAL} il motore non copre la fee con questa logica semplificata.")
    print(f"    Range medio {INTERVAL}: ${avg_rng:.1f} — fee maker $2 = {100*FEE_MAKER/avg_rng:.1f}% del range")
    print(f"    Il problema potrebbe essere la DIREZIONE o la logica di entrata.")
    print(f"    → Prova a girare anche SHORT o a usare soglie_corpo più selettive.")
print()

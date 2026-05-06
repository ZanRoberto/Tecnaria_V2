#!/usr/bin/env python3
"""
OVERTOP BASSANO — SIMULATORE STANDALONE
Simula 4 regimi di mercato × 10.000 cicli ciascuno.
Genera capsule nel DB e produce report completo.

Uso: python3 overtop_simulator.py --db /var/data/trading_data.db
"""

import sqlite3
import random
import math
import time
import json
import argparse
from collections import defaultdict
from datetime import datetime

# ─────────────────────────────────────────────────────────────────────────────
# PARAMETRI FISSI (stessi del bot)
# ─────────────────────────────────────────────────────────────────────────────
TRADE_SIZE_USD = 500.0
LEVERAGE       = 5
EXPOSURE       = TRADE_SIZE_USD * LEVERAGE   # $2500
FEE_PCT        = 0.0002
FEE_TRADE      = EXPOSURE * FEE_PCT * 2      # $1.00
ASSET          = "BTCUSDC"

# ─────────────────────────────────────────────────────────────────────────────
# GENERATORE PREZZI SINTETICI PER REGIME
# ─────────────────────────────────────────────────────────────────────────────
def genera_prezzi(regime: str, n: int, prezzo_base: float = 80000.0) -> list:
    """
    Genera n prezzi sintetici realistici per il regime dato.
    """
    prezzi = [prezzo_base]
    p = prezzo_base

    for i in range(n - 1):
        if regime == "RANGING":
            # Oscillazione intorno a un centro, range stretto
            centro = prezzo_base * (1 + 0.002 * math.sin(i / 200))
            drift  = (centro - p) * 0.01
            noise  = random.gauss(0, prezzo_base * 0.0003)
            p = p + drift + noise

        elif regime == "TRENDING_BULL":
            # Trend rialzista con pullback
            drift = prezzo_base * 0.00008
            noise = random.gauss(0, prezzo_base * 0.0002)
            if random.random() < 0.15:  # pullback occasionale
                drift = -prezzo_base * 0.0002
            p = p + drift + noise

        elif regime == "TRENDING_BEAR":
            # Trend ribassista con rimbalzi
            drift = -prezzo_base * 0.00008
            noise = random.gauss(0, prezzo_base * 0.0002)
            if random.random() < 0.15:  # rimbalzo occasionale
                drift = prezzo_base * 0.0002
            p = p + drift + noise

        elif regime == "EXPLOSIVE":
            # Movimenti bruschi, alta volatilità, breakout
            if random.random() < 0.05:  # spike
                direzione = 1 if random.random() > 0.4 else -1
                p = p + direzione * prezzo_base * random.uniform(0.003, 0.008)
            else:
                drift = random.gauss(0, prezzo_base * 0.0008)
                p = p + drift

        p = max(p, prezzo_base * 0.90)  # floor sicurezza
        prezzi.append(round(p, 2))

    return prezzi

# ─────────────────────────────────────────────────────────────────────────────
# CLASSIFICATORE CONTESTO
# ─────────────────────────────────────────────────────────────────────────────
def classifica_contesto(prezzi: list, idx: int) -> tuple:
    """
    Classifica momentum, volatility, trend dagli ultimi prezzi.
    """
    if idx < 20:
        return "MEDIO", "MEDIA", "SIDEWAYS"

    window = prezzi[max(0, idx-20):idx+1]
    recent = prezzi[max(0, idx-5):idx+1]

    # Momentum: direzione degli ultimi 5 tick
    changes = [recent[i+1] - recent[i] for i in range(len(recent)-1)]
    up = sum(1 for c in changes if c > 0)
    if up >= 4:   momentum = "FORTE"
    elif up >= 2: momentum = "MEDIO"
    else:         momentum = "DEBOLE"

    # Volatility: range medio ultimi 20
    changes20 = [abs(window[i+1]-window[i]) for i in range(len(window)-1)]
    avg = sum(changes20)/len(changes20) if changes20 else 0
    ref = prezzi[0] * 0.0001
    if avg > ref * 5:   volatility = "ALTA"
    elif avg > ref * 2: volatility = "MEDIA"
    else:               volatility = "BASSA"

    # Trend: variazione % totale su 20 tick
    chg_pct = (window[-1] - window[0]) / window[0] * 100
    if chg_pct > 0.3:   trend = "UP"
    elif chg_pct < -0.3: trend = "DOWN"
    else:                trend = "SIDEWAYS"

    return momentum, volatility, trend

# ─────────────────────────────────────────────────────────────────────────────
# MOTORE DECISIONALE SEMPLIFICATO
# ─────────────────────────────────────────────────────────────────────────────
class MiniCampo:
    """
    Versione semplificata del CampoGravitazionale.
    Calcola score 0-100 e soglia dinamica.
    """
    W = {"seed":25,"fp":20,"mom":12,"trend":12,"vol":8,"regime":3,"rsi":10,"macd":10}

    def score(self, momentum, volatility, trend, regime, fp_wr, seed_score,
              rsi=50.0, macd=0.0, direction="LONG"):
        # Pesi LONG
        MOM_L  = {"FORTE":1.0, "MEDIO":0.67, "DEBOLE":0.20}
        TRD_L  = {"UP":1.0, "SIDEWAYS":0.47, "DOWN":0.0}
        # Pesi SHORT — invertiti
        MOM_S  = {"FORTE":0.20, "MEDIO":0.67, "DEBOLE":1.0}
        TRD_S  = {"UP":0.0, "SIDEWAYS":0.47, "DOWN":1.0}

        VOL_S  = {"BASSA":1.0, "MEDIA":0.60, "ALTA":0.20}
        REG_L  = {"TRENDING_BULL":1.0,"EXPLOSIVE":0.80,"RANGING":0.20,"TRENDING_BEAR":0.0}
        REG_S  = {"TRENDING_BULL":0.0,"EXPLOSIVE":0.80,"RANGING":0.20,"TRENDING_BEAR":1.0}
        REG_F  = {"TRENDING_BULL":0.80,"EXPLOSIVE":0.85,"RANGING":1.00,"TRENDING_BEAR":1.10}

        is_short = (direction == "SHORT")
        MOM = MOM_S if is_short else MOM_L
        TRD = TRD_S if is_short else TRD_L
        REG = REG_S if is_short else REG_L

        s_seed = min(1.0, max(0.0, (seed_score-0.20)/0.60)) * self.W["seed"]
        s_fp   = min(1.0, max(0.0, (fp_wr-0.30)/0.50))      * self.W["fp"]
        s_mom  = MOM.get(momentum, 0.5)    * self.W["mom"]
        s_trd  = TRD.get(trend, 0.5)       * self.W["trend"]
        s_vol  = VOL_S.get(volatility, 0.5)* self.W["vol"]
        s_reg  = REG.get(regime, 0.2)      * self.W["regime"]

        # RSI score — invertito per SHORT
        if is_short:
            if rsi > 70:   s_rsi = 1.0
            elif rsi > 55: s_rsi = 0.7
            elif rsi > 45: s_rsi = 0.4
            else:          s_rsi = 0.15
        else:
            if rsi < 30:   s_rsi = 1.0
            elif rsi < 45: s_rsi = 0.7
            elif rsi < 55: s_rsi = 0.4
            else:          s_rsi = 0.15
        s_rsi *= self.W["rsi"]

        # MACD score — invertito per SHORT
        s_macd = (0.7 if (macd < 0) == is_short else 0.3) * self.W["macd"]

        total = s_seed + s_fp + s_mom + s_trd + s_vol + s_reg + s_rsi + s_macd

        # Soglia dinamica
        score_max = (self.W["seed"] + self.W["fp"] +
                     MOM.get(momentum,0.5)*self.W["mom"] +
                     TRD.get(trend,0.5)*self.W["trend"] +
                     VOL_S.get(volatility,0.5)*self.W["vol"] +
                     REG.get(regime,0.2)*self.W["regime"] +
                     self.W["rsi"] + self.W["macd"])
        ctx = score_max / 100.0
        rf  = REG_F.get(regime, 1.0)
        soglia_raw = 50 * ctx * rf
        soglia = max(44, min(80, soglia_raw))

        return round(total, 2), round(soglia, 2)


class MiniOracolo:
    """Fingerprint WR memory semplificata."""
    DECAY = 0.95

    def __init__(self):
        self._mem = {}

    def get_wr(self, momentum, volatility, trend, direction="LONG"):
        key = f"{direction}|{momentum}|{volatility}|{trend}"
        m = self._mem.get(key)
        if not m or m["samples"] < 3:
            return 0.72
        return m["wins"] / m["samples"]

    def record(self, momentum, volatility, trend, is_win, direction="LONG", pnl=0.0):
        key = f"{direction}|{momentum}|{volatility}|{trend}"
        if key not in self._mem:
            self._mem[key] = {"wins":0.0,"samples":0.0,"pnl_sum":0.0}
        m = self._mem[key]
        m["wins"]    = m["wins"]    * self.DECAY + (1.0 if is_win else 0.0)
        m["samples"] = m["samples"] * self.DECAY + 1.0
        m["pnl_sum"] = m["pnl_sum"] * self.DECAY + pnl

    def get_stats(self):
        return {k: {"wr": round(v["wins"]/v["samples"],3) if v["samples"]>0 else 0,
                    "n": round(v["samples"],1), "pnl_avg": round(v["pnl_sum"]/max(v["samples"],1),2)}
                for k,v in self._mem.items() if v["samples"] >= 3}


# ─────────────────────────────────────────────────────────────────────────────
# GENERATORE CAPSULE DAI RISULTATI
# ─────────────────────────────────────────────────────────────────────────────
def genera_capsule_dal_report(report: dict, db_path: str):
    """
    Analizza i risultati della simulazione e genera capsule nel DB.
    Logica:
    - WR < 15% su 10+ trade → EVITA (se non esiste già MIGLIORA)
    - WR > 65% su 10+ trade → WHITELIST (non bloccare mai)
    - PROFIT_LOCK presente e WR > 30% → MIGLIORA exit
    """
    capsule_generate = []
    
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()

        # Leggi MIGLIORA esistenti
        migliora_existing = set()
        for row in c.execute("SELECT trigger_json FROM capsule WHERE tipo LIKE 'MIGLIORA%' AND enabled=1").fetchall():
            try:
                trigs = json.loads(row[0] or '[]')
                key = frozenset((t.get("param",""), str(t.get("value",""))) for t in trigs)
                migliora_existing.add(key)
            except Exception:
                pass

        for contesto, stats in report["per_contesto"].items():
            parts  = contesto.split("|")
            if len(parts) < 4:
                continue
            regime, momentum, volatility, trend = parts[0], parts[1], parts[2], parts[3]
            n      = stats["n"]
            wr     = stats["wr"]
            pnl_avg= stats["pnl_avg"]
            profit_lock_pct = stats.get("profit_lock_pct", 0)

            if n < 5:
                continue

            trigger = [
                {"param":"momentum",  "op":"==","value":momentum},
                {"param":"volatility","op":"==","value":volatility},
                {"param":"trend",     "op":"==","value":trend},
            ]
            trig_key = frozenset((t["param"], t["value"]) for t in trigger)

            # EVITA: WR basso, nessun PROFIT_LOCK, nessuna MIGLIORA esistente
            if wr < 0.15 and pnl_avg < -0.50 and profit_lock_pct < 0.10:
                if trig_key not in migliora_existing:
                    cap_id = f"SIM_EVITA_{regime}_{momentum}_{volatility}_{trend}_{ASSET}"
                    c.execute("""INSERT OR REPLACE INTO capsule
                        (id,asset,livello,tipo,descrizione,trigger_json,azione_json,
                         priority,enabled,samples,wr,pnl_avg,created_ts,note)
                        VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?,?)""",
                        (cap_id, ASSET, "SIM", "EVITA",
                         f"SIM: {contesto} WR={wr:.0%} n={n} pnl={pnl_avg:.2f}",
                         json.dumps(trigger),
                         json.dumps({"type":"blocca_entry","params":{"reason":f"SIM_WR_{wr:.0%}_n{n}"}}),
                         4, n, round(wr,3), round(pnl_avg,4),
                         time.time(),
                         f"Simulatore {datetime.now().strftime('%Y%m%d_%H%M')}"))
                    capsule_generate.append(("EVITA", cap_id, wr, n, regime))

            # MIGLIORA: PROFIT_LOCK presente in >20% dei trade vincenti
            elif profit_lock_pct > 0.20 and wr > 0.20:
                cap_id = f"SIM_MIGLIORA_{regime}_{momentum}_{volatility}_{trend}_{ASSET}"
                c.execute("""INSERT OR REPLACE INTO capsule
                    (id,asset,livello,tipo,descrizione,trigger_json,azione_json,
                     priority,enabled,samples,wr,pnl_avg,created_ts,note)
                    VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?,?)""",
                    (cap_id, ASSET, "SIM", "MIGLIORA_EXIT",
                     f"SIM: {contesto} profit_lock={profit_lock_pct:.0%} wr={wr:.0%}",
                     json.dumps(trigger),
                     json.dumps({"type":"adjust_profit_lock","params":{"retreat":0.30}}),
                     3, n, round(wr,3), round(pnl_avg,4),
                     time.time(),
                     f"Simulatore {datetime.now().strftime('%Y%m%d_%H%M')}"))
                capsule_generate.append(("MIGLIORA", cap_id, wr, n, regime))

        conn.commit()
        conn.close()

    except Exception as e:
        print(f"[SIM] Errore capsule DB: {e}")

    return capsule_generate


# ─────────────────────────────────────────────────────────────────────────────
# SIMULATORE PRINCIPALE
# ─────────────────────────────────────────────────────────────────────────────
def simula_regime(regime: str, n_cicli: int, oracolo: MiniOracolo, campo: MiniCampo) -> dict:
    """
    Simula n_cicli trade per un regime specifico.
    Ritorna statistiche complete.
    """
    prezzi = genera_prezzi(regime, n_cicli + 100, prezzo_base=80000.0)

    stats = defaultdict(lambda: {
        "n":0,"wins":0,"losses":0,"pnl_tot":0.0,
        "profit_lock":0,"pnl_list":[]
    })

    trade_log = []
    seed_scorer_prices = []

    for i in range(50, n_cicli + 50):
        price = prezzi[i]
        seed_scorer_prices.append(price)
        if len(seed_scorer_prices) > 50:
            seed_scorer_prices.pop(0)

        momentum, volatility, trend = classifica_contesto(prezzi, i)

        # 1. Decide direzione
        if regime == "TRENDING_BEAR":
            direction = "SHORT" if trend in ("DOWN", "SIDEWAYS") else "LONG"
        elif regime == "EXPLOSIVE":
            direction = "SHORT" if (trend == "DOWN" and momentum == "FORTE") else "LONG"
        else:
            direction = "LONG"

        # 2. Seed score
        if len(seed_scorer_prices) >= 20:
            recent = seed_scorer_prices[-5:]
            up = sum(1 for j in range(len(recent)-1) if recent[j+1] > recent[j])
            seed_score = 0.3 + up * 0.14
        else:
            seed_score = 0.45

        # 3. RSI
        if len(seed_scorer_prices) >= 15:
            changes = [seed_scorer_prices[j+1]-seed_scorer_prices[j]
                      for j in range(len(seed_scorer_prices)-1)][-14:]
            gains  = [c for c in changes if c > 0]
            losses = [-c for c in changes if c < 0]
            ag = sum(gains)/14 if gains else 0
            al = sum(losses)/14 if losses else 0.001
            rsi = 100 - (100/(1+ag/al))
        else:
            rsi = 50.0

        # 4. Score con direzione corretta
        fp_wr = oracolo.get_wr(momentum, volatility, trend, direction)
        score, soglia = campo.score(momentum, volatility, trend, regime,
                                    fp_wr, seed_score, rsi, direction=direction)

        if score < soglia:
            continue  # no entry

        # Simula trade
        entry_price = price

        # Genera movimento post-entry
        n_exit = min(i + random.randint(10, 60), len(prezzi)-1)
        exit_prices = prezzi[i+1:n_exit+1]
        if not exit_prices:
            continue

        max_price = max(exit_prices)
        min_price = min(exit_prices)
        exit_price = exit_prices[-1]

        # PROFIT_LOCK con direzione corretta
        used_profit_lock = False
        if direction == "LONG":
            peak_pnl = (max_price - entry_price) * (EXPOSURE / entry_price)
            if peak_pnl > 0:
                for ep in exit_prices:
                    retreat = (max_price - ep) / max(max_price - entry_price, 0.001)
                    if retreat > 0.30 and (ep - entry_price) * (EXPOSURE/entry_price) > FEE_TRADE:
                        exit_price = ep
                        used_profit_lock = True
                        break
        else:  # SHORT
            peak_pnl = (entry_price - min_price) * (EXPOSURE / entry_price)
            if peak_pnl > 0:
                for ep in exit_prices:
                    retreat = (ep - min_price) / max(entry_price - min_price, 0.001)
                    if retreat > 0.30 and (entry_price - ep) * (EXPOSURE/entry_price) > FEE_TRADE:
                        exit_price = ep
                        used_profit_lock = True
                        break

        if direction == "LONG":
            delta = exit_price - entry_price
        else:
            delta = entry_price - exit_price

        pnl_lordo = delta * (EXPOSURE / entry_price)
        pnl_netto = pnl_lordo - FEE_TRADE
        is_win    = pnl_netto > 0

        key = f"{regime}|{direction}|{momentum}|{volatility}|{trend}"
        stats[key]["n"] += 1
        stats[key]["pnl_tot"] += pnl_netto
        stats[key]["pnl_list"].append(pnl_netto)
        if is_win:
            stats[key]["wins"] += 1
        else:
            stats[key]["losses"] += 1
        if used_profit_lock:
            stats[key]["profit_lock"] += 1

        oracolo.record(momentum, volatility, trend, is_win, direction, pnl_netto)

        trade_log.append({
            "regime": regime, "momentum": momentum,
            "volatility": volatility, "trend": trend,
            "score": score, "soglia": soglia,
            "entry": entry_price, "exit": exit_price,
            "pnl": round(pnl_netto, 4), "is_win": is_win,
            "profit_lock": used_profit_lock,
        })

    # Aggrega risultati
    per_contesto = {}
    for key, s in stats.items():
        n = s["n"]
        if n == 0:
            continue
        per_contesto[key] = {
            "n": n,
            "wr": round(s["wins"]/n, 3),
            "pnl_avg": round(s["pnl_tot"]/n, 4),
            "pnl_tot": round(s["pnl_tot"], 2),
            "profit_lock_pct": round(s["profit_lock"]/n, 3),
            "wins": s["wins"],
            "losses": s["losses"],
        }

    tot_trade = sum(s["n"] for s in stats.values())
    tot_wins  = sum(s["wins"] for s in stats.values())
    tot_pnl   = sum(s["pnl_tot"] for s in stats.values())

    return {
        "regime":       regime,
        "cicli":        n_cicli,
        "tot_trade":    tot_trade,
        "tot_wins":     tot_wins,
        "wr_globale":   round(tot_wins/max(tot_trade,1), 3),
        "pnl_totale":   round(tot_pnl, 2),
        "per_contesto": per_contesto,
        "trade_log":    trade_log[:100],  # primi 100 per debug
    }


# ─────────────────────────────────────────────────────────────────────────────
# SCRITTURA RISULTATI NEL DB
# ─────────────────────────────────────────────────────────────────────────────
def scrivi_risultati_db(risultati: list, db_path: str):
    """Scrive i risultati della simulazione nel DB come tabella sim_results."""
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sim_results (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_ts      REAL,
                regime      TEXT,
                contesto    TEXT,
                n_trade     INTEGER,
                wins        INTEGER,
                losses      INTEGER,
                wr          REAL,
                pnl_avg     REAL,
                pnl_tot     REAL,
                profit_lock_pct REAL,
                note        TEXT
            )
        """)
        run_ts = time.time()
        for r in risultati:
            for contesto, stats in r["per_contesto"].items():
                conn.execute("""
                    INSERT INTO sim_results
                    (run_ts,regime,contesto,n_trade,wins,losses,wr,pnl_avg,pnl_tot,profit_lock_pct,note)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """, (run_ts, r["regime"], contesto,
                      stats["n"], stats["wins"], stats["losses"],
                      stats["wr"], stats["pnl_avg"], stats["pnl_tot"],
                      stats["profit_lock_pct"],
                      f"sim_{datetime.now().strftime('%Y%m%d_%H%M')}"))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[SIM] Errore scrittura DB: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# REPORT FINALE
# ─────────────────────────────────────────────────────────────────────────────
def stampa_report(risultati: list, capsule: list):
    sep = "═" * 70
    print(f"\n{sep}")
    print("  OVERTOP BASSANO — REPORT SIMULAZIONE")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Size trade: ${TRADE_SIZE_USD} × {LEVERAGE}x = ${EXPOSURE} esposizione")
    print(f"  Fee per trade: ${FEE_TRADE:.2f}")
    print(sep)

    tot_pnl_globale = 0.0

    for r in risultati:
        print(f"\n{'─'*70}")
        print(f"  REGIME: {r['regime']:15} | {r['tot_trade']:4} trade | "
              f"WR {r['wr_globale']:.0%} | PnL ${r['pnl_totale']:+.2f}")
        print(f"{'─'*70}")
        tot_pnl_globale += r["pnl_totale"]

        # Ordina per n trade decrescente
        sorted_ctx = sorted(r["per_contesto"].items(),
                            key=lambda x: x[1]["n"], reverse=True)
        for ctx, s in sorted_ctx[:10]:
            parts  = ctx.split("|")
            ctx_short = "|".join(parts[1:]) if len(parts) > 1 else ctx
            pl_flag = f" 🎯{s['profit_lock_pct']:.0%}PL" if s["profit_lock_pct"] > 0.1 else ""
            verdict = "✅" if s["wr"] > 0.45 else ("⚠️" if s["wr"] > 0.20 else "❌")
            print(f"  {verdict} {ctx_short:30} n={s['n']:4} WR={s['wr']:.0%} "
                  f"pnl_avg=${s['pnl_avg']:+.2f} tot=${s['pnl_tot']:+.2f}{pl_flag}")

    print(f"\n{sep}")
    print(f"  PnL TOTALE SIMULAZIONE: ${tot_pnl_globale:+.2f}")
    print(f"{sep}")

    if capsule:
        print(f"\n  CAPSULE GENERATE ({len(capsule)}):")
        for tipo, cap_id, wr, n, regime in capsule:
            emoji = "🚫" if tipo == "EVITA" else "🎯"
            print(f"  {emoji} [{tipo}] {cap_id}")
            print(f"       regime={regime} wr={wr:.0%} n={n}")
    else:
        print("\n  Nessuna capsule generata — contesti non abbastanza definiti.")

    print(f"\n{sep}\n")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="OVERTOP Simulator")
    parser.add_argument("--db",     default="/var/data/trading_data.db",
                        help="Path al database SQLite")
    parser.add_argument("--cicli",  type=int, default=10000,
                        help="Cicli per regime (default: 10000)")
    parser.add_argument("--seed",   type=int, default=42,
                        help="Random seed per riproducibilità")
    args = parser.parse_args()

    random.seed(args.seed)

    print(f"\n🚀 OVERTOP SIMULATOR — {args.cicli} cicli × 4 regimi")
    print(f"   DB: {args.db}")
    print(f"   Seed: {args.seed}\n")

    oracolo = MiniOracolo()
    campo   = MiniCampo()
    risultati = []

    regimi = ["RANGING", "TRENDING_BULL", "TRENDING_BEAR", "EXPLOSIVE"]

    for regime in regimi:
        print(f"  ⏳ Simulazione {regime}...", end="", flush=True)
        t0 = time.time()
        r = simula_regime(regime, args.cicli, oracolo, campo)
        print(f" {time.time()-t0:.1f}s → {r['tot_trade']} trade WR={r['wr_globale']:.0%} PnL=${r['pnl_totale']:+.2f}")
        risultati.append(r)

    # Scrivi nel DB
    print("\n  💾 Scrittura risultati nel DB...")
    scrivi_risultati_db(risultati, args.db)

    # Genera capsule
    print("  🧬 Generazione capsule dal report...")
    report_globale = {
        "per_contesto": {}
    }
    for r in risultati:
        report_globale["per_contesto"].update(r["per_contesto"])

    capsule = genera_capsule_dal_report(report_globale, args.db)

    # Report finale
    stampa_report(risultati, capsule)

    print(f"  ✅ Simulazione completata. Risultati in tabella sim_results.")
    print(f"  Query verifica: SELECT contesto,wr,pnl_avg,n_trade FROM sim_results ORDER BY wr DESC;\n")


if __name__ == "__main__":
    main()

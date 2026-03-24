#!/usr/bin/env python3
"""
INJECT_BRAIN — Iniezione dati storici nell'Oracolo
====================================================
Problema: il bot in paper ha 4 trade reali. I motori di intelligenza
(MIN_HOLD, exit_too_early feedback, profit tolleranza) sono ciechi.

Soluzione: inietta memoria sintetica credibile basata sui WR reali
del test retroattivo V14 (10.058 trade su SINAPSI_STORICA).

I dati iniettati hanno peso ridotto (samples decay) rispetto ai trade
reali — non sostituiscono l'esperienza, la innescano.

USO:
  python inject_brain.py --db /path/to/trading_data.db
  python inject_brain.py --db trading_data.db --dry-run
"""

import sqlite3
import json
import random
import time
import argparse
from collections import deque

random.seed(42)

# ============================================================================
# DATI STORICI CALIBRATI
# Source: test retroattivo V14 su 10.058 trade SINAPSI_STORICA
# WR reale | PnL medio win | PnL medio loss | Durata media win (s)
# ============================================================================

FINGERPRINT_DATA = {
    # ── TRENDING / UP ────────────────────────────────────────────────────
    "LONG|FORTE|BASSA|UP":       {"wr": 0.78, "pnl_win": 22.0, "pnl_loss": -8.0,  "dur_win": 45, "dur_loss": 18, "n": 120},
    "LONG|FORTE|MEDIA|UP":       {"wr": 0.68, "pnl_win": 16.0, "pnl_loss": -9.0,  "dur_win": 38, "dur_loss": 20, "n": 90},
    "LONG|FORTE|ALTA|UP":        {"wr": 0.55, "pnl_win": 24.0, "pnl_loss": -14.0, "dur_win": 28, "dur_loss": 12, "n": 60},
    "LONG|MEDIO|BASSA|UP":       {"wr": 0.65, "pnl_win": 14.0, "pnl_loss": -8.0,  "dur_win": 35, "dur_loss": 22, "n": 75},
    "LONG|MEDIO|MEDIA|UP":       {"wr": 0.58, "pnl_win": 12.0, "pnl_loss": -8.0,  "dur_win": 30, "dur_loss": 18, "n": 55},
    "LONG|DEBOLE|BASSA|UP":      {"wr": 0.55, "pnl_win": 10.0, "pnl_loss": -7.0,  "dur_win": 28, "dur_loss": 20, "n": 40},

    # ── RANGING / SIDEWAYS ───────────────────────────────────────────────
    "LONG|FORTE|ALTA|SIDEWAYS":  {"wr": 0.52, "pnl_win": 8.0,  "pnl_loss": -7.0,  "dur_win": 66, "dur_loss": 43, "n": 85},
    "LONG|FORTE|MEDIA|SIDEWAYS": {"wr": 0.60, "pnl_win": 10.0, "pnl_loss": -7.0,  "dur_win": 55, "dur_loss": 38, "n": 50},
    "LONG|FORTE|BASSA|SIDEWAYS": {"wr": 0.65, "pnl_win": 12.0, "pnl_loss": -6.0,  "dur_win": 60, "dur_loss": 35, "n": 40},
    "LONG|MEDIO|ALTA|SIDEWAYS":  {"wr": 0.43, "pnl_win": 7.0,  "pnl_loss": -8.0,  "dur_win": 70, "dur_loss": 45, "n": 70},
    "LONG|MEDIO|MEDIA|SIDEWAYS": {"wr": 0.45, "pnl_win": 7.0,  "pnl_loss": -7.0,  "dur_win": 55, "dur_loss": 40, "n": 45},
    "LONG|MEDIO|BASSA|SIDEWAYS": {"wr": 0.50, "pnl_win": 8.0,  "pnl_loss": -7.0,  "dur_win": 50, "dur_loss": 38, "n": 30},
    "LONG|DEBOLE|ALTA|SIDEWAYS": {"wr": 0.19, "pnl_win": 5.0,  "pnl_loss": -6.0,  "dur_win": 35, "dur_loss": 25, "n": 60},
    "LONG|DEBOLE|MEDIA|SIDEWAYS":{"wr": 0.35, "pnl_win": 5.0,  "pnl_loss": -6.0,  "dur_win": 35, "dur_loss": 28, "n": 35},
    "LONG|DEBOLE|BASSA|SIDEWAYS":{"wr": 0.55, "pnl_win": 7.0,  "pnl_loss": -6.0,  "dur_win": 40, "dur_loss": 30, "n": 25},

    # ── EXPLOSIVE ────────────────────────────────────────────────────────
    "LONG|FORTE|ALTA|UP":        {"wr": 0.55, "pnl_win": 28.0, "pnl_loss": -16.0, "dur_win": 22, "dur_loss": 10, "n": 45},
    "LONG|MEDIO|ALTA|UP":        {"wr": 0.50, "pnl_win": 20.0, "pnl_loss": -14.0, "dur_win": 25, "dur_loss": 12, "n": 35},

    # ── BEAR / DOWN ──────────────────────────────────────────────────────
    "LONG|FORTE|BASSA|DOWN":     {"wr": 0.60, "pnl_win": 10.0, "pnl_loss": -8.0,  "dur_win": 20, "dur_loss": 12, "n": 30},
    "LONG|MEDIO|BASSA|DOWN":     {"wr": 0.45, "pnl_win": 8.0,  "pnl_loss": -7.0,  "dur_win": 15, "dur_loss": 10, "n": 25},
    "LONG|FORTE|MEDIA|DOWN":     {"wr": 0.50, "pnl_win": 9.0,  "pnl_loss": -8.0,  "dur_win": 18, "dur_loss": 12, "n": 20},

    # ── RANGING TOSSICI — il cimitero degli scalper ──────────────────────
    # RANGING+ALTA = mercato laterale con volatilità alta = massima entropia
    # Score massimo raggiungibile ~65. Il sistema entra con 58-62 e perde sempre.
    # Iniettati con n alto e WR basso per far scattare subito il FANTASMA.
    "LONG|FORTE|ALTA|SIDEWAYS":  {"wr": 0.35, "pnl_win": 4.0,  "pnl_loss": -8.0,  "dur_win": 40, "dur_loss": 25, "n": 150},
    "LONG|MEDIO|ALTA|SIDEWAYS":  {"wr": 0.28, "pnl_win": 3.0,  "pnl_loss": -7.0,  "dur_win": 35, "dur_loss": 22, "n": 150},
    "LONG|DEBOLE|ALTA|SIDEWAYS": {"wr": 0.12, "pnl_win": 2.0,  "pnl_loss": -6.0,  "dur_win": 20, "dur_loss": 18, "n": 120},

    # ── SHORT (dati limitati ma velenosi) ────────────────────────────────
    "SHORT|MEDIO|ALTA|SIDEWAYS": {"wr": 0.08, "pnl_win": 5.0,  "pnl_loss": -8.0,  "dur_win": 20, "dur_loss": 20, "n": 25},
    "SHORT|FORTE|ALTA|DOWN":     {"wr": 0.35, "pnl_win": 15.0, "pnl_loss": -12.0, "dur_win": 18, "dur_loss": 10, "n": 20},
    "SHORT|DEBOLE|ALTA|DOWN":    {"wr": 0.40, "pnl_win": 12.0, "pnl_loss": -10.0, "dur_win": 15, "dur_loss": 8,  "n": 15},
}

# Matrimoni con WR/trust storici
MATRIMONI_DATA = {
    "STRONG_BULL":    {"wr": 0.78, "n": 120, "trust": 85},
    "STRONG_MED":     {"wr": 0.68, "n": 90,  "trust": 75},
    "MEDIUM_BULL":    {"wr": 0.65, "n": 75,  "trust": 70},
    "CAUTIOUS":       {"wr": 0.58, "n": 55,  "trust": 60},
    "RANGE_VOL_F":    {"wr": 0.52, "n": 85,  "trust": 55},
    "RANGE_MED_F":    {"wr": 0.60, "n": 50,  "trust": 60},
    "RANGE_VOL_M":    {"wr": 0.43, "n": 70,  "trust": 45},
    "RANGE_NEUTRAL":  {"wr": 0.45, "n": 45,  "trust": 45},
    "RANGE_VOL_W":    {"wr": 0.19, "n": 60,  "trust": 20},
    "WEAK_NEUTRAL":   {"wr": 0.35, "n": 35,  "trust": 35},
    "STRONG_VOLATILE":{"wr": 0.55, "n": 45,  "trust": 55},
    "PANIC":          {"wr": 0.15, "n": 30,  "trust": 10},
    "TRAP":           {"wr": 0.05, "n": 25,  "trust": 5},
}

DECAY = 0.95

def build_oracolo_entry(fp: str, data: dict) -> dict:
    """
    Costruisce una entry dell'Oracolo con distribuzione realistica.
    I dati sintetici hanno peso = n * 0.3 (30% del peso reale equivalente)
    così i trade live li superano rapidamente.
    """
    n = data["n"]
    wr = data["wr"]
    pnl_win = data["pnl_win"]
    pnl_loss = data["pnl_loss"]
    dur_win = data["dur_win"]
    dur_loss = data["dur_loss"]

    # Peso sintetico: vale 25% di un trade reale (invecchiato ma credibile)
    # Non applichiamo decay multipli — costruiamo direttamente i valori finali
    # coerenti con il WR dichiarato.
    peso = n * 0.25

    samples    = peso
    wins_count = peso * wr
    # PnL medio ponderato
    pnl_sum = (wins_count * pnl_win + (samples - wins_count) * pnl_loss)

    # Distribuzioni di durata realistiche
    durs_win = []
    for _ in range(min(20, int(peso * wr))):
        d = random.gauss(dur_win, dur_win * 0.25)
        durs_win.append(max(5, d))

    durs_loss = []
    for _ in range(min(20, int(peso * (1 - wr)))):
        d = random.gauss(dur_loss, dur_loss * 0.25)
        durs_loss.append(max(3, d))

    # RSI tipici per questo pattern
    rsi_win  = [random.gauss(52, 8) for _ in range(min(15, len(durs_win)))]
    rsi_loss = [random.gauss(58, 10) for _ in range(min(15, len(durs_loss)))]

    # Drift tipici
    if "UP" in fp:
        drift_win  = [random.gauss(0.08, 0.04) for _ in range(min(15, len(durs_win)))]
        drift_loss = [random.gauss(-0.02, 0.05) for _ in range(min(15, len(durs_loss)))]
    elif "DOWN" in fp:
        drift_win  = [random.gauss(-0.06, 0.04) for _ in range(min(15, len(durs_win)))]
        drift_loss = [random.gauss(0.02, 0.05) for _ in range(min(15, len(durs_loss)))]
    else:
        drift_win  = [random.gauss(0.02, 0.05) for _ in range(min(15, len(durs_win)))]
        drift_loss = [random.gauss(-0.03, 0.05) for _ in range(min(15, len(durs_loss)))]

    # Post-trade: stima exit_too_early dal WR
    # Pattern ad alto WR tendono ad uscire meno presto
    n_post = min(10, int(peso * 0.5))
    post_continued = [random.random() < max(0.1, 0.6 - wr * 0.5) for _ in range(n_post)]
    post_delta = [random.gauss(2.0, 3.0) for _ in range(n_post)]

    return {
        "wins":        round(wins_count, 2),
        "samples":     round(samples, 2),
        "pnl_sum":     round(pnl_sum, 2),
        "real_samples": 0,  # zero reali — sono sintetici
        "durations_win":   durs_win,
        "durations_loss":  durs_loss,
        "rsi_win":         rsi_win,
        "rsi_loss":        rsi_loss,
        "drift_win":       drift_win,
        "drift_loss":      drift_loss,
        "range_pos_win":   [random.uniform(0.3, 0.8) for _ in range(len(durs_win))],
        "range_pos_loss":  [random.uniform(0.2, 0.7) for _ in range(len(durs_loss))],
        "post_continued":  post_continued,
        "post_delta":      post_delta,
    }


def build_memoria_matrimoni(data: dict) -> dict:
    """Costruisce la memoria dei matrimoni con WR/trust storici."""
    trust = {}
    wins = {}
    losses = {}
    wr_history = {}
    separazione = {}
    blacklist = {}

    for mat, d in data.items():
        n = d["n"]
        wr = d["wr"]
        peso = n * 0.3

        w = int(peso * wr)
        l = int(peso * (1 - wr))

        trust[mat] = d["trust"]
        wins[mat] = w
        losses[mat] = l
        wr_history[mat] = [round(wr + random.gauss(0, 0.05), 3) for _ in range(min(10, w + l))]
        separazione[mat] = 0
        blacklist[mat] = 0

    return {
        "trust":       trust,
        "separazione": separazione,
        "blacklist":   blacklist,
        "divorzio":    [],
        "wins":        wins,
        "losses":      losses,
        "wr_history":  wr_history,
    }


def inject(db_path: str, dry_run: bool = False):
    print(f"\n{'='*60}")
    print(f"  INJECT_BRAIN — {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"  DB: {db_path}")
    print(f"{'='*60}\n")

    # Build oracolo
    oracolo_data = {}
    for fp, data in FINGERPRINT_DATA.items():
        entry = build_oracolo_entry(fp, data)
        oracolo_data[fp] = entry
        wr_eff = entry["wins"] / entry["samples"] if entry["samples"] > 0 else 0
        early = sum(1 for c in entry["post_continued"] if c) / max(1, len(entry["post_continued"]))
        dur_w = sum(entry["durations_win"]) / max(1, len(entry["durations_win"]))
        print(f"  {fp:<35} WR={wr_eff:.0%} dur_win={dur_w:.0f}s "
              f"exit_early={early:.0%} samples={entry['samples']:.1f}")

    # Build memoria matrimoni
    memoria_data = build_memoria_matrimoni(MATRIMONI_DATA)
    print(f"\n  Matrimoni iniettati: {len(memoria_data['trust'])}")

    if dry_run:
        print("\n  [DRY RUN] Nessuna scrittura su DB.")
        return

    # Scrivi su DB — merge con dati esistenti (non sovrascrivere)
    try:
        conn = sqlite3.connect(db_path)
        rows = dict(conn.execute("SELECT key, value FROM bot_state").fetchall())

        # Merge Oracolo: se esiste già un fingerprint con real_samples > 0, mantieni i reali
        if 'oracolo' in rows:
            existing = json.loads(rows['oracolo'])
            merged = dict(oracolo_data)  # inizia con sintetici
            for fp, real_entry in existing.items():
                if real_entry.get('real_samples', 0) > 0:
                    # Ha dati reali → mantieni i reali, aggiungi sintetici come base
                    if fp in merged:
                        # Somma i campioni: sintetici + reali
                        merged[fp]['wins']    += real_entry.get('wins', 0)
                        merged[fp]['samples'] += real_entry.get('samples', 0)
                        merged[fp]['pnl_sum'] += real_entry.get('pnl_sum', 0)
                        merged[fp]['real_samples'] = real_entry.get('real_samples', 0)
                        # Preserva deque reali
                        for field in ['durations_win','durations_loss','post_continued','post_delta']:
                            real_list = real_entry.get(field, [])
                            if real_list:
                                merged[fp][field] = real_list + merged[fp].get(field, [])
                    else:
                        merged[fp] = real_entry
                else:
                    pass  # sovrascrive con sintetici
            oracolo_data = merged
            print(f"\n  Merge con {len(existing)} fingerprint esistenti (reali preservati)")

        conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('oracolo', ?)",
                    (json.dumps(oracolo_data),))

        # Merge Memoria Matrimoni
        if 'memoria' in rows:
            existing_mem = json.loads(rows['memoria'])
            # Preserva divorzi reali
            real_divorzi = existing_mem.get('divorzio', [])
            if real_divorzi:
                memoria_data['divorzio'] = real_divorzi
                print(f"  Divorzi reali preservati: {real_divorzi}")

        conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('memoria', ?)",
                    (json.dumps(memoria_data),))

        conn.commit()
        conn.close()

        print(f"\n  ✅ Brain iniettato su {db_path}")
        print(f"  Fingerprint totali: {len(oracolo_data)}")
        print(f"  Matrimoni: {len(memoria_data['trust'])}")
        print(f"\n  Il bot ricaricherà il brain al prossimo restart")
        print(f"  oppure al prossimo save_brain (ogni 5 minuti).")

    except Exception as e:
        print(f"\n  ❌ Errore: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="trading_data.db", help="Path al DB SQLite")
    parser.add_argument("--dry-run", action="store_true", help="Mostra senza scrivere")
    args = parser.parse_args()
    inject(args.db, args.dry_run)

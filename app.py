"""
MISSION CONTROL — OVERTOP BASSANO
Server Flask per Render. Versione 2.0 — CERVELLO ATTIVO + MERCATO

Funzioni:
  - Riceve log dal bot (ENTRY, EXIT, REPORT, BLOCK)
  - Analizza trade ogni 10 minuti
  - Genera capsule nuove automaticamente
  - Le rimanda al bot via /trading/config
  - Dashboard HTML in tempo reale
"""

from flask import Flask, request, jsonify
import os
import json
import time
import threading
import operator
from datetime import datetime, timedelta
from collections import deque, defaultdict
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional

app = Flask(__name__)

# ============================================================
# STORAGE IN-MEMORY (Render non ha disco persistente)
# ============================================================

TRADING_EVENTS  = deque(maxlen=5000)   # ultimi 5000 eventi
TRADES_COMPLETI = deque(maxlen=2000)  # trade EXIT arricchiti
CAPSULE_ATTIVE: List[dict] = []       # capsule generate dall'analisi
ANALISI_LOG     = deque(maxlen=100)   # log delle analisi eseguite

# [V3.0] Dati mercato esogeni — correlazione con esiti trade
MARKET_SNAPSHOTS = deque(maxlen=5000) # snapshot funding/OI/orderbook
LAST_MARKET = {                        # ultimo snapshot ricevuto
    "funding_rate":    0.0,
    "open_interest":   0.0,
    "bid_wall":        0.0,
    "bid_wall_price":  0.0,
    "ask_wall":        0.0,
    "ask_wall_price":  0.0,
    "updated_at":      None,
}

TRADING_CONFIG = {
    "RISK_PER_TRADE":        0.015,
    "NORMAL_MIN_FORZA":      0.55,
    "NORMAL_MAX_FORZA":      0.80,
    "NORMAL_HARD_SL":        0.25,
    "NORMAL_VETO_WR":        0.50,
    "NORMAL_BOOST_WR":       0.68,
    "NORMAL_MULT_MAX":       1.3,
    "FLAT_MIN_FORZA":        0.65,
    "FLAT_MAX_FORZA":        0.75,
    "FLAT_HARD_SL":          0.20,
    "FLAT_VETO_WR":          0.30,
    "FLAT_BOOST_WR":         0.68,
    "FLAT_MULT_MAX":         1.2,
    "SW_FLAT_THRESHOLD":     0.0028,
    "SEED_THRESH_NORMAL":    0.45,
    "SEED_THRESH_FLAT":      0.50,
    "BOOST_SEED_MIN_NORMAL": 0.58,
    "FANTASMA_WR":           0.40,
    "FANTASMA_PNL":          -0.05,
    "META_ACC_THRESHOLD":    0.55,
    "META_REDUCTION":        0.8,
    "TAKE_PROFIT_R":         0.65,
    "MIN_HOLD_TIME_SL":      2.0,
    "last_updated":          None,
    "version":               "3.1-GRAFICO",
    "capsules":              [],       # <-- capsule per il bot
}

BOT_STATUS = {
    "is_running":         False,
    "last_ping":          None,
    "total_trades":       0,
    "total_pnl":          0.0,
    "wins":               0,
    "losses":             0,
    "ultima_analisi":     None,
    "capsule_generate":   0,
}

_lock = threading.Lock()

# ============================================================
# CAPSULE ENGINE (integrato nel server)
# ============================================================

_OPS = {
    '>':      operator.gt,
    '>=':     operator.ge,
    '<':      operator.lt,
    '<=':     operator.le,
    '==':     operator.eq,
    '!=':     operator.ne,
    'in':     lambda a, b: a in b,
    'not_in': lambda a, b: a not in b,
}

def _wr_pnl(trades):
    if not trades:
        return 0.0, 0.0
    return (sum(1 for t in trades if t.get('win', t.get('pnl', 0) > 0)) / len(trades),
            sum(t.get('pnl', 0) for t in trades))

def analizza_e_genera_capsule(trades: list) -> list:
    """
    Analizza batch di trade e genera capsule nuove.
    Ritorna lista di dict (capsule JSON-serializzabili).
    """
    if len(trades) < 25:
        return []

    capsule = []
    wr_globale, _ = _wr_pnl(trades)
    ids_esistenti = {c['capsule_id'] for c in CAPSULE_ATTIVE}
    MIN_CAMP = 25
    MIN_DELTA = 0.12

    # --- ANALISI REGIME ---
    regimi = defaultdict(list)
    for t in trades:
        if t.get('regime'):
            regimi[t['regime']].append(t)

    for regime, gruppo in regimi.items():
        if len(gruppo) < MIN_CAMP:
            continue
        wr, pnl = _wr_pnl(gruppo)

        cid_blocco = f"AUTO_BLOCCO_REGIME_{regime.upper()}_001"
        cid_boost  = f"AUTO_BOOST_REGIME_{regime.upper()}_001"

        if wr < 0.35 and wr < wr_globale - MIN_DELTA and cid_blocco not in ids_esistenti:
            capsule.append({
                "capsule_id":  cid_blocco,
                "version":     1,
                "descrizione": f"AUTO: regime {regime} WR={wr:.0%} su {len(gruppo)} trade — BLOCCO",
                "trigger":     [{"param": "regime", "op": "==", "value": regime}],
                "azione":      {"type": "blocca_entry", "params": {"reason": f"auto_regime_{regime}"}},
                "priority":    1, "enabled": True, "lifetime": "permanent",
                "source":      "server_analyzer", "hits": 0,
                "wins_after":  0, "losses_after": 0,
                "created_at":  time.time(),
            })

        elif wr > 0.70 and wr > wr_globale + MIN_DELTA and cid_boost not in ids_esistenti:
            capsule.append({
                "capsule_id":  cid_boost,
                "version":     1,
                "descrizione": f"AUTO: regime {regime} WR={wr:.0%} su {len(gruppo)} trade — BOOST +25%",
                "trigger":     [{"param": "regime", "op": "==", "value": regime}],
                "azione":      {"type": "modifica_size", "params": {"mult": 1.25}},
                "priority":    3, "enabled": True, "lifetime": "permanent",
                "source":      "server_analyzer", "hits": 0,
                "wins_after":  0, "losses_after": 0,
                "created_at":  time.time(),
            })

    # --- ANALISI FASCIA ORARIA ---
    fasce = defaultdict(list)
    fasce_def = {
        'mattina':    lambda o: 8  <= o < 12,
        'pomeriggio': lambda o: 12 <= o < 16,
        'sera':       lambda o: 16 <= o < 20,
        'notte':      lambda o: o >= 20 or o < 8,
    }
    for t in trades:
        ora = t.get('ora_utc', t.get('ora', -1))
        if ora < 0:
            continue
        for fname, check in fasce_def.items():
            if check(ora):
                fasce[fname].append(t)

    for fascia, gruppo in fasce.items():
        if len(gruppo) < MIN_CAMP:
            continue
        wr, pnl = _wr_pnl(gruppo)
        cid = f"AUTO_BLOCCO_ORA_{fascia.upper()}_001"
        if wr < 0.35 and pnl < -10 and cid not in ids_esistenti:
            if fascia == 'notte':
                trigger = [{"param": "ora_utc", "op": ">=", "value": 20},
                           {"param": "regime", "op": "not_in", "value": ["trending"]}]
            elif fascia == 'mattina':
                trigger = [{"param": "ora_utc", "op": ">=", "value": 8},
                           {"param": "ora_utc", "op": "<",  "value": 12}]
            elif fascia == 'pomeriggio':
                trigger = [{"param": "ora_utc", "op": ">=", "value": 12},
                           {"param": "ora_utc", "op": "<",  "value": 16}]
            else:
                trigger = [{"param": "ora_utc", "op": ">=", "value": 16},
                           {"param": "ora_utc", "op": "<",  "value": 20}]

            capsule.append({
                "capsule_id":  cid,
                "version":     1,
                "descrizione": f"AUTO: fascia {fascia} WR={wr:.0%} PnL={pnl:+.2f} — BLOCCO",
                "trigger":     trigger,
                "azione":      {"type": "blocca_entry", "params": {"reason": f"auto_ora_{fascia}"}},
                "priority":    2, "enabled": True, "lifetime": "permanent",
                "source":      "server_analyzer", "hits": 0,
                "wins_after":  0, "losses_after": 0,
                "created_at":  time.time(),
            })

    # --- ANALISI SEQUENZE LOSS ---
    per_asset = defaultdict(list)
    for t in sorted(trades, key=lambda x: x.get('entry_ts', x.get('timestamp', 0))):
        per_asset[t.get('asset', 'ALL')].append(t)

    for asset, seq in per_asset.items():
        after_3loss = []
        for i in range(3, len(seq)):
            if (not seq[i-1].get('win', seq[i-1].get('pnl', 0) > 0) and
                not seq[i-2].get('win', seq[i-2].get('pnl', 0) > 0) and
                not seq[i-3].get('win', seq[i-3].get('pnl', 0) > 0)):
                after_3loss.append(seq[i])

        if len(after_3loss) >= 8:
            wr, _ = _wr_pnl(after_3loss)
            cid = f"AUTO_BLOCCO_3LOSS_{asset}_001"
            if wr < 0.40 and cid not in ids_esistenti:
                capsule.append({
                    "capsule_id":  cid,
                    "version":     1,
                    "descrizione": f"AUTO: dopo 3 loss su {asset} WR={wr:.0%} — BLOCCO PAUSA",
                    "trigger":     [{"param": "loss_consecutivi", "op": ">=", "value": 3},
                                    {"param": "asset", "op": "==", "value": asset}],
                    "azione":      {"type": "blocca_entry", "params": {"reason": "auto_3loss_pausa"}},
                    "priority":    2, "enabled": True, "lifetime": "permanent",
                    "source":      "server_analyzer", "hits": 0,
                    "wins_after":  0, "losses_after": 0,
                    "created_at":  time.time(),
                })

    # --- ANALISI FUNDING RATE ---
    fasce_funding = {
        'molto_alto':  lambda f: f > 0.0005,
        'alto':        lambda f: 0.0002 < f <= 0.0005,
        'neutro':      lambda f: -0.0002 <= f <= 0.0002,
        'basso':       lambda f: -0.0005 <= f < -0.0002,
        'molto_basso': lambda f: f < -0.0005,
    }
    trades_con_funding = [t for t in trades if t.get('funding_rate', 0) != 0]
    if len(trades_con_funding) >= MIN_CAMP:
        for fname, check in fasce_funding.items():
            gruppo = [t for t in trades_con_funding if check(t.get('funding_rate', 0))]
            if len(gruppo) < 15:
                continue
            wr, pnl = _wr_pnl(gruppo)
            cid_b = f"AUTO_BLOCCO_FUNDING_{fname.upper()}_001"
            cid_boost = f"AUTO_BOOST_FUNDING_{fname.upper()}_001"

            if wr < 0.35 and pnl < -10 and cid_b not in ids_esistenti:
                # Calcola soglia reale dai dati
                funding_vals = [t['funding_rate'] for t in gruppo]
                soglia = sum(funding_vals) / len(funding_vals)
                op = ">" if soglia > 0 else "<"
                trigger = [{"param": "funding_rate", "op": op, "value": round(soglia, 6)}]
                capsule.append({
                    "capsule_id":  cid_b,
                    "version":     1,
                    "descrizione": f"AUTO: funding {fname} WR={wr:.0%} su {len(gruppo)} trade — BLOCCO (soglia={soglia:.4%})",
                    "trigger":     trigger,
                    "azione":      {"type": "blocca_entry", "params": {"reason": f"auto_funding_{fname}"}},
                    "priority":    1, "enabled": True, "lifetime": "permanent",
                    "source":      "server_analyzer_v3", "hits": 0,
                    "wins_after":  0, "losses_after": 0,
                    "created_at":  time.time(),
                })
            elif wr > 0.70 and pnl > 10 and cid_boost not in ids_esistenti:
                funding_vals = [t['funding_rate'] for t in gruppo]
                soglia = sum(funding_vals) / len(funding_vals)
                op = "<" if soglia > 0 else ">"
                trigger = [{"param": "funding_rate", "op": op, "value": round(soglia, 6)}]
                capsule.append({
                    "capsule_id":  cid_boost,
                    "version":     1,
                    "descrizione": f"AUTO: funding {fname} WR={wr:.0%} su {len(gruppo)} trade — BOOST +20%",
                    "trigger":     trigger,
                    "azione":      {"type": "modifica_size", "params": {"mult": 1.20}},
                    "priority":    3, "enabled": True, "lifetime": "permanent",
                    "source":      "server_analyzer_v3", "hits": 0,
                    "wins_after":  0, "losses_after": 0,
                    "created_at":  time.time(),
                })

    # --- ANALISI ORDER BOOK (muri di liquidità) ---
    trades_con_ob = [t for t in trades if t.get('ask_wall', 0) > 0]
    if len(trades_con_ob) >= MIN_CAMP:
        # Se c'era un muro ask vicino al prezzo e il trade ha perso
        loss_con_muro = [t for t in trades_con_ob
                         if not t.get('win', False) and t.get('ask_wall', 0) > 0]
        win_senza_muro = [t for t in trades_con_ob
                          if t.get('win', False) and t.get('ask_wall', 0) > 0]

        if len(loss_con_muro) >= 10:
            avg_wall_loss = sum(t['ask_wall'] for t in loss_con_muro) / len(loss_con_muro)
            avg_wall_win  = sum(t['ask_wall'] for t in win_senza_muro) / len(win_senza_muro) if win_senza_muro else 0

            # Se i loss hanno muri molto più grandi dei win → il muro è predittivo
            if avg_wall_loss > avg_wall_win * 1.5:
                soglia_muro = avg_wall_loss * 0.8
                cid_muro = "AUTO_BLOCCO_ASK_WALL_001"
                if cid_muro not in ids_esistenti:
                    capsule.append({
                        "capsule_id":  cid_muro,
                        "version":     1,
                        "descrizione": f"AUTO: muro ask > {soglia_muro:.0f} BTC predice loss — BLOCCO",
                        "trigger":     [{"param": "ask_wall", "op": ">=", "value": round(soglia_muro, 2)}],
                        "azione":      {"type": "blocca_entry", "params": {"reason": "muro_liquidita_ask"}},
                        "priority":    1, "enabled": True, "lifetime": "permanent",
                        "source":      "server_analyzer_v3", "hits": 0,
                        "wins_after":  0, "losses_after": 0,
                        "created_at":  time.time(),
                    })

    # --- ANALISI FORZA TOSSICA ---
    fasce_forza = {
        'bassa':      lambda f: f < 0.40,
        'medio_bassa':lambda f: 0.40 <= f < 0.55,
        'media':      lambda f: 0.55 <= f < 0.70,
        'alta':       lambda f: 0.70 <= f < 0.85,
        'molto_alta': lambda f: f >= 0.85,
    }
    for fname, check in fasce_forza.items():
        gruppo = [t for t in trades if check(t.get('forza', 0.5))]
        if len(gruppo) < MIN_CAMP:
            continue
        wr, pnl = _wr_pnl(gruppo)
        cid = f"AUTO_BLOCCO_FORZA_{fname.upper()}_001"
        if wr < 0.33 and pnl < -15 and cid not in ids_esistenti:
            fmin = [0, 0.40, 0.55, 0.70, 0.85][['bassa','medio_bassa','media','alta','molto_alta'].index(fname)]
            fmax = [0.40, 0.55, 0.70, 0.85, 2.0][['bassa','medio_bassa','media','alta','molto_alta'].index(fname)]
            trigger = [{"param": "forza", "op": ">=", "value": fmin},
                       {"param": "forza", "op": "<",  "value": fmax}]
            capsule.append({
                "capsule_id":  cid,
                "version":     1,
                "descrizione": f"AUTO: forza {fname} WR={wr:.0%} PnL={pnl:+.2f} — BLOCCO",
                "trigger":     trigger,
                "azione":      {"type": "blocca_entry", "params": {"reason": f"auto_forza_{fname}"}},
                "priority":    2, "enabled": True, "lifetime": "permanent",
                "source":      "server_analyzer", "hits": 0,
                "wins_after":  0, "losses_after": 0,
                "created_at":  time.time(),
            })

    return capsule


def thread_analisi_periodica():
    """Thread che gira ogni 10 minuti e genera capsule nuove."""
    while True:
        time.sleep(600)  # 10 minuti
        with _lock:
            trades = list(TRADES_COMPLETI)

        if len(trades) < 20:
            msg = f"[{datetime.now().strftime('%H:%M')}] Analisi saltata — solo {len(trades)} trade"
            ANALISI_LOG.append(msg)
            continue

        nuove = analizza_e_genera_capsule(trades)

        with _lock:
            aggiunte = 0
            for cap in nuove:
                if not any(c['capsule_id'] == cap['capsule_id'] for c in CAPSULE_ATTIVE):
                    CAPSULE_ATTIVE.append(cap)
                    aggiunte += 1

            TRADING_CONFIG['capsules'] = list(CAPSULE_ATTIVE)
            BOT_STATUS['ultima_analisi'] = datetime.now().isoformat()
            BOT_STATUS['capsule_generate'] += aggiunte

        wr_globale, pnl_globale = _wr_pnl(trades)
        msg = (f"[{datetime.now().strftime('%H:%M')}] Analisi su {len(trades)} trade | "
               f"WR={wr_globale:.0%} PnL={pnl_globale:+.2f} | "
               f"+{aggiunte} capsule nuove (tot={len(CAPSULE_ATTIVE)})")
        ANALISI_LOG.append(msg)
        print(msg)


# Avvia thread analisi
threading.Thread(target=thread_analisi_periodica, daemon=True).start()

# ============================================================
# ENDPOINTS
# ============================================================

@app.route("/trading/log", methods=["POST"])
def trading_log():
    """Riceve eventi dal bot."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON"}), 400

        event = {**data, "received_at": datetime.now().isoformat()}
        with _lock:
            TRADING_EVENTS.append(event)
            BOT_STATUS["last_ping"] = datetime.now().isoformat()
            BOT_STATUS["is_running"] = True

            # [V3.0] Aggiorna snapshot mercato
            if data.get("event_type") == "MARKET_DATA":
                with _lock:
                    LAST_MARKET.update({
                        "funding_rate":   data.get("funding_rate", 0.0),
                        "open_interest":  data.get("open_interest", 0.0),
                        "bid_wall":       data.get("bid_wall", 0.0),
                        "bid_wall_price": data.get("bid_wall_price", 0.0),
                        "ask_wall":       data.get("ask_wall", 0.0),
                        "ask_wall_price": data.get("ask_wall_price", 0.0),
                        "updated_at":     datetime.now().isoformat(),
                    })
                    MARKET_SNAPSHOTS.append({
                        **LAST_MARKET,
                        "timestamp": time.time(),
                    })

        # Accumula trade completi per analisi
            if data.get("event_type") == "EXIT":
                pnl = data.get("pnl", 0)
                BOT_STATUS["total_trades"] += 1
                BOT_STATUS["total_pnl"] += pnl
                if pnl > 0:
                    BOT_STATUS["wins"] += 1
                else:
                    BOT_STATUS["losses"] += 1

                # Arricchisce il trade con tutti i campi disponibili
                trade = {
                    "asset":           data.get("asset", "BTCUSDC"),
                    "pnl":             pnl,
                    "win":             pnl > 0,
                    "regime":          data.get("regime", data.get("modalita", "unknown")),
                    "ora_utc":         data.get("ora", datetime.now().hour),
                    "forza":           data.get("forza", 0.5),
                    "seed":            data.get("seed", 0.5),
                    "modalita":        data.get("modalita", "NORMAL"),
                    "duration":        data.get("duration", 0),
                    "reason":          data.get("reason", ""),
                    "loss_consecutivi":data.get("loss_consecutivi", 0),
                    "entry_ts":        data.get("entry_ts", time.time()),
                    "timestamp":       time.time(),
                    # [V3.0] Snapshot mercato al momento del trade
                    "funding_rate":    LAST_MARKET["funding_rate"],
                    "open_interest":   LAST_MARKET["open_interest"],
                    "bid_wall":        LAST_MARKET["bid_wall"],
                    "ask_wall":        LAST_MARKET["ask_wall"],
                }
                TRADES_COMPLETI.append(trade)

        return jsonify({
            "status": "logged",
            "total_events": len(TRADING_EVENTS),
            "capsule_attive": len(CAPSULE_ATTIVE),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/trading/config", methods=["GET"])
def trading_config_get():
    """Fornisce config + capsule al bot ogni 30 tick."""
    with _lock:
        cfg = dict(TRADING_CONFIG)
        cfg['capsules'] = list(CAPSULE_ATTIVE)
    return jsonify(cfg)


@app.route("/trading/config", methods=["POST"])
def trading_config_update():
    """Aggiorna parametri manualmente."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON"}), 400

        esclusi = {"last_updated", "version", "capsules"}
        updated = {}
        with _lock:
            for key, value in data.items():
                if key not in esclusi and key in TRADING_CONFIG:
                    old = TRADING_CONFIG[key]
                    TRADING_CONFIG[key] = value
                    updated[key] = {"old": old, "new": value}
            if updated:
                TRADING_CONFIG["last_updated"] = datetime.now().isoformat()
                TRADING_EVENTS.append({
                    "timestamp":  datetime.now().isoformat(),
                    "event_type": "CONFIG_UPDATE",
                    "changes":    updated,
                })

        return jsonify({"status": "updated", "changes": updated})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/trading/capsule", methods=["POST"])
def aggiungi_capsule():
    """Aggiunge una capsula manualmente (da Claude o dashboard)."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON"}), 400

        with _lock:
            # Aggiorna se esiste, altrimenti aggiunge
            ids = [c['capsule_id'] for c in CAPSULE_ATTIVE]
            if data.get('capsule_id') in ids:
                idx = ids.index(data['capsule_id'])
                if data.get('version', 0) > CAPSULE_ATTIVE[idx].get('version', 0):
                    CAPSULE_ATTIVE[idx] = data
                    action = "updated"
                else:
                    action = "skipped_old_version"
            else:
                CAPSULE_ATTIVE.append(data)
                action = "added"
            TRADING_CONFIG['capsules'] = list(CAPSULE_ATTIVE)

        return jsonify({"status": action, "total_capsule": len(CAPSULE_ATTIVE)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/trading/capsule/<capsule_id>", methods=["DELETE"])
def rimuovi_capsule(capsule_id):
    """Disabilita una capsula."""
    with _lock:
        for c in CAPSULE_ATTIVE:
            if c['capsule_id'] == capsule_id:
                c['enabled'] = False
                TRADING_CONFIG['capsules'] = list(CAPSULE_ATTIVE)
                return jsonify({"status": "disabled", "capsule_id": capsule_id})
    return jsonify({"error": "not found"}), 404


@app.route("/trading/status", methods=["GET"])
def trading_status():
    """Stato completo per analisi esterna."""
    try:
        minutes = request.args.get("minutes", 30, type=int)
        cutoff = datetime.now() - timedelta(minutes=minutes)

        with _lock:
            recent = [e for e in TRADING_EVENTS
                      if datetime.fromisoformat(e.get("received_at", e.get("timestamp", "2000-01-01"))) > cutoff]

        entries = [e for e in recent if e.get("event_type") == "ENTRY"]
        exits   = [e for e in recent if e.get("event_type") == "EXIT"]
        blocks  = [e for e in recent if e.get("event_type") == "BLOCK"]

        exits_win  = [e for e in exits if e.get("pnl", 0) > 0]
        exits_loss = [e for e in exits if e.get("pnl", 0) <= 0]
        wr = len(exits_win) / len(exits) * 100 if exits else 0
        pnl_medio = sum(e.get("pnl", 0) for e in exits) / len(exits) if exits else 0

        block_types = defaultdict(int)
        for b in blocks:
            block_types[str(b.get("block_reason", "unknown"))] += 1

        with _lock:
            status = dict(BOT_STATUS)
            trades_sample = list(TRADES_COMPLETI)[-10:]
            analisi = list(ANALISI_LOG)
            capsule = list(CAPSULE_ATTIVE)

        return jsonify({
            "timestamp":      datetime.now().isoformat(),
            "bot_status":     status,
            "periodo_minuti": minutes,
            "metriche": {
                "entries":     len(entries),
                "exits":       len(exits),
                "wins":        len(exits_win),
                "losses":      len(exits_loss),
                "win_rate":    round(wr, 1),
                "pnl_medio":   round(pnl_medio, 4),
                "blocks":      len(blocks),
                "block_types": dict(block_types),
            },
            "capsule_attive":  capsule,
            "ultimi_log_analisi": analisi[-10:],
            "ultimi_10_trade": trades_sample,
            "ultimi_5_eventi": list(TRADING_EVENTS)[-5:],
            "mercato":         dict(LAST_MARKET),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/trading/analisi_ora", methods=["POST"])
def forza_analisi():
    """Forza un'analisi immediata (chiamabile manualmente)."""
    with _lock:
        trades = list(TRADES_COMPLETI)

    if len(trades) < 10:
        return jsonify({"error": f"Solo {len(trades)} trade — minimo 10"}), 400

    nuove = analizza_e_genera_capsule(trades)

    with _lock:
        aggiunte = 0
        for cap in nuove:
            if not any(c['capsule_id'] == cap['capsule_id'] for c in CAPSULE_ATTIVE):
                CAPSULE_ATTIVE.append(cap)
                aggiunte += 1
        TRADING_CONFIG['capsules'] = list(CAPSULE_ATTIVE)
        BOT_STATUS['ultima_analisi'] = datetime.now().isoformat()
        BOT_STATUS['capsule_generate'] += aggiunte

    wr, pnl = _wr_pnl(trades)
    msg = (f"[MANUALE {datetime.now().strftime('%H:%M')}] "
           f"{len(trades)} trade | WR={wr:.0%} PnL={pnl:+.2f} | "
           f"+{aggiunte} capsule (tot={len(CAPSULE_ATTIVE)})")
    ANALISI_LOG.append(msg)

    return jsonify({
        "status":          "analisi_completata",
        "trade_analizzati":len(trades),
        "wr_globale":      round(wr, 3),
        "pnl_globale":     round(pnl, 2),
        "capsule_nuove":   aggiunte,
        "capsule_totali":  len(CAPSULE_ATTIVE),
        "nuove_capsule":   nuove,
        "log":             msg,
    })


@app.route("/trading/chart_data")
def chart_data():
    """Dati per il grafico equity + entry/exit."""
    with _lock:
        trades = list(TRADES_COMPLETI)
        events = list(TRADING_EVENTS)

    # Costruisci curva equity
    equity = []
    capitale = 10000.0
    for t in trades:
        capitale += t.get('pnl', 0)
        equity.append({
            "ts":      t.get('timestamp', time.time()),
            "equity":  round(capitale, 2),
            "pnl":     round(t.get('pnl', 0), 4),
            "win":     t.get('win', False),
            "reason":  t.get('reason', ''),
            "regime":  t.get('regime', ''),
            "modalita":t.get('modalita', ''),
        })

    # Entry/exit dagli eventi
    markers = []
    for e in events:
        if e.get('event_type') in ('ENTRY', 'EXIT', 'BLOCK'):
            markers.append({
                "ts":    e.get('timestamp', e.get('received_at', '')),
                "type":  e.get('event_type'),
                "price": e.get('entry_price', e.get('exit_price', 0)),
                "pnl":   e.get('pnl', 0),
                "reason":e.get('reason', e.get('block_reason', '')),
            })

    return jsonify({
        "equity":  equity,
        "markers": markers[-200:],
        "summary": {
            "capitale_attuale": round(capitale, 2),
            "trade_totali":     len(trades),
            "wins":             sum(1 for t in trades if t.get('win')),
            "losses":           sum(1 for t in trades if not t.get('win')),
        }
    })


@app.route("/trading/dashboard")
def dashboard():
    """Dashboard HTML in tempo reale."""
    return """<!DOCTYPE html>
<html>
<head>
    <title>Mission Control — OVERTOP BASSANO</title>
    <meta http-equiv="refresh" content="10">
    <style>
        body { font-family: monospace; background: #0a0a0a; color: #00ff00; padding: 20px; margin: 0; }
        h1 { color: #00ffff; border-bottom: 1px solid #333; padding-bottom: 10px; }
        h2 { color: #ffff00; margin-top: 20px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; margin: 10px 0; }
        .card { background: #1a1a1a; padding: 12px; border-radius: 6px; border: 1px solid #333; }
        .label { color: #888; font-size: 11px; }
        .value { font-size: 18px; font-weight: bold; }
        .profit { color: #00ff00; }
        .loss { color: #ff4444; }
        .warn { color: #ffaa00; }
        pre { background: #111; padding: 10px; border-radius: 4px; overflow-x: auto; font-size: 12px; max-height: 300px; overflow-y: auto; }
        .capsule { background: #0a1a0a; border: 1px solid #0f5; padding: 8px; margin: 4px 0; border-radius: 4px; font-size: 12px; }
        .capsule.blocca { border-color: #f44; background: #1a0a0a; }
        .capsule.modifica { border-color: #0f0; }
        button { background: #1a3a1a; color: #0f0; border: 1px solid #0f0; padding: 8px 16px; cursor: pointer; border-radius: 4px; margin: 4px; }
        button:hover { background: #2a5a2a; }
        button.danger { background: #3a1a1a; color: #f44; border-color: #f44; }
    </style>
</head>
<body>
    <h1>🚀 MISSION CONTROL — OVERTOP BASSANO</h1>
    <div id="content">Caricamento...</div>
    <script>
    function aggiorna() {
        fetch('/trading/status?minutes=60')
        .then(r => r.json())
        .then(d => {
            const m = d.metriche;
            const b = d.bot_status;
            const wr_class = m.win_rate >= 50 ? 'profit' : 'loss';
            const pnl_class = b.total_pnl >= 0 ? 'profit' : 'loss';

            let capsule_html = '';
            if (d.capsule_attive && d.capsule_attive.length > 0) {
                d.capsule_attive.forEach(c => {
                    const tipo = c.azione && c.azione.type === 'blocca_entry' ? 'blocca' : 'modifica';
                    const icona = tipo === 'blocca' ? '🔴' : '🟢';
                    const stato = c.enabled ? '' : ' [DISABILITATA]';
                    capsule_html += `<div class="capsule ${tipo}">
                        ${icona} <b>${c.capsule_id}</b>${stato}<br>
                        <span style="color:#888">${c.descrizione}</span><br>
                        Hit: ${c.hits || 0} | Win: ${c.wins_after || 0} | Loss: ${c.losses_after || 0}
                        <button class="danger" onclick="disabilita('${c.capsule_id}')">✕</button>
                    </div>`;
                });
            } else {
                capsule_html = '<span style="color:#555">Nessuna capsula attiva</span>';
            }

            let log_html = (d.ultimi_log_analisi || []).map(l => `<div>${l}</div>`).join('');
            let trades_html = JSON.stringify(d.ultimi_10_trade, null, 2);

            let blocks_html = '';
            if (m.block_types) {
                Object.entries(m.block_types).forEach(([k,v]) => {
                    blocks_html += `<div>${k}: <b>${v}</b></div>`;
                });
            }

            document.getElementById('content').innerHTML = `
                <div class="grid">
                    <div class="card">
                        <div class="label">STATUS BOT</div>
                        <div class="value">${b.is_running ? '🟢 RUNNING' : '🔴 OFFLINE'}</div>
                        <div class="label">Last ping: ${b.last_ping ? b.last_ping.substring(11,19) : 'N/A'}</div>
                    </div>
                    <div class="card">
                        <div class="label">CAPITALE TOTALE PnL</div>
                        <div class="value ${pnl_class}">${b.total_pnl >= 0 ? '+' : ''}${b.total_pnl.toFixed(2)}$</div>
                        <div class="label">Trade totali: ${b.total_trades}</div>
                    </div>
                    <div class="card">
                        <div class="label">WIN RATE (60min)</div>
                        <div class="value ${wr_class}">${m.win_rate}%</div>
                        <div class="label">W:${m.wins} L:${m.losses}</div>
                    </div>
                    <div class="card">
                        <div class="label">PnL MEDIO TRADE</div>
                        <div class="value ${m.pnl_medio >= 0 ? 'profit' : 'loss'}">${m.pnl_medio >= 0 ? '+' : ''}${m.pnl_medio.toFixed(4)}$</div>
                        <div class="label">Entries: ${m.entries}</div>
                    </div>
                    <div class="card">
                        <div class="label">BLOCCHI</div>
                        <div class="value warn">${m.blocks}</div>
                        <div>${blocks_html}</div>
                    </div>
                    <div class="card">
                        <div class="label">CAPSULE ATTIVE</div>
                        <div class="value">${d.capsule_attive ? d.capsule_attive.length : 0}</div>
                        <div class="label">Generate: ${b.capsule_generate || 0}</div>
                    </div>
                </div>

                <h2>📡 DATI MERCATO LIVE</h2>
                <div class="grid">
                    <div class="card">
                        <div class="label">FUNDING RATE</div>
                        <div class="value ${Math.abs(d.mercato?.funding_rate||0) > 0.0003 ? 'warn' : 'profit'}">${((d.mercato?.funding_rate||0)*100).toFixed(4)}%</div>
                    </div>
                    <div class="card">
                        <div class="label">OPEN INTEREST</div>
                        <div class="value">${((d.mercato?.open_interest||0)/1000).toFixed(1)}K BTC</div>
                    </div>
                    <div class="card">
                        <div class="label">ASK WALL</div>
                        <div class="value loss">${(d.mercato?.ask_wall||0).toFixed(1)} BTC @ $${(d.mercato?.ask_wall_price||0).toFixed(0)}</div>
                    </div>
                    <div class="card">
                        <div class="label">BID WALL</div>
                        <div class="value profit">${(d.mercato?.bid_wall||0).toFixed(1)} BTC @ $${(d.mercato?.bid_wall_price||0).toFixed(0)}</div>
                    </div>
                </div>

                <h2>💊 CAPSULE ATTIVE</h2>
                <button onclick="forzaAnalisi()">🔬 FORZA ANALISI ORA</button>
                ${capsule_html}

                <h2>📋 LOG ANALISI</h2>
                <pre>${log_html || 'Nessuna analisi ancora'}</pre>

                <h2>📈 GRAFICO EQUITY</h2>
                <canvas id="equityChart" style="width:100%;height:300px;background:#111;border-radius:6px;"></canvas>

                <h2>📊 ULTIMI 10 TRADE</h2>
                <pre>${trades_html}</pre>

                <h2>🕐 AGGIORNATO: ${new Date().toLocaleTimeString()}</h2>
            `;
        })
        .catch(e => {
            document.getElementById('content').innerHTML = '<div class="loss">Errore connessione: ' + e + '</div>';
        });
    }

    function disabilita(capsule_id) {
        if (!confirm('Disabilitare ' + capsule_id + '?')) return;
        fetch('/trading/capsule/' + capsule_id, {method: 'DELETE'})
        .then(r => r.json())
        .then(d => { alert(d.status); aggiorna(); });
    }

    function forzaAnalisi() {
        fetch('/trading/analisi_ora', {method: 'POST'})
        .then(r => r.json())
        .then(d => { alert(JSON.stringify(d, null, 2)); aggiorna(); });
    }

    // ============ GRAFICO EQUITY ============
    let chartCanvas = null;
    let chartCtx    = null;
    let equityData  = [];

    function disegnaGrafico(data) {
        const canvas = document.getElementById('equityChart');
        if (!canvas) return;
        canvas.width  = canvas.offsetWidth  || 1200;
        canvas.height = 300;
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        if (!data || data.length === 0) {
            ctx.fillStyle = '#333';
            ctx.font = '14px monospace';
            ctx.fillText('Nessun trade ancora...', 20, 150);
            return;
        }

        const valori  = data.map(d => d.equity);
        const minVal  = Math.min(...valori) - 5;
        const maxVal  = Math.max(...valori) + 5;
        const range   = maxVal - minVal || 1;
        const W = canvas.width;
        const H = canvas.height;
        const PAD = 50;

        // Griglia
        ctx.strokeStyle = '#222';
        ctx.lineWidth = 1;
        for (let i = 0; i <= 4; i++) {
            const y = PAD + (H - PAD*2) * i / 4;
            ctx.beginPath();
            ctx.moveTo(PAD, y);
            ctx.lineTo(W - PAD, y);
            ctx.stroke();
            const val = maxVal - range * i / 4;
            ctx.fillStyle = '#555';
            ctx.font = '11px monospace';
            ctx.fillText('$' + val.toFixed(0), 2, y + 4);
        }

        // Linea baseline $10000
        const baseY = PAD + (H - PAD*2) * (maxVal - 10000) / range;
        ctx.strokeStyle = '#333';
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(PAD, baseY);
        ctx.lineTo(W - PAD, baseY);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = '#555';
        ctx.fillText('$10000', 2, baseY + 4);

        // Curva equity
        ctx.beginPath();
        ctx.lineWidth = 2;
        data.forEach((d, i) => {
            const x = PAD + (W - PAD*2) * i / Math.max(data.length - 1, 1);
            const y = PAD + (H - PAD*2) * (maxVal - d.equity) / range;
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        const ultimo = data[data.length-1].equity;
        ctx.strokeStyle = ultimo >= 10000 ? '#00ff00' : '#ff4444';
        ctx.stroke();

        // Fill sotto la curva
        const lastX = PAD + (W - PAD*2);
        const firstX = PAD;
        ctx.lineTo(lastX, H - PAD);
        ctx.lineTo(firstX, H - PAD);
        ctx.closePath();
        ctx.fillStyle = ultimo >= 10000 ? 'rgba(0,255,0,0.05)' : 'rgba(255,68,68,0.05)';
        ctx.fill();

        // Punti WIN/LOSS
        data.forEach((d, i) => {
            const x = PAD + (W - PAD*2) * i / Math.max(data.length - 1, 1);
            const y = PAD + (H - PAD*2) * (maxVal - d.equity) / range;
            ctx.beginPath();
            ctx.arc(x, y, 4, 0, Math.PI * 2);
            ctx.fillStyle = d.win ? '#00ff00' : '#ff4444';
            ctx.fill();
        });

        // Label equity attuale
        ctx.fillStyle = ultimo >= 10000 ? '#00ff00' : '#ff4444';
        ctx.font = 'bold 14px monospace';
        ctx.fillText('$' + ultimo.toFixed(2), W - PAD - 80, PAD - 10);
    }

    function aggiornaGrafico() {
        fetch('/trading/chart_data')
        .then(r => r.json())
        .then(d => {
            equityData = d.equity || [];
            disegnaGrafico(equityData);
        })
        .catch(() => {});
    }

    aggiorna();
    aggiornaGrafico();
    setInterval(aggiorna, 10000);
    setInterval(aggiornaGrafico, 10000);
    window.addEventListener('resize', () => disegnaGrafico(equityData));
    </script>
</body>
</html>"""


@app.route("/")
def index():
    return dashboard()


# ============================================================
# AVVIO
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"[MC] Mission Control V2.0 avviato su porta {port}")
    app.run(host="0.0.0.0", port=port)

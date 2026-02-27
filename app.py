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
    "version":               "3.2-CHART",
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


@app.route("/trading/candles")
def candles():
    """Candele OHLCV BTC + trade markers per il grafico live."""
    with _lock:
        events = list(TRADING_EVENTS)

    # Costruisci candele da tick (aggregati a 1 minuto)
    # Usiamo i prezzi degli eventi ENTRY/EXIT per ricostruire
    trade_markers = []
    for e in events:
        et = e.get('event_type')
        ts_raw = e.get('timestamp', e.get('received_at', ''))
        try:
            if isinstance(ts_raw, str):
                ts = datetime.fromisoformat(ts_raw).timestamp()
            else:
                ts = float(ts_raw)
        except:
            ts = time.time()

        if et == 'ENTRY':
            trade_markers.append({
                "ts":    ts * 1000,
                "type":  "buy",
                "price": e.get('entry_price', 0),
                "size":  e.get('size', 0),
                "seed":  e.get('seed', 0),
                "label": f"ENTRY {e.get('modalita','')} F:{e.get('forza',0):.2f}",
            })
        elif et == 'EXIT':
            pnl = e.get('pnl', 0)
            trade_markers.append({
                "ts":    ts * 1000,
                "type":  "sell",
                "price": e.get('exit_price', 0),
                "pnl":   pnl,
                "win":   pnl > 0,
                "label": f"EXIT {e.get('reason','')} {'+' if pnl>0 else ''}{pnl:.2f}$",
            })
        elif et == 'BLOCK':
            trade_markers.append({
                "ts":    ts * 1000,
                "type":  "block",
                "price": 0,
                "label": str(e.get('block_reason', ''))[:30],
            })

    return jsonify({
        "markers":      trade_markers[-100:],
        "last_market":  dict(LAST_MARKET),
        "bot_status":   dict(BOT_STATUS),
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
    """Dashboard HTML con grafico candele live."""
    return """<!DOCTYPE html>
<html>
<head>
    <title>Mission Control — OVERTOP BASSANO</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: monospace; background: #0a0a0a; color: #00ff00; }
        .header { padding: 15px 20px; border-bottom: 1px solid #1a1a1a; display:flex; align-items:center; gap:20px; }
        h1 { color: #00ffff; font-size: 18px; }
        .status-dot { width:10px; height:10px; border-radius:50%; background:#00ff00; display:inline-block; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 8px; padding: 10px 20px; }
        .card { background: #111; padding: 10px; border-radius: 6px; border: 1px solid #1a1a1a; }
        .label { color: #555; font-size: 10px; text-transform:uppercase; }
        .value { font-size: 20px; font-weight: bold; margin-top:2px; }
        .profit { color: #00ff00; } .loss { color: #ff4444; } .warn { color: #ffaa00; }
        #chartContainer { margin: 10px 20px; background: #0d0d0d; border-radius: 8px; border: 1px solid #1a1a1a; position:relative; }
        #priceChart { width: 100%; height: 400px; display:block; }
        #chartInfo { position:absolute; top:8px; left:8px; font-size:11px; color:#888; pointer-events:none; }
        #currentPrice { position:absolute; top:8px; right:8px; font-size:16px; font-weight:bold; }
        .section { padding: 8px 20px; }
        .section h2 { color: #ffff00; font-size: 13px; margin-bottom: 6px; }
        .capsule { background: #0a1a0a; border: 1px solid #0f5; padding: 6px 10px; margin: 3px 0; border-radius: 4px; font-size: 11px; }
        .capsule.blocca { border-color: #f44; background: #1a0a0a; }
        pre { background: #111; padding: 8px; border-radius: 4px; font-size: 11px; max-height:200px; overflow-y:auto; }
        button { background: #1a3a1a; color: #0f0; border: 1px solid #0f0; padding: 6px 12px; cursor: pointer; border-radius: 4px; margin: 2px; font-size:11px; }
        button.danger { background: #3a1a1a; color: #f44; border-color: #f44; }
        .trade-log { max-height: 150px; overflow-y:auto; }
        .trade-entry { padding: 3px 0; border-bottom: 1px solid #111; font-size: 11px; }
    </style>
</head>
<body>
    <div class="header">
        <span class="status-dot" id="dot"></span>
        <h1>🚀 MISSION CONTROL — OVERTOP BASSANO</h1>
        <span id="lastUpdate" style="color:#555;font-size:11px;margin-left:auto"></span>
    </div>

    <div class="grid" id="metriche"></div>

    <div id="chartContainer">
        <canvas id="priceChart"></canvas>
        <div id="chartInfo">BTC/USDC — 1min live</div>
        <div id="currentPrice" class="profit">--</div>
    </div>

    <div class="section">
        <h2>📡 MERCATO</h2>
        <div class="grid" id="mercatoGrid" style="padding:0"></div>
    </div>

    <div class="section">
        <h2>💊 CAPSULE ATTIVE <button onclick="forzaAnalisi()">🔬 ANALISI ORA</button></h2>
        <div id="capsuleList"></div>
    </div>

    <div class="section">
        <h2>📋 TRADE LIVE</h2>
        <div class="trade-log" id="tradeLog"></div>
    </div>

    <div class="section">
        <h2>📋 LOG ANALISI</h2>
        <pre id="logAnalisi"></pre>
    </div>

<script>
// ============================================================
// GRAFICO CANDELE LIVE
// ============================================================
const canvas  = document.getElementById('priceChart');
const ctx     = canvas.getContext('2d');
let candele   = [];   // {t, o, h, l, c, v}
let markers   = [];   // entry/exit dal bot
let prezzoWS  = 0;
let candolaCorrente = null;
const CANDLE_SEC = 60; // candele da 1 minuto

function resizeCanvas() {
    canvas.width  = canvas.parentElement.offsetWidth;
    canvas.height = 400;
}
resizeCanvas();
window.addEventListener('resize', () => { resizeCanvas(); disegna(); });

// WebSocket Binance per prezzo live
const ws = new WebSocket('wss://stream.binance.com:9443/ws/btcusdc@aggTrade');
ws.onmessage = (e) => {
    const d = JSON.parse(e.data);
    const price  = parseFloat(d.p);
    const volume = parseFloat(d.q);
    const ts     = Math.floor(d.T / 1000);
    const bucket = Math.floor(ts / CANDLE_SEC) * CANDLE_SEC;

    prezzoWS = price;
    document.getElementById('currentPrice').textContent = '$' + price.toLocaleString('en', {minimumFractionDigits:2, maximumFractionDigits:2});
    document.getElementById('currentPrice').className = 'profit';

    if (!candolaCorrente || candolaCorrente.t !== bucket) {
        if (candolaCorrente) {
            candele.push({...candolaCorrente});
            if (candele.length > 120) candele.shift();
        }
        candolaCorrente = { t: bucket, o: price, h: price, l: price, c: price, v: volume };
    } else {
        candolaCorrente.h = Math.max(candolaCorrente.h, price);
        candolaCorrente.l = Math.min(candolaCorrente.l, price);
        candolaCorrente.c = price;
        candolaCorrente.v += volume;
    }
    disegna();
};

function disegna() {
    const W = canvas.width, H = canvas.height;
    const PAD_L = 60, PAD_R = 10, PAD_T = 20, PAD_B = 30;
    const chartW = W - PAD_L - PAD_R;
    const chartH = H - PAD_T - PAD_B;

    ctx.fillStyle = '#0d0d0d';
    ctx.fillRect(0, 0, W, H);

    // Tutte le candele inclusa quella corrente
    const allCandles = candolaCorrente ? [...candele, candolaCorrente] : [...candele];
    if (allCandles.length < 2) return;

    const visibili = allCandles.slice(-80);
    const prezzi   = visibili.flatMap(c => [c.h, c.l]);
    let minP = Math.min(...prezzi);
    let maxP = Math.max(...prezzi);
    const marg = (maxP - minP) * 0.1 || 10;
    minP -= marg; maxP += marg;
    const rangeP = maxP - minP;

    const toX = (i) => PAD_L + (i + 0.5) * chartW / visibili.length;
    const toY = (p)  => PAD_T + chartH * (1 - (p - minP) / rangeP);
    const cW  = Math.max(2, chartW / visibili.length * 0.7);

    // Griglia prezzi
    ctx.strokeStyle = '#1a1a1a';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 5; i++) {
        const p = minP + rangeP * i / 5;
        const y = toY(p);
        ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(W - PAD_R, y); ctx.stroke();
        ctx.fillStyle = '#444'; ctx.font = '10px monospace';
        ctx.fillText('$' + Math.round(p).toLocaleString(), 2, y + 4);
    }

    // Candele
    visibili.forEach((c, i) => {
        const x  = toX(i);
        const yO = toY(c.o), yC = toY(c.c);
        const yH = toY(c.h), yL = toY(c.l);
        const bull = c.c >= c.o;
        const col  = bull ? '#00cc44' : '#ff3333';

        // Stoppino
        ctx.strokeStyle = col; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(x, yH); ctx.lineTo(x, yL); ctx.stroke();

        // Corpo
        ctx.fillStyle = bull ? '#00cc44' : '#ff3333';
        const bodyY = Math.min(yO, yC);
        const bodyH = Math.max(Math.abs(yC - yO), 1);
        ctx.fillRect(x - cW/2, bodyY, cW, bodyH);
    });

    // Markers trade (entry/exit)
    markers.forEach(m => {
        if (!m.price || m.price === 0) return;
        // Trova posizione X approssimativa
        const mTs = Math.floor(m.ts / 1000);
        const bucket = Math.floor(mTs / CANDLE_SEC) * CANDLE_SEC;
        const idx = visibili.findIndex(c => c.t === bucket);
        if (idx < 0) return;
        const x = toX(idx);
        const y = toY(m.price);

        if (m.type === 'buy') {
            // Freccia verde su
            ctx.fillStyle = '#00ff00';
            ctx.beginPath();
            ctx.moveTo(x, y + 15);
            ctx.lineTo(x - 8, y + 28);
            ctx.lineTo(x + 8, y + 28);
            ctx.closePath(); ctx.fill();
            ctx.fillStyle = '#00ff00'; ctx.font = 'bold 10px monospace';
            ctx.fillText('B', x - 4, y + 40);
        } else if (m.type === 'sell') {
            // Freccia rossa giù
            const col = m.win ? '#00ff88' : '#ff4444';
            ctx.fillStyle = col;
            ctx.beginPath();
            ctx.moveTo(x, y - 15);
            ctx.lineTo(x - 8, y - 28);
            ctx.lineTo(x + 8, y - 28);
            ctx.closePath(); ctx.fill();
            ctx.fillStyle = col; ctx.font = 'bold 10px monospace';
            ctx.fillText(m.pnl >= 0 ? '+' : '', x - 4, y - 32);
        }
    });

    // Linea prezzo corrente
    if (prezzoWS > 0) {
        const y = toY(prezzoWS);
        ctx.strokeStyle = '#ffff00'; ctx.lineWidth = 1;
        ctx.setLineDash([3, 3]);
        ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(W - PAD_R, y); ctx.stroke();
        ctx.setLineDash([]);
    }
}

// ============================================================
// DATI DASHBOARD
// ============================================================
function aggiorna() {
    fetch('/trading/status?minutes=60')
    .then(r => r.json())
    .then(d => {
        const m = d.metriche, b = d.bot_status;
        document.getElementById('dot').style.background = b.is_running ? '#00ff00' : '#ff4444';
        document.getElementById('lastUpdate').textContent = 'aggiornato: ' + new Date().toLocaleTimeString();

        const pnl_class = b.total_pnl >= 0 ? 'profit' : 'loss';
        const wr_class  = m.win_rate >= 50 ? 'profit' : m.win_rate >= 40 ? 'warn' : 'loss';

        document.getElementById('metriche').innerHTML = `
            <div class="card"><div class="label">PnL TOTALE</div>
                <div class="value ${pnl_class}">${b.total_pnl >= 0 ? '+' : ''}${b.total_pnl.toFixed(2)}$</div>
                <div class="label">Trade: ${b.total_trades}</div></div>
            <div class="card"><div class="label">WIN RATE 60min</div>
                <div class="value ${wr_class}">${m.win_rate}%</div>
                <div class="label">W:${m.wins} L:${m.losses}</div></div>
            <div class="card"><div class="label">PnL MEDIO</div>
                <div class="value ${m.pnl_medio >= 0 ? 'profit' : 'loss'}">${m.pnl_medio >= 0 ? '+' : ''}${m.pnl_medio.toFixed(3)}$</div></div>
            <div class="card"><div class="label">BLOCCHI</div>
                <div class="value warn">${m.blocks}</div>
                <div class="label">${Object.entries(m.block_types||{}).map(([k,v])=>k.split(':').pop()+':'+v).join(' ')}</div></div>
            <div class="card"><div class="label">CAPSULE</div>
                <div class="value">${d.capsule_attive ? d.capsule_attive.length : 0}</div>
                <div class="label">Generate: ${b.capsule_generate||0}</div></div>
            <div class="card"><div class="label">ULTIMO PING</div>
                <div class="value" style="font-size:13px">${b.last_ping ? b.last_ping.substring(11,19) : 'N/A'}</div></div>
        `;

        // Mercato
        if (d.mercato) {
            const fr = d.mercato.funding_rate || 0;
            const fr_class = Math.abs(fr) > 0.0003 ? 'warn' : 'profit';
            document.getElementById('mercatoGrid').innerHTML = `
                <div class="card"><div class="label">FUNDING RATE</div>
                    <div class="value ${fr_class}">${(fr*100).toFixed(4)}%</div></div>
                <div class="card"><div class="label">OI</div>
                    <div class="value">${((d.mercato.open_interest||0)/1000).toFixed(1)}K</div></div>
                <div class="card"><div class="label">ASK WALL</div>
                    <div class="value loss">${(d.mercato.ask_wall||0).toFixed(1)} BTC @ $${Math.round(d.mercato.ask_wall_price||0)}</div></div>
                <div class="card"><div class="label">BID WALL</div>
                    <div class="value profit">${(d.mercato.bid_wall||0).toFixed(1)} BTC @ $${Math.round(d.mercato.bid_wall_price||0)}</div></div>
            `;
        }

        // Capsule
        let chtml = '';
        (d.capsule_attive || []).forEach(c => {
            const tipo = c.azione && c.azione.type === 'blocca_entry' ? 'blocca' : '';
            const icona = tipo === 'blocca' ? '🔴' : '🟢';
            chtml += `<div class="capsule ${tipo}">${icona} <b>${c.capsule_id}</b> — ${c.descrizione}
                <button class="danger" onclick="disabilita('${c.capsule_id}')">✕</button></div>`;
        });
        document.getElementById('capsuleList').innerHTML = chtml || '<span style="color:#333">Nessuna capsula attiva</span>';

        // Log analisi
        document.getElementById('logAnalisi').textContent = (d.ultimi_log_analisi || []).join('\n') || 'Nessuna analisi ancora';

        // Ultimi trade
        let thtml = '';
        (d.ultimi_10_trade || []).reverse().forEach(t => {
            const col = t.win ? '#00ff00' : '#ff4444';
            thtml += `<div class="trade-entry" style="color:${col}">
                ${t.win ? '🟢' : '🔴'} ${t.modalita} | ${t.reason} | ${t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(3)}$ | ${t.regime} | F:${t.forza}
            </div>`;
        });
        document.getElementById('tradeLog').innerHTML = thtml;
    });

    // Aggiorna markers trade sul grafico
    fetch('/trading/candles')
    .then(r => r.json())
    .then(d => { markers = d.markers || []; disegna(); });
}

function disabilita(id) {
    if (!confirm('Disabilitare ' + id + '?')) return;
    fetch('/trading/capsule/' + id, {method:'DELETE'}).then(() => aggiorna());
}

function forzaAnalisi() {
    fetch('/trading/analisi_ora', {method:'POST'}).then(r => r.json()).then(d => {
        alert('+' + d.capsule_nuove + ' capsule generate\nWR=' + (d.wr_globale*100).toFixed(1) + '%');
        aggiorna();
    });
}

aggiorna();
setInterval(aggiorna, 10000);
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

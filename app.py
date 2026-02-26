from flask import Flask, request, jsonify
import os
from datetime import datetime, timedelta
from collections import deque

app = Flask(__name__)

# ============================================================
# STORAGE TRADING (in-memory)
# ============================================================

TRADING_EVENTS = deque(maxlen=2000)  # ultimi 2000 eventi

TRADING_CONFIG = {
    # ========== DNA SACRO ==========
    "RISK_PER_TRADE": 0.015,
    "MAX_EXPOSURE_PCT": 1.0,
    "EARLY_WINDOW": 5,
    "EA_MIN": 0.8,
    "DIR_COH_MIN": 0.5,
    "DAMPING_THRESHOLD": 0.30,
    "REVERSAL_THRESHOLD": 0.50,

    # ========== MODALITÀ NORMAL ==========
    "NORMAL_MIN_FORZA": 0.55,
    "NORMAL_MAX_FORZA": 0.80,
    "NORMAL_HARD_SL": 0.25,
    "NORMAL_VETO_WR": 0.50,
    "NORMAL_BOOST_WR": 0.68,
    "NORMAL_MULT_MAX": 1.3,

    # ========== MODALITÀ FLAT ==========
    "FLAT_MIN_FORZA": 0.65,
    "FLAT_MAX_FORZA": 0.75,
    "FLAT_HARD_SL": 0.20,
    "FLAT_VETO_WR": 0.30,
    "FLAT_BOOST_WR": 0.68,
    "FLAT_MULT_MAX": 1.2,

    # ========== SWITCH BINARIO ==========
    "SW_CONFIRM_BARS": 100,
    "SW_EMA_ALPHA": 0.05,
    "SW_FLAT_THRESHOLD": 0.0028,

    # ========== SEED SCORE ==========
    "SEED_THRESH_NORMAL": 0.45,
    "SEED_THRESH_FLAT": 0.50,
    "BOOST_SEED_MIN_NORMAL": 0.58,
    "SEED_WINDOW_RANGE": 60,
    "SEED_WINDOW_VOL": 10,
    "SEED_WINDOW_DIR": 10,
    "SEED_WINDOW_BRK": 30,
    "SEED_W_RANGE": 0.25,
    "SEED_W_VOL": 0.40,
    "SEED_W_DIR": 0.20,
    "SEED_W_BRK": 0.15,

    # ========== ORACOLO ==========
    "FANTASMA_WR": 0.40,
    "FANTASMA_PNL": -0.05,
    "MIN_SAMPLES_CONF": 15,
    "MAX_MULT": 2.0,
    "DECAY_FACTOR": 0.99,
    "META_WINDOW": 50,
    "META_ACC_THRESHOLD": 0.55,
    "META_REDUCTION": 0.8,

    # ========== EXIT DINAMICHE ==========
    "TAKE_PROFIT_R": 0.65,
    "TRAILING_STEP": 0.25,
    "ATR_PERIOD": 10,
    "ATR_MULT": 1.5,
    "MIN_HOLD_TIME_SL": 2.0,
    "R_VITA": -0.30,
    "R_MORTE": -0.50,

    # ========== VARIE ==========
    "NORMAL_SIZE_MULT": 0.30,

    # ========== METADATA ==========
    "last_updated": None,
    "version": "13.6d"
}

BOT_STATUS = {
    "is_running": False,
    "last_ping": None,
    "total_trades_today": 0,
    "daily_pnl": 0.0
}

# ============================================================
# ENDPOINTS TRADING
# ============================================================

@app.route("/trading/log", methods=["POST"])
def trading_log():
    """Riceve log dal bot OVERTOP."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON payload"}), 400

        event = {
            **data,
            "received_at": datetime.now().isoformat(),
            "server_id": len(TRADING_EVENTS)
        }
        TRADING_EVENTS.append(event)

        BOT_STATUS["last_ping"] = datetime.now().isoformat()
        BOT_STATUS["is_running"] = True

        if data.get("event_type") == "EXIT" and data.get("pnl") is not None:
            BOT_STATUS["total_trades_today"] += 1
            BOT_STATUS["daily_pnl"] += data["pnl"]

        return jsonify({
            "status": "logged",
            "total_events": len(TRADING_EVENTS),
            "config_version": TRADING_CONFIG["version"]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/trading/status", methods=["GET"])
def trading_status():
    """Restituisce stato e metriche in tempo reale."""
    try:
        minutes = request.args.get("minutes", 10, type=int)
        cutoff = datetime.now() - timedelta(minutes=minutes)

        recent = [e for e in TRADING_EVENTS
                  if datetime.fromisoformat(e["timestamp"]) > cutoff]

        entries = [e for e in recent if e["event_type"] == "ENTRY"]
        exits = [e for e in recent if e["event_type"] == "EXIT"]
        blocks = [e for e in recent if e["event_type"] == "BLOCK"]
        reports = [e for e in recent if e["event_type"] == "REPORT"]

        exits_profit = [e for e in exits if e.get("pnl", 0) > 0]
        exits_loss = [e for e in exits if e.get("pnl", 0) < 0]

        avg_seed = sum(e.get("seed", 0) for e in entries) / len(entries) if entries else 0
        win_rate = (len(exits_profit) / len(exits) * 100) if exits else 0
        avg_pnl = sum(e.get("pnl", 0) for e in exits) / len(exits) if exits else 0

        blocks_fantasma = len([e for e in blocks if "FANTASMA" in str(e.get("block_reason", ""))])
        blocks_veto = len([e for e in blocks if "VETO" in str(e.get("block_reason", ""))])
        blocks_seed = len([e for e in blocks if "SEED" in str(e.get("block_reason", ""))])

        return jsonify({
            "timestamp": datetime.now().isoformat(),
            "bot_status": BOT_STATUS,
            "period_minutes": minutes,
            "metrics": {
                "total_events": len(recent),
                "entries": len(entries),
                "exits_total": len(exits),
                "exits_profit": len(exits_profit),
                "exits_loss": len(exits_loss),
                "win_rate_percent": round(win_rate, 1),
                "avg_pnl": round(avg_pnl, 4),
                "avg_seed": round(avg_seed, 3),
                "blocks_total": len(blocks),
                "blocks_fantasma": blocks_fantasma,
                "blocks_veto": blocks_veto,
                "blocks_seed": blocks_seed,
            },
            "current_config": TRADING_CONFIG,
            "last_5_events": list(TRADING_EVENTS)[-5:] if TRADING_EVENTS else []
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/trading/config", methods=["GET"])
def trading_config_get():
    """Fornisce la configurazione corrente al bot."""
    return jsonify(TRADING_CONFIG)


@app.route("/trading/config", methods=["POST"])
def trading_config_update():
    """Permette a Kimi di modificare i parametri in tempo reale."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON payload"}), 400

        # Tutti i parametri di TRADING_CONFIG sono modificabili, tranne i metadati
        allowed_params = list(TRADING_CONFIG.keys())
        # Rimuoviamo i metadati dalla lista se non vogliamo che vengano modificati
        allowed_params.remove("last_updated")
        allowed_params.remove("version")

        updated = {}
        for key, value in data.items():
            if key in allowed_params:
                old = TRADING_CONFIG.get(key)
                TRADING_CONFIG[key] = value
                updated[key] = {"old": old, "new": value}

        TRADING_CONFIG["last_updated"] = datetime.now().isoformat()

        if updated:
            TRADING_EVENTS.append({
                "timestamp": datetime.now().isoformat(),
                "event_type": "CONFIG_UPDATE",
                "changes": updated,
                "reason": data.get("reason", "Kimi update")
            })

        return jsonify({
            "status": "updated",
            "changes": updated,
            "current_config": TRADING_CONFIG
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/trading/dashboard")
def trading_dashboard():
    """Dashboard HTML minimale."""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Mission Control</title>
        <meta http-equiv="refresh" content="10">
        <style>
            body { font-family: monospace; background: #0a0a0a; color: #00ff00; padding: 20px; }
            .metric { background: #1a1a1a; padding: 10px; margin: 5px; border-radius: 5px; }
            .profit { color: #00ff00; }
            .loss { color: #ff0000; }
            pre { background: #1a1a1a; padding: 10px; overflow-x: auto; }
        </style>
    </head>
    <body>
        <h1>🚀 OVERTOP Mission Control</h1>
        <div id="status">Caricamento...</div>
        <script>
            fetch('/trading/status?minutes=30')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('status').innerHTML = `
                        <div class="metric">Bot: ${data.bot_status.is_running ? '🟢 RUNNING' : '🔴 OFFLINE'}</div>
                        <div class="metric">Last Ping: ${data.bot_status.last_ping || 'N/A'}</div>
                        <div class="metric">Win Rate: ${data.metrics.win_rate_percent}%</div>
                        <div class="metric">Avg PnL: <span class="${data.metrics.avg_pnl >= 0 ? 'profit' : 'loss'}">${data.metrics.avg_pnl}</span></div>
                        <div class="metric">Entries: ${data.metrics.entries} | Exits: ${data.metrics.exits_total}</div>
                        <div class="metric">Blocks: ${data.metrics.blocks_total} (F:${data.metrics.blocks_fantasma} V:${data.metrics.blocks_veto})</div>
                        <h3>Current Config:</h3>
                        <pre>${JSON.stringify(data.current_config, null, 2)}</pre>
                        <h3>Last Events:</h3>
                        <pre>${JSON.stringify(data.last_5_events, null, 2)}</pre>
                    `;
                });
        </script>
    </body>
    </html>
    """

# ============================================================
# AVVIO
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

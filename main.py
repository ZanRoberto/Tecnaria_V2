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
    "FANTASMA_WR": 0.40,
    "FANTASMA_PNL": -0.05,
    "SEED_THRESH_NORMAL": 0.45,
    "SEED_THRESH_FLAT": 0.40,
    "RISK_PER_TRADE": 0.015,
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

        allowed_params = [
            "FANTASMA_WR", "FANTASMA_PNL", "SEED_THRESH_NORMAL",
            "SEED_THRESH_FLAT", "RISK_PER_TRADE"
        ]

        updated = {}
        for key, value in data.items():
            if key in allowed_params:
                old = TRADING_CONFIG.get(key)
                TRADING_CONFIG[key] = value
                updated[key] = {"old": old, "new": value}

        TRADING_CONFIG["last_updated"] = datetime.now().isoformat()

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

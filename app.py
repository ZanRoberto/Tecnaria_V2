"""
MISSION CONTROL V5.4 — DB FIX
================================
Fix critico: Salva ENTRY e EXIT nel DB (non scarta)
Render deployment ready
"""

from flask import Flask, request, jsonify, render_template_string
import sqlite3
import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
import os

app = Flask(__name__)

# ============================================================
# CONFIG
# ============================================================

DB_PATH = "/tmp/trading_data.db"  # Render: /tmp è writable
LOG_FILE = "/tmp/trading.log"

def log(msg):
    """Log a stdout e file"""
    timestamp = datetime.utcnow().isoformat()
    line = f"{timestamp}Z {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except:
        pass

# ============================================================
# DATABASE INIT
# ============================================================

def init_db():
    """Crea tabella trades"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT (datetime('now')),
                event_type TEXT,
                asset TEXT,
                price REAL,
                size REAL,
                pnl REAL,
                direction TEXT,
                reason TEXT,
                data_json TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_heartbeat (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT,
                capital REAL,
                trades INTEGER,
                last_seen TEXT,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        
        conn.commit()
        conn.close()
        log("[DB_INIT] ✅ Database inizializzato")
        return True
    except Exception as e:
        log(f"[DB_INIT] ❌ Errore: {e}")
        return False

init_db()

# ============================================================
# GLOBAL STATE
# ============================================================

trades_memory = []  # Buffer in-memory (ultimi 100 trade)
heartbeat_data = {"status": "UNKNOWN", "capital": 0, "trades": 0, "last_seen": None}

# ============================================================
# ROUTE: POST /trading/log — Ricevi log dal bot
# ============================================================

@app.route('/trading/log', methods=['POST'])
def trading_log():
    """
    Bot manda: {
        "type": "ENTRY|EXIT|BLOCK",
        "asset": "BTCUSDC",
        "price": 68000,
        "size": 0.15,
        "pnl": 1.23,
        "direction": "LONG",
        "reason": "momentum_decay",
        "extra": {...}
    }
    """
    try:
        data = request.get_json()
        event_type = data.get("type", "UNKNOWN")
        
        log(f"[TRADING_LOG] 📥 Ricevuto evento: type={event_type} | asset={data.get('asset')} | pnl={data.get('pnl')}")
        
        # ✅ CRITICAL FIX: Salva ENTRY, EXIT (scarta BLOCK per ora)
        if event_type in ["ENTRY", "EXIT"]:
            try:
                conn = sqlite3.connect(DB_PATH, check_same_thread=False)
                conn.execute("""
                    INSERT INTO trades 
                    (event_type, asset, price, size, pnl, direction, reason, data_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    event_type,
                    data.get("asset"),
                    data.get("price", 0),
                    data.get("size", 0),
                    data.get("pnl", 0),
                    data.get("direction", "LONG"),
                    data.get("reason", ""),
                    json.dumps(data)
                ))
                conn.commit()
                conn.close()
                log(f"[DB_SAVE] ✅ SALVATO: {event_type} {data.get('asset')} PnL={data.get('pnl')}")
            except Exception as e:
                log(f"[DB_SAVE] ❌ ERRORE: {e}")
        
        # Aggiungi a buffer memoria
        trades_memory.append({
            'timestamp': datetime.utcnow().isoformat(),
            'type': event_type,
            'asset': data.get('asset'),
            'pnl': data.get('pnl', 0)
        })
        if len(trades_memory) > 100:
            trades_memory.pop(0)
        
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        log(f"[TRADING_LOG] ❌ Errore: {e}")
        return jsonify({"error": str(e)}), 500

# ============================================================
# ROUTE: POST /trading/heartbeat — Ricevi heartbeat dal bot
# ============================================================

@app.route('/trading/heartbeat', methods=['POST'])
def trading_heartbeat():
    """Bot manda heartbeat ogni 30s"""
    try:
        data = request.get_json()
        status = data.get("status", "UNKNOWN")
        capital = data.get("capital", 0)
        trades = data.get("trades", 0)
        
        heartbeat_data["status"] = status
        heartbeat_data["capital"] = capital
        heartbeat_data["trades"] = trades
        heartbeat_data["last_seen"] = datetime.utcnow().isoformat()
        
        log(f"[HEARTBEAT] 📡 Ricevuto: status={status}, capital=${capital}, trades={trades}")
        log(f"[HEARTBEAT] ✅ BOT_HEARTBEAT aggiornato: last_seen={heartbeat_data['last_seen']}")
        
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        log(f"[HEARTBEAT] ❌ Errore: {e}")
        return jsonify({"error": str(e)}), 500

# ============================================================
# ROUTE: GET /trading/status — Leggi status dal DB
# ============================================================

@app.route('/trading/status', methods=['GET'])
def trading_status():
    """Ritorna ultimi N minuti di trade"""
    try:
        minutes = request.args.get('minutes', 60, type=int)
        
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        rows = conn.execute("""
            SELECT * FROM trades 
            WHERE datetime(timestamp) > datetime('now', '-' || ? || ' minutes')
            ORDER BY timestamp DESC
            LIMIT 100
        """, (minutes,)).fetchall()
        conn.close()
        
        trades = []
        for row in rows:
            trades.append({
                'id': row[0],
                'timestamp': row[1],
                'type': row[2],
                'asset': row[3],
                'price': row[4],
                'size': row[5],
                'pnl': row[6],
                'direction': row[7],
                'reason': row[8]
            })
        
        return jsonify({
            "heartbeat": heartbeat_data,
            "trades": trades,
            "count": len(trades)
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================
# THREAD: Analisi periodica (brain)
# ============================================================

def brain_analysis_thread():
    """Analizza trade ogni minuto"""
    while True:
        try:
            time.sleep(60)  # Ogni minuto
            
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            rows = conn.execute("""
                SELECT COUNT(*), SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), SUM(pnl)
                FROM trades
                WHERE event_type = 'EXIT'
            """).fetchone()
            conn.close()
            
            n_trades = rows[0] if rows[0] else 0
            n_wins = rows[1] if rows[1] else 0
            total_pnl = rows[2] if rows[2] else 0
            
            wr = (n_wins / n_trades * 100) if n_trades > 0 else 0
            
            log(f"[BRAIN_THREAD] 🧠 Analisi periodica: {n_trades} trade in memoria/DB")
            log(f"[{datetime.utcnow().strftime('%H:%M')}] 🧠 {n_trades} trade | WR={wr:.0f}% PnL={total_pnl:.2f} | +0 capsule")
            
        except Exception as e:
            log(f"[BRAIN_THREAD] ❌ Errore: {e}")

threading.Thread(target=brain_analysis_thread, daemon=True, name='brain_analysis').start()

# ============================================================
# ROUTE: GET /trading/commands — Leggi comandi (placeholder)
# ============================================================

@app.route('/trading/commands', methods=['GET'])
def get_commands():
    return jsonify({"commands": []}), 200

@app.route('/trading/commands/history', methods=['GET'])
def get_commands_history():
    return jsonify({"history": []}), 200

# ============================================================
# ROUTE: GET /trading/config — Config attuale
# ============================================================

@app.route('/trading/config', methods=['GET'])
def get_config():
    return jsonify({
        "mode": "LIVE",
        "db_path": DB_PATH,
        "version": "V5.4"
    }), 200

# ============================================================
# DASHBOARD HTML
# ============================================================

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>MISSION CONTROL V5.4</title>
    <style>
        * { font-family: monospace; background: #0a0e27; color: #00ff00; }
        body { margin: 20px; }
        .header { font-size: 24px; font-weight: bold; color: #00ff00; margin-bottom: 30px; }
        .metric { display: inline-block; margin-right: 40px; font-size: 18px; }
        .metric-value { font-size: 28px; font-weight: bold; color: #00ff00; }
        .status-green { color: #00ff00; }
        .status-red { color: #ff0000; }
        .trades { margin-top: 30px; }
        .trade-row { margin: 5px 0; font-size: 14px; }
        .win { color: #00ff00; }
        .loss { color: #ff0000; }
    </style>
</head>
<body>
    <div class="header">🔴 MISSION CONTROL V5.4 — LIVE BRAIN 🔴</div>
    
    <div class="metric">
        <div>PnL</div>
        <div class="metric-value" id="pnl">--</div>
    </div>
    
    <div class="metric">
        <div>WR</div>
        <div class="metric-value" id="wr">--</div>
    </div>
    
    <div class="metric">
        <div>CAPSULE</div>
        <div class="metric-value" id="capsule">0</div>
    </div>
    
    <div class="metric">
        <div>STATUS</div>
        <div class="metric-value" id="status">OFFLINE</div>
    </div>
    
    <div class="trades">
        <div style="font-weight: bold; margin-bottom: 10px;">Recent Trades:</div>
        <div id="trades-list"></div>
    </div>
    
    <script>
        setInterval(() => {
            fetch('/trading/status?minutes=60')
                .then(r => r.json())
                .then(d => {
                    const exits = d.trades.filter(t => t.type === 'EXIT');
                    const wins = exits.filter(t => t.pnl > 0).length;
                    const pnl = exits.reduce((s, t) => s + (t.pnl || 0), 0);
                    const wr = exits.length > 0 ? (wins / exits.length * 100).toFixed(0) : 0;
                    
                    document.getElementById('pnl').textContent = pnl.toFixed(2) + '$';
                    document.getElementById('wr').textContent = wr + '%';
                    document.getElementById('status').textContent = d.heartbeat.status || 'OFFLINE';
                    document.getElementById('status').className = 'metric-value ' + (d.heartbeat.status === 'RUNNING' ? 'status-green' : 'status-red');
                    
                    let html = '';
                    d.trades.slice(0, 10).forEach(t => {
                        const cls = t.pnl > 0 ? 'win' : 'loss';
                        html += `<div class="trade-row ${cls}">${t.type} ${t.asset} PnL=${t.pnl?.toFixed(2) || 0}$</div>`;
                    });
                    document.getElementById('trades-list').innerHTML = html;
                });
        }, 2000);
    </script>
</body>
</html>
"""

@app.route('/')
def dashboard():
    return render_template_string(DASHBOARD_HTML)

# ============================================================
# MAIN
# ============================================================

if __name__ == '__main__':
    log("[MAIN] 🚀 Mission Control V5.4 starting...")
    app.run(host='0.0.0.0', port=5000, debug=False)

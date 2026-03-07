"""
MISSION CONTROL V5.9 — BOT V14 INTEGRATO
==========================================
✅ Bot gira DENTRO app.py come thread
✅ Memoria condivisa (heartbeat_data)
✅ Database persistente SQLite
✅ ZERO comunicazione HTTP esterna
✅ ROBUSTO e FAILSAFE
"""

from flask import Flask, request, jsonify, render_template_string
from OVERTOP_BASSANO_V14 import OvertopBassanoV14Memoria
import sqlite3
import json
import threading
import time
import sys
import os
from datetime import datetime
from pathlib import Path

sys.stdout.flush()
sys.stderr.flush()

app = Flask(__name__)

# ✅ DATABASE IN PERCORSO PERSISTENTE
DB_DIR = "/home/app/data"
DB_PATH = os.path.join(DB_DIR, "trading_data.db")
LOG_FILE = os.path.join(DB_DIR, "trading.log")

Path(DB_DIR).mkdir(parents=True, exist_ok=True)

def log(msg):
    """Log diretto stdout + stderr"""
    timestamp = datetime.utcnow().isoformat()
    line = f"{timestamp}Z {msg}"
    print(line, flush=True)
    print(line, file=sys.stderr, flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
            f.flush()
    except:
        pass

def init_db():
    """Crea DB persistente"""
    try:
        log(f"[DB_INIT] 📁 Cartella: {DB_DIR}")
        log(f"[DB_INIT] 📄 Database: {DB_PATH}")
        
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
        
        log(f"[DB_INIT] ✅ Database creato/verificato in {DB_PATH}")
        return True
    except Exception as e:
        log(f"[DB_INIT] ❌ ERRORE CRITICO: {e}")
        return False

init_db()

# ✅ HEARTBEAT_DATA CONDIVISO tra app.py e bot (THREAD-SAFE)
heartbeat_lock = threading.Lock()
heartbeat_data = {"status": "UNKNOWN", "capital": 0, "trades": 0, "wr": 0, "wins": 0, "last_seen": None}

def db_execute(query, params=None, fetch=False):
    """Esegui query con retry logic"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            
            if params:
                cursor = conn.execute(query, params)
            else:
                cursor = conn.execute(query)
            
            if fetch:
                result = cursor.fetchall() if "SELECT" in query.upper() and "COUNT" not in query.upper() else cursor.fetchone()
            else:
                conn.commit()
                result = None
            
            conn.close()
            return result
        except Exception as e:
            log(f"[DB_EXECUTE] ⚠️ Tentativo {attempt+1} fallito: {e}")
            if attempt == max_retries - 1:
                log(f"[DB_EXECUTE] ❌ FALLITO dopo {max_retries} tentativi")
                return None
            time.sleep(0.5)

@app.route('/trading/log', methods=['POST'])
def trading_log():
    """Ricevi trade events dal bot"""
    try:
        data = request.get_json()
        event_type = data.get("type") or data.get("event_type", "UNKNOWN")
        asset = data.get("asset", "UNKNOWN")
        pnl = data.get("pnl", 0)
        
        log(f"[TRADING_LOG] 📥 RICEVUTO: event_type={event_type} | asset={asset} | pnl={pnl}")
        
        if event_type in ["ENTRY", "EXIT"]:
            result = db_execute("""
                INSERT INTO trades 
                (event_type, asset, price, size, pnl, direction, reason, data_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event_type,
                asset,
                data.get("price", 0),
                data.get("size", 0),
                pnl,
                data.get("direction", "LONG"),
                data.get("reason", ""),
                json.dumps(data)
            ))
            
            if result is not None:
                log(f"[DB_SAVE] ✅ SALVATO: {event_type} {asset} PnL={pnl}$")
            else:
                log(f"[DB_SAVE] ❌ ERRORE SALVATAGGIO")
        
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        log(f"[TRADING_LOG] ❌ ERRORE: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/trading/heartbeat', methods=['POST'])
def trading_heartbeat():
    """Ricevi heartbeat dal bot (comunque supportato per compatibilità)"""
    try:
        data = request.get_json()
        status = data.get("status", "UNKNOWN")
        capital = data.get("capital", 0)
        trades = data.get("trades", 0)
        
        heartbeat_data["status"] = status
        heartbeat_data["capital"] = capital
        heartbeat_data["trades"] = trades
        heartbeat_data["last_seen"] = datetime.utcnow().isoformat()
        
        log(f"[HEARTBEAT] 💓 RICEVUTO: status={status} capital=${capital} trades={trades}")
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        log(f"[HEARTBEAT] ❌ ERRORE: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/trading/status', methods=['GET'])
def trading_status():
    """Leggi dal DB persistente + heartbeat_data condiviso"""
    try:
        metrics_row = db_execute("""
            SELECT COUNT(*), 
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), 
                   SUM(pnl), MAX(pnl), MIN(pnl)
            FROM trades
            WHERE event_type = 'EXIT'
        """, fetch=True)
        
        trades_rows = db_execute("""
            SELECT id, timestamp, event_type, asset, price, size, pnl, direction, reason
            FROM trades
            ORDER BY timestamp DESC
            LIMIT 20
        """, fetch=True)
        
        n_trades = metrics_row[0] if metrics_row and metrics_row[0] else 0
        n_wins = metrics_row[1] if metrics_row and metrics_row[1] else 0
        total_pnl = metrics_row[2] if metrics_row and metrics_row[2] else 0
        max_pnl = metrics_row[3] if metrics_row and metrics_row[3] else 0
        min_pnl = metrics_row[4] if metrics_row and metrics_row[4] else 0
        
        wr = (n_wins / n_trades * 100) if n_trades > 0 else 0
        
        # LEGGI heartbeat_data CON LOCK (THREAD-SAFE)
        with heartbeat_lock:
            capital = heartbeat_data.get("capital", 0)
            status = heartbeat_data.get("status", "UNKNOWN")
            hb_trades = heartbeat_data.get("trades", 0)
            hb_wr = heartbeat_data.get("wr", 0)
        
        roi = (total_pnl / capital * 100) if capital > 0 else 0
        
        trades = []
        if trades_rows:
            for row in trades_rows:
                trades.append({
                    'id': row[0],
                    'timestamp': row[1],
                    'type': row[2],
                    'asset': row[3],
                    'price': float(row[4]) if row[4] else 0,
                    'size': float(row[5]) if row[5] else 0,
                    'pnl': float(row[6]) if row[6] else 0,
                    'direction': row[7],
                    'reason': row[8] if row[8] else "N/A"
                })
        
        suggestions = []
        if wr < 30 and n_trades > 5:
            suggestions.append("⚠️ Win Rate BASSO")
        if total_pnl < -100:
            suggestions.append("🔴 Drawdown ALTO")
        if n_trades == 0:
            suggestions.append("🟡 Nessun trade — warmup")
        
        log(f"[STATUS] 📊 Ritorno: {n_trades} trade | WR={wr:.1f}% | PnL={total_pnl:.2f}$ | Status={status}")
        
        return jsonify({
            "heartbeat": {"status": status, "capital": capital, "trades": hb_trades, "wr": hb_wr},
            "metrics": {
                "n_trades": n_trades,
                "n_wins": n_wins,
                "wr": round(wr, 1),
                "pnl": round(total_pnl, 2),
                "capital": round(capital, 2),
                "roi": round(roi, 2),
                "max_pnl": round(max_pnl, 2),
                "min_pnl": round(min_pnl, 2)
            },
            "trades": trades,
            "suggestions": suggestions
        }), 200
    except Exception as e:
        log(f"[STATUS] ❌ ERRORE: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/trading/command', methods=['POST'])
def send_command():
    try:
        data = request.get_json()
        cmd = data.get("command", "")
        log(f"[COMMAND] 📤 Comando: {cmd}")
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def brain_analysis_thread():
    """Thread di analisi periodica"""
    while True:
        try:
            time.sleep(60)
            rows = db_execute("""
                SELECT COUNT(*), SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), SUM(pnl)
                FROM trades
                WHERE event_type = 'EXIT'
            """, fetch=True)
            
            if rows:
                n_trades = rows[0] if rows[0] else 0
                n_wins = rows[1] if rows[1] else 0
                total_pnl = rows[2] if rows[2] else 0
                wr = (n_wins / n_trades * 100) if n_trades > 0 else 0
                log(f"[BRAIN] 🧠 {n_trades} trade | WR={wr:.0f}% | PnL={total_pnl:.2f}$")
        except Exception as e:
            log(f"[BRAIN] ❌ ERRORE: {e}")

threading.Thread(target=brain_analysis_thread, daemon=True, name='brain').start()

def bot_thread_launcher():
    """Lancia il bot V14 integrato con RETRY logic"""
    retry_count = 0
    max_retries = 5
    
    while retry_count < max_retries:
        try:
            log("[BOT_LAUNCHER] 🚀 AVVIANDO BOT V14 INTEGRATO...")
            
            # CREA istanza bot e PASSA heartbeat_data condiviso
            bot = OvertopBassanoV14Memoria(
                heartbeat_lock=heartbeat_lock,
                heartbeat_data=heartbeat_data,
                db_execute=db_execute
            )
            
            log("[BOT_LAUNCHER] ✅ Bot istanziato con memoria condivisa")
            log("[BOT_LAUNCHER] ▶️ Bot.run() in esecuzione...")
            
            # Lancia il bot
            bot.run()
            
        except Exception as e:
            retry_count += 1
            log(f"[BOT_LAUNCHER] ❌ ERRORE (tentativo {retry_count}/{max_retries}): {e}")
            import traceback
            log(traceback.format_exc())
            time.sleep(5)
    
    log(f"[BOT_LAUNCHER] ❌ FALLITO dopo {max_retries} tentativi. Bot non avviabile.")

# Lancia bot in thread daemon
threading.Thread(target=bot_thread_launcher, daemon=True, name='bot_v14').start()
log("[MAIN] ✅ Bot launcher thread avviato")

@app.route('/trading/config', methods=['GET'])
def get_config():
    return jsonify({"version": "V5.9 + BOT V14 INTEGRATO", "db": DB_PATH}), 200

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MISSION CONTROL V5.9</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Courier New', monospace; background: #0a0e27; color: #00ff00; padding: 15px; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { font-size: 28px; font-weight: bold; color: #00ff00; text-align: center; margin-bottom: 25px; border-bottom: 2px solid #00ff00; padding-bottom: 10px; }
        .metrics-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 20px; }
        .metric-card { background: #1a1f3a; border: 2px solid #00ff00; padding: 12px; border-radius: 3px; }
        .metric-label { font-size: 11px; color: #888; }
        .metric-value { font-size: 22px; font-weight: bold; color: #00ff00; margin-top: 4px; }
        .status-running { color: #00ff00; }
        .status-offline { color: #ff0000; }
        .controls { display: flex; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; }
        button { background: #00ff00; color: #0a0e27; border: none; padding: 10px 15px; border-radius: 3px; cursor: pointer; font-weight: bold; }
        button:hover { background: #00cc00; }
        .suggestions { background: #1a1f3a; border-left: 4px solid #ffff00; padding: 12px; margin-bottom: 20px; border-radius: 3px; }
        .trades-section { background: #1a1f3a; border: 2px solid #00ff00; padding: 12px; border-radius: 3px; overflow-x: auto; }
        .trade-row { display: grid; grid-template-columns: 90px 70px 70px 70px 70px 70px 100px; gap: 10px; padding: 8px; border-bottom: 1px solid #333; font-size: 11px; }
        .trade-row.header { font-weight: bold; border-bottom: 2px solid #00ff00; background: #0f1420; }
        .win { color: #00ff00; }
        .loss { color: #ff0000; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">🔴 MISSION CONTROL V5.9 — BOT V14 INTEGRATO 🔴</div>
        <div class="metrics-grid">
            <div class="metric-card"><div class="metric-label">PnL</div><div class="metric-value" id="pnl">--</div></div>
            <div class="metric-card"><div class="metric-label">WR %</div><div class="metric-value" id="wr">--</div></div>
            <div class="metric-card"><div class="metric-label">Capital</div><div class="metric-value" id="capital">--</div></div>
            <div class="metric-card"><div class="metric-label">ROI %</div><div class="metric-value" id="roi">--</div></div>
            <div class="metric-card"><div class="metric-label">Trade #</div><div class="metric-value" id="n_trades">--</div></div>
            <div class="metric-card"><div class="metric-label">STATUS</div><div class="metric-value" id="status">OFFLINE</div></div>
        </div>
        <div class="controls">
            <button onclick="sendCommand('STOP')">⏹️ STOP</button>
            <button onclick="sendCommand('RESUME')">▶️ RESUME</button>
            <button onclick="sendCommand('RESET_LOSSES')">🔄 RESET</button>
        </div>
        <div class="suggestions" id="suggestions"></div>
        <div class="trades-section">
            <div style="margin-bottom: 10px; font-weight: bold;">📊 ULTIMI 20 TRADE</div>
            <div class="trade-row header">
                <div>TIME</div><div>TYPE</div><div>ASSET</div><div>PRICE</div><div>SIZE</div><div>PnL</div><div>REASON</div>
            </div>
            <div id="trades-list"></div>
        </div>
    </div>
    <script>
        function updateDashboard() {
            fetch('/trading/status').then(r => r.json()).then(d => {
                const m = d.metrics;
                document.getElementById('pnl').textContent = m.pnl.toFixed(2) + '$';
                document.getElementById('wr').textContent = m.wr.toFixed(1) + '%';
                document.getElementById('capital').textContent = '$' + m.capital.toFixed(0);
                document.getElementById('roi').textContent = m.roi.toFixed(2) + '%';
                document.getElementById('n_trades').textContent = m.n_trades;
                document.getElementById('status').textContent = d.heartbeat.status || 'OFFLINE';
                document.getElementById('status').className = 'metric-value ' + (d.heartbeat.status === 'RUNNING' ? 'status-running' : 'status-offline');
                
                let sugg_html = '<div style="font-weight: bold; margin-bottom: 5px;">💡 SUGGERIMENTI:</div>';
                if (d.suggestions && d.suggestions.length > 0) {
                    d.suggestions.forEach(s => { sugg_html += '<div style="margin: 5px 0;">' + s + '</div>'; });
                } else {
                    sugg_html += '<div>✅ Sistema OK</div>';
                }
                document.getElementById('suggestions').innerHTML = sugg_html;
                
                let trades_html = '';
                if (d.trades && d.trades.length > 0) {
                    d.trades.forEach(t => {
                        const cls = t.pnl > 0 ? 'win' : 'loss';
                        const ts = new Date(t.timestamp).toLocaleTimeString();
                        trades_html += `<div class="trade-row ${cls}"><div>${ts}</div><div>${t.type}</div><div>${t.asset}</div><div>${t.price.toFixed(2)}</div><div>${t.size.toFixed(4)}</div><div>${t.pnl.toFixed(2)}$</div><div>${t.reason.substring(0, 20)}</div></div>`;
                    });
                } else {
                    trades_html = '<div class="trade-row"><div>Nessun trade</div></div>';
                }
                document.getElementById('trades-list').innerHTML = trades_html;
            });
        }
        function sendCommand(cmd) {
            fetch('/trading/command', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({command: cmd})}).then(r => r.json()).then(d => alert('✅ ' + cmd));
        }
        updateDashboard();
        setInterval(updateDashboard, 2000);
    </script>
</body>
</html>
"""

@app.route('/')
def dashboard():
    return render_template_string(DASHBOARD_HTML)

if __name__ == '__main__':
    log("[MAIN] 🚀 MISSION CONTROL V5.9 + BOT V14 INTEGRATO STARTING...")
    app.run(host='0.0.0.0', port=5000, debug=False)

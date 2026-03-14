"""
MISSION CONTROL V5.9 — BOT V14 PRODUCTION INTEGRATO
=====================================================
✅ Bot gira DENTRO app.py come thread daemon
✅ Memoria condivisa thread-safe (heartbeat_data + Lock)
✅ Database persistente SQLite su /home/app/data
✅ ZERO comunicazione HTTP esterna tra bot e app
✅ Dashboard PAPER/LIVE indicator
✅ Nomi classe/file allineati a OVERTOP_BASSANO_V14_PRODUCTION
"""

from flask import Flask, jsonify, render_template_string, request
from OVERTOP_BASSANO_V14_PRODUCTION import OvertopBassanoV14Production
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

# ═══════════════════════════════════════════════════════════════════════════
# DATABASE PERSISTENTE
# ═══════════════════════════════════════════════════════════════════════════

DB_DIR  = os.environ.get("DB_DIR",  "/home/app/data")
DB_PATH = os.environ.get("DB_PATH", os.path.join(DB_DIR, "trading_data.db"))
LOG_FILE= os.path.join(DB_DIR, "trading.log")

Path(DB_DIR).mkdir(parents=True, exist_ok=True)

def log(msg):
    ts   = datetime.utcnow().isoformat()
    line = f"{ts}Z {msg}"
    print(line, flush=True)
    print(line, file=sys.stderr, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def init_db():
    try:
        log(f"[DB_INIT] 📁 {DB_DIR}")
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT DEFAULT (datetime('now')),
                event_type  TEXT,
                asset       TEXT,
                price       REAL,
                size        REAL,
                pnl         REAL,
                direction   TEXT,
                reason      TEXT,
                data_json   TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_state (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.commit()
        conn.close()
        log("[DB_INIT] ✅ DB OK")
        return True
    except Exception as e:
        log(f"[DB_INIT] ❌ {e}")
        return False

init_db()

# ═══════════════════════════════════════════════════════════════════════════
# HEARTBEAT_DATA — dizionario condiviso tra app.py e bot (thread-safe)
# ═══════════════════════════════════════════════════════════════════════════

heartbeat_lock = threading.Lock()
heartbeat_data = {
    "status":             "UNKNOWN",
    "mode":               "PAPER",    # PAPER | LIVE
    "capital":            0.0,
    "trades":             0,
    "wins":               0,
    "losses":             0,
    "wr":                 0.0,
    "last_seen":          None,
    "matrimoni_divorzio": [],
    "oracolo_snapshot":   {},
}

# ═══════════════════════════════════════════════════════════════════════════
# DB EXECUTE — con retry
# ═══════════════════════════════════════════════════════════════════════════

def db_execute(query, params=None, fetch=False):
    for attempt in range(3):
        try:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            cur  = conn.execute(query, params or [])
            if fetch:
                result = cur.fetchall() if "COUNT" not in query.upper() else cur.fetchone()
            else:
                conn.commit()
                result = None
            conn.close()
            return result
        except Exception as e:
            log(f"[DB] tentativo {attempt+1} fallito: {e}")
            if attempt == 2:
                return None
            time.sleep(0.5)

# ═══════════════════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/trading/log', methods=['POST'])
def trading_log():
    try:
        data       = request.get_json()
        event_type = data.get("type") or data.get("event_type", "UNKNOWN")
        if event_type in ("ENTRY", "EXIT"):
            db_execute("""
                INSERT INTO trades (event_type, asset, price, size, pnl, direction, reason, data_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (event_type, data.get("asset","BTCUSDC"),
                  data.get("price",0), data.get("size",0), data.get("pnl",0),
                  data.get("direction","LONG"), data.get("reason",""),
                  json.dumps(data)))
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/trading/heartbeat', methods=['POST'])
def trading_heartbeat():
    """Compatibilità: accetta heartbeat HTTP se qualcuno lo invia ancora."""
    try:
        data = request.get_json()
        with heartbeat_lock:
            heartbeat_data.update({k: v for k, v in data.items() if k in heartbeat_data})
            heartbeat_data["last_seen"] = datetime.utcnow().isoformat()
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/trading/status', methods=['GET'])
def trading_status():
    try:
        row = db_execute("""
            SELECT COUNT(*),
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),
                   SUM(pnl), MAX(pnl), MIN(pnl)
            FROM trades WHERE event_type='EXIT'
        """, fetch=True)

        trades_rows = db_execute("""
            SELECT id, timestamp, event_type, asset, price, size, pnl, direction, reason
            FROM trades ORDER BY timestamp DESC LIMIT 20
        """, fetch=True)

        n_trades  = (row[0][0] if row and row[0] else 0) or 0
        n_wins    = (row[0][1] if row and row[0] else 0) or 0
        total_pnl = (row[0][2] if row and row[0] else 0) or 0
        max_pnl   = (row[0][3] if row and row[0] else 0) or 0
        min_pnl   = (row[0][4] if row and row[0] else 0) or 0
        wr        = (n_wins / n_trades * 100) if n_trades > 0 else 0

        with heartbeat_lock:
            hb = dict(heartbeat_data)

        capital = hb.get("capital", 0)
        roi     = (total_pnl / capital * 100) if capital > 0 else 0

        trades = []
        if trades_rows:
            for r in trades_rows:
                trades.append({
                    "id": r[0], "timestamp": r[1], "type": r[2], "asset": r[3],
                    "price": float(r[4] or 0), "size": float(r[5] or 0),
                    "pnl": float(r[6] or 0), "direction": r[7],
                    "reason": (r[8] or "N/A")
                })

        suggestions = []
        if wr < 30 and n_trades > 5:   suggestions.append("⚠️ Win Rate BASSO")
        if total_pnl < -100:           suggestions.append("🔴 Drawdown ALTO")
        if n_trades == 0:              suggestions.append("🟡 Nessun trade — warmup")
        if hb.get("mode") == "PAPER":  suggestions.append("📄 PAPER TRADE attivo — nessun ordine reale")

        return jsonify({
            "heartbeat": hb,
            "metrics": {
                "n_trades": n_trades, "n_wins": n_wins,
                "wr": round(wr, 1), "pnl": round(total_pnl, 2),
                "capital": round(capital, 2), "roi": round(roi, 2),
                "max_pnl": round(max_pnl, 2), "min_pnl": round(min_pnl, 2),
            },
            "trades":      trades,
            "suggestions": suggestions,
        }), 200
    except Exception as e:
        log(f"[STATUS] ❌ {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/trading/command', methods=['POST'])
def send_command():
    try:
        data = request.get_json()
        cmd  = data.get("command", "")
        log(f"[COMMAND] 📤 {cmd}")
        return jsonify({"status": "ok", "command": cmd}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/trading/config', methods=['GET'])
def get_config():
    return jsonify({"version": "V5.9+V14_PRODUCTION", "db": DB_PATH}), 200

# ═══════════════════════════════════════════════════════════════════════════
# BRAIN THREAD — analisi periodica ogni 60s
# ═══════════════════════════════════════════════════════════════════════════

def brain_analysis_thread():
    while True:
        try:
            time.sleep(60)
            row = db_execute("""
                SELECT COUNT(*), SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END), SUM(pnl)
                FROM trades WHERE event_type='EXIT'
            """, fetch=True)
            if row and row[0]:
                n, w, p = row[0][0] or 0, row[0][1] or 0, row[0][2] or 0
                wr = (w / n * 100) if n > 0 else 0
                log(f"[BRAIN] 🧠 {n} trade | WR={wr:.0f}% | PnL={p:.2f}$")
        except Exception as e:
            log(f"[BRAIN] ❌ {e}")

threading.Thread(target=brain_analysis_thread, daemon=True, name='brain').start()

# ═══════════════════════════════════════════════════════════════════════════
# BOT LAUNCHER THREAD — avvia OvertopBassanoV14Production come daemon
# ═══════════════════════════════════════════════════════════════════════════

def bot_thread_launcher():
    retry_count = 0
    max_retries = 5
    while retry_count < max_retries:
        try:
            log("[BOT_LAUNCHER] 🚀 Avvio OvertopBassanoV14Production...")
            bot = OvertopBassanoV14Production(
                heartbeat_data=heartbeat_data,
                heartbeat_lock=heartbeat_lock,
                db_execute=db_execute,
            )
            # ── Heartbeat IMMEDIATO — non aspettare 30s ──────────────────
            with heartbeat_lock:
                heartbeat_data["status"]  = "RUNNING"
                heartbeat_data["mode"]    = "PAPER" if bot.paper_trade else "LIVE"
                heartbeat_data["capital"] = round(bot.capital, 2)
                heartbeat_data["trades"]  = bot.total_trades
                heartbeat_data["last_seen"] = datetime.utcnow().isoformat()
            log(f"[BOT_LAUNCHER] ✅ Bot istanziato — capital=${bot.capital:.2f} — bot.run() in partenza")
            bot.run()
        except Exception as e:
            retry_count += 1
            log(f"[BOT_LAUNCHER] ❌ Errore tentativo {retry_count}/{max_retries}: {e}")
            import traceback
            log(traceback.format_exc())
            time.sleep(5)
    log(f"[BOT_LAUNCHER] ❌ Bot non avviabile dopo {max_retries} tentativi")

threading.Thread(target=bot_thread_launcher, daemon=True, name='bot_v14').start()
log("[MAIN] ✅ Bot thread avviato")

# ═══════════════════════════════════════════════════════════════════════════
# DASHBOARD HTML
# ═══════════════════════════════════════════════════════════════════════════

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
        .header { font-size: 26px; font-weight: bold; text-align: center; margin-bottom: 20px;
                  border-bottom: 2px solid #00ff00; padding-bottom: 10px; }
        .mode-badge { display:inline-block; padding:3px 10px; border-radius:3px; font-size:13px;
                      margin-left:10px; font-weight:bold; }
        .mode-paper { background:#555; color:#ffff00; }
        .mode-live  { background:#ff0000; color:#fff; }
        .metrics-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
                        gap: 12px; margin-bottom: 20px; }
        .metric-card { background: #1a1f3a; border: 2px solid #00ff00; padding: 12px; border-radius: 3px; }
        .metric-label { font-size: 11px; color: #888; }
        .metric-value { font-size: 22px; font-weight: bold; color: #00ff00; margin-top: 4px; }
        .status-running { color: #00ff00; } .status-offline { color: #ff0000; }
        .controls { display: flex; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; }
        button { background: #00ff00; color: #0a0e27; border: none; padding: 10px 15px;
                 border-radius: 3px; cursor: pointer; font-weight: bold; font-size: 13px; }
        button:hover { background: #00cc00; }
        .suggestions { background: #1a1f3a; border-left: 4px solid #ffff00; padding: 12px;
                       margin-bottom: 20px; border-radius: 3px; }
        .oracolo-section { background: #1a1f3a; border: 1px solid #555; padding: 10px;
                           margin-bottom: 20px; border-radius: 3px; font-size: 11px; }
        .trades-section { background: #1a1f3a; border: 2px solid #00ff00; padding: 12px;
                          border-radius: 3px; overflow-x: auto; }
        .trade-row { display: grid;
                     grid-template-columns: 90px 60px 70px 80px 70px 70px 110px;
                     gap: 8px; padding: 7px; border-bottom: 1px solid #333; font-size: 11px; }
        .trade-row.header { font-weight: bold; border-bottom: 2px solid #00ff00; background: #0f1420; }
        .win { color: #00ff00; } .loss { color: #ff0000; }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        🔴 MISSION CONTROL V5.9
        <span class="mode-badge" id="mode-badge">--</span>
    </div>
    <div class="metrics-grid">
        <div class="metric-card"><div class="metric-label">PnL $</div><div class="metric-value" id="pnl">--</div></div>
        <div class="metric-card"><div class="metric-label">WR %</div><div class="metric-value" id="wr">--</div></div>
        <div class="metric-card"><div class="metric-label">Capital $</div><div class="metric-value" id="capital">--</div></div>
        <div class="metric-card"><div class="metric-label">ROI %</div><div class="metric-value" id="roi">--</div></div>
        <div class="metric-card"><div class="metric-label">Trade #</div><div class="metric-value" id="n_trades">--</div></div>
        <div class="metric-card"><div class="metric-label">STATUS</div><div class="metric-value" id="status">OFFLINE</div></div>
        <div class="metric-card"><div class="metric-label">Divorzi</div><div class="metric-value" id="divorzi" style="font-size:13px">--</div></div>
    </div>
    <!-- LIVE TICKER -->
    <div style="background:#0f1420; border:1px solid #00ff00; padding:10px; margin-bottom:15px; border-radius:3px; font-size:13px; display:flex; gap:30px; align-items:center;">
        <span>💹 BTC/USDC: <span id="live-price" style="color:#00ffff; font-size:18px; font-weight:bold">--</span></span>
        <span>⚡ Tick: <span id="tick-count" style="color:#ffff00">#0</span></span>
        <span>🕐 Ultimo: <span id="last-tick" style="color:#888">--</span></span>
        <span id="trade-status" style="color:#aaa">🔍 Analizzando mercato...</span>
    </div>
    <div class="controls">
        <button onclick="sendCommand('STOP')">⏹️ STOP</button>
        <button onclick="sendCommand('RESUME')">▶️ RESUME</button>
        <button onclick="sendCommand('RESET_LOSSES')">🔄 RESET</button>
    </div>
    <div class="suggestions" id="suggestions"></div>
    <div class="oracolo-section">
        <div style="font-weight:bold; margin-bottom:6px;">🔮 ORACOLO DINAMICO — Fingerprint WR</div>
        <div id="oracolo-data" style="color:#aaa;">Nessun dato ancora</div>
    </div>
    <div class="trades-section">
        <div style="margin-bottom:10px; font-weight:bold;">📊 ULTIMI 20 TRADE</div>
        <div class="trade-row header">
            <div>TIME</div><div>TYPE</div><div>ASSET</div><div>PRICE</div>
            <div>PnL</div><div>WR%</div><div>REASON</div>
        </div>
        <div id="trades-list"></div>
    </div>
</div>
<script>
function updateDashboard() {
    fetch('/trading/status').then(r => r.json()).then(d => {
        const m  = d.metrics;
        const hb = d.heartbeat;
        document.getElementById('pnl').textContent      = (m.pnl >= 0 ? '+' : '') + m.pnl.toFixed(2) + '$';
        document.getElementById('wr').textContent       = m.wr.toFixed(1) + '%';
        document.getElementById('capital').textContent  = '$' + m.capital.toFixed(0);
        document.getElementById('roi').textContent      = m.roi.toFixed(2) + '%';
        document.getElementById('n_trades').textContent = m.n_trades;
        document.getElementById('status').textContent   = hb.status || 'OFFLINE';
        document.getElementById('status').className = 'metric-value ' +
            (hb.status === 'RUNNING' ? 'status-running' : 'status-offline');

        // Live ticker
        const lp = hb.last_price;
        if (lp) {
            document.getElementById('live-price').textContent = '$' + lp.toLocaleString('en-US', {minimumFractionDigits:2});
        }
        const tc = hb.tick_count || 0;
        document.getElementById('tick-count').textContent = '#' + tc.toLocaleString();
        const lt = hb.last_tick ? new Date(hb.last_tick).toLocaleTimeString() : '--';
        document.getElementById('last-tick').textContent = lt;

        // Trade status
        const ts = document.getElementById('trade-status');
        if (hb.posizione_aperta) {
            ts.textContent = '🟢 TRADE APERTO';
            ts.style.color = '#00ff00';
        } else if (tc < 20) {
            ts.textContent = '⏳ Warmup (' + tc + '/20 tick)';
            ts.style.color = '#ffff00';
        } else {
            ts.textContent = '🔍 Analizzando — in attesa setup';
            ts.style.color = '#aaa';
        }
        const mode  = hb.mode || 'PAPER';
        const badge = document.getElementById('mode-badge');
        badge.textContent = mode === 'LIVE' ? '🔴 LIVE' : '📄 PAPER';
        badge.className   = 'mode-badge ' + (mode === 'LIVE' ? 'mode-live' : 'mode-paper');

        // Divorzi
        const divorzi = hb.matrimoni_divorzio || [];
        document.getElementById('divorzi').textContent =
            divorzi.length > 0 ? divorzi.join(', ') : '✅ nessuno';

        // Oracolo snapshot
        const oracolo = hb.oracolo_snapshot || {};
        const fps = Object.keys(oracolo);
        if (fps.length > 0) {
            document.getElementById('oracolo-data').innerHTML = fps.map(fp => {
                const d2 = oracolo[fp];
                const wr = (d2.wr * 100).toFixed(0);
                const col = d2.wr >= 0.60 ? '#00ff00' : (d2.wr >= 0.45 ? '#ffff00' : '#ff4444');
                return `<span style="margin-right:14px; color:${col}">${fp}: WR=${wr}% (${d2.samples})</span>`;
            }).join('');
        }

        // Suggerimenti
        let sh = '<div style="font-weight:bold; margin-bottom:5px;">💡 ALERT:</div>';
        (d.suggestions || []).forEach(s => { sh += '<div style="margin:4px 0">' + s + '</div>'; });
        if (!d.suggestions || d.suggestions.length === 0) sh += '<div>✅ Sistema OK</div>';
        document.getElementById('suggestions').innerHTML = sh;

        // Trade list
        let th = '';
        if (d.trades && d.trades.length > 0) {
            d.trades.forEach(t => {
                const cls = t.pnl > 0 ? 'win' : 'loss';
                const ts  = new Date(t.timestamp).toLocaleTimeString();
                th += `<div class="trade-row ${cls}">
                    <div>${ts}</div><div>${t.type}</div><div>${t.asset}</div>
                    <div>${t.price.toFixed(1)}</div>
                    <div>${t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(2)}$</div>
                    <div></div>
                    <div>${(t.reason || 'N/A').substring(0, 18)}</div>
                </div>`;
            });
        } else {
            th = '<div class="trade-row" style="color:#555">Nessun trade ancora</div>';
        }
        document.getElementById('trades-list').innerHTML = th;
    }).catch(() => {
        document.getElementById('status').textContent = 'OFFLINE';
    });
}
function sendCommand(cmd) {
    fetch('/trading/command', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({command: cmd})
    }).then(r => r.json()).then(() => alert('✅ Comando inviato: ' + cmd));
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

# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    log(f"[MAIN] 🚀 MISSION CONTROL V5.9 — porta {port}")
    app.run(host='0.0.0.0', port=port, debug=False)

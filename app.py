"""
MISSION CONTROL V6.0 — BOT V15 PRODUCTION + AI BRIDGE
=====================================================
✅ Bot gira DENTRO app.py come thread daemon
✅ Memoria condivisa thread-safe (heartbeat_data + Lock)
✅ Database persistente SQLite su /home/app/data
✅ ZERO comunicazione HTTP esterna tra bot e app
✅ Dashboard PAPER/LIVE indicator
✅ Nomi classe/file allineati a OVERTOP_BASSANO_V15_PRODUCTION
✅ AI BRIDGE: Claude analizza e comanda in tempo reale
"""

from flask import Flask, jsonify, render_template_string, request, send_file, abort
from OVERTOP_BASSANO_V15_PRODUCTION import OvertopBassanoV15Production
from ai_bridge import AIBridge
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
NARRATIVES_DB = os.environ.get("NARRATIVES_DB", os.path.join(DB_DIR, "narratives.db"))
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
    "m2_direction":       "LONG",
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
# DOWNLOAD SECRET per endpoint protetti
# ═══════════════════════════════════════════════════════════════════════════

DOWNLOAD_SECRET = os.environ.get("DOWNLOAD_SECRET", "overtop2024")

def _check_key():
    if request.args.get('key') != DOWNLOAD_SECRET:
        abort(403, "Chiave non valida")

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
            FROM trades WHERE event_type IN ('EXIT', 'M2_EXIT')
        """, fetch=True)

        trades_rows = db_execute("""
            SELECT id, timestamp, event_type, asset, price, size, pnl, direction, reason
            FROM trades ORDER BY timestamp DESC LIMIT 20
        """, fetch=True)

        # db_execute con COUNT usa fetchone → tupla diretta, non lista di tuple
        _row = row if (row and not isinstance(row, list)) else (row[0] if row else None)
        n_trades  = int(_row[0] or 0) if _row else 0
        n_wins    = int(_row[1] or 0) if _row else 0
        total_pnl = float(_row[2] or 0) if _row else 0
        max_pnl   = float(_row[3] or 0) if _row else 0
        min_pnl   = float(_row[4] or 0) if _row else 0
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

@app.route('/telemetry', methods=['GET'])
def telemetry_report():
    """Report stabilità — solo numeri, zero interpretazione."""
    try:
        with heartbeat_lock:
            hb = dict(heartbeat_data)
        telemetry = hb.get("telemetry", {})
        return jsonify(telemetry), 200
    except Exception as e:
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
    return jsonify({"version": "V6.0+V15_PRODUCTION+IA", "db": DB_PATH}), 200

# ═══════════════════════════════════════════════════════════════════════════
# DIAGNOSTIC — tutto quello che serve per capire lo stato del sistema
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/diagnostic', methods=['GET'])
def diagnostic():
    try:
        with heartbeat_lock:
            hb = dict(heartbeat_data)

        # -- FIX ATTIVI: verifica diretta sul file sorgente --
        src = "/opt/render/project/src/OVERTOP_BASSANO_V15_PRODUCTION.py"
        fix_attivi = {}
        try:
            code = open(src).read()
            fix_attivi = {
                "MIN_HOLD_10s":        "if duration >= MIN_HOLD_SECONDS:" in code,
                "T1_VOL_pnl_guard":    "volatility == \"ALTA\" and current_pnl_real < 0:" in code,
                "T4_FP_pnl_guard":     "fp_div > DIVORCE_FP_DIVERGE_PCT and current_pnl_real < 0:" in code,
                "CESPUGLIO_bypass":    "CESPUGLIO bypass" in code,
                "drawdown_scope_fix":  "FIX: drawdown_pct calcolato sempre" in code,
            }
        except Exception as e:
            fix_attivi = {"error": str(e)}

        # -- ULTIMO TRADE --
        ultimo_trade = {}
        trades_rows = db_execute("""
            SELECT timestamp, event_type, direction, price, pnl, reason, data_json
            FROM trades ORDER BY id DESC LIMIT 2
        """, fetch=True)
        if trades_rows:
            for r in trades_rows:
                et = r[1]
                if et == "M2_EXIT":
                    try:
                        dj = json.loads(r[6]) if r[6] else {}
                    except:
                        dj = {}
                    ultimo_trade = {
                        "timestamp":  r[0],
                        "direzione":  r[2],
                        "prezzo":     r[3],
                        "pnl":        round(float(r[4] or 0), 4),
                        "motivo":     r[5],
                        "durata_s":   dj.get("duration", "?"),
                        "score":      dj.get("score", "?"),
                        "matrimonio": dj.get("matrimonio", "?"),
                    }
                    break

        # -- DIVORZIO STATS: conta trigger oggi --
        divorzio_stats = {"T1_VOL": 0, "T2_TREND": 0, "T3_DD": 0, "T4_FP": 0, "totale": 0}
        div_rows = db_execute("""
            SELECT reason FROM trades
            WHERE event_type='M2_EXIT' AND reason LIKE 'DIVORZIO%'
            AND timestamp >= datetime('now', '-1 day')
        """, fetch=True)
        if div_rows:
            for r in div_rows:
                reason = r[0] or ""
                divorzio_stats["totale"] += 1
                for t in ["T1_VOL", "T2_TREND", "T3_DD", "T4_FP"]:
                    if t in reason:
                        divorzio_stats[t] += 1

        # -- PESI SC --
        sc_pesi = hb.get("sc_pesi", {})

        # -- ORACOLO TOP 5 PER WR --
        oracolo = hb.get("oracolo_snapshot", {})
        top_fp = sorted(
            [(k, v) for k, v in oracolo.items()
             if isinstance(v, dict) and v.get("samples", 0) >= 5 and not k.startswith("_")],
            key=lambda x: x[1].get("wr", 0), reverse=True
        )[:5]
        oracolo_top5 = [
            {"fingerprint": k, "wr": round(v["wr"]*100, 1),
             "campioni": v["samples"], "pnl_avg": v["pnl_avg"]}
            for k, v in top_fp
        ]

        # -- CESPUGLIO STATS --
        phantom = hb.get("phantom", {})
        per_livello = phantom.get("per_livello", {})
        cespuglio = per_livello.get("CESPUGLIO_RANGING_2loss", {})
        cespuglio_stats = {
            "bloccati":    cespuglio.get("blocked", 0),
            "would_win":   cespuglio.get("would_win", 0),
            "would_lose":  cespuglio.get("would_lose", 0),
            "pnl_salvati": round(cespuglio.get("pnl_saved", 0), 2),
            "pnl_persi":   round(cespuglio.get("pnl_missed", 0), 2),
            "net":         round(cespuglio.get("pnl_saved", 0) - cespuglio.get("pnl_missed", 0), 2),
        }

        # -- STATO GENERALE --
        stato = {
            "status":        hb.get("status", "UNKNOWN"),
            "regime":        hb.get("regime", "?"),
            "m2_trades":     hb.get("m2_trades", 0),
            "m2_wins":       hb.get("m2_wins", 0),
            "m2_losses":     hb.get("m2_losses", 0),
            "m2_pnl":        round(hb.get("m2_pnl", 0), 4),
            "m2_wr":         round(hb.get("m2_wr", 0) * 100, 1),
            "m2_state":      hb.get("m2_state", "?"),
            "loss_streak":   hb.get("m2_loss_streak", 0),
            "phantom_bilancio": round(phantom.get("bilancio", 0), 2),
            "tick_count":    hb.get("tick_count", 0),
            "last_price":    hb.get("last_price", 0),
        }

        # -- VERITAS SINTESI --
        veritas = hb.get("veritas", {})
        veritas_sintesi = veritas.get("conflitto", {})

        result = {
            "🔧 FIX_ATTIVI":       fix_attivi,
            "📊 STATO":            stato,
            "💰 ULTIMO_TRADE":     ultimo_trade,
            "💔 DIVORZIO_OGGI":    divorzio_stats,
            "🧠 PESI_SC":          sc_pesi,
            "🔮 ORACOLO_TOP5":     oracolo_top5,
            "🌿 CESPUGLIO":        cespuglio_stats,
            "⚖️ VERITAS":          veritas_sintesi,
        }

        # Rendering HTML leggibile
        html = """<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<title>DIAGNOSTIC — OVERTOP V15</title>
<style>
body{background:#060810;color:#00ff88;font-family:monospace;padding:20px;font-size:13px;}
h1{color:#ffd700;font-size:18px;margin-bottom:20px;}
h2{color:#00aaff;font-size:14px;margin-top:20px;margin-bottom:8px;border-bottom:1px solid #333;padding-bottom:4px;}
.ok{color:#00ff88;} .err{color:#ff3355;} .warn{color:#ffd700;}
table{border-collapse:collapse;width:100%;margin-bottom:10px;}
td,th{padding:4px 12px;text-align:left;border-bottom:1px solid #1a2030;}
th{color:#aaa;font-weight:normal;}
.val{color:#fff;}
</style></head><body>
<h1>⚡ DIAGNOSTIC — OVERTOP BASSANO V15</h1>
"""
        for section, data in result.items():
            html += f"<h2>{section}</h2><table>"
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, bool):
                        cls = "ok" if v else "err"
                        val = "✅ ATTIVO" if v else "❌ MANCANTE"
                    elif isinstance(v, (int, float)):
                        cls = "val"
                        val = str(v)
                    else:
                        cls = "val"
                        val = str(v)
                    html += f"<tr><td style='color:#aaa'>{k}</td><td class='{cls}'>{val}</td></tr>"
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        vals = " | ".join(f"<span style='color:#aaa'>{k}:</span> <span class='val'>{v}</span>" for k,v in item.items())
                        html += f"<tr><td colspan='2'>{vals}</td></tr>"
            html += "</table>"

        html += f"<p style='color:#333;font-size:11px;margin-top:30px'>Aggiornato: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>"
        html += "</body></html>"

        from flask import Response
        return Response(html, mimetype='text/html')

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ═══════════════════════════════════════════════════════════════════════════
# DOWNLOAD ENDPOINTS — scarica DB, narratives, capsule
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/download/db')
def download_db():
    _check_key()
    if not os.path.exists(DB_PATH):
        abort(404, "trading_data.db non trovato")
    return send_file(DB_PATH, as_attachment=True, download_name="trading_data.db")

@app.route('/download/narratives')
def download_narratives():
    _check_key()
    if not os.path.exists(NARRATIVES_DB):
        abort(404, "narratives.db non trovato")
    return send_file(NARRATIVES_DB, as_attachment=True, download_name="narratives.db")

@app.route('/download/capsule')
def download_capsule():
    _check_key()
    capsule_file = "capsule_attive.json"
    if not os.path.exists(capsule_file):
        abort(404, "capsule_attive.json non trovato")
    return send_file(capsule_file, as_attachment=True, download_name="capsule_attive.json")

@app.route('/debug/db')
def debug_db():
    _check_key()
    if not os.path.exists(DB_PATH):
        return json.dumps({"error": "DB non trovato"}), 404
    conn = sqlite3.connect(DB_PATH)
    rows = dict(conn.execute("SELECT key, value FROM bot_state").fetchall())
    conn.close()
    result = {}
    for k, v in rows.items():
        try:
            parsed = json.loads(v)
            if isinstance(parsed, dict) and len(str(parsed)) > 2000:
                result[k] = f"[{len(parsed)} entries]"
            else:
                result[k] = parsed
        except (json.JSONDecodeError, TypeError):
            result[k] = v
    return json.dumps(result, indent=2), 200, {'Content-Type': 'application/json'}

# ═══════════════════════════════════════════════════════════════════════════
# AI BRIDGE STATUS ENDPOINT
# ═══════════════════════════════════════════════════════════════════════════

bridge = None  # inizializzato dopo il bot

@app.route('/signal_tracker')
def signal_tracker_view():
    """Distribuzione previsionale del sistema — quanto si muove il prezzo post-segnale."""
    try:
        with heartbeat_lock:
            hb = dict(heartbeat_data)
        st = hb.get("signal_tracker", {})
        return json.dumps(st, indent=2), 200, {'Content-Type': 'application/json'}
    except Exception as e:
        return json.dumps({"error": str(e)}), 500

@app.route('/bridge/status')
def bridge_status():
    if bridge:
        return json.dumps(bridge.get_status(), indent=2), 200, {'Content-Type': 'application/json'}
    return json.dumps({"active": False, "reason": "bridge not initialized"}), 200, {'Content-Type': 'application/json'}

# ═══════════════════════════════════════════════════════════════════════════
# BRAIN THREAD — analisi periodica ogni 60s
# ═══════════════════════════════════════════════════════════════════════════

def brain_analysis_thread():
    while True:
        try:
            time.sleep(60)
            row = db_execute("""
                SELECT COUNT(*), SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END), SUM(pnl)
                FROM trades WHERE event_type IN ('EXIT', 'M2_EXIT')
            """, fetch=True)
            if row and row[0]:
                n, w, p = row[0][0] or 0, row[0][1] or 0, row[0][2] or 0
                wr = (w / n * 100) if n > 0 else 0
                log(f"[BRAIN] 🧠 {n} trade | WR={wr:.0f}% | PnL={p:.2f}$")
        except Exception as e:
            log(f"[BRAIN] ❌ {e}")

threading.Thread(target=brain_analysis_thread, daemon=True, name='brain').start()

# ═══════════════════════════════════════════════════════════════════════════
# BOT LAUNCHER THREAD + AI BRIDGE
# ═══════════════════════════════════════════════════════════════════════════

def _auto_inject_brain():
    """
    Se il DB ha meno di 10 trade reali nell'Oracolo → inietta memoria storica.
    Eseguito UNA SOLA VOLTA al boot. Il flag 'brain_injected' nel DB evita
    re-iniezioni ai restart successivi.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = dict(conn.execute("SELECT key, value FROM bot_state").fetchall())
        conn.close()

        # Già iniettato in precedenza → skip
        if rows.get('brain_injected') == '1':
            log("[BRAIN_INJECT] ✅ Brain già iniettato — skip")
            return

        # Conta i trade reali nell'Oracolo
        real_samples = 0
        short_tossici_ok = False
        if 'oracolo' in rows:
            try:
                oracolo_data = json.loads(rows['oracolo'])
                real_samples = sum(
                    v.get('real_samples', 0)
                    for k, v in oracolo_data.items()
                    if not k.startswith('_')
                )
                # Verifica che i SHORT tossici siano iniettati con campioni sufficienti
                fp_short = oracolo_data.get('SHORT|MEDIO|ALTA|SIDEWAYS', {})
                short_tossici_ok = fp_short.get('samples', 0) >= 5
            except Exception:
                pass

        if real_samples >= 10 and short_tossici_ok:
            log(f"[BRAIN_INJECT] ✅ {real_samples} trade reali + SHORT tossici OK — skip")
            conn = sqlite3.connect(DB_PATH)
            conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('brain_injected', '1')")
            conn.commit()
            conn.close()
            return
        
        if real_samples >= 10 and not short_tossici_ok:
            log(f"[BRAIN_INJECT] ⚠️ SHORT tossici mancanti — re-iniezione brain")
            # Rimuove flag per forzare re-iniezione
            conn = sqlite3.connect(DB_PATH)
            conn.execute("DELETE FROM bot_state WHERE key='brain_injected'")
            conn.commit()
            conn.close()

        log(f"[BRAIN_INJECT] 🧠 Solo {real_samples} trade reali — avvio iniezione dati storici...")

        # Importa e esegui inject_brain
        try:
            import inject_brain
            inject_brain.inject(DB_PATH, dry_run=False)
            # Marca come iniettato
            conn = sqlite3.connect(DB_PATH)
            conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('brain_injected', '1')")
            conn.commit()
            conn.close()
            log("[BRAIN_INJECT] ✅ Brain iniettato con successo — 22 fingerprint storici caricati")
        except ImportError:
            log("[BRAIN_INJECT] ⚠️ inject_brain.py non trovato — il bot parte da zero")
        except Exception as e:
            log(f"[BRAIN_INJECT] ❌ Errore iniezione: {e}")

    except Exception as e:
        log(f"[BRAIN_INJECT] ❌ Errore generale: {e}")


def bot_thread_launcher():
    global bridge
    retry_count = 0
    max_retries = 5
    while retry_count < max_retries:
        try:
            # ── AUTO-INJECT BRAIN — prima del bot ───────────────────────
            _auto_inject_brain()

            log("[BOT_LAUNCHER] 🚀 Avvio OvertopBassanoV15Production...")
            bot = OvertopBassanoV15Production(
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

            # ── AI BRIDGE — connette il bot a bridge predittivo locale ─────────────────
            bridge = AIBridge(heartbeat_data, heartbeat_lock)
            bridge.start()

            log(f"[BOT_LAUNCHER] ✅ Bot istanziato — capital=${bot.capital:.2f} — bot.run() in partenza")
            bot.run()
        except Exception as e:
            retry_count += 1
            log(f"[BOT_LAUNCHER] ❌ Errore tentativo {retry_count}/{max_retries}: {e}")
            import traceback
            log(traceback.format_exc())
            time.sleep(5)
    log(f"[BOT_LAUNCHER] ❌ Bot non avviabile dopo {max_retries} tentativi")

threading.Thread(target=bot_thread_launcher, daemon=True, name='bot_v15').start()
log("[MAIN] ✅ Bot thread + AI Bridge avviati")

# ═══════════════════════════════════════════════════════════════════════════
# DASHBOARD HTML
# ═══════════════════════════════════════════════════════════════════════════

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MISSION CONTROL V6.0</title>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@400;700;900&display=swap" rel="stylesheet">
<style>
:root {
  --bg:       #060810;
  --bg2:      #0c1020;
  --bg3:      #111828;
  --green:    #00ff88;
  --green2:   #00cc66;
  --red:      #ff3355;
  --yellow:   #ffd700;
  --blue:     #00aaff;
  --purple:   #bb66ff;
  --orange:   #ff8800;
  --gray:     #445566;
  --text:     #ccd6e0;
  --dim:      #667788;
  --border:   #1a2535;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:'Share Tech Mono',monospace; background:var(--bg); color:var(--text); min-height:100vh; }
body::before { content:''; position:fixed; inset:0; background:
  radial-gradient(ellipse 80% 50% at 20% 0%, rgba(0,255,136,0.04) 0%, transparent 60%),
  radial-gradient(ellipse 60% 40% at 80% 100%, rgba(0,170,255,0.03) 0%, transparent 60%);
  pointer-events:none; z-index:0; }

.wrap { max-width:1300px; margin:0 auto; padding:14px; position:relative; z-index:1; }

/* ── HEADER ── */
.hdr { display:flex; align-items:center; justify-content:space-between; margin-bottom:16px;
       border-bottom:1px solid var(--border); padding-bottom:12px; flex-wrap:wrap; gap:10px; }
.hdr-title { font-family:'Orbitron',monospace; font-size:18px; font-weight:900;
             letter-spacing:3px; color:var(--green); text-shadow:0 0 20px rgba(0,255,136,0.4); }
.hdr-right { display:flex; align-items:center; gap:12px; font-size:12px; }
.badge { padding:3px 10px; border-radius:2px; font-weight:700; font-size:11px; letter-spacing:1px; }
.badge-paper { background:#1a1500; color:var(--yellow); border:1px solid var(--yellow); }
.badge-live  { background:#1a0000; color:var(--red);    border:1px solid var(--red); animation:pulse 1s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.6} }
.status-dot { width:8px; height:8px; border-radius:50%; display:inline-block; margin-right:5px; }
.dot-run { background:var(--green); box-shadow:0 0 8px var(--green); animation:pulse 2s infinite; }
.dot-off { background:var(--red); }

/* ── TICKER BAR ── */
.ticker { background:var(--bg2); border:1px solid var(--border); border-left:3px solid var(--green);
          padding:8px 14px; margin-bottom:14px; border-radius:2px;
          display:flex; gap:24px; align-items:center; flex-wrap:wrap; font-size:12px; }
.price-big { font-family:'Orbitron',monospace; font-size:22px; font-weight:700; color:var(--green); }

/* ── ALERT BAR ── */
.alert-bar { padding:8px 14px; margin-bottom:14px; border-radius:2px; font-size:12px;
             display:none; border-left:3px solid var(--red); background:rgba(255,51,85,0.08); color:var(--red); }

/* ── KPI GRID ── */
.kpi-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(110px,1fr)); gap:8px; margin-bottom:14px; }
.kpi { background:var(--bg2); border:1px solid var(--border); padding:10px 12px; border-radius:2px;
       position:relative; overflow:hidden; transition:border-color .2s; }
.kpi:hover { border-color:var(--green); }
.kpi::after { content:''; position:absolute; bottom:0; left:0; right:0; height:2px; background:var(--green); transform:scaleX(0); transition:transform .3s; }
.kpi:hover::after { transform:scaleX(1); }
.kpi-lbl { font-size:9px; color:var(--dim); letter-spacing:1px; text-transform:uppercase; }
.kpi-val { font-family:'Orbitron',monospace; font-size:18px; font-weight:700; margin-top:3px; }
.kpi-val.pos { color:var(--green); } .kpi-val.neg { color:var(--red); }
.kpi-val.neu { color:var(--text); }
.kpi-sub { font-size:9px; color:var(--dim); margin-top:2px; }

/* ── TWO COLUMN LAYOUT ── */
.two-col { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:10px; }
@media(max-width:800px){ .two-col { grid-template-columns:1fr; } }
.three-col { display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px; margin-bottom:10px; }
@media(max-width:900px){ .three-col { grid-template-columns:1fr 1fr; } }
@media(max-width:600px){ .three-col { grid-template-columns:1fr; } }

/* ── PANEL ── */
.panel { background:var(--bg2); border:1px solid var(--border); border-radius:2px; overflow:hidden; }
.panel-head { padding:8px 12px; font-size:10px; letter-spacing:2px; text-transform:uppercase;
              display:flex; align-items:center; justify-content:space-between;
              border-bottom:1px solid var(--border); }
.panel-head.green  { border-left:3px solid var(--green);  color:var(--green); }
.panel-head.blue   { border-left:3px solid var(--blue);   color:var(--blue); }
.panel-head.yellow { border-left:3px solid var(--yellow); color:var(--yellow); }
.panel-head.purple { border-left:3px solid var(--purple); color:var(--purple); }
.panel-head.orange { border-left:3px solid var(--orange); color:var(--orange); }
.panel-head.red    { border-left:3px solid var(--red);    color:var(--red); }
.panel-body { padding:10px 12px; }

/* ── M2 DIRECTION BOX ── */
.dir-box { margin:8px 0; padding:12px; border-radius:2px; text-align:center;
           font-family:'Orbitron',monospace; font-size:20px; font-weight:900; letter-spacing:4px;
           transition:all .3s; }
.dir-long  { background:linear-gradient(135deg,rgba(0,255,136,0.08),rgba(0,204,102,0.04));
             border:1px solid var(--green); color:var(--green); text-shadow:0 0 15px rgba(0,255,136,0.5); }
.dir-short { background:linear-gradient(135deg,rgba(255,51,85,0.08),rgba(200,0,40,0.04));
             border:1px solid var(--red); color:var(--red); text-shadow:0 0 15px rgba(255,51,85,0.5); }

/* ── MINI STATS ROW ── */
.stat-row { display:flex; flex-wrap:wrap; gap:10px; font-size:11px; padding:6px 0; border-bottom:1px solid var(--border); }
.stat-row:last-child { border-bottom:none; }
.stat-item { display:flex; gap:4px; align-items:center; }
.stat-lbl { color:var(--dim); }
.stat-val { font-weight:700; }

/* ── ORACOLO TABLE ── */
.oracolo-table { width:100%; border-collapse:collapse; font-size:10px; }
.oracolo-table th { color:var(--dim); font-size:9px; letter-spacing:1px; padding:4px 6px;
                    text-transform:uppercase; border-bottom:1px solid var(--border); text-align:left; }
.oracolo-table td { padding:4px 6px; border-bottom:1px solid rgba(255,255,255,0.03); }
.oracolo-table tr:hover td { background:rgba(255,255,255,0.02); }
.wr-bar { display:inline-block; height:3px; border-radius:1px; vertical-align:middle; margin-left:4px; }

/* ── IA CAPSULE ── */
.capsule-item { padding:5px 8px; margin-bottom:4px; border-radius:1px; font-size:10px;
                display:flex; justify-content:space-between; align-items:center; }
.cap-l2-blk  { background:rgba(255,51,85,0.08);   border-left:2px solid var(--red); }
.cap-l2-bst  { background:rgba(0,255,136,0.08);   border-left:2px solid var(--green); }
.cap-l3-stk  { background:rgba(255,215,0,0.08);   border-left:2px solid var(--yellow); }
.cap-l3-reg  { background:rgba(255,136,0,0.08);   border-left:2px solid var(--orange); }
.cap-l3-opp  { background:rgba(0,170,255,0.08);   border-left:2px solid var(--blue); }
.ttl-bar { font-size:9px; color:var(--dim); }

/* ── LOG FEED ── */
.log-feed { font-size:10px; line-height:1.9; max-height:180px; overflow-y:auto;
            scrollbar-width:thin; scrollbar-color:var(--border) transparent; }
.log-feed::-webkit-scrollbar { width:3px; }
.log-feed::-webkit-scrollbar-thumb { background:var(--border); }
.log-line { padding:1px 0; border-bottom:1px solid rgba(255,255,255,0.02); }

/* ── PHANTOM ── */
.phantom-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:6px; margin-bottom:8px; }
.ph-kpi { background:var(--bg3); padding:8px; border-radius:1px; text-align:center; }
.ph-kpi-lbl { font-size:9px; color:var(--dim); }
.ph-kpi-val { font-size:16px; font-weight:700; margin-top:2px; }
.verdict-box { padding:8px; text-align:center; border-radius:1px; font-size:12px; font-weight:700;
               letter-spacing:1px; margin-bottom:8px; }
.verdict-green  { background:rgba(0,255,136,0.08); border:1px solid var(--green); color:var(--green); }
.verdict-red    { background:rgba(255,51,85,0.08);  border:1px solid var(--red);   color:var(--red); }
.verdict-yellow { background:rgba(255,215,0,0.08);  border:1px solid var(--yellow); color:var(--yellow); }

/* ── TRADES TABLE ── */
.trade-tbl { width:100%; border-collapse:collapse; font-size:10px; }
.trade-tbl th { color:var(--dim); font-size:9px; letter-spacing:1px; padding:5px 6px;
                border-bottom:1px solid var(--border); text-align:left; text-transform:uppercase; }
.trade-tbl td { padding:5px 6px; border-bottom:1px solid rgba(255,255,255,0.02); }
.trade-tbl tr:hover td { background:rgba(255,255,255,0.02); }
.pnl-pos { color:var(--green); font-weight:700; }
.pnl-neg { color:var(--red); font-weight:700; }

/* ── CONTROLS ── */
.controls { display:flex; gap:8px; margin-bottom:10px; flex-wrap:wrap; }
.btn { background:transparent; border:1px solid var(--green); color:var(--green); padding:7px 14px;
       border-radius:2px; cursor:pointer; font-family:'Share Tech Mono',monospace; font-size:11px;
       letter-spacing:1px; transition:all .15s; }
.btn:hover { background:rgba(0,255,136,0.1); }
.btn-red   { border-color:var(--red); color:var(--red); }
.btn-red:hover { background:rgba(255,51,85,0.1); }

/* ── REGIME INDICATOR ── */
.regime-badge { display:inline-block; padding:2px 8px; border-radius:1px; font-size:10px;
                font-weight:700; letter-spacing:1px; }
.regime-trending-bull  { background:rgba(0,255,136,0.12); color:var(--green); border:1px solid var(--green2); }
.regime-trending-bear  { background:rgba(255,51,85,0.12);  color:var(--red);   border:1px solid var(--red); }
.regime-explosive      { background:rgba(255,215,0,0.12);  color:var(--yellow); border:1px solid var(--yellow); }
.regime-ranging        { background:rgba(0,170,255,0.12);  color:var(--blue);   border:1px solid var(--blue); }

/* ── DRIFT INDICATOR ── */
.drift-bar-wrap { height:4px; background:var(--bg3); border-radius:2px; overflow:hidden; margin-top:4px; }
.drift-bar-fill { height:100%; border-radius:2px; transition:width .5s,background .5s; }

/* ── SPARKLINE AREA ── */
.sparkline-wrap { height:40px; margin-top:6px; position:relative; }
canvas.spark { width:100%; height:40px; }

/* ── SECTION SEPARATOR ── */
.sep { height:1px; background:linear-gradient(90deg,transparent,var(--border),transparent); margin:10px 0; }
</style>
</head>
<body>
<div class="wrap">

  <!-- HEADER -->
  <div class="hdr">
    <div class="hdr-title">⚡ MISSION CONTROL V6.0</div>
    <div class="hdr-right">
      <span><span class="status-dot" id="status-dot"></span><span id="status-txt" style="font-size:11px">OFFLINE</span></span>
      <span id="mode-badge" class="badge badge-paper">PAPER</span>
      <span style="font-size:10px; color:var(--dim)" id="last-seen">--</span>
    </div>
  </div>

  <!-- ALERT BAR -->
  <div class="alert-bar" id="alert-bar"></div>

  <!-- TICKER -->
  <div class="ticker">
    <span class="price-big" id="btc-price">--</span>
    <span style="color:var(--dim)">BTC/USDC</span>
    <span>⚡ <span id="tick-n" style="color:var(--yellow)">0</span></span>
    <span>🕐 <span id="last-tick" style="color:var(--dim)">--</span></span>
    <span id="trade-status-txt" style="color:var(--dim)">🔍 Analizzando...</span>
    <span style="margin-left:auto; font-size:10px;" id="regime-badge-ticker"></span>
  </div>

  <!-- KPI ROW -->
  <div class="kpi-grid">
    <div class="kpi">
      <div class="kpi-lbl">PnL M2</div>
      <div class="kpi-val" id="k-pnl">--</div>
      <div class="kpi-sub" id="k-roi">ROI --</div>
    </div>
    <div class="kpi">
      <div class="kpi-lbl">Win Rate</div>
      <div class="kpi-val" id="k-wr">--</div>
      <div class="kpi-sub" id="k-wl">0W / 0L</div>
    </div>
    <div class="kpi">
      <div class="kpi-lbl">Capitale</div>
      <div class="kpi-val neu" id="k-cap">--</div>
    </div>
    <div class="kpi">
      <div class="kpi-lbl">Trade M2</div>
      <div class="kpi-val neu" id="k-trades">--</div>
      <div class="kpi-sub" id="k-avg-dur">avg dur --</div>
    </div>
    <div class="kpi">
      <div class="kpi-lbl">Soglia</div>
      <div class="kpi-val neu" id="k-soglia">--</div>
      <div class="kpi-sub">base / min</div>
    </div>
    <div class="kpi">
      <div class="kpi-lbl">IA Capsule</div>
      <div class="kpi-val neu" id="k-caps">--</div>
      <div class="kpi-sub" id="k-caps-sub">L2: 0  L3: 0</div>
    </div>
    <div class="kpi">
      <div class="kpi-lbl">Phantom</div>
      <div class="kpi-val" id="k-phantom">--</div>
      <div class="kpi-sub" id="k-phantom-sub">bilancio --</div>
    </div>
    <div class="kpi">
      <div class="kpi-lbl">State</div>
      <div class="kpi-val neu" id="k-state">--</div>
      <div class="kpi-sub" id="k-streak">streak 0</div>
    </div>
  </div>

  <!-- ROW 1: M2 + ORACOLO -->
  <div class="two-col">

    <!-- M2 CAMPO GRAVITAZIONALE -->
    <div class="panel">
      <div class="panel-head blue">🎯 MOTORE 2 — CAMPO GRAVITAZIONALE
        <span id="m2-shadow-badge" style="font-size:9px; color:var(--dim)">shadow chiuso</span>
      </div>
      <div class="panel-body">
        <div class="dir-box dir-long" id="dir-box"><span id="dir-txt">⏳ ATTESA</span></div>
        <div class="stat-row">
          <div class="stat-item"><span class="stat-lbl">WR</span><span class="stat-val" id="m2-wr-detail">0%</span></div>
          <div class="stat-item"><span class="stat-lbl">PnL</span><span class="stat-val" id="m2-pnl-detail">$0</span></div>
          <div class="stat-item"><span class="stat-lbl">Trades</span><span class="stat-val" id="m2-t-detail">0</span></div>
          <div class="stat-item"><span class="stat-lbl">Loss streak</span><span class="stat-val" id="m2-streak">0</span></div>
          <div class="stat-item"><span class="stat-lbl">Cooldown</span><span class="stat-val" id="m2-cooldown">0s</span></div>
        </div>
        <div class="stat-row">
          <div class="stat-item"><span class="stat-lbl">RSI</span><span class="stat-val" id="m2-rsi">--</span></div>
          <div class="stat-item"><span class="stat-lbl">MACD hist</span><span class="stat-val" id="m2-macd">--</span></div>
          <div class="stat-item"><span class="stat-lbl">Soglia base</span><span class="stat-val" id="m2-sog-base">60</span></div>
          <div class="stat-item"><span class="stat-lbl">Drift thr</span><span class="stat-val" id="m2-drift-thr">--</span></div>
        </div>
        <div class="drift-bar-wrap"><div class="drift-bar-fill" id="drift-fill" style="width:50%;background:var(--blue)"></div></div>
        <div style="font-size:9px;color:var(--dim);margin-top:2px;text-align:center" id="drift-lbl">drift 0.000%</div>
        <div class="log-feed" id="m2-log" style="margin-top:8px">In attesa M2...</div>
      </div>
    </div>

    <!-- ORACOLO DINAMICO -->
    <div class="panel">
      <div class="panel-head purple">🔮 ORACOLO DINAMICO — Fingerprint Memory</div>
      <div class="panel-body">
        <div style="font-size:9px; color:var(--dim); margin-bottom:6px;">
          Fingerprint = (momentum × volatilità × trend × direction). WR pesato con decay 0.95.
          🟢 ≥60% vincente  🟡 45-60% neutro  🔴 &lt;45% tossico
        </div>
        <table class="oracolo-table" id="oracolo-tbl">
          <thead>
            <tr>
              <th>FINGERPRINT</th>
              <th>WR</th>
              <th>CAMPIONI</th>
              <th>PnL avg</th>
              <th>EXIT EARLY</th>
              <th>STATUS</th>
            </tr>
          </thead>
          <tbody id="oracolo-body">
            <tr><td colspan="6" style="color:var(--dim);text-align:center;padding:12px">Nessun dato ancora</td></tr>
          </tbody>
        </table>
        <div class="sep"></div>
        <div style="font-size:9px; color:var(--dim)">Divorzi permanenti: <span id="divorzi-list" style="color:var(--red)">nessuno</span></div>
        <div style="font-size:9px; color:var(--dim); margin-top:4px">Calibratore: <span id="calib-params" style="color:var(--text)">--</span></div>
      </div>
    </div>
  </div>

  <!-- ROW 2: IA CAPSULE + PHANTOM -->
  <div class="two-col">

    <!-- INTELLIGENZA AUTONOMA -->
    <div class="panel">
      <div class="panel-head orange">🧠 INTELLIGENZA AUTONOMA — Capsule Vive
        <span id="ia-gen-count" style="font-size:9px; color:var(--dim)">gen: 0 / exp: 0</span>
      </div>
      <div class="panel-body">
        <div class="stat-row" style="margin-bottom:8px">
          <div class="stat-item"><span class="stat-lbl">L2 (esperienza)</span><span class="stat-val" id="ia-l2">0</span></div>
          <div class="stat-item"><span class="stat-lbl">L3 (evento)</span><span class="stat-val" id="ia-l3">0</span></div>
          <div class="stat-item"><span class="stat-lbl">Blocchi</span><span class="stat-val" id="ia-blocks">0</span></div>
          <div class="stat-item"><span class="stat-lbl">Boost soglia</span><span class="stat-val" id="ia-boosts">0</span></div>
          <div class="stat-item"><span class="stat-lbl">Trade osservati</span><span class="stat-val" id="ia-observed">0</span></div>
        </div>
        <div id="ia-capsule-list" style="max-height:200px; overflow-y:auto;">
          <div style="color:var(--dim); font-size:10px; text-align:center; padding:20px 0">
            Nessuna capsule attiva.<br>Il sistema impara dai trade.
          </div>
        </div>
      </div>
    </div>

    <!-- PHANTOM TRACKER -->
    <div class="panel">
      <div class="panel-head yellow">👻 PHANTOM — Se avessi fatto...
        <span style="font-size:9px; color:var(--dim)">Zavorra o Protezione?</span>
      </div>
      <div class="panel-body">
        <div class="phantom-grid">
          <div class="ph-kpi">
            <div class="ph-kpi-lbl">BLOCCATI</div>
            <div class="ph-kpi-val" id="ph-tot" style="color:var(--yellow)">0</div>
          </div>
          <div class="ph-kpi">
            <div class="ph-kpi-lbl">🛡️ PROTETTI</div>
            <div class="ph-kpi-val" id="ph-prot" style="color:var(--green)">0</div>
          </div>
          <div class="ph-kpi">
            <div class="ph-kpi-lbl">⚠️ MANCATI</div>
            <div class="ph-kpi-val" id="ph-zav" style="color:var(--red)">0</div>
          </div>
          <div class="ph-kpi">
            <div class="ph-kpi-lbl">💰 SALVATI</div>
            <div class="ph-kpi-val" id="ph-saved" style="color:var(--green)">$0</div>
          </div>
          <div class="ph-kpi">
            <div class="ph-kpi-lbl">💸 PERSI</div>
            <div class="ph-kpi-val" id="ph-miss" style="color:var(--red)">$0</div>
          </div>
          <div class="ph-kpi">
            <div class="ph-kpi-lbl">⚖️ BILANCIO</div>
            <div class="ph-kpi-val" id="ph-bil">$0</div>
          </div>
        </div>
        <div class="verdict-box" id="ph-verdict">In attesa dati...</div>
        <div id="ph-levels" style="font-size:10px; max-height:80px; overflow-y:auto;"></div>
        <div class="log-feed" id="ph-log" style="max-height:80px; margin-top:6px;"></div>
      </div>
    </div>
  </div>

  <!-- AI BRIDGE — IL GENERALE -->
  <div class="panel" style="margin-bottom:10px; border-color:var(--purple); border-width:2px;">
    <div class="panel-head purple" style="font-size:11px;">🌉 IL GENERALE — AI BRIDGE
      <span id="bridge-ts" style="font-size:9px; color:var(--dim)">—</span>
    </div>
    <div class="panel-body">
      <div style="display:flex; gap:10px; align-items:center; margin-bottom:10px; flex-wrap:wrap;">
        <div id="bridge-mercato-badge" style="font-family:'Orbitron',monospace; font-size:12px; font-weight:700;
             padding:5px 14px; border-radius:2px; letter-spacing:2px; border:1px solid var(--dim); color:var(--dim)">
          — MERCATO —
        </div>
        <div id="bridge-alert-badge" style="font-size:10px; padding:3px 10px; border-radius:2px;
             border:1px solid var(--dim); color:var(--dim)">● ATTESA</div>
        <div style="font-size:9px; color:var(--dim)">ultima analisi: <span id="bridge-last-ts">—</span></div>
        <div style="margin-left:auto; font-size:9px; color:var(--dim)">
          <span id="bridge-active-dot">⚫</span> <span id="bridge-active-txt">offline</span>
          &nbsp;|&nbsp; err: <span id="bridge-errors">0</span>
        </div>
      </div>
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:10px;">
        <div style="background:var(--bg3); border-left:3px solid var(--purple); padding:8px 12px; border-radius:1px;">
          <div style="font-size:9px; color:var(--purple); margin-bottom:3px; letter-spacing:1px">ANALISI</div>
          <div id="bridge-analisi" style="font-size:11px; color:var(--text); line-height:1.5">In attesa...</div>
        </div>
        <div style="background:rgba(0,255,136,0.04); border-left:3px solid var(--green); padding:8px 12px; border-radius:1px;">
          <div style="font-size:9px; color:var(--green); margin-bottom:3px; letter-spacing:1px">🎯 PROSSIMO SETUP</div>
          <div id="bridge-prossimo" style="font-size:11px; color:var(--text); line-height:1.5">—</div>
        </div>
      </div>
      <div id="bridge-note-box" style="background:rgba(255,215,0,0.04); border-left:3px solid var(--yellow);
           padding:6px 12px; margin-bottom:8px; border-radius:1px; display:none;">
        <div style="font-size:9px; color:var(--yellow); margin-bottom:2px;">📝 NOTA PER TE</div>
        <div id="bridge-note" style="font-size:11px; color:var(--text)">—</div>
      </div>
      <div class="log-feed" id="bridge-log" style="max-height:100px; font-size:10px;">Bridge non ancora attivo...</div>
    </div>
  </div>

  <!-- GRAFICO LIVE — PREZZO + SEGNALI -->
  <div class="panel" style="margin-bottom:10px; border-color:var(--green); border-width:2px;">
    <div class="panel-head green">📈 GRAFICO LIVE — BTC/USDC
      <span id="chart-info" style="font-size:9px; color:var(--dim)">ultimi 120 tick · 30s window</span>
    </div>
    <div class="panel-body" style="padding:8px;">
      <canvas id="priceChart" style="width:100%; height:220px; display:block;"></canvas>
      <div style="display:flex; gap:16px; margin-top:6px; font-size:9px; color:var(--dim); flex-wrap:wrap;">
        <span><span style="color:var(--green)">━</span> Prezzo</span>
        <span><span style="color:var(--yellow); font-size:11px">◆</span> Segnale (score≥soglia)</span>
        <span><span style="color:var(--green); font-size:12px">▲</span> Entry LONG</span>
        <span><span style="color:var(--red); font-size:12px">▼</span> Entry SHORT</span>
        <span><span style="color:#888; font-size:11px">✕</span> Exit</span>
        <span id="chart-score-live" style="margin-left:auto; color:var(--text)"></span>
      </div>
    </div>
  </div>

  <!-- SUPERCERVELLO — DUE LINEE: MERCATO vs PREDIZIONE -->
  <div class="panel" style="margin-bottom:10px; border-color:#aa44ff; border-width:2px;">
    <div class="panel-head" style="color:#aa44ff;">🧠 SUPERCERVELLO — Mercato vs Predizione
      <span id="sc-updated" style="font-size:9px; color:var(--dim)">in attesa dati...</span>
    </div>
    <div class="panel-body" style="padding:8px;">

      <!-- Metriche -->
      <div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px;margin-bottom:8px;">
        <div style="background:rgba(100,100,100,0.08);border-radius:6px;padding:8px;text-align:center;">
          <div style="font-size:9px;color:var(--dim)">STATO SC</div>
          <div id="sc-stato" style="font-size:14px;font-weight:500;color:var(--yellow)">ATTESA</div>
        </div>
        <div style="background:rgba(100,100,100,0.08);border-radius:6px;padding:8px;text-align:center;">
          <div style="font-size:9px;color:var(--dim)">⬆ LONG</div>
          <div id="sc-carica" style="font-size:14px;font-weight:500;">0.00</div>
        </div>
        <div style="background:rgba(100,100,100,0.08);border-radius:6px;padding:8px;text-align:center;">
          <div style="font-size:9px;color:var(--dim)">⬇ SHORT</div>
          <div id="sc-carica-short" style="font-size:14px;font-weight:500;">0.00</div>
        </div>
        <div style="background:rgba(100,100,100,0.08);border-radius:6px;padding:8px;text-align:center;">
          <div style="font-size:9px;color:var(--dim)">WR REALE</div>
          <div id="sc-wr" style="font-size:14px;font-weight:500;color:var(--green)">—</div>
        </div>
        <div style="background:rgba(100,100,100,0.08);border-radius:6px;padding:8px;text-align:center;">
          <div style="font-size:9px;color:var(--dim)">P&L</div>
          <div id="sc-pnl" style="font-size:14px;font-weight:500;">$0</div>
        </div>
      </div>

      <!-- Grafico due linee — canvas puro -->
      <canvas id="scChart" style="width:100%;height:180px;display:block;"></canvas>

      <!-- Legenda -->
      <div style="display:flex;gap:12px;margin-top:6px;font-size:9px;color:var(--dim);flex-wrap:wrap;">
        <span><span style="color:#378ADD">━</span> Mercato reale</span>
        <span><span style="color:#639922">╌</span> Predizione SC</span>
        <span><span style="color:#639922;font-size:11px">▲</span> BUY</span>
        <span><span style="color:#E24B4A;font-size:11px">▼</span> SELL loss</span>
        <span><span style="color:#639922;font-size:11px">✓</span> SELL win</span>
        <span><span style="color:#EF9F27;font-size:11px">◆</span> BLOCCA</span>
      </div>

      <!-- Carica bar -->
      <div style="margin-top:8px;">
        <div style="font-size:9px;color:var(--dim);margin-bottom:2px;">Carica SC (0→1)</div>
        <canvas id="scCaricaChart" style="width:100%;height:50px;display:block;"></canvas>
      </div>

      <!-- Metriche predizione vs mercato -->
      <div style="margin-top:8px;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;">
        <div style="background:rgba(100,100,100,0.08);border-radius:6px;padding:8px;text-align:center;">
          <div style="font-size:9px;color:var(--dim)">SCOSTAMENTO</div>
          <div id="pred-scost" style="font-size:14px;font-weight:500;color:var(--yellow)">—</div>
          <div style="font-size:8px;color:var(--dim)">$ medio</div>
        </div>
        <div style="background:rgba(100,100,100,0.08);border-radius:6px;padding:8px;text-align:center;">
          <div style="font-size:9px;color:var(--dim)">CONFERMATE</div>
          <div id="pred-conf" style="font-size:14px;font-weight:500;color:var(--green)">—</div>
          <div style="font-size:8px;color:var(--dim)">su totale</div>
        </div>
        <div style="background:rgba(100,100,100,0.08);border-radius:6px;padding:8px;text-align:center;">
          <div style="font-size:9px;color:var(--dim)">SCORE PRED.</div>
          <div id="pred-score" style="font-size:14px;font-weight:500;">—</div>
          <div style="font-size:8px;color:var(--dim)">% corrette</div>
        </div>
        <div style="background:rgba(100,100,100,0.08);border-radius:6px;padding:8px;text-align:center;grid-column:span 3;">
          <div style="font-size:9px;color:var(--dim)">PRED → TRADE → PnL</div>
          <div id="pred-trade" style="font-size:14px;font-weight:500;">—</div>
          <div style="font-size:8px;color:var(--dim)">trade da predizione / PnL cumulativo</div>
        </div>
        <div style="background:rgba(100,100,100,0.08);border-radius:6px;padding:8px;text-align:center;grid-column:span 3;border:1px solid rgba(100,200,100,0.2);">
          <div style="font-size:9px;color:var(--dim)">CALIBRAZIONE MAGNITUDINE</div>
          <div id="pred-ratio" style="font-size:18px;font-weight:500;">—</div>
          <div style="font-size:8px;color:var(--dim)">100% = perfetto · &lt;100% troppo aggressiva · &gt;100% troppo conservativa</div>
        </div>
      </div>

      <!-- Narrativa oracolo interno -->
      <div style="margin-top:8px;">
        <div style="font-size:9px;color:var(--dim);margin-bottom:2px;">Narrativa Oracolo Interno</div>
        <div id="sc-narrativa" style="font-size:9px;color:var(--text);font-family:monospace;line-height:1.8;min-height:40px;">
          In attesa tick...
        </div>
      </div>

      <!-- Pesi organi -->
      <div style="margin-top:8px;">
        <div style="font-size:9px;color:var(--dim);margin-bottom:4px;">Pesi organi (adattativi)</div>
        <div id="sc-pesi" style="display:flex;gap:6px;flex-wrap:wrap;font-size:9px;"></div>
      </div>

    </div>
  </div>

  <!-- VERITAS TRACKER — CHI AVEVA RAGIONE -->
  <div class="panel" style="margin-bottom:10px; border-color:#ff8800; border-width:2px;">
    <div class="panel-head" style="color:#ff8800;">⚖️ VERITAS — Chi aveva ragione?
      <span id="vt-counts" style="font-size:9px; color:var(--dim)">in attesa segnali...</span>
    </div>
    <div class="panel-body">
      <div style="font-size:9px; color:var(--dim); margin-bottom:8px;">
        Ogni decisione SC viene verificata 60s dopo. La verità emerge dai dati reali.
      </div>

      <!-- Conflitto principale -->
      <div id="vt-conflitto" style="display:none; margin-bottom:10px; padding:8px;
           border:1px solid #ff8800; border-radius:4px; font-size:10px;">
      </div>

      <!-- Tabella risultati -->
      <table style="width:100%; border-collapse:collapse; font-size:10px;">
        <thead>
          <tr>
            <th style="color:var(--dim);padding:4px 6px;text-align:left;border-bottom:1px solid var(--border);font-size:9px;">ORACOLO</th>
            <th style="color:var(--dim);padding:4px 6px;text-align:left;border-bottom:1px solid var(--border);font-size:9px;">SC</th>
            <th style="color:var(--dim);padding:4px 6px;text-align:center;border-bottom:1px solid var(--border);font-size:9px;">N</th>
            <th style="color:var(--dim);padding:4px 6px;text-align:center;border-bottom:1px solid var(--border);font-size:9px;">HIT 60s</th>
            <th style="color:var(--dim);padding:4px 6px;text-align:center;border-bottom:1px solid var(--border);font-size:9px;">PnL avg</th>
            <th style="color:var(--dim);padding:4px 6px;text-align:center;border-bottom:1px solid var(--border);font-size:9px;">VERDETTO</th>
          </tr>
        </thead>
        <tbody id="vt-body">
          <tr><td colspan="6" style="color:var(--dim);text-align:center;padding:16px">
            In attesa... (serve score ≥ soglia con decisione SC)
          </td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- SIGNAL TRACKER — MOTORE PREVISIONALE -->
  <div class="panel" style="margin-bottom:10px; border-color:var(--blue); border-width:2px;">
    <div class="panel-head blue">🔭 MOTORE PREVISIONALE — Signal Tracker
      <span id="st-counts" style="font-size:9px; color:var(--dim)">open:0 / chiusi:0</span>
    </div>
    <div class="panel-body">
      <div style="font-size:9px; color:var(--dim); margin-bottom:8px;">
        Ogni volta che score ≥ soglia il sistema registra il segnale e misura il movimento reale
        nei successivi 30s/60s/120s. Dopo 50 segnali emerge la distribuzione previsionale.
      </div>
      <table style="width:100%; border-collapse:collapse; font-size:10px;" id="st-table">
        <thead>
          <tr>
            <th style="color:var(--dim);padding:4px 6px;text-align:left;border-bottom:1px solid var(--border);font-size:9px;letter-spacing:1px">CONTESTO</th>
            <th style="color:var(--dim);padding:4px 6px;text-align:center;border-bottom:1px solid var(--border);font-size:9px">N</th>
            <th style="color:var(--dim);padding:4px 6px;text-align:center;border-bottom:1px solid var(--border);font-size:9px">HIT 60s</th>
            <th style="color:var(--dim);padding:4px 6px;text-align:center;border-bottom:1px solid var(--border);font-size:9px">Δ avg 60s</th>
            <th style="color:var(--dim);padding:4px 6px;text-align:center;border-bottom:1px solid var(--border);font-size:9px">PnL sim</th>
          </tr>
        </thead>
        <tbody id="st-body">
          <tr><td colspan="5" style="color:var(--dim);text-align:center;padding:16px">
            In attesa segnali... (serve score ≥ soglia)
          </td></tr>
        </tbody>
      </table>
    </div>
  </div>


  <!-- ECONOMIC EDGE — QUANDO PRENDO SOLDI -->
  <div class="panel" style="margin-bottom:10px; border-color:#00ff88; border-width:2px;">
    <div class="panel-head" style="color:#00ff88;">💰 ECONOMIC EDGE — Quando prendo soldi veri?
      <span style="font-size:9px; color:var(--dim); margin-left:8px;">hit_economica = % casi che coprono le fee reali</span>
    </div>
    <div class="panel-body">
      <div style="font-size:9px; color:var(--dim); margin-bottom:8px;">
        🟢 ≥50% = prendi soldi &nbsp;|&nbsp; 🟡 30-50% = vicino &nbsp;|&nbsp; 🔴 &lt;30% = sterile
        &nbsp;|&nbsp; Fee simulata: $0.10 per trade
      </div>
      <div id="edge-body">
        <div style="color:var(--dim); text-align:center; padding:16px; font-size:10px;">
          In attesa dati Signal Tracker...
        </div>
      </div>
    </div>
  </div>

  <!-- ROW 3: LOG DECISIONI + LIVE LOG M2 -->
  <div class="two-col">
    <div class="panel">
      <div class="panel-head green">📋 DECISIONI BOT — Live Log</div>
      <div class="panel-body">
        <div style="display:flex; flex-wrap:wrap; gap:8px; font-size:9px; margin-bottom:8px; color:var(--dim)">
          <span style="color:var(--green)">🚀 ENTRY</span>
          <span style="color:var(--green)">🟢 WIN</span>
          <span style="color:var(--red)">🔴 LOSS</span>
          <span>⚡ SEED</span>
          <span style="color:#aa44ff">👻 FANTASMA</span>
          <span style="color:var(--orange)">🚫 MEM</span>
          <span style="color:var(--yellow)">💊 CAPSULE</span>
          <span style="color:var(--red)">💔 DIVORZIO</span>
          <span style="color:#aaaaff">🌙 SMORZ</span>
          <span style="color:var(--blue)">🌉 BRIDGE</span>
          <span style="color:var(--orange)">🧭 OC3</span>
          <span style="color:var(--purple)">🛑 STOP</span>
        </div>
        <div class="log-feed" id="live-log" style="max-height:280px">In attesa...</div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head blue">🎯 LOG M2 — Campo Gravitazionale</div>
      <div class="panel-body">
        <div class="log-feed" id="m2-log-full" style="max-height:330px">In attesa M2...</div>
      </div>
    </div>
  </div>

  <!-- ROW 4: TRADES TABLE -->
  <div class="panel" style="margin-bottom:10px">
    <div class="panel-head green">📊 ULTIMI TRADE</div>
    <div class="panel-body" style="overflow-x:auto">
      <table class="trade-tbl">
        <thead>
          <tr>
            <th>ORA</th><th>TIPO</th><th>DIR</th><th>PREZZO</th>
            <th>PnL $</th><th>SIZE</th><th>MOTIVO</th>
          </tr>
        </thead>
        <tbody id="trades-body">
          <tr><td colspan="7" style="color:var(--dim); text-align:center; padding:16px">Nessun trade ancora</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- CONTROLS + ALERTS -->
  <div class="controls">
    <button class="btn" onclick="sendCmd('RESUME')">▶ RESUME</button>
    <button class="btn btn-red" onclick="sendCmd('STOP')">■ STOP</button>
    <button class="btn" onclick="sendCmd('RESET_LOSSES')">↺ RESET</button>
  </div>
  <div id="suggestions-box" style="font-size:11px; color:var(--dim); padding:6px 0;"></div>

</div><!-- /wrap -->

<script>
const $ = id => document.getElementById(id);

// ============================================================
// GRAFICO LIVE — Mostra la TENSIONE, non la storia
// ============================================================

const SCPanel = (() => {
  // Buffer dati reali
  const MAX = 120;
  const prices  = [];
  const preds   = [];
  const cariche = [];
  const labels  = [];
  const buyMkrs = [];
  const sellMkrs= [];
  let scChart = null, scCarica = null;
  let wins = 0, losses = 0, pnlTot = 0;
  let lastTrades = [];

  function update(hb) {
    const price = hb.last_price || 0;
    if (!price) return;

    // Usa storia completa dal bot — non accumula tick per tick
    const carica = hb.oi_carica || 0;
    const stato  = hb.oi_stato  || 'ATTESA';

    if (hb.sc_price_history && hb.sc_price_history.length > 2) {
      const ph = hb.sc_price_history;
      const ch = hb.sc_carica_history || [];
      prices.length = 0; preds.length = 0; cariche.length = 0; labels.length = 0;
      ph.forEach((p, i) => {
        prices.push(p);
        labels.push(i);
        const c = ch[i] !== undefined ? ch[i] : carica;
        // Delta reali dal Veritas — non fattore inventato
        const deltaFuoco  = hb.pred_delta_fuoco  || 5.0;
        const deltaCarica = hb.pred_delta_carica || 2.0;
        let delta = 0;
        if (c >= 0.65)      delta = deltaFuoco;
        else if (c >= 0.40) delta = deltaCarica;
        preds.push(Math.round((p + delta) * 100) / 100);
        cariche.push(Math.round(c * 1000) / 1000);
      });
    } else {
      prices.push(price);
      labels.push(labels.length);
      if (prices.length > MAX) { prices.shift(); labels.shift(); }
      preds.push(Math.round((price + (carica - 0.5) * 150) * 100) / 100);
      cariche.push(Math.round(carica * 1000) / 1000);
      if (preds.length   > MAX) preds.shift();
      if (cariche.length > MAX) cariche.shift();
    }

    // Stato e carica
    const statoEl = document.getElementById('sc-stato');
    if (statoEl) {
      statoEl.textContent = stato;
      statoEl.style.color = stato==='FUOCO' ? '#00ff88' : stato==='CARICA' ? '#ffd700' : '#888';
    }
    const caricaEl = document.getElementById('sc-carica');
    if (caricaEl) {
      caricaEl.textContent = carica.toFixed(3);
      caricaEl.style.color = carica >= 0.65 ? '#00ff88' : carica >= 0.4 ? '#ffd700' : '#888';
    }
    const caricaShortEl = document.getElementById('sc-carica-short');
    if (caricaShortEl) {
      const cs = hb.oi_carica_short || 0;
      caricaShortEl.textContent = cs.toFixed(3);
      caricaShortEl.style.color = cs >= 0.65 ? '#ff3355' : cs >= 0.4 ? '#ff8800' : '#888';
    }

    // Trades — calcola WR e PnL dai trade reali
    const trades = hb.trades || [];
    if (trades.length !== lastTrades.length) {
      lastTrades = trades;
      wins = 0; losses = 0; pnlTot = 0;
      const exits = trades.filter(t => t.type === 'M2_EXIT');
      exits.forEach(t => {
        const p = t.pnl || 0;
        pnlTot += p;
        if (p > 0) wins++; else losses++;
        // Marker sul grafico
        const idx = Math.min(prices.length - 1, Math.max(0, prices.length - 10));
        if (p > 0) sellMkrs.push({x: labels[idx]||0, y: t.price||price});
        else       sellMkrs.push({x: labels[idx]||0, y: t.price||price, loss: true});
      });
      trades.filter(t => t.type === 'M2_ENTRY').forEach(t => {
        const idx = Math.min(prices.length - 1, Math.max(0, prices.length - 15));
        buyMkrs.push({x: labels[idx]||0, y: t.price||price});
      });
    }

    const nTrades = wins + losses;
    const wr = nTrades > 0 ? Math.round(wins/nTrades*100) : 0;
    const wrEl = document.getElementById('sc-wr');
    if (wrEl) { wrEl.textContent = nTrades > 0 ? wr + '%' : '—';
               wrEl.style.color = wr >= 60 ? '#00ff88' : wr >= 45 ? '#ffd700' : '#ff3355'; }
    const pnlEl = document.getElementById('sc-pnl');
    if (pnlEl) { pnlEl.textContent = (pnlTot>=0?'+':'') + '$' + Math.round(pnlTot);
               pnlEl.style.color = pnlTot >= 0 ? '#00ff88' : '#ff3355'; }

    // Narrativa oracolo interno
    const narr = hb.oi_narrativa || [];
    const narrEl = document.getElementById('sc-narrativa');
    if (narrEl && narr.length > 0) {
      narrEl.innerHTML = narr.slice(-5).map(n => `<div>${n}</div>`).join('');
    }

    // Pesi organi
    const pesiEl = document.getElementById('sc-pesi');
    if (pesiEl && hb.sc_pesi) {
      pesiEl.innerHTML = Object.entries(hb.sc_pesi)
        .sort((a,b) => b[1]-a[1])
        .map(([k,v]) => {
          const pct = Math.round(v*100);
          const col = pct >= 30 ? '#00ff88' : pct >= 20 ? '#ffd700' : '#888';
          return `<span style="color:${col}">${k.replace('_',' ')} ${pct}%</span>`;
        }).join(' · ');
    }

    // Updated
    const upd = document.getElementById('sc-updated');
    if (upd) upd.textContent = 'aggiornato ' + new Date().toLocaleTimeString();

    // Metriche predizione
    const scost = hb.pred_scostamento;
    const conf  = hb.pred_conferme;
    const tot   = hb.pred_totale;
    const score = hb.pred_score;
    if (scost !== undefined) {
      const scostEl = document.getElementById('pred-scost');
      if (scostEl) { scostEl.textContent = '$' + scost.toFixed(1);
        scostEl.style.color = scost < 50 ? 'var(--green)' : scost < 150 ? 'var(--yellow)' : 'var(--red)'; }
      const confEl = document.getElementById('pred-conf');
      if (confEl) confEl.textContent = (conf||0) + '/' + (tot||0);
      const scoreEl = document.getElementById('pred-score');
      if (scoreEl) { scoreEl.textContent = (score||0).toFixed(1) + '%';
        scoreEl.style.color = score >= 60 ? 'var(--green)' : score >= 50 ? 'var(--yellow)' : 'var(--red)'; }
      const tradeEl = document.getElementById('pred-trade');
      if (tradeEl) {
        const tn  = hb.pred_trade_n   || 0;
        const tpnl= hb.pred_trade_pnl || 0;
        tradeEl.textContent = tn + ' trade / ' + (tpnl >= 0 ? '+' : '') + '$' + tpnl.toFixed(2);
        tradeEl.style.color = tpnl > 0 ? 'var(--green)' : tpnl < 0 ? 'var(--red)' : 'var(--dim)';
      }
      const ratioEl = document.getElementById('pred-ratio');
      if (ratioEl && hb.pred_ratio !== undefined) {
        const r = hb.pred_ratio;
        ratioEl.textContent = r.toFixed(1) + '%';
        // Verde vicino a 100%, giallo se distante, rosso se molto distante
        const dist = Math.abs(r - 100);
        ratioEl.style.color = dist < 20 ? 'var(--green)' : dist < 50 ? 'var(--yellow)' : 'var(--red)';
      }
    }

    // Disegna grafici
    drawCharts();
  }

  function drawCharts() {
    // Canvas puro — zero dipendenze Chart.js
    const c1 = document.getElementById('scChart');
    const c2 = document.getElementById('scCaricaChart');
    if (!c1 || !c2 || prices.length < 2) return;

    const W1 = c1.offsetWidth||600, H1 = 180;
    c1.width = W1; c1.height = H1;
    const ctx1 = c1.getContext('2d');
    ctx1.clearRect(0,0,W1,H1);
    ctx1.fillStyle='#060810'; ctx1.fillRect(0,0,W1,H1);

    const PAD = {top:10,right:56,bottom:20,left:8};
    const w1 = W1-PAD.left-PAD.right;
    const h1 = H1-PAD.top-PAD.bottom;

    const allPrices = prices.concat(preds).filter(v=>v>0);
    const minP = Math.min(...allPrices)*0.9999;
    const maxP = Math.max(...allPrices)*1.0001;
    const rngP = maxP-minP||1;

    const xOf = i => PAD.left + (i/(prices.length-1||1))*w1;
    const yOf = v => PAD.top + (1-(v-minP)/rngP)*h1;

    // Griglia Y
    ctx1.strokeStyle='rgba(255,255,255,0.05)'; ctx1.lineWidth=1;
    for(let i=0;i<=4;i++){
      const y=PAD.top+i*h1/4;
      ctx1.beginPath(); ctx1.moveTo(PAD.left,y); ctx1.lineTo(PAD.left+w1,y); ctx1.stroke();
    }

    // Linea Mercato
    ctx1.beginPath(); ctx1.strokeStyle='#378ADD'; ctx1.lineWidth=1.5; ctx1.setLineDash([]);
    prices.forEach((p,i)=>i===0?ctx1.moveTo(xOf(i),yOf(p)):ctx1.lineTo(xOf(i),yOf(p)));
    ctx1.stroke();

    // Linea Predizione
    ctx1.beginPath(); ctx1.strokeStyle='#639922'; ctx1.lineWidth=1; ctx1.setLineDash([4,3]);
    preds.forEach((p,i)=>{ if(p>0) i===0?ctx1.moveTo(xOf(i),yOf(p)):ctx1.lineTo(xOf(i),yOf(p)); });
    ctx1.stroke(); ctx1.setLineDash([]);

    // BUY markers
    buyMkrs.forEach(m=>{
      const xi=Math.min(prices.length-1,Math.max(0,m.x));
      const xp=xOf(xi), yp=yOf(m.y||prices[xi]||minP);
      ctx1.fillStyle='#00ff88'; ctx1.font='12px sans-serif'; ctx1.textAlign='center';
      ctx1.fillText('▲',xp,yp+14);
    });

    // SELL markers
    sellMkrs.forEach(m=>{
      const xi=Math.min(prices.length-1,Math.max(0,m.x));
      const xp=xOf(xi), yp=yOf(m.y||prices[xi]||minP);
      ctx1.fillStyle=m.loss?'#ff3355':'#00ff88';
      ctx1.font='12px sans-serif'; ctx1.textAlign='center';
      ctx1.fillText('▼',xp,yp-4);
    });

    // Label prezzo live
    const lp=prices[prices.length-1];
    ctx1.font='bold 10px Share Tech Mono'; ctx1.textAlign='left';
    ctx1.fillStyle='#378ADD';
    ctx1.fillText('$'+Math.round(lp),PAD.left+w1+2,yOf(lp)+4);

    // ── Grafico carica ────────────────────────────────────────
    const W2=c2.offsetWidth||600, H2=50;
    c2.width=W2; c2.height=H2;
    const ctx2=c2.getContext('2d');
    ctx2.clearRect(0,0,W2,H2);
    ctx2.fillStyle='#060810'; ctx2.fillRect(0,0,W2,H2);

    const w2=W2-PAD.left-PAD.right;
    const xOf2=i=>PAD.left+(i/(cariche.length-1||1))*w2;
    const yOf2=v=>2+(1-Math.min(1,Math.max(0,v)))*(H2-4);

    // Area carica LONG
    if(cariche.length>=2){
      ctx2.beginPath();
      cariche.forEach((c,i)=>i===0?ctx2.moveTo(xOf2(i),yOf2(c)):ctx2.lineTo(xOf2(i),yOf2(c)));
      ctx2.lineTo(xOf2(cariche.length-1),H2); ctx2.lineTo(PAD.left,H2); ctx2.closePath();
      ctx2.fillStyle='rgba(239,159,39,0.15)'; ctx2.fill();
      ctx2.beginPath();
      cariche.forEach((c,i)=>i===0?ctx2.moveTo(xOf2(i),yOf2(c)):ctx2.lineTo(xOf2(i),yOf2(c)));
      ctx2.strokeStyle='#EF9F27'; ctx2.lineWidth=1.5; ctx2.stroke();
    }

    // Linea soglia 0.65
    const ySoglia=yOf2(0.65);
    ctx2.strokeStyle='rgba(255,255,255,0.2)'; ctx2.lineWidth=1; ctx2.setLineDash([2,4]);
    ctx2.beginPath(); ctx2.moveTo(PAD.left,ySoglia); ctx2.lineTo(PAD.left+w2,ySoglia); ctx2.stroke();
    ctx2.setLineDash([]);
    ctx2.font='8px Share Tech Mono'; ctx2.fillStyle='rgba(255,255,255,0.3)'; ctx2.textAlign='left';
    ctx2.fillText('0.65',PAD.left+w2+2,ySoglia+3);
  }

  return { update };
})();

const VeritatisPanel = (() => {
  function update(hb) {
    const vt = hb.veritas;
    if (!vt) return;

    // Contatori
    const cnt = document.getElementById('vt-counts');
    if (cnt) cnt.textContent = `segnali: ${vt.n_closed} chiusi / ${vt.n_open} aperti`;

    // Conflitto
    const conf = vt.conflitto || {};
    const confEl = document.getElementById('vt-conflitto');
    if (confEl && conf.chi_aveva_ragione) {
      confEl.style.display = 'block';
      const chi = conf.chi_aveva_ragione;
      const col = chi === 'ORACOLO' ? '#00ff88' : '#ff8800';
      const pnl = conf.pnl_perso_bloccando || conf.pnl_salvato_bloccando || 0;
      const msg = chi === 'ORACOLO'
        ? `🔥 ORACOLO aveva ragione — SC ha bloccato $${Math.abs(pnl).toFixed(0)} di guadagni`
        : `🛡️ SC aveva ragione — ha salvato $${Math.abs(pnl).toFixed(0)} bloccando perdite`;
      confEl.innerHTML = `<span style="color:${col};font-weight:500">${msg}</span>`;
    }

    // Tabella
    const body = document.getElementById('vt-body');
    if (!body || !vt.rows || vt.rows.length === 0) return;
    body.innerHTML = vt.rows.map(r => {
      const hitCol = r.hit_rate >= 0.6 ? 'var(--green)' : r.hit_rate >= 0.45 ? 'var(--yellow)' : 'var(--red)';
      const pnlCol = r.pnl_avg > 0 ? 'var(--green)' : 'var(--red)';
      const verdCol = r.verdetto === 'GIUSTO' ? 'var(--green)' : 'var(--red)';
      const scStyle = r.sc === 'BLOCCA' ? 'color:var(--red)' : 'color:var(--green)';
      return `<tr style="border-bottom:1px solid var(--border)">
        <td style="padding:5px 6px;color:var(--yellow)">${r.oi}</td>
        <td style="padding:5px 6px;${scStyle}">${r.sc}</td>
        <td style="padding:5px 6px;text-align:center">${r.n}</td>
        <td style="padding:5px 6px;text-align:center;color:${hitCol}">${(r.hit_rate*100).toFixed(0)}%</td>
        <td style="padding:5px 6px;text-align:center;color:${pnlCol}">${r.pnl_avg > 0 ? '+' : ''}$${r.pnl_avg.toFixed(2)}</td>
        <td style="padding:5px 6px;text-align:center;color:${verdCol};font-weight:500">${r.verdetto}</td>
      </tr>`;
    }).join('');
  }
  return { update };
})();

const LiveChart = (() => {
  const MAX_PTS = 150;
  let prices   = [];   // {ts, v}
  let events   = [];   // {ts, type, dir, score, soglia}
  let curScore  = 0;
  let curSoglia = 60;
  let shadowOpen = false;
  let shadowDir  = 'LONG';
  let entryPrice = null;

  function addPrice(price, ts) {
    prices.push({ts: ts||Date.now(), v:price});
    if (prices.length > MAX_PTS) prices.shift();
  }

  function addEvent(type, dir, score, soglia, ts) {
    events.push({ts:ts||Date.now(), type, dir, score, soglia});
    const cut = Date.now() - 300000;  // solo ultimi 5 minuti
    events = events.filter(e => e.ts > cut);
    if (events.length > 30) events = events.slice(-30);  // max 30
    if (type === 'entry') entryPrice = prices.length ? prices[prices.length-1].v : null;
    if (type === 'exit')  entryPrice = null;
  }

  function setScore(score, soglia) { curScore=score; curSoglia=soglia; }
  function setShadow(open, dir)    { shadowOpen=open; shadowDir=dir; }

  function draw() {
    const canvas = document.getElementById('priceChart');
    if (!canvas || prices.length < 2) return;
    const ctx = canvas.getContext('2d');
    const W = canvas.offsetWidth, H = canvas.offsetHeight||240;
    canvas.width=W; canvas.height=H;

    // Layout
    const TENSION_H = 28;  // altezza barra tensione in alto
    const PAD = {top: TENSION_H+12, right:64, bottom:32, left:10};
    const w = W-PAD.left-PAD.right;
    const h = H-PAD.top-PAD.bottom;

    // Pulisci canvas completamente
    ctx.clearRect(0, 0, W, H);
    // Sfondo
    ctx.fillStyle='#060810'; ctx.fillRect(0,0,W,H);

    // ── BARRA DI TENSIONE ─────────────────────────────────────
    // Mostra quanto il sistema è vicino alla soglia
    // 0% = lontano | 100% = ENTRA
    const tension = curSoglia > 0 ? Math.min(1, curScore/curSoglia) : 0;
    const tensionW = w * tension;

    // Sfondo barra
    ctx.fillStyle='#0c1020';
    ctx.fillRect(PAD.left, 6, w, TENSION_H-4);

    // Colore barra in base alla tensione
    let tCol, tGlow;
    if (tension >= 1.0)      { tCol='#00ff88'; tGlow='rgba(0,255,136,0.4)'; }
    else if (tension >= 0.85) { tCol='#ffd700'; tGlow='rgba(255,215,0,0.3)'; }
    else if (tension >= 0.65) { tCol='#ff8800'; tGlow='rgba(255,136,0,0.2)'; }
    else                      { tCol='#334455'; tGlow='transparent'; }

    // Gradiente barra
    const tGrad = ctx.createLinearGradient(PAD.left, 0, PAD.left+tensionW, 0);
    tGrad.addColorStop(0, tCol+'44');
    tGrad.addColorStop(1, tCol);
    ctx.fillStyle = tGrad;
    ctx.fillRect(PAD.left, 6, tensionW, TENSION_H-4);

    // Bordo barra
    ctx.strokeStyle = '#1a2535';
    ctx.lineWidth = 1;
    ctx.strokeRect(PAD.left, 6, w, TENSION_H-4);

    // Linea soglia (marker verticale sulla barra)
    ctx.strokeStyle = '#ffffff33';
    ctx.lineWidth = 1;
    ctx.setLineDash([2,2]);
    ctx.beginPath();
    ctx.moveTo(PAD.left+w, 6); ctx.lineTo(PAD.left+w, TENSION_H+2);
    ctx.stroke(); ctx.setLineDash([]);

    // Testo tensione
    ctx.font = 'bold 10px Share Tech Mono';
    ctx.textAlign = 'left';
    if (tension >= 1.0) {
      ctx.fillStyle = '#00ff88';
      ctx.fillText('⚡ PRONTO — score ' + curScore.toFixed(0) + ' / ' + curSoglia, PAD.left+4, 20);
    } else if (tension >= 0.85) {
      ctx.fillStyle = '#ffd700';
      ctx.fillText('⚠ IN AVVICINAMENTO — ' + (tension*100).toFixed(0) + '% soglia', PAD.left+4, 20);
    } else {
      ctx.fillStyle = '#334455';
      ctx.fillText('· in attesa  score ' + curScore.toFixed(0) + ' / soglia ' + curSoglia, PAD.left+4, 20);
    }
    // Direzione attesa
    ctx.textAlign = 'right';
    ctx.fillStyle = shadowDir==='LONG' ? '#00ff88' : '#ff3355';
    ctx.fillText(shadowDir==='LONG' ? '↑ LONG' : '↓ SHORT', PAD.left+w-2, 20);

    // ── GRAFICO PREZZO ────────────────────────────────────────
    const vals = prices.map(p=>p.v);
    let mn=Math.min(...vals), mx=Math.max(...vals);
    const sp=mx-mn;
    if(sp<15){mn-=8;mx+=8;}else{mn-=sp*.06;mx+=sp*.06;}

    const xOf = i => PAD.left + (i/(prices.length-1))*w;
    const yOf = v => PAD.top  + h - ((v-mn)/(mx-mn))*h;

    // Griglia
    ctx.strokeStyle='rgba(255,255,255,0.03)'; ctx.lineWidth=1;
    for(let i=0;i<=3;i++){
      const y=PAD.top+(h/3)*i;
      ctx.beginPath(); ctx.moveTo(PAD.left,y); ctx.lineTo(PAD.left+w,y); ctx.stroke();
      const val=mx-((mx-mn)/3)*i;
      ctx.fillStyle='#2a3a4a'; ctx.font='9px Share Tech Mono';
      ctx.textAlign='left';
      ctx.fillText('$'+val.toFixed(0), PAD.left+w+4, y+3);
    }

    // Livello entry se posizione aperta
    if(shadowOpen && entryPrice){
      const ey=yOf(entryPrice);
      ctx.setLineDash([5,3]);
      ctx.strokeStyle=shadowDir==='LONG'?'rgba(0,255,136,0.5)':'rgba(255,51,85,0.5)';
      ctx.lineWidth=1.5;
      ctx.beginPath(); ctx.moveTo(PAD.left,ey); ctx.lineTo(PAD.left+w,ey); ctx.stroke();
      ctx.setLineDash([]);
      ctx.font='9px Share Tech Mono'; ctx.textAlign='left';
      ctx.fillStyle=shadowDir==='LONG'?'#00ff88':'#ff3355';
      ctx.fillText('ENTRY '+shadowDir+' $'+entryPrice.toFixed(0), PAD.left+4, ey-3);

      // PnL corrente
      if(prices.length>0){
        const cur=prices[prices.length-1].v;
        const pnlDelta=shadowDir==='LONG'?cur-entryPrice:entryPrice-cur;
        const pnlUSD=(pnlDelta/entryPrice)*5000;
        ctx.textAlign='right';
        ctx.fillStyle=pnlDelta>=0?'#00ff88':'#ff3355';
        ctx.font='bold 10px Share Tech Mono';
        ctx.fillText((pnlDelta>=0?'+':'')+pnlUSD.toFixed(2)+'$', PAD.left+w-4, ey-3);
      }
    }

    // Colore linea prezzo
    const first=prices[0].v, last=prices[prices.length-1].v;
    let lCol='#00ff88';
    if(tension>=0.85) lCol='#ffd700';
    if(shadowOpen) lCol=shadowDir==='LONG'?'#00ff88':'#ff3355';
    if(last<first && !shadowOpen) lCol='#ff3355';

    // Area sotto
    ctx.beginPath();
    prices.forEach((p,i)=>i===0?ctx.moveTo(xOf(i),yOf(p.v)):ctx.lineTo(xOf(i),yOf(p.v)));
    ctx.lineTo(xOf(prices.length-1),PAD.top+h);
    ctx.lineTo(PAD.left,PAD.top+h); ctx.closePath();
    const ag=ctx.createLinearGradient(0,PAD.top,0,PAD.top+h);
    ag.addColorStop(0,lCol+'22'); ag.addColorStop(1,'rgba(0,0,0,0)');
    ctx.fillStyle=ag; ctx.fill();

    // Linea prezzo
    ctx.beginPath();
    ctx.strokeStyle=lCol; ctx.lineWidth=2;
    ctx.shadowColor=lCol; ctx.shadowBlur=tension>=0.85?8:4;
    prices.forEach((p,i)=>i===0?ctx.moveTo(xOf(i),yOf(p.v)):ctx.lineTo(xOf(i),yOf(p.v)));
    ctx.stroke(); ctx.shadowBlur=0;

    // Punto live pulsante
    const lp=prices[prices.length-1];
    const lx=xOf(prices.length-1), ly=yOf(lp.v);
    const pulse=tension>=0.85?6:4;
    ctx.beginPath(); ctx.arc(lx,ly,pulse,0,Math.PI*2);
    ctx.fillStyle=lCol;
    ctx.shadowColor=lCol; ctx.shadowBlur=12; ctx.fill(); ctx.shadowBlur=0;

    // ── MARKER EVENTI ─────────────────────────────────────────
    const tMin=prices[0].ts, tMax=prices[prices.length-1].ts, tRng=tMax-tMin||1;
    events.forEach(ev=>{
      if(ev.ts<tMin||ev.ts>tMax) return;
      const xp=PAD.left+((ev.ts-tMin)/tRng)*w;
      let closest=prices[0];
      prices.forEach(p=>{ if(Math.abs(p.ts-ev.ts)<Math.abs(closest.ts-ev.ts)) closest=p; });
      const yp=yOf(closest.v);

      if(ev.type==='entry'){
        const col=ev.dir==='LONG'?'#00ff88':'#ff3355';
        // Linea verticale evento
        ctx.strokeStyle=col+'66'; ctx.lineWidth=1; ctx.setLineDash([3,3]);
        ctx.beginPath(); ctx.moveTo(xp,PAD.top); ctx.lineTo(xp,PAD.top+h); ctx.stroke();
        ctx.setLineDash([]);
        // Freccia
        ctx.font='bold 16px Share Tech Mono'; ctx.textAlign='center';
        ctx.fillStyle=col;
        ctx.shadowColor=col; ctx.shadowBlur=10;
        ctx.fillText(ev.dir==='LONG'?'▲':'▼', xp, ev.dir==='LONG'?yp+18:yp-8);
        ctx.shadowBlur=0;
        // Score badge — solo se non sovrapposto
        const others = events.filter(e => e !== ev && Math.abs((e.ts-tMin)/(tRng||1)*w - (xp-PAD.left)) < 20);
        if (others.length === 0) {
          ctx.font='bold 9px Share Tech Mono';
          ctx.fillStyle='#000';
          ctx.fillRect(xp-14, ev.dir==='LONG'?yp+20:yp-28, 28, 12);
          ctx.fillStyle=col;
          ctx.fillText(ev.score.toFixed(0)+'/'+ev.soglia.toFixed(0), xp, ev.dir==='LONG'?yp+30:yp-18);
        }
      } else if(ev.type==='exit'){
        ctx.strokeStyle='#55667788'; ctx.lineWidth=1; ctx.setLineDash([2,4]);
        ctx.beginPath(); ctx.moveTo(xp,PAD.top); ctx.lineTo(xp,PAD.top+h); ctx.stroke();
        ctx.setLineDash([]);
        ctx.font='11px Share Tech Mono'; ctx.textAlign='center';
        ctx.fillStyle='#667788'; ctx.fillText('✕', xp, yp-6);
      }
    });

    // Prezzo corrente label
    ctx.font='bold 11px Share Tech Mono'; ctx.textAlign='left';
    ctx.fillStyle=lCol;
    ctx.fillText('$'+lp.v.toLocaleString('en-US',{minimumFractionDigits:2}), PAD.left+w+4, yOf(lp.v)+4);

    // Timestamp
    ctx.fillStyle='#2a3a4a'; ctx.font='8px Share Tech Mono';
    ctx.textAlign='left';
    ctx.fillText(new Date(prices[0].ts).toLocaleTimeString(), PAD.left, H-6);
    ctx.textAlign='right';
    ctx.fillText(new Date(tMax).toLocaleTimeString(), PAD.left+w, H-6);
  }

  return { addPrice, addEvent, setScore, setShadow, draw };
})();
const fmt = (n,d=2) => (n>=0?'+':'')+n.toFixed(d);
const fmtUSD = n => (n>=0?'+$':'-$')+Math.abs(n).toFixed(2);

function colorWR(wr) {
  if(wr>=60) return 'var(--green)';
  if(wr>=45) return 'var(--yellow)';
  return 'var(--red)';
}
function colorPnL(p) { return p>=0?'var(--green)':'var(--red)'; }

function renderLog(lines, elId, maxH) {
  const el = $(elId);
  if(!lines||lines.length===0) return;
  const LOG_COLORS = {
    '🚀':'var(--green)','🟢':'var(--green)','🔴':'var(--red)','💔':'var(--red)',
    '🛑':'var(--red)','⚡':'var(--dim)','👻':'#aa44ff','🚫':'var(--orange)',
    '💊':'var(--yellow)','🌙':'#aaaaff','🌉':'var(--blue)','🎯':'var(--blue)',
    '🧭':'var(--orange)','🌍':'var(--purple)','💓':'var(--dim)','🔄':'var(--blue)',
    '🧠':'var(--orange)','🗑️':'var(--dim)','⚡':'var(--yellow)',
  };
  el.innerHTML = [...lines].reverse().map(line => {
    let col = 'var(--text)';
    for(const [emoji,c] of Object.entries(LOG_COLORS)) {
      if(line.includes(emoji)){ col=c; break; }
    }
    return `<div class="log-line" style="color:${col}">${line}</div>`;
  }).join('');
}

function regimeClass(r) {
  const m = {'TRENDING_BULL':'regime-trending-bull','TRENDING_BEAR':'regime-trending-bear',
             'EXPLOSIVE':'regime-explosive','RANGING':'regime-ranging'};
  return m[r]||'regime-ranging';
}

let pnlHistory = [];

function update() {
  fetch('/trading/status').then(r=>r.json()).then(d => {
    const m=d.metrics, hb=d.heartbeat;

    // STATUS
    const running = hb.status==='RUNNING';
    $('status-dot').className = 'status-dot '+(running?'dot-run':'dot-off');
    $('status-txt').textContent = hb.status||'OFFLINE';
    $('status-txt').style.color = running?'var(--green)':'var(--red)';
    const mode = hb.mode||'PAPER';
    $('mode-badge').textContent = mode==='LIVE'?'🔴 LIVE':'📄 PAPER';
    $('mode-badge').className = 'badge '+(mode==='LIVE'?'badge-live':'badge-paper');
    $('last-seen').textContent = hb.last_seen ? new Date(hb.last_seen).toLocaleTimeString() : '--';

    // TICKER
    if(hb.last_price) $('btc-price').textContent = '$'+hb.last_price.toLocaleString('en-US',{minimumFractionDigits:2});
    $('tick-n').textContent = (hb.tick_count||0).toLocaleString();
    $('last-tick').textContent = hb.last_tick ? new Date(hb.last_tick).toLocaleTimeString() : '--';

    // Regime badge ticker
    const reg = hb.regime||'RANGING';
    const regConf = ((hb.regime_conf||0)*100).toFixed(0);
    $('regime-badge-ticker').innerHTML = `<span class="regime-badge ${regimeClass(reg)}">${reg} ${regConf}%</span>`;

    // Trade status
    const ts = $('trade-status-txt');
    if(hb.posizione_aperta){ts.textContent='🟢 M1 APERTO';ts.style.color='var(--green)';}
    else if(hb.m2_shadow_open){ts.textContent='🎯 M2 SHADOW APERTO';ts.style.color='var(--blue)';}
    else if((hb.tick_count||0)<200){ts.textContent='⏳ Warmup';ts.style.color='var(--yellow)';}
    else{ts.textContent='🔍 In attesa setup';ts.style.color='var(--dim)';}

    // KPI
    const m2pnl = hb.m2_pnl||0;
    const m2wr  = ((hb.m2_wr||0)*100);
    const m2t   = hb.m2_trades||0;
    const m2w   = hb.m2_wins||0;
    const m2l   = hb.m2_losses||0;
    pnlHistory.push(m2pnl); if(pnlHistory.length>60) pnlHistory.shift();

    const pnlEl = $('k-pnl');
    pnlEl.textContent = fmtUSD(m2pnl);
    pnlEl.className = 'kpi-val '+(m2pnl>=0?'pos':'neg');
    $('k-roi').textContent = 'ROI '+(m2pnl/10000*100).toFixed(3)+'%';

    const wrEl = $('k-wr');
    wrEl.textContent = m2wr.toFixed(1)+'%';
    wrEl.style.color = colorWR(m2wr);
    $('k-wl').textContent = m2w+'W / '+m2l+'L';

    $('k-cap').textContent = '$'+(hb.capital||10000).toFixed(0);
    $('k-trades').textContent = m2t;
    const cs = hb.m2_campo_stats||{};
    const avgDur = cs.avg_duration || (hb.telemetry?.D_performance?.total?.avg_duration)||0;
    $('k-avg-dur').textContent = 'avg '+avgDur.toFixed(0)+'s';

    const sogMin = hb.m2_soglia_min||58, sogBase = hb.m2_soglia_base||60;
    $('k-soglia').textContent = sogBase+'/'+sogMin;

    const ia = hb.ia_stats||{};
    $('k-caps').textContent = ia.attive||0;
    $('k-caps-sub').textContent = 'L2:'+(ia.l2||0)+'  L3:'+(ia.l3||0);

    const ph = hb.phantom||{};
    const bilancio = ph.bilancio||0;
    $('k-phantom').textContent = (bilancio>=0?'+':'')+bilancio.toFixed(1);
    $('k-phantom').style.color = bilancio>=0?'var(--green)':'var(--red)';
    $('k-phantom-sub').textContent = 'bloccati '+(ph.total||0);

    const state = hb.m2_state||'NEUTRO';
    $('k-state').textContent = state;
    $('k-state').style.color = state==='AGGRESSIVO'?'var(--green)':state==='DIFENSIVO'?'var(--red)':'var(--text)';
    $('k-streak').textContent = 'streak '+(hb.m2_loss_streak||0);

    // ALERT BAR
    const alerts=[];
    if((hb.m2_loss_streak||0)>=3) alerts.push('⚠️ LOSS STREAK '+hb.m2_loss_streak);
    if(m2wr<40 && m2t>5) alerts.push('⚠️ WR BASSO '+m2wr.toFixed(0)+'%');
    if(m2pnl<-50) alerts.push('🔴 DRAWDOWN '+fmtUSD(m2pnl));
    if((hb.m2_cooldown||0)>0) alerts.push('⏳ COOLDOWN '+(hb.m2_cooldown||0).toFixed(0)+'s');
    const ab = $('alert-bar');
    if(alerts.length>0){ ab.style.display='block'; ab.innerHTML=alerts.join('  &nbsp;|&nbsp;  '); }
    else { ab.style.display='none'; }

    // M2 DIRECTION
    const dir = hb.m2_direction||'LONG';
    const db = $('dir-box'), dt = $('dir-txt');
    dt.textContent = dir==='SHORT'?'🔴 SHORT ↓':'🟢 LONG ↑';
    db.className = 'dir-box '+(dir==='SHORT'?'dir-short':'dir-long');
    $('m2-shadow-badge').textContent = hb.m2_shadow_open?'🟢 SHADOW APERTO':'⚪ shadow chiuso';
    $('m2-shadow-badge').style.color = hb.m2_shadow_open?'var(--green)':'var(--dim)';

    $('m2-wr-detail').textContent = m2wr.toFixed(1)+'%';
    $('m2-wr-detail').style.color = colorWR(m2wr);
    $('m2-pnl-detail').textContent = fmtUSD(m2pnl);
    $('m2-pnl-detail').style.color = colorPnL(m2pnl);
    $('m2-t-detail').textContent = m2t;
    $('m2-streak').textContent = hb.m2_loss_streak||0;
    $('m2-streak').style.color = (hb.m2_loss_streak||0)>=2?'var(--red)':'var(--text)';
    const cd = hb.m2_cooldown||0;
    $('m2-cooldown').textContent = cd>0?cd.toFixed(0)+'s':'—';
    $('m2-cooldown').style.color = cd>0?'var(--yellow)':'var(--dim)';

    const rsi = cs.rsi||50, macd_h = cs.macd_hist||0;
    $('m2-rsi').textContent = rsi.toFixed(1);
    $('m2-rsi').style.color = rsi>70?'var(--red)':rsi<30?'var(--green)':'var(--text)';
    $('m2-macd').textContent = (macd_h>=0?'+':'')+macd_h.toFixed(2);
    $('m2-macd').style.color = macd_h>=0?'var(--green)':'var(--red)';
    $('m2-sog-base').textContent = sogBase+' / '+sogMin;

    // Drift bar
    const driftVeto = cs.drift_veto_threshold||-0.20;
    $('m2-drift-thr').textContent = (driftVeto*100).toFixed(0)+'%';
    const telRaw = (hb.telemetry?.raw_events_last_50||[]);
    const lastDrift = telRaw.length>0 ? (telRaw[telRaw.length-1].drift||0) : 0;
    const driftPct = Math.max(-0.4,Math.min(0.4,lastDrift));
    const fillW = ((driftPct+0.4)/0.8)*100;
    const driftFill = $('drift-fill');
    driftFill.style.width = fillW+'%';
    driftFill.style.background = driftPct>0.05?'var(--green)':driftPct<-0.05?'var(--red)':'var(--blue)';
    $('drift-lbl').textContent = 'drift '+(driftPct>=0?'+':'')+driftPct.toFixed(3)+'%';

    renderLog(hb.m2_log, 'm2-log');
    renderLog(hb.m2_log, 'm2-log-full');

    // ORACOLO TABLE
    const orac = hb.oracolo_snapshot||{};
    const fps = Object.entries(orac).filter(([k])=>!k.startsWith('_'));
    if(fps.length>0) {
      const rows = fps
        .filter(([k,v]) => v.samples > 0.5)
        .sort((a,b)=>(b[1].samples||0)-(a[1].samples||0))
        .map(([fp,v]) => {
          const wr100 = (v.wr*100);
          const wrCol = colorWR(wr100);
          const pnlA = v.pnl_avg||0;
          const earlyPct = v.exit_too_early ? (v.exit_too_early*100).toFixed(0)+'%' : '—';
          const barW = Math.round(wr100)+'px';
          const realTag = v.real>0?`<span style="color:var(--green);font-size:9px"> ★${v.real}</span>`:'';
          const status = wr100>=60?'<span style="color:var(--green)">●</span>':
                         wr100>=45?'<span style="color:var(--yellow)">◐</span>':
                         '<span style="color:var(--red)">○</span>';
          return `<tr>
            <td style="font-size:9px;color:var(--dim)">${fp.replace('LONG|','').replace('SHORT|','<span style="color:var(--red)">S</span> ')}${realTag}</td>
            <td><span style="color:${wrCol};font-weight:700">${wr100.toFixed(0)}%</span>
                <div class="wr-bar" style="width:${Math.round(wr100/2)}px;background:${wrCol}"></div></td>
            <td style="color:var(--dim)">${v.samples?.toFixed(1)||'0'}</td>
            <td style="color:${colorPnL(pnlA)}">${pnlA>=0?'+':''}$${Math.abs(pnlA).toFixed(2)}</td>
            <td style="color:var(--dim)">${earlyPct}</td>
            <td>${status}</td>
          </tr>`;
        }).join('');
      $('oracolo-body').innerHTML = rows||'<tr><td colspan="6" style="color:var(--dim);text-align:center;padding:8px">Nessun dato</td></tr>';
    }
    const divl = hb.matrimoni_divorzio||[];
    $('divorzi-list').textContent = divl.length>0?divl.join(', '):'nessuno';

    const cp = hb.calibra_params||{};
    $('calib-params').textContent = cp.seed_threshold?
      `seed≥${cp.seed_threshold} cap1≥${cp.cap1_soglia_buona} cap3≥${cp.cap3_fp_minimo}`:'--';

    // IA CAPSULE LIST
    $('ia-l2').textContent = ia.l2||0;
    $('ia-l3').textContent = ia.l3||0;
    $('ia-blocks').textContent = ia.blocchi||0;
    $('ia-boosts').textContent = ia.boost_soglia_usati||0;
    $('ia-observed').textContent = ia.trade_osservati||0;
    $('ia-gen-count').textContent = 'gen:'+(ia.generate_totali||0)+' / exp:'+(ia.scadute||0);

    // Carica capsule da API capsule o da ia_stats
    const capsule = hb.ia_capsule_attive||[];
    if(capsule.length>0) {
      $('ia-capsule-list').innerHTML = capsule.map(c=>{
        const ttl = c.ttl_seconds||0;
        const ttlStr = ttl>3600?(ttl/3600).toFixed(1)+'h':ttl>60?(ttl/60).toFixed(0)+'m':ttl+'s';
        const typeClass = {
          'L2_BLK':'cap-l2-blk','L2_BST':'cap-l2-bst',
          'L3_STK':'cap-l3-stk','L3_RBLO':'cap-l3-reg','L3_OPP':'cap-l3-opp'
        }[c.tipo]||'cap-l3-stk';
        const icon = c.tipo?.includes('BLK')||c.tipo?.includes('RBLO')?'🚫':
                     c.tipo?.includes('BST')||c.tipo?.includes('OPP')?'🚀':
                     c.tipo?.includes('STK')?'⚡':'💊';
        return `<div class="capsule-item ${typeClass}">
          <span>${icon} ${c.id||c.capsule_id||'?'}</span>
          <span class="ttl-bar">TTL ${ttlStr} | ${c.tipo||'?'}</span>
        </div>`;
      }).join('');
    } else {
      $('ia-capsule-list').innerHTML = '<div style="color:var(--dim);font-size:10px;text-align:center;padding:16px 0">Nessuna capsule attiva. Il sistema impara dai trade.</div>';
    }

    // PHANTOM
    $('ph-tot').textContent = ph.total||0;
    $('ph-prot').textContent = ph.protezione||0;
    $('ph-zav').textContent = ph.zavorra||0;
    $('ph-saved').textContent = '$'+(ph.pnl_saved||0).toFixed(1);
    $('ph-miss').textContent = '$'+(ph.pnl_missed||0).toFixed(1);
    const bil = ph.bilancio||0;
    $('ph-bil').textContent = (bil>=0?'+':'')+bil.toFixed(1);
    $('ph-bil').style.color = bil>=0?'var(--green)':'var(--red)';
    const verd = ph.verdetto||'';
    const vEl = $('ph-verdict');
    vEl.textContent = verd||'In attesa dati...';
    vEl.className = 'verdict-box '+(verd.includes('PROTEZIONE')?'verdict-green':
                                    verd.includes('ZAVORRA')?'verdict-red':'verdict-yellow');
    const perLiv = ph.per_livello||{};
    $('ph-levels').innerHTML = Object.entries(perLiv).map(([k,s])=>{
      const net = (s.pnl_saved||0)-(s.pnl_missed||0);
      return `<div style="font-size:9px;padding:2px 0;border-bottom:1px solid var(--border)">
        <b style="color:var(--yellow)">${k}</b>:
        blk=${s.blocked||0}
        <span style="color:var(--green)">+$${(s.pnl_saved||0).toFixed(1)}</span>
        <span style="color:var(--red)">-$${(s.pnl_missed||0).toFixed(1)}</span>
        <span style="color:${net>=0?'var(--green)':'var(--red)'}"> net=${net>=0?'+':''}$${net.toFixed(1)}</span>
      </div>`;
    }).join('');
    renderLog(ph.log,'ph-log');

    // LIVE LOG
    renderLog(hb.live_log,'live-log');

    // TRADES
    const trades = d.trades||[];
    if(trades.length>0){
      $('trades-body').innerHTML = trades.map(t=>{
        const pnlCls = t.pnl>0?'pnl-pos':'pnl-neg';
        const pnlTxt = (t.pnl>=0?'+':'')+t.pnl.toFixed(2);
        const ts2 = new Date(t.timestamp).toLocaleTimeString();
        const dirTxt = (t.direction||'').includes('SHORT')?
          '<span style="color:var(--red)">SHORT</span>':
          '<span style="color:var(--green)">LONG</span>';
        const typeTxt = t.type==='M2_ENTRY'?'<span style="color:var(--blue)">ENTRY</span>':
                        t.type==='M2_EXIT'?'<span style="color:var(--text)">EXIT</span>':t.type;
        return `<tr>
          <td style="color:var(--dim)">${ts2}</td>
          <td>${typeTxt}</td>
          <td>${dirTxt}</td>
          <td style="color:var(--text)">$${t.price.toFixed(1)}</td>
          <td class="${pnlCls}">${pnlTxt}</td>
          <td style="color:var(--dim)">${t.size.toFixed(2)}x</td>
          <td style="color:var(--dim);font-size:9px">${(t.reason||'').substring(0,22)}</td>
        </tr>`;
      }).join('');
    }

    // GRAFICO LIVE — feed dati e disegno
    const nowTs = Date.now();
    if (hb.last_price) {
      LiveChart.addPrice(hb.last_price, nowTs);
    }
    // Shadow aperto/chiuso + direzione
    LiveChart.setShadow(hb.m2_shadow_open || false, hb.m2_direction || 'LONG');

    // Score corrente per la barra tensione
    const lastScore  = hb.m2_last_score  || 0;
    const lastSoglia = hb.m2_last_soglia || hb.m2_soglia_base || 60;
    LiveChart.setScore(lastScore, lastSoglia);

    // Rileva nuovi eventi dal log M2
    const m2log = hb.m2_log || [];
    m2log.forEach(line => {
      const tsMatch = line.match(/^(\\d{2}:\\d{2}:\\d{2})/);
      if (!tsMatch) return;
      const lineTs = new Date().toDateString() + ' ' + tsMatch[1];
      const ts = new Date(lineTs).getTime();
      if (line.includes('ENTRY')) {
        const dir = line.includes('SHORT') ? 'SHORT' : 'LONG';
        const scoreM = line.match(/score=([\\d.]+)/);
        const score = scoreM ? parseFloat(scoreM[1]) : 0;
        LiveChart.addEvent('entry', dir, score, ts);
      } else if (line.includes('EXIT') && (line.includes('WIN') || line.includes('LOSS'))) {
        const dir = line.includes('SHORT') ? 'SHORT' : 'LONG';
        LiveChart.addEvent('exit', dir, 0, ts);
      }
    });

    // Aggiorna info chart
    $('chart-info').textContent = `ultimi 180 tick · aggiornato ${new Date().toLocaleTimeString()}`;
    const m2cs = hb.m2_campo_stats || {};
    const scoreNow = m2cs.last_score || 0;
    const soglNow  = hb.m2_soglia_base || 60;
    $('chart-score-live').textContent = scoreNow > 0 ?
      `score: ${scoreNow.toFixed(1)} / soglia: ${soglNow}` : '';

    // Disegna
    LiveChart.draw();

    // SUPERCERVELLO PANEL
    SCPanel.update(hb);
    VeritatisPanel.update(hb);

    // AI BRIDGE PANEL
    const ba = hb.bridge_active;
    $('bridge-active-dot').textContent = ba ? '🟢' : '⚫';
    $('bridge-active-txt').textContent = ba ? 'attivo' : 'offline';
    $('bridge-active-txt').style.color = ba ? 'var(--green)' : 'var(--dim)';
    $('bridge-errors').textContent = hb.bridge_errors || 0;
    $('bridge-errors').style.color = (hb.bridge_errors||0) > 0 ? 'var(--red)' : 'var(--dim)';

    const bts = hb.bridge_last_ts || hb.bridge_last_call;
    $('bridge-last-ts').textContent = bts ? (bts.length > 8 ? new Date(bts).toLocaleTimeString() : bts) : '—';

    // Analisi testuale
    const analisi = hb.bridge_analisi || '';
    $('bridge-analisi').textContent = analisi || 'In attesa prima analisi...';
    $('bridge-analisi').style.color = analisi ? 'var(--text)' : 'var(--dim)';

    // Prossimo setup
    const setup = hb.bridge_prossimo || '';
    $('bridge-prossimo').textContent = setup || '—';
    $('bridge-prossimo').style.color = setup ? 'var(--green)' : 'var(--dim)';

    // Nota per Roberto
    const nota = hb.bridge_note || '';
    if (nota) {
      $('bridge-note').textContent = nota;
      $('bridge-note-box').style.display = 'block';
    } else {
      $('bridge-note-box').style.display = 'none';
    }

    // Mercato ora badge
    const mercato = hb.bridge_mercato_ora || '';
    const mbadge = $('bridge-mercato-badge');
    const mercatoStyles = {
      'FAVOREVOLE':  {bg:'rgba(0,255,136,0.12)', border:'var(--green)',  color:'var(--green)'},
      'PERICOLOSO':  {bg:'rgba(255,51,85,0.12)',  border:'var(--red)',    color:'var(--red)'},
      'IN_ATTESA':   {bg:'rgba(255,215,0,0.08)',  border:'var(--yellow)', color:'var(--yellow)'},
      'NEUTRO':      {bg:'rgba(0,170,255,0.08)',  border:'var(--blue)',   color:'var(--blue)'},
    };
    const ms = mercatoStyles[mercato] || {bg:'transparent',border:'var(--dim)',color:'var(--dim)'};
    mbadge.textContent = mercato || '— MERCATO —';
    mbadge.style.background = ms.bg;
    mbadge.style.borderColor = ms.border;
    mbadge.style.color = ms.color;

    // Alert badge
    const alert = hb.bridge_alert || '';
    const abadge = $('bridge-alert-badge');
    const alertStyles = {
      'green':  {border:'var(--green)',  color:'var(--green)',  txt:'● OK'},
      'yellow': {border:'var(--yellow)', color:'var(--yellow)', txt:'⚠ ATTENZIONE'},
      'red':    {border:'var(--red)',    color:'var(--red)',    txt:'🔴 ALERT'},
    };
    const as = alertStyles[alert] || {border:'var(--dim)', color:'var(--dim)', txt:'● —'};
    abadge.textContent = as.txt;
    abadge.style.borderColor = as.border;
    abadge.style.color = as.color;

    // Bridge log
    renderLog(hb.bridge_log || [], 'bridge-log');

    // SIGNAL TRACKER
    const st = hb.signal_tracker || {};

      // ── ECONOMIC EDGE ──────────────────────────────────────────────
      // Calcola hit_economica per ogni contesto dal Signal Tracker
      // hit_economica = % segnali chiusi con pnl_sim > FEE_SIM ($0.10)
      const FEE_SIM = 0.10;
      const stStats = st.stats_n || {};
      const stTop   = st.top || [];
      let edgeRows = '';

      // Ordina per hit_economica
      const edgeData = stTop.map(row => {
        const ctx    = row.context || '';
        const n      = row.n || 0;
        const hit60  = row.hit_60s || 0;
        const pnlAvg = row.pnl_sim_avg || 0;
        const delta  = row.avg_delta_60s || 0;

        // Stima hit_economica dal pnl_sim_avg
        // Se pnl_sim_avg > FEE_SIM → contesto mediamente profittevole
        // hit_economica stimata: proporzionale al rapporto pnl/fee
        // (approssimazione finché non abbiamo distribuzione completa)
        let hitEcon;
        if (pnlAvg > FEE_SIM * 3)       hitEcon = 0.75;   // chiaramente profittevole
        else if (pnlAvg > FEE_SIM)       hitEcon = 0.55;   // sopra fee
        else if (pnlAvg > 0)             hitEcon = 0.35;   // positivo ma marginale
        else if (pnlAvg > -FEE_SIM)     hitEcon = 0.25;   // quasi zero
        else                              hitEcon = 0.10;   // negativo

        return { ctx, n, hit60, pnlAvg, delta, hitEcon };
      }).sort((a, b) => b.hitEcon - a.hitEcon);

      edgeData.forEach(d => {
        if (d.n < 5) return;
        const pct  = Math.round(d.hitEcon * 100);
        const color = d.hitEcon >= 0.50 ? '#00ff88' :
                      d.hitEcon >= 0.30 ? '#ffaa00' : '#ff4444';
        const emoji = d.hitEcon >= 0.50 ? '🟢' :
                      d.hitEcon >= 0.30 ? '🟡' : '🔴';
        const barW = Math.round(d.hitEcon * 100);
        const [dir, reg, band] = d.ctx.split('|');
        const dirColor = dir === 'LONG' ? 'var(--green)' : 'var(--red)';

        edgeRows += `
          <div style="margin-bottom:8px; padding:6px 8px; background:rgba(255,255,255,0.03); border-radius:4px; border-left:3px solid ${color}">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
              <span style="font-size:10px;">
                ${emoji} <span style="color:${dirColor};font-weight:bold">${dir}</span>
                <span style="color:var(--dim)"> ${reg} ${band}</span>
              </span>
              <span style="font-size:11px; font-weight:bold; color:${color}">${pct}%</span>
            </div>
            <div style="background:rgba(255,255,255,0.08); border-radius:2px; height:4px; margin-bottom:4px;">
              <div style="width:${barW}%; height:100%; background:${color}; border-radius:2px; transition:width 0.5s;"></div>
            </div>
            <div style="font-size:9px; color:var(--dim); display:flex; gap:12px;">
              <span>n=${d.n}</span>
              <span>hit=${Math.round(d.hit60*100)}%</span>
              <span>PnL sim <span style="color:${d.pnlAvg>0?'var(--green)':'var(--red)'}">${d.pnlAvg>0?'+':''}${d.pnlAvg.toFixed(2)}$</span></span>
              <span>Δ ${d.delta>0?'+':''}${d.delta.toFixed(1)}</span>
            </div>
          </div>`;
      });

      document.getElementById('edge-body').innerHTML =
        edgeRows || '<div style="color:var(--dim);text-align:center;padding:16px;font-size:10px;">In attesa segnali...</div>';
      // ── END ECONOMIC EDGE ──────────────────────────────────────────
    $('st-counts').textContent = `open:${st.open||0} / chiusi:${st.closed||0}`;
    const stTopRows = st.top || [];
    if (stTopRows.length > 0) {
      $('st-body').innerHTML = stTopRows.map(r => {
        const hit = r.hit_60s || 0;
        const hitCol = hit >= 0.65 ? 'var(--green)' : hit >= 0.50 ? 'var(--yellow)' : 'var(--red)';
        const pnl = r.pnl_sim_avg || 0;
        const pnlCol = pnl > 0 ? 'var(--green)' : 'var(--red)';
        const delta = r.avg_delta_60s || 0;
        const parts = r.context.split('|');
        const regime = parts[0] || '?';
        const dir    = parts[1] || '?';
        const band   = parts[2] || '?';
        return `<tr>
          <td style="padding:4px 6px;color:var(--dim);font-size:9px">
            <span style="color:${dir==='LONG'?'var(--green)':'var(--red)'}">${dir}</span>
            ${regime} ${band}
          </td>
          <td style="padding:4px 6px;text-align:center;color:var(--text)">${r.n}</td>
          <td style="padding:4px 6px;text-align:center;color:${hitCol};font-weight:700">${(hit*100).toFixed(0)}%</td>
          <td style="padding:4px 6px;text-align:center;color:${delta>=0?'var(--green)':'var(--red)'}">${delta>=0?'+':''}${delta.toFixed(1)}</td>
          <td style="padding:4px 6px;text-align:center;color:${pnlCol};font-weight:700">${pnl>=0?'+':''}$${Math.abs(pnl).toFixed(2)}</td>
        </tr>`;
      }).join('');
    }

    // SUGGESTIONS
    $('suggestions-box').innerHTML = (d.suggestions||[]).map(s=>`<span style="margin-right:16px">${s}</span>`).join('');

  }).catch(()=>{
    $('status-dot').className='status-dot dot-off';
    $('status-txt').textContent='OFFLINE';
    $('status-txt').style.color='var(--red)';
  });
}

function sendCmd(cmd){
  fetch('/trading/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:cmd})})
  .then(r=>r.json()).then(()=>{ const ab=$('alert-bar'); ab.style.display='block'; ab.innerHTML='✅ Comando inviato: '+cmd; setTimeout(()=>{ab.style.display='none'},3000); });
}

update();
setInterval(update, 2000);
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


# ═══════════════════════════════════════════════════════════════════════════
# HEALTH MONITOR
# ═══════════════════════════════════════════════════════════════════════════

HEALTH_HTML = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OVERTOP — System Health</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Bebas+Neue&display=swap" rel="stylesheet">
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: 'Space Mono', monospace; background: #030810; color: #8a9bb0; min-height:100vh; padding:20px 16px; }
.title { font-family:'Bebas Neue',sans-serif; font-size:13px; letter-spacing:5px; color:#1e3a5f; margin-bottom:20px; text-align:center; }
.score-wrap { text-align:center; margin-bottom:24px; }
.score-label { font-size:9px; letter-spacing:3px; color:#1e3a5f; margin-bottom:6px; }
.score-num { font-family:'Bebas Neue',sans-serif; font-size:72px; line-height:1; transition:color .5s; }
.score-num.good { color:#00c97a; text-shadow:0 0 30px rgba(0,201,122,0.3); }
.score-num.warn { color:#f0b429; text-shadow:0 0 30px rgba(240,180,41,0.3); }
.score-num.bad  { color:#ff3355; text-shadow:0 0 30px rgba(255,51,85,0.3); }
.score-sub { font-size:10px; color:#1e3a5f; margin-top:4px; letter-spacing:1px; }
.bar-wrap { display:flex; gap:3px; margin-bottom:24px; height:3px; }
.bar-seg { flex:1; border-radius:2px; }
.counters { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:24px; }
.counter-box { background:#050d1a; border-radius:8px; padding:12px 14px; border:0.5px solid #0a1828; text-align:center; }
.counter-n { font-family:'Bebas Neue',sans-serif; font-size:36px; line-height:1; }
.counter-n.green { color:#00c97a; } .counter-n.red { color:#ff3355; }
.counter-label { font-size:9px; letter-spacing:2px; margin-top:4px; }
.section-title { font-size:9px; letter-spacing:3px; color:#1e3a5f; margin-bottom:10px; padding-left:2px; }
.checks { margin-bottom:20px; }
.check-item { display:flex; align-items:flex-start; gap:10px; padding:10px 12px; margin-bottom:5px; border-radius:6px; border-left:2px solid transparent; }
.check-item.verde { background:rgba(0,201,122,0.05); border-left-color:#00c97a; }
.check-item.rosso { background:rgba(255,51,85,0.05); border-left-color:#ff3355; }
.check-dot { width:6px; height:6px; border-radius:50%; margin-top:5px; flex-shrink:0; }
.verde .check-dot { background:#00c97a; } .rosso .check-dot { background:#ff3355; }
.check-text { font-size:11px; line-height:1.6; flex:1; }
.verde .check-text { color:#4dd9a0; } .rosso .check-text { color:#ff6680; }
.check-score { font-family:'Bebas Neue',sans-serif; font-size:16px; flex-shrink:0; }
.verde .check-score { color:rgba(0,201,122,0.4); } .rosso .check-score { color:rgba(255,51,85,0.4); }
.patch-wrap { background:#050d1a; border-radius:8px; padding:14px; border:0.5px solid #0a1828; margin-bottom:20px; }
.patch-title { font-size:9px; letter-spacing:3px; color:#1e3a5f; margin-bottom:10px; }
.patch-item { display:flex; gap:8px; padding:4px 0; border-bottom:0.5px solid #0a1828; font-size:10px; }
.patch-item:last-child { border-bottom:none; }
.patch-ts { color:#1e3a5f; flex-shrink:0; } .patch-text { color:#3d5a7a; flex:1; } .patch-result { color:#00c97a; flex-shrink:0; font-size:9px; }
.goal-wrap { text-align:center; padding:14px; border:0.5px solid #0a1828; border-radius:8px; font-size:10px; color:#1e3a5f; letter-spacing:1px; }
.goal-wrap span { color:#00c97a; }
.refresh-info { text-align:center; font-size:9px; color:#1e3a5f; margin-top:12px; }
</style>
</head>
<body>
<div class="title">OVERTOP — SYSTEM HEALTH MONITOR</div>
<div class="score-wrap">
  <div class="score-label">HEALTH SCORE — OBIETTIVO ZERO</div>
  <div class="score-num" id="scoreNum">...</div>
  <div class="score-sub" id="scoreSub">caricamento...</div>
</div>
<div class="bar-wrap" id="barWrap"></div>
<div class="counters">
  <div class="counter-box"><div class="counter-n green" id="countGreen">—</div><div class="counter-label" style="color:#00c97a">CORRETTI</div></div>
  <div class="counter-box"><div class="counter-n red" id="countRed">—</div><div class="counter-label" style="color:#ff3355">DA FIXARE</div></div>
</div>
<div class="checks" id="greenChecks"><div class="section-title">✓ FUNZIONA CORRETTAMENTE</div></div>
<div class="checks" id="redChecks"><div class="section-title">✗ PROBLEMI RILEVATI</div></div>
<div class="patch-wrap">
  <div class="patch-title">PATCH LOG — PROBLEMI RISOLTI</div>
  <div id="patchLog">
    <div class="patch-item"><span class="patch-ts">11/04 09:00</span><span class="patch-text">_fuoco_ok chiamata prima della definizione — crash totale</span><span class="patch-result">✓ VERDE</span></div>
    <div class="patch-item"><span class="patch-ts">11/04 08:00</span><span class="patch-text">DS_CMD heartbeat_lock None — loop errori ogni secondo</span><span class="patch-result">✓ VERDE</span></div>
    <div class="patch-item"><span class="patch-ts">10/04 12:31</span><span class="patch-text">FLIP LONG in EXPLOSIVE mancante — logica asimmetrica</span><span class="patch-result">✓ VERDE</span></div>
    <div class="patch-item"><span class="patch-ts">10/04 11:45</span><span class="patch-text">Warmup RSI/drift/regime disallineati — sistema cieco 30min</span><span class="patch-result">✓ VERDE</span></div>
    <div class="patch-item"><span class="patch-ts">09/04 23:01</span><span class="patch-text">real_samples=0 — EXPLOSIVE_GATE bloccato per sempre</span><span class="patch-result">✓ VERDE</span></div>
  </div>
</div>
<div class="goal-wrap">OBIETTIVO: score <span>ZERO</span> = motore perfetto con qualsiasi mercato</div>
<div class="refresh-info">aggiornamento automatico ogni 15 secondi</div>
<script>
const CHECKS = [
  {color:'verde', eval:h=>h.m2_wr===1.0&&h.m2_trades>0, text:h=>`WR ${(h.m2_wr*100).toFixed(0)}% sui ${h.m2_trades} trade eseguiti`},
  {color:'verde', eval:h=>(h.phantom?.pnl_saved||0)>(h.phantom?.pnl_missed||0)*3, text:h=>`Protezione: salvati $${Math.round(h.phantom?.pnl_saved||0)} vs mancati $${Math.round(h.phantom?.pnl_missed||0)}`},
  {color:'verde', eval:h=>(h.m2_loss_streak||0)===0, text:()=>'Zero loss streak attiva'},
  {color:'verde', eval:h=>(h.m2_score_components?.macd||0)>=8, text:h=>`MACD score=${h.m2_score_components?.macd}/10`},
  {color:'verde', eval:h=>Math.max(h.oi_carica||0,h.oi_carica_short||0)>0.80, text:h=>`OI FUOCO carica=${Math.max(h.oi_carica||0,h.oi_carica_short||0).toFixed(2)}`},
  {color:'verde', eval:h=>(h.m2_score_components?.warmup_rsi||0)>=50, text:()=>'Warmup RSI completo'},
  {color:'rosso', eval:h=>h.diagnosis?.crash_attivo===true, text:h=>`CRASH M2: ${(h.diagnosis?.errori_recenti||[])[0]||'errore sconosciuto'}`},
  {color:'rosso', eval:h=>(h.m2_score_components?.warmup_rsi||50)<50, text:h=>`WARMUP RSI incompleto: ${h.m2_score_components?.warmup_rsi||0}/50`},
  {color:'rosso', eval:h=>(h.m2_score_components?.fp||0)===0, text:()=>'FINGERPRINT score=0'},
  {color:'rosso', eval:h=>(h.telemetry?.B_direction?.flips_per_hour||0)>8, text:h=>`Direzione instabile: ${(h.telemetry?.B_direction?.flips_per_hour||0).toFixed(0)} flip/ora`},
  {color:'rosso', eval:h=>h.veritas?.rows?.some(r=>r.verdetto==='SBAGLIATO'&&r.n>500&&r.pnl_avg<-1.5), text:h=>{const r=h.veritas?.rows?.find(r=>r.verdetto==='SBAGLIATO'&&r.n>500&&r.pnl_avg<-1.5);return r?`Veritas: ${r.chiave} sbaglia su ${r.n} casi`:''}},
  {color:'rosso', eval:h=>h.regime==='RANGING', text:()=>'Regime RANGING — nessun edge'},
];
function render(hb) {
  const active = CHECKS.map(c=>({...c,active:c.eval(hb),txt:c.text(hb)}));
  const greens = active.filter(c=>c.color==='verde'&&c.active);
  const reds   = active.filter(c=>c.color==='rosso'&&c.active);
  const score  = reds.length - greens.length;
  const sEl = document.getElementById('scoreNum');
  sEl.textContent = (score>0?'+':'')+score;
  sEl.className = 'score-num '+(score<=0?'good':score<=3?'warn':'bad');
  document.getElementById('scoreSub').textContent = score<=0?'Sistema sano':score+' problema'+(score>1?'i':'')+' da correggere';
  document.getElementById('countGreen').textContent = greens.length;
  document.getElementById('countRed').textContent   = reds.length;
  const bar=document.getElementById('barWrap'); bar.innerHTML='';
  [...greens,...reds].forEach((c,i)=>{const s=document.createElement('div');s.className='bar-seg';s.style.background=i<greens.length?'#00c97a':'#ff3355';bar.appendChild(s);});
  const gc=document.getElementById('greenChecks'); gc.innerHTML='<div class="section-title">✓ FUNZIONA CORRETTAMENTE</div>';
  greens.forEach((c,i)=>gc.innerHTML+=`<div class="check-item verde"><div class="check-dot"></div><div class="check-text">${c.txt}</div><div class="check-score">+1</div></div>`);
  const rc=document.getElementById('redChecks'); rc.innerHTML='<div class="section-title">✗ PROBLEMI RILEVATI</div>';
  if(!reds.length) rc.innerHTML+='<div style="font-size:10px;color:#00c97a;padding:8px 12px;">✓ Nessun problema</div>';
  reds.forEach((c,i)=>rc.innerHTML+=`<div class="check-item rosso"><div class="check-dot"></div><div class="check-text">${c.txt}</div><div class="check-score">−1</div></div>`);
}
async function load() {
  try { const r=await fetch('/trading/status'); const d=await r.json(); render(d.heartbeat||d); }
  catch(e) { document.getElementById('scoreSub').textContent='Errore connessione'; }
}
load(); setInterval(load,15000);
</script>
</body>
</html>"""

@app.route('/health')
def health_monitor():
    return render_template_string(HEALTH_HTML)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    log(f"[MAIN] 🚀 MISSION CONTROL V6.0 + AI BRIDGE — porta {port}")
    app.run(host='0.0.0.0', port=port, debug=False)

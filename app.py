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
try:
    import supervisor_new as sv_new
    _sv_new_ok = True
except ImportError:
    _sv_new_ok = False
    sv_new = None
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
# NARRATORE AI — Dialogo tra due AI ogni 60 secondi
# ═══════════════════════════════════════════════════════════════════════════
#
# LIVELLO 1 — OSSERVATORE: legge lo status completo, trova la domanda giusta
# LIVELLO 2 — RAGIONATORE: riceve solo la domanda, ragiona senza pregiudizi
# Il dialogo produce una narrativa viva che racconta cosa sta pensando il sistema
#
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
NARRATORE_INTERVAL = 60  # secondi tra ogni ciclo narrativo

PROMPT_OSSERVATORE = """Sei l'Osservatore di un sistema di trading algoritmico su BTC/USDC.
Hai accesso allo status completo del sistema in questo momento.
Il tuo compito NON è descrivere i numeri. È trovare LA DOMANDA più importante che emerge dai dati.
Una sola domanda — quella che, se risposta, spiega meglio cosa sta succedendo.
Cerca discrepanze, tensioni tra componenti, segnali contrastanti.
Rispondi SOLO con la domanda, in italiano, senza spiegazioni aggiuntive.
Esempio: "Perché l'OI è a FUOCO ma il sistema non entra nonostante il Signal Tracker mostri 87% di hit rate?"
"""

PROMPT_RAGIONATORE = """Sei il Ragionatore del sistema OVERTOP BASSANO — trading bot BTC/USDC.
Sei l'intelligenza centrale del sistema. Ogni tua capsula viene iniettata nel bot e cambia il suo comportamento.
La tua responsabilità è totale: capsule sbagliate fanno perdere soldi. Capsule giuste fanno guadagnare.

═══ ARCHITETTURA COMPLETA ═══

CAMPO GRAVITAZIONALE (motore entry):
- Score 0-100: seed(25pt) + fingerprint_wr(25pt) + RSI(10pt) + MACD(10pt) + regime(15pt) + prebreakout(15pt)
- Soglia dinamica 48-55 in RANGING, 40-48 in EXPLOSIVE, 38-45 in TRENDING
- Entra solo se score >= soglia
- seed basso (< 15pt) = momentum debole o warmup incompleto
- fingerprint_wr basso (< 10pt) = Oracolo non conosce questo contesto
- Se score=0 E soglia=0 → il campo ha restituito VETO prima di calcolare

ORACOLO DINAMICO (memoria fingerprint):
- Accumula real_samples da ogni trade chiuso
- Chiave: LONG|momentum|volatility|trend (es. LONG|DEBOLE|ALTA|SIDEWAYS)
- WR = win rate reale su quel contesto
- Quando real_samples >= 5 con WR < 20% → attiva FANTASMA automatico (blocca quel contesto)
- Quando real_samples >= 20 con WR < 15% → contesto confermato tossico

CAPSULE INTELLIGENTE (CI):
- CI_RANGING_EDGE: abbassa soglia -8 se Signal Tracker hit > 55% in RANGING su 200+ segnali
- CI_FUOCO_WINDOW: abbassa soglia -6 per 45s dopo un evento FUOCO
- CI_OI_ESTREMO: abbassa soglia -12 quando OI carica >= 0.95
- CI_RAIN_SIZE: riduce size a 0.20 quando nervosismo > 60%

INTELLIGENZA AUTONOMA (capsule apprese):
- STATIC: 8 veti hardcodati su contesti storicamente tossici (WR < 35%)
- LEARNED: generate dall'esperienza reale — matrimoni tossici, regimi tossici
- AUTO: generate dal Phantom Supervisor quando WR blocco < 25%
- Quando esistono capsule LEARNED o AUTO → il sistema ha già imparato, rispettale

PHANTOM TRACKER:
- Simula ogni trade bloccato: se avesse fatto quel trade, quanto avrebbe guadagnato/perso?
- zavorra = trade bloccati che avrebbero VINTO (opportunità perse)
- protezione = trade bloccati che avrebbero PERSO (perdite evitate)
- zavorra% = zavorra / (zavorra + protezione)
- zavorra < 7% → il sistema blocca correttamente
- zavorra 7-15% → zona grigia, monitora
- zavorra > 15% → stiamo bloccando troppo, considera bypass

PHANTOM SUPERVISOR:
- Ogni 60s analizza WR dei blocchi per componente
- Se VETO_TOSSICO blocca con WR < 4% → AUTO_IRRIGIDISCE soglia (alza da 3 a 6 punti)
- Se VETO_TOSSICO blocca con WR > 45% → AUTO_ALLENTA soglia (abbassa 3 punti)
- Questo è il loop chiuso — il sistema si autocorregge da solo

VERITAS TRACKER:
- Misura chi aveva ragione: OI o SuperCervello?
- FUOCO|BLOCCA verdetto SBAGLIATO → ogni volta che OI era FUOCO e SC bloccava, il prezzo saliva
- hit_rate > 0.60 in FUOCO|BLOCCA → SC stava bloccando trade vincenti sistematicamente
- hit_rate < 0.40 in FUOCO|BLOCCA → SC aveva ragione a bloccare

SIGNAL TRACKER:
- Registra ogni segnale potenziale con contesto e misura delta prezzo a 60s
- hit_60s = % volte che il prezzo è andato nella direzione prevista
- n = numero osservazioni
- hit < 30% = segnale tossico confermato
- hit 30-50% = segnale debole
- hit 50-60% = segnale discreto
- hit > 60% = segnale solido con edge statistico

MATRIMONI INTELLIGENTI:
- RANGE_VOL_W = DEBOLE|ALTA|SIDEWAYS → WR atteso 19% (quasi sempre perde)
- RANGE_VOL_M = MEDIO|ALTA|SIDEWAYS → WR atteso 28%
- STRONG_BULL = FORTE|BASSA|UP → WR atteso 85% (quasi sempre vince)
- Il matrimonio attivo dice qual è il contesto del trade aperto ora

STATE ENGINE:
- NEUTRO: entra normalmente
- DIFENSIVO: loss_streak >= 2, cooldown attivo — non entra
- AGGRESSIVO: win_streak >= 3 — entra con size maggiore
- Auto-reset dopo 5 min in DIFENSIVO

═══ COME LEGGERE I DATI ═══

REGIME_LOG: ultimi cambi EXPLOSIVE/RANGING. Se EXPLOSIVE dura < 90s → falso breakout
ASSETTO_STORIA: ultimi switch DIFENSIVO/NEUTRO/ATTACCO. Se oscilla ogni 2 min → mercato indeciso
OI_LONG/SHORT: carica accumulata. FUOCO = energia dichiarata, > 0.85 = mercato urla
CI_STORIA: cosa ha fatto la CI negli ultimi minuti — segnali di allarme o opportunità
TRADES_STATS: risultato reale — n, wr, pnl_tot, consecutive_losses, last_context
ULTIMI_TRADE: ultimi trade con momentum|volatility|trend, PnL, motivo exit
ORACOLO real: campioni reali per contesto — da qui emerge la verità statistica

═══ GERARCHIA DELLE DECISIONI ═══

1. Se esistono capsule LEARNED/AUTO che bloccano un regime/contesto → RISPETTALE sempre
   Non generare BOOST_SEED o ABBASSA_SOGLIA per bypassarle — il sistema ha già imparato
   
2. BOOST_SEED serve SOLO quando il sistema è cieco (n_trade = 0, seed = 0, fp = 0)
   Con 10+ trade reali il sistema NON è cieco — vede correttamente, anche se perde
   Se perde sistematicamente con 10+ trade → serve BLOCCA_CONTESTO, non BOOST_SEED

3. ABBASSA_SOGLIA ha senso solo se:
   - Signal Tracker hit > 50% nel contesto attuale
   - Phantom zavorra > 10%
   - Regime stabile > 90s
   
4. BLOCCA_CONTESTO ha senso quando:
   - TRADES_STATS wr < 20% su n >= 10 trade reali
   - Ultimi 5 trade tutti stesso contesto e tutti in perdita
   - Oracolo real_samples >= 5 con WR < 20% su quel fingerprint

5. CAPSULA: null quando:
   - Il sistema sta già bloccando correttamente (zavorra < 7%)
   - Non c'è nulla da aggiungere che il sistema non stia già facendo

═══ REGOLE SPECIFICHE ═══

R1. RANGING stabile > 10 min → NON generare capsule offensive, solo preparatorie
R2. EXPLOSIVE < 90s → falso breakout, NON abbassare soglia
R3. OI FUOCO + hit ST < 50% → capsula leggera max delta -6
R4. OI FUOCO + hit ST > 60% + regime stabile > 90s → capsula forte delta -10/-12
R5. Phantom zavorra < 7% → VETO funziona, non toccare
R6. Phantom zavorra > 15% → VETO troppo aggressivo, bypass giustificato
R6b. REGOLA FONDAMENTALE — quando zavorra > 15% NON aggiungere altri BLOCCA_CONTESTO generici. Il problema non è bloccare di meno — è discriminare meglio. Guarda ANALISI_WIN e ANALISI_LOSS nel summary: i trade che vincono hanno un pattern preciso (score alto, contesto specifico, OI confermato). I trade che perdono hanno un pattern diverso (score basso, stesso contesto ma condizioni diverse). La soluzione non è aprire tutto né bloccare tutto — è capire quale combinazione di score+contesto+OI produce WIN e quale produce LOSS, e bloccare SOLO quella combinazione tossica. Con zavorra > 15% usa ABBASSA_SOGLIA mirato sul contesto che ha WIN reali, non BLOCCA_CONTESTO cieco su tutto il contesto.
R7. n_trade < 5 E seed=0 E fp=0 → BOOST_SEED per rompere paradosso zero-trade
R7b. PERICOLO CRITICO — soglia=0 nel MOTORE: quando il campo restituisce score=0 E soglia=0 significa che un VETO ha bloccato PRIMA del calcolo. In questo caso BOOST_SEED bypassa tutto e il sistema entra CIECO senza nessuna valutazione reale del mercato. MAI generare BOOST_SEED quando vedi soglia=0.0 nello status MOTORE. Aspetta che il sistema esca dal VETO naturalmente oppure genera BLOCCA_CONTESTO sul contesto tossico.
R8. n_trade > 10 E wr < 20% → sistema NON è cieco, vede il problema: BLOCCA_CONTESTO
R9. Capsule LEARNED/AUTO presenti → non bypassarle, il sistema ha già imparato
R10. MATRIMONIO_TOSSICO RANGE_VOL_W attivo → non generare capsule che permettono entry in DEBOLE|ALTA|SIDEWAYS
R11. Veritas FUOCO|BLOCCA hit > 60% → SC blocca sistematicamente segnali vincenti → ABBASSA_SOGLIA forte
R12. Veritas FUOCO|BLOCCA hit < 40% → SC aveva ragione → ALZA_SOGLIA o null
R13. consecutive_losses >= 5 → STATE ENGINE in DIFENSIVO è corretto, non forzare
R14. BTC in RANGING con alta volatilità (ALTA) → contesto sfavorevole, aspetta EXPLOSIVE stabile

═══ CONOSCENZA AVANZATA — LEGGI TUTTO ═══

LEGGERE I DATI QUANTITATIVI:
- seed=3pt su 25pt = momentum molto debole, warmup incompleto — il campo non ha storia sufficiente
- exit_too_early=0.65 = 65% dei trade chiusi prima del massimo profitto — sistema troppo nervoso nell'exit
- dur_win_avg >> dur_loss_avg = i trade vincenti durano molto più dei perdenti — segnale sano
- dur_win_avg << dur_loss_avg = il sistema esce troppo presto dai vincenti e tarda sui perdenti — problema
- tick_count / m2_trades < 0.05% = sistema quasi paralizzato, troppi blocchi
- real_samples = 0 su Oracolo = dato sintetico, NON affidabile per decisioni — attendere 5+ campioni reali
- real_samples >= 30 = dato statisticamente robusto, decisioni basate su evidenza solida
- pred_ratio vicino 100% = predizione calibrata perfettamente — ottimo
- sc_pesi campo_carica >> oracolo_fp = SC si fida più del campo che dell'Oracolo — se Oracolo ha dati reali è squilibrio da correggere

DECODIFICARE I MOTIVI DI EXIT:
- EXIT_E20_S45 = energy score 20 con soglia 45 = uscita tardiva, prezzo già sceso molto
- EXIT_E40_S47_WIN_+41 con PnL negativo = sistema ha toccato il profitto (WIN_+41 ticks) ma è uscito quando il prezzo è tornato sotto entry — exit logic non ha catturato il profitto
- EXIT_E15_S44 ripetuto = sistema entra con score troppo basso, il movimento non si sviluppa mai
- WIN_+N nel motivo con PnL≈0 = fee di trading azzerano il profitto — edge troppo piccolo

SEGNALI DI MERCATO AVANZATI:
- MACD hist negativo + RSI > 60 = contraddizione — momentum in calo nonostante RSI rialzista, contesto instabile
- MACD hist sale da -8 a -1 in 10 tick = divergenza positiva, possibile inversione imminente — precursore non entry
- drift history tutti negativi = pressione ribassista costante, LONG è contrarian pericoloso
- Volume +50% + prezzo fermo = assorbimento istituzionale, possibile inversione imminente
- compressione ≈ 0 = mercato completamente fermo, breakout imminente ma direzione ignota — NON anticipare
- compressione 0.23 + volume +50% = accumulo istituzionale con compressione, precede EXPLOSIVE
- OI_SHORT FUOCO mentre prezzo sale = possibile short squeeze, potenziale esplosione rialzista
- OI_LONG carica scende velocemente (0.95→0.50 in 5 tick) = energia esaurita, pericoloso entrare LONG
- RANGING > 2 ore = statisticamente precede breakout violento — preparare capsule per EXPLOSIVE
- BREATH_FASE=ESALAZIONE = mercato rilascia energia, pericoloso entrare, aspettare INALAZIONE o PICCO
- nervosismo negativo = mercato rilassato, gomme SLICK, size piena permessa
- nervosismo > 0.6 = mercato in tensione, gomme RAIN, size ridotta obbligatoria
- pre_breakout_signals=3 factor=0.92 = 3 segnali di energia accumulata = entry con score alto molto probabile
- BTC tocca livello tondo (es. 75000) e ritraccia = comportamento normale, secondo tocco più affidabile
- BTC sale 500$ in 2 minuti DOPO = troppo tardi, NON inseguire movimenti già avvenuti

REGOLE STATISTICHE:
- n < 10 in Signal Tracker = nessuna evidenza, ignorare
- n >= 50 = evidenza discreta
- n >= 200 = evidenza solida, decisioni affidabili
- n >= 1000 + hit < 35% = contesto CONFERMATO tossico → BLOCCA_CONTESTO vita 3600s+
- WR < 20% su real_samples >= 5 = fantasma automatico dovrebbe attivarsi
- WR < 20% su real_samples >= 30 = tossicità confermata robusta
- hit_60s alto (69%) ma pnl_sim_avg negativo = edge statistico SENZA edge economico — i movimenti sono troppo piccoli per coprire le fee — NON abbassare la soglia
- 30 trade per contesto = margine di errore ±18% sul WR
- 100 trade per contesto = alta confidenza statistica

GESTIONE CAPSULE — ERRORI DA EVITARE:
- 3+ capsule ABBASSA_SOGLIA attive contemporaneamente = si sommano, possibile entry con score 30 invece di 48
- Stessa capsula generata ogni ciclo ma bloccata (già_attiva) = cambiar strategia, non ripetere
- Se BOOST_SEED non produce entry dopo 3 cicli = il problema non è la soglia → BLOCCA_CONTESTO
- Capsula vita=3600s forza=0.9 = dominante per un'ora, rischiosa se mercato cambia — preferire vita 600s con rinnovo
- Se ABBASSA_SOGLIA già attiva non produce entry = il VETO persiste per altro motivo, non aggiungere altre capsule soglia
- Capsule RA_ vita < 300s = possono scadere prima del rinnovo del Narratore — vita minima 300s

STRATEGIE OPERATIVE:
- WR 20% RANGING + WR 100% EXPLOSIVE = strategia ottimale: bloccare RANGING completamente, aspettare solo EXPLOSIVE
- 0 trade al giorno in mercato sbagliato è MEGLIO di 10 trade in perdita — selettività massima
- win_streak >= 3 = State ENGINE dovrebbe essere AGGRESSIVO → BOOST_SIZE mult=1.2-1.3 giustificato
- consecutive_losses >= 10 = DIFENSIVO obbligatorio + BLOCCA_CONTESTO 3600s su last_context
- Sistema in RANGING da 3+ ore = non è un problema del sistema, è il mercato — NON forzare con capsule
- NON resettare il sistema con WR basso — i dati accumulati sono il patrimonio più prezioso
- Phantom bilancio $1070 ma PnL -$95 = VETO funziona bene ma la selezione dei trade eseguiti è sbagliata

QUANDO IL RAGIONATORE SBAGLIA:
- Se genera capsule opposte in cicli consecutivi = oscillazione, dati insufficienti → capsule vita più lunga
- Se capsula RA_ aumenta zavorra = sta peggiorando il sistema → null nel ciclo successivo
- Il Phantom è il giudice finale: se una capsula RA_ fa entrare in contesti che il Phantom avrebbe bloccato → la capsula è sbagliata

SESSIONI DI MERCATO:
- Asia (0-8 UTC): liquidità bassa, RANGING frequente, evitare
- Europa (8-16 UTC): migliore sessione per BTC, movimenti affidabili
- USA (14-22 UTC): alta volatilità, EXPLOSIVE frequenti ma anche falsi breakout
- Notte (22-6 UTC): peggior sessione, spread alti, NON tradare in RANGING notturno

COME LEGGERE SCORE_VS_RISULTATO:
- avg_score_WIN >> avg_score_LOSS → la soglia operativa reale è tra i due valori — usala come riferimento
- score<35: WR basso → BLOCCA_CONTESTO su trade con score < 35
- score35-40: WR medio-basso → zona grigia, dipende dal contesto
- score40-45: WR accettabile → lasciare passare se OI FUOCO confermato
- score>45: WR alto → non bloccare questi trade, sono i migliori
- Se avg_score_WIN=44 e avg_score_LOSS=34 → soglia operativa reale = 42-43, non 48

COME LEGGERE ZAVORRA_ANALISI:
- BLOCCO_ECCESSIVO (zavorra > 15%) → stiamo perdendo troppo — ridurre i blocchi, aumentare vita capsule, NON aggiungere nuovi BLOCCA_CONTESTO
- ZONA_GRIGIA (7-15%) → monitorare, non intervenire
- BLOCCO_CORRETTO (< 7%) → sistema protegge bene, capsule funzionano
- Quando BLOCCO_ECCESSIVO: generare ABBASSA_SOGLIA leggero invece di BLOCCA_CONTESTO

FISICA DEL SEME — LA LEGGE FONDAMENTALE:
Il CampoGravitazionale misura l'energia del seme all'entry. seed score = fino a 25 punti.
Un seme debole in un campo instabile muore prima di generare profitto sufficiente a coprire le fee.

REGOLA FISICA:
- seed < 15 punti + RANGING + volatilità ALTA = seme senza energia = movimento collassa in EXIT_E15/E20
- Questo non è probabilistico — è fisico. Il seme non ha carburante per svilupparsi.
- EXIT_E15_S46 ripetuto = conferma che il seme muore sempre prima della soglia
- EXIT_E20_S46 ripetuto = il seme sopravvive poco più ma non abbastanza

CAPSULA CORRETTA per seme debole:
Quando vedi seed < 15 nei SCORE_DETTAGLIO + RANGING + EXIT_E15/E20 negli ULTIMI_TRADE:
{"id": "RA_SEME_DEBOLE", "azione": "ALZA_SOGLIA", "params": {"delta": 8},
 "motivo": "seed<15 in RANGING|ALTA = seme senza energia, EXIT_E15 garantito", "vita": 600, "forza": 0.75}

Con soglia più alta il sistema aspetta un seme con abbastanza energia — seed >= 20 — che ha
carburante sufficiente per sviluppare un movimento che copre le fee e genera profitto reale.

COME LEGGERE L'ENERGIA DEL SEME:
- seed 20-25 = seme forte, movimento atteso > 50 tick, copre fee con margine
- seed 15-20 = seme medio, movimento atteso 30-50 tick, borderline
- seed < 15 = seme debole, movimento atteso < 30 tick, fee mangiano tutto
- seed = 0 = sistema cieco, non valutare

EDGE ECONOMICO — LA REGOLA PIU' IMPORTANTE:
Un trade che vince in tick ma perde in dollari NON è una vittoria. È un dissanguamento lento.
Fee Binance su size 0.15 BTC = circa $0.15-0.20 per trade completo (entry + exit).
Se WIN_+N nel motivo ma PnL negativo o < $0.10 = le fee hanno mangiato tutto.
Questo si chiama EDGE ECONOMICO NEGATIVO — il sistema trova la direzione giusta ma il movimento è troppo piccolo.

COME RICONOSCERLO:
- WIN_+12 con PnL=-$0.50 → movimento 12 tick non copre le fee
- WIN_+27 con PnL=+$0.24 → appena sopra zero, non sostenibile
- WIN_+35 con PnL=+$0.78 → borderline
- WIN_+60 con PnL=+$1.90 → questo è edge economico reale

SOGLIA MINIMA DI PROFITTO ATTESO:
- In RANGING con volatilità ALTA → movimento medio < 30 tick → edge economico negativo → ALZA_SOGLIA
- In EXPLOSIVE → movimento medio > 60 tick → edge positivo → soglia normale
- Se ULTIMI_TRADE mostrano 3+ trade con WIN_+N ma PnL < $0.30 → genera ALZA_SOGLIA delta +6/+8
  per filtrare i trade con movimento insufficiente a coprire le fee

CAPSULA CORRETTA quando vedi edge economico negativo:
{"id": "RA_EDGE_ECONOMICO", "azione": "ALZA_SOGLIA", "params": {"delta": 6}, 
 "motivo": "WIN_+N con PnL negativo = fee mangiano profitto, soglia insufficiente", "vita": 600, "forza": 0.7}

NON generare BLOCCA_CONTESTO per questo problema — il contesto non è tossico, è la soglia troppo bassa.
La soglia più alta filtra i segnali deboli e lascia passare solo i movimenti abbastanza grandi da coprire le fee.

CONOSCENZA OPERATIVA AVANZATA — LEGGILA TUTTA:

GAP 0 — REGOLA ASSOLUTA: NESSUNA ENTRY NEI PRIMI 60 SECONDI DAL BOOT:
Il sistema al boot e cieco. Durante il warmup (RSI < 20 campioni) NON generare capsule ABBASSA_SOGLIA o BOOST_SEED.
Queste capsule durante il warmup abbassano la soglia e permettono entry in contesti tossici prima che il sistema abbia dati reali.
Durante il warmup genera SOLO capsule BLOCCA_CONTESTO o null.
Dopo il warmup completo aspetta ancora 60 secondi prima di valutare qualsiasi entry.
Segnale di warmup in corso: n_trade=0 + seed < 10 + fp=0 + uptime < 3 minuti.

GAP 0 — REGOLA ASSOLUTA: NESSUNA ENTRY NEI PRIMI 60 SECONDI DAL BOOT:
Il sistema al boot è cieco — RSI buffer vuoto, Oracolo senza campioni reali, seed=0.
Qualsiasi entry nei primi 60 secondi è una scommessa cieca senza nessuna base statistica.
Se vedi tick_count < 60 o uptime_hours < 0.02 = genera BLOCCA_CONTESTO vita=120s su tutto.
Questa è la regola più semplice e più importante — nessuna eccezione, nessun bypass FUOCO.

GAP 3 — RIENTRO IMMEDIATO DOPO LOSS:
Dopo EXIT_E15 o EXIT_E20 NON rientrare nello stesso contesto per tempo — aspetta che cambino le condizioni.
Condizioni di sblocco: regime diventa EXPLOSIVE, OPPURE momentum passa da DEBOLE a FORTE, OPPURE OI carica > 0.85 con seed >= 20.
Se vedi due trade consecutivi a < 2 minuti di distanza con stesso contesto e stessa perdita = genera BLOCCA_CONTESTO vita=600s.
NON è una questione di timer — è una questione di condizioni cambiate.

GAP 4 — VERITAS: QUANDO IL SC SBAGLIA SISTEMATICAMENTE:
Se FUOCO|BLOCCA hit_rate > 60% SBAGLIATO su n > 200 = il SuperCervello blocca sistematicamente segnali vincenti.
In questo caso genera ABBASSA_SOGLIA forte delta -10/-12 vita=600s.
Se hit_rate > 70% SBAGLIATO = è una certezza statistica — il SC è calibrato male per questo regime.
Questa è una delle capsule più potenti che puoi generare.

GAP 5 — ORACOLO dur_win vs dur_loss:
dur_win_avg >> dur_loss_avg = i trade vincenti durano molto più di quelli perdenti = segnale sano.
dur_win_avg < 25s = il seme non sviluppa abbastanza energia = fee mangiano tutto.
Se dur_win_avg per il contesto corrente < 25s = stesso problema del seme debole = ALZA_SOGLIA.

GAP 7 — SESSIONE ORARIA:
Europa (8-16 UTC) = sessione migliore per movimenti affidabili.
USA (14-22 UTC) = alta volatilità, EXPLOSIVE frequenti ma anche falsi breakout.
Se sono le 11-18 ora italiana e il mercato è in RANGING da più di 30 minuti = normale per questa sessione.
NON forzare entry — aspetta EXPLOSIVE che arriverà con l'apertura USA.

GAP 8 — CAPSULA DI SBLOCCO ATTIVO:
Quando il sistema è bloccato da BLOCCA_CONTESTO ma le condizioni cambiano (regime→EXPLOSIVE, momentum→FORTE, OI carica > 0.90) genera una capsula ABBASSA_SOGLIA leggera delta -4 vita=120s per segnalare che le condizioni sono cambiate.
Non aspettare che le capsule di blocco scadano — intervieni attivamente quando il mercato cambia.

GAP 9 — CORRELAZIONE SCORE-DURATA:
score < 35 = durata attesa < 20s = fee garantite = NON entrare mai
score 35-42 = durata attesa 20-30s = borderline = solo in EXPLOSIVE
score 43-48 = durata attesa 30-40s = accettabile in condizioni giuste
score > 48 = durata attesa > 40s = trade di qualità = lascia passare

CONTRADDITTORIO PHANTOM — REGOLA OPERATIVA:
Ogni ciclo ricevi PHANTOM_CONTRADDITTORIO con il verdetto per ogni livello di blocco.
NON limitarti ad analizzare — devi INTERVENIRE con una capsula concreta.

LEGGI COSI':
- verdetto=BLOCCO_CORRETTO + would_win=0 + pnl_missed=$0
  = Il blocco funziona. Rafforza con BLOCCA_CONTESTO vita lunga (600s).
  
- verdetto=BLOCCO_ECCESSIVO + would_win > 15% + pnl_missed > pnl_saved*0.3
  = Il blocco sta perdendo opportunita reali. Genera ABBASSA_SOGLIA delta -6 vita=300s.
  
- verdetto=ZONA_GRIGIA
  = Osserva ancora. Capsula null.

REGOLA ASSOLUTA DEL CONTRADDITTORIO:
Se PHANTOM_CONTRADDITTORIO dice BLOCCO_CORRETTO su un contesto
E ANALISI_TRADE_RECENTI dice LOSS su quello stesso contesto
= I due concordano. Genera BLOCCA_CONTESTO vita=600s. Non servono altre capsule.

Se PHANTOM_CONTRADDITTORIO dice BLOCCO_ECCESSIVO
E ANALISI_TRADE_RECENTI dice LOSS
= Contraddizione. Phantom vede opportunita che il sistema non cattura.
= Genera ABBASSA_SOGLIA mirato su quel contesto specifico.

VERDETTO PHANTOM — IL GIUDICE FINALE:
Ogni ciclo devi confrontare Analizzatore Trade e Phantom e dichiarare il verdetto:

CASO 1 — ALLINEAMENTO PERFETTO:
Analizzatore dice LOSS + Phantom would_win=0 + pnl_missed=$0
= Il blocco era corretto. Analizzatore e Phantom concordano.
= CAPSULA: null. Non toccare nulla. Il sistema funziona.

CASO 2 — PHANTOM DIVENTA ZAVORRA:
Phantom would_win > 15% dei bloccati + pnl_missed > pnl_saved * 0.3
= Il blocco sta perdendo opportunità reali.
= CAPSULA: ABBASSA_SOGLIA mirato sul contesto con would_win alto.

CASO 3 — ANALIZZATORE IN CONFLITTO CON PHANTOM:
Analizzatore dice non doveva entrare ma Phantom dice would_win alto su quel contesto
= Il sistema ha imparato male, il blocco e eccessivo.
= CAPSULA: ABBASSA_SOGLIA leggero delta -4 vita=300s per quel contesto specifico.

CASO 4 — PHANTOM CONFERMA ANALIZZATORE:
Entrambi dicono che il trade era sbagliato.
= CAPSULA: BLOCCA_CONTESTO vita=600s per quel contesto.

REGOLA ASSOLUTA:
Il Ragionatore e il giudice. Quando i due concordano rafforza la decisione.
Quando i due divergono indaga il perche e genera capsula calibrata.
Il verdetto va dichiarato esplicitamente nell'ANALISI prima della capsula.

QUANDO PASSARE AL LIVE:
- WR > 55% su 200+ trade paper
- PnL positivo su 7 giorni consecutivi
- Phantom zavorra < 10% stabile
- Oracolo con 20+ real_samples su 3+ contesti con WR > 50%
- Nessun trade in RANGING puro da 48h

IL TUO COMPITO:
Ricevi una domanda con lo status completo inclusa la storia.
Ragiona sul FILM non sulla fotografia — guarda la tendenza, non il singolo momento.
Rispondi ESATTAMENTE in questo formato:

ANALISI: [una frase che spiega il problema considerando la storia]
CAPSULA: {"id": "RA_NOME", "azione": "AZIONE", "params": {...}, "motivo": "motivazione", "vita": 300, "forza": 0.65}

AZIONI DISPONIBILI:
- ABBASSA_SOGLIA: params {"delta": -N} — abbassa la soglia di N punti
- ALZA_SOGLIA: params {"delta": +N} — alza la soglia di N punti  
- BOOST_SEED: params {"delta": +N} — aggiunge N punti allo score (solo se cieco)
- BLOCCA_CONTESTO: params {"momentum": "X", "volatility": "Y", "trend": "Z", "durata": N} — blocca entry in quel contesto per N secondi
- RIDUCI_SIZE: params {"mult": 0.5} — riduce la size del trade
- BOOST_SIZE: params {"mult": 1.5} — aumenta la size del trade

REGOLE FORMATO:
- SEMPRE ANALISI: poi CAPSULA: su righe separate
- JSON su una sola riga, valido, senza caratteri extra
- id SEMPRE inizia con RA_
- delta tra -15 e +15, forza 0.5-0.9, vita 120-3600
- Se non serve capsula: CAPSULA: null
- ANALISI in italiano, JSON in inglese
"""

PROMPT_CAPSULA_JSON = """Sei il generatore di capsule del sistema OVERTOP BASSANO.
Ricevi una domanda su un'anomalia del sistema e devi rispondere SOLO con un oggetto JSON valido.

ARCHITETTURA:
- CampoGravitazionale: score 0-100. seed(25pt)+fingerprint(25pt)+RSI(10pt)+MACD(10pt)+regime(15pt)+prebreakout(15pt). Soglia 48-55.
- VETO_TOSSICO: blocca DEBOLE+ALTA+SIDEWAYS (WR 19%). Bypassabile da CI.
- CapsuleIntelligente: CI_RANGING_EDGE(-8), CI_FUOCO_WINDOW(-6), CI_OI_ESTREMO(-12).
- Phantom zavorra > 15% = stiamo bloccando troppo. zavorra < 7% = blocco corretto.
- Signal Tracker hit > 55% = segnale solido. hit < 50% = segnale debole.
- Regime EXPLOSIVE stabile > 90s = breakout reale. < 90s = falso.
- LEARNED/AUTO capsule presenti = sistema ha già imparato, rispettarle.
- n_trade > 10 con wr < 20% = sistema vede correttamente, serve BLOCCA_CONTESTO non BOOST_SEED.

REGOLE DECISIONE:
- n_trade < 5 E seed=0 E fp=0 → BOOST_SEED delta +15 (sistema cieco)
- PERICOLO: MAI BOOST_SEED quando soglia=0.0 nel MOTORE — significa VETO attivo, sistema entrerebbe cieco
- n_trade > 10 E wr < 20% → BLOCCA_CONTESTO (sistema vede, non cieco)
- hit < 50% → forza max 0.55, delta max -6
- hit > 55% + regime stabile → forza 0.65-0.75, delta -8/-12
- RANGING instabile → vita 180, delta -4 (preparatoria)
- zavorra < 7% → null (il blocco funziona)
- zavorra > 15% → bypass giustificato, delta -8
- Capsule LEARNED presenti → null (rispetta apprendimento sistema)
- consecutive_losses >= 5 → BLOCCA_CONTESTO su last_context

AZIONI:
- ABBASSA_SOGLIA: {"delta": -N}
- BOOST_SEED: {"delta": +N}
- BLOCCA_CONTESTO: {"momentum": "X", "volatility": "Y", "trend": "Z", "durata": N}
- ALZA_SOGLIA: {"delta": +N}

Rispondi SOLO con questo JSON, nient'altro:
{"id": "RA_NOME", "azione": "ABBASSA_SOGLIA", "params": {"delta": -8}, "motivo": "spiegazione breve", "vita": 300, "forza": 0.65}

Se non serve capsula rispondi: {"id": null}
"""

def _chiama_deepseek(prompt_sistema: str, messaggio: str, max_tokens: int = 200) -> str:
    """Chiama DeepSeek — risposta libera in prosa."""
    if not DEEPSEEK_API_KEY:
        return ""
    try:
        import urllib.request
        payload = json.dumps({
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": prompt_sistema},
                {"role": "user",   "content": messaggio}
            ],
            "max_tokens": max_tokens,
            "temperature": 0.7,
        }).encode()
        req = urllib.request.Request(
            "https://api.deepseek.com/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log(f"[NARRATORE] Errore DeepSeek: {e}")
        return ""


def _chiama_deepseek_json(prompt_sistema: str, messaggio: str) -> dict:
    """Chiama DeepSeek con response_format JSON forzato. Ritorna dict o {}."""
    if not DEEPSEEK_API_KEY:
        return {}
    try:
        import urllib.request
        payload = json.dumps({
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": prompt_sistema},
                {"role": "user",   "content": messaggio}
            ],
            "max_tokens": 300,
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }).encode()
        req = urllib.request.Request(
            "https://api.deepseek.com/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"].strip()
            return json.loads(content)
    except Exception as e:
        log(f"[NARRATORE] Errore DeepSeek JSON: {e}")
        return {}

def _build_status_summary(hb: dict) -> str:
    """Costruisce un riassunto con storia temporale per il Ragionatore."""
    try:
        ph      = hb.get("phantom", {})
        ci      = hb.get("ci_capsule", [])
        sig     = hb.get("signal_tracker", {}).get("top", [{}])
        vr      = hb.get("veritas", {}).get("rows", [])
        sc_pesi = hb.get("sc_pesi", {})

        ci_str  = ", ".join([f"{c['id']}(forza={c['forza']:.0%})" for c in ci]) or "nessuna"
        sig_str = ", ".join([f"{s.get('context','?')} hit={s.get('hit_60s',0):.0%} n={s.get('n',0)}"
                             for s in sig[:3]]) or "nessuno"
        vr_str  = ", ".join([f"{r.get('chiave','?')} hit={r.get('hit_rate',0):.0%} n={r.get('n',0)}"
                             for r in vr[:3]]) or "nessuno"

        # ── STORIA REGIME — ultimi 10 switch ─────────────────────────
        switch_log = hb.get("switch_log", [])[-8:]
        regime_storia = " → ".join([
            f"{s.get('da','?')}→{s.get('a','?')}({s.get('ts','?')[-5:]})"
            for s in switch_log
        ]) or "nessuna storia"

        # ── STORIA LIVE LOG — ultimi 8 cambi regime ──────────────────
        live_log = hb.get("live_log", [])[-6:]
        regime_log = " | ".join(live_log) or "nessuno"

        # ── TREND OI — storia carica recente ─────────────────────────
        sc_history = hb.get("sc_carica_history", [])
        oi_trend = ""
        if len(sc_history) >= 10:
            recent = sc_history[-10:]
            delta = recent[-1] - recent[0]
            oi_trend = f"trend_10tick={'↑' if delta > 0.02 else '↓' if delta < -0.02 else '→'} delta={delta:+.3f}"

        # ── CI STORIA — ultimi 5 eventi ───────────────────────────────
        ci_storia = hb.get("ci_storia", [])[-5:]
        ci_storia_str = " | ".join(ci_storia) or "nessuna"

        # ── NARRATIVA OI — ultimi 3 tick ─────────────────────────────
        oi_narrativa = hb.get("oi_narrativa", [])[-3:]
        oi_narr_str = " | ".join(oi_narrativa) or "nessuna"

        # ── SCORE COMPONENTI ─────────────────────────────────────────
        score_comp = hb.get("m2_score_components", {})
        score_str = f"seed={score_comp.get('seed',0):.1f} fp={score_comp.get('fp',0):.1f} rsi={score_comp.get('rsi',0):.1f} macd={score_comp.get('macd',0):.1f}"

        # ── RISULTATI TRADE REALI — il Narratore impara dal dissanguamento ──
        trade_stats = hb.get('narratore_trade_stats', {})
        trade_storia = hb.get('narratore_trade_storia', [])[-5:]
        trade_str = ""
        if trade_stats.get('n', 0) > 0:
            trade_str = (f"n={trade_stats['n']} wr={trade_stats.get('wr',0):.0%} "
                        f"pnl_tot=${trade_stats.get('pnl_tot',0):.2f} "
                        f"loss_consecutivi={trade_stats.get('consecutive_losses',0)} "
                        f"ultimo_contesto={trade_stats.get('last_context','?')}")
        ultimi_trade = " | ".join([
            f"{t.get('ts','?')} {t.get('momentum','?')}|{t.get('volatility','?')}|{t.get('trend','?')} "
            f"score={t.get('score',0):.1f} pnl=${t.get('pnl',0):.2f} {'WIN' if t.get('is_win') else 'LOSS'} [{t.get('reason','?')}]"
            for t in trade_storia
        ]) or "nessun trade ancora"

        # ── CORRELAZIONE SCORE → RISULTATO ────────────────────────────────
        # Il Ragionatore deve vedere quali fasce di score producono WIN vs LOSS
        # Questo è il dato che permette di calibrare la soglia operativa reale
        tutti_trade = hb.get('narratore_trade_storia', [])
        score_win = [t.get('score',0) for t in tutti_trade if t.get('is_win') and t.get('score',0) > 0]
        score_loss = [t.get('score',0) for t in tutti_trade if not t.get('is_win') and t.get('score',0) > 0]
        score_corr = "nessun dato"
        if score_win or score_loss:
            avg_win  = round(sum(score_win)/len(score_win),1)  if score_win  else 0
            avg_loss = round(sum(score_loss)/len(score_loss),1) if score_loss else 0
            # Conta WIN e LOSS per fascia di score
            fasce = {'<35':{'w':0,'l':0}, '35-40':{'w':0,'l':0}, '40-45':{'w':0,'l':0}, '45-50':{'w':0,'l':0}, '>50':{'w':0,'l':0}}
            for t in tutti_trade:
                s = t.get('score', 0)
                if s <= 0: continue
                k = '<35' if s < 35 else '35-40' if s < 40 else '40-45' if s < 45 else '45-50' if s < 50 else '>50'
                if t.get('is_win'): fasce[k]['w'] += 1
                else: fasce[k]['l'] += 1
            fascia_str = " | ".join([
                f"score{k}: {v['w']}W/{v['l']}L WR={round(v['w']/(v['w']+v['l'])*100) if v['w']+v['l']>0 else 0}%"
                for k,v in fasce.items() if v['w']+v['l'] > 0
            ])
            score_corr = f"avg_score_WIN={avg_win} avg_score_LOSS={avg_loss} | {fascia_str}"

        # ── ANALISI WIN — perché i trade vincono ─────────────────────────
        # Il Ragionatore deve capire il pattern dei vincitori
        win_trades  = [t for t in tutti_trade if t.get('is_win')]
        loss_trades = [t for t in tutti_trade if not t.get('is_win')]

        def _analisi_trade(trades, label):
            if not trades:
                return f"nessun {label} ancora"
            contesti = {}
            for t in trades:
                ctx = f"{t.get('momentum','?')}|{t.get('volatility','?')}|{t.get('trend','?')}"
                if ctx not in contesti:
                    contesti[ctx] = {'n':0, 'pnl':0.0, 'scores':[]}
                contesti[ctx]['n'] += 1
                contesti[ctx]['pnl'] += t.get('pnl', 0)
                if t.get('score', 0) > 0:
                    contesti[ctx]['scores'].append(t.get('score', 0))
            parts = []
            for ctx, v in sorted(contesti.items(), key=lambda x: -x[1]['n'])[:3]:
                avg_score = round(sum(v['scores'])/len(v['scores']),1) if v['scores'] else 0
                avg_pnl   = round(v['pnl']/v['n'], 2)
                parts.append(f"{ctx} n={v['n']} avg_score={avg_score} avg_pnl=${avg_pnl}")
            return " | ".join(parts)

        analisi_win  = _analisi_trade(win_trades,  'WIN')

        # ── ANALISI TRADE per il Narratore ───────────────────────────────
        _trade_analisi = hb.get('trade_analisi', [])
        if _trade_analisi:
            analisi_trade_str = " | ".join([
                f"{a.get('ts','?')} {a.get('esito','?')} ${a.get('pnl',0):.2f} score={a.get('score',0):.1f} [{a.get('analisi','')[:80]}]"
                for a in _trade_analisi[-3:]
            ])
        else:
            analisi_trade_str = "nessuna analisi ancora"
        analisi_loss = _analisi_trade(loss_trades, 'LOSS')
        ph_data = hb.get('phantom', {})
        zavorra_n = ph_data.get('zavorra', 0)
        prot_n    = ph_data.get('protezione', 0)
        tot_n     = ph_data.get('total', 1)
        zavorra_pct = round(zavorra_n / tot_n * 100, 1) if tot_n > 0 else 0
        pnl_missed = ph_data.get('pnl_missed', 0)
        pnl_saved  = ph_data.get('pnl_saved', 0)
        zavorra_str = (f"zavorra={zavorra_n}/{tot_n} ({zavorra_pct}%) "
                      f"pnl_mancato=${pnl_missed:.0f} pnl_salvato=${pnl_saved:.0f} "
                      f"{'BLOCCO_ECCESSIVO' if zavorra_pct > 15 else 'BLOCCO_CORRETTO' if zavorra_pct < 7 else 'ZONA_GRIGIA'}")

        # PHANTOM_CONTRADDITTORIO — verdetto per ogni livello di blocco
        _ph_livelli = hb.get('phantom', {}).get('per_livello', {})
        _ph_parts = []
        for _livello, _dati in _ph_livelli.items():
            _blk = _dati.get('blocked', 0)
            _win = _dati.get('would_win', 0)
            _los = _dati.get('would_lose', 0)
            _mis = round(_dati.get('pnl_missed', 0), 2)
            _sav = round(_dati.get('pnl_saved', 0), 2)
            if _blk > 0:
                _wr_bloccati = round(_win / _blk * 100, 1)
                if _win == 0:
                    _vrd = "BLOCCO_CORRETTO"
                elif _wr_bloccati > 15:
                    _vrd = "BLOCCO_ECCESSIVO"
                else:
                    _vrd = "ZONA_GRIGIA"
                _ph_parts.append(
                    f"{_livello}: blocked={_blk} would_win={_win} would_lose={_los} "
                    f"pnl_missed=${_mis} pnl_saved=${_sav} verdetto={_vrd}"
                )
        phantom_contraddittorio = " || ".join(_ph_parts) if _ph_parts else "nessun dato"

        return f"""STATUS OVERTOP — {hb.get('last_seen','?')}
MERCATO_ORA: regime={hb.get('regime','?')} conf={hb.get('regime_conf',0):.0%} | BTC={hb.get('last_price',0):.0f}
REGIME_STORIA: {regime_log}
ASSETTO_STORIA: {regime_storia}
OI_LONG: stato={hb.get('oi_stato','?')} carica={hb.get('oi_carica',0):.3f} | {oi_trend}
OI_SHORT: stato={hb.get('oi_stato_short','?')} carica={hb.get('oi_carica_short',0):.3f}
OI_NARRATIVA: {oi_narr_str}
MOTORE: score={hb.get('m2_last_score',0):.1f} soglia={hb.get('m2_last_soglia',0):.1f} direction={hb.get('m2_direction','?')} state={hb.get('m2_state','?')}
SCORE_DETTAGLIO: {score_str}
COMPARTO: {hb.get('comparto','?')} | nervosismo={hb.get('nervosismo',0):.0%} | gomme={hb.get('gomme','?')}
CI_CAPSULE: [{ci_str}]
CI_STORIA: {ci_storia_str}
SIGNAL_TRACKER: {sig_str}
PHANTOM: bilancio=+${ph.get('bilancio',0):.0f} protezione={ph.get('protezione',0)} zavorra={ph.get('zavorra',0)} mancati=${ph.get('pnl_missed',0):.1f}
VERITAS: {vr_str}
SC_PESI: campo={sc_pesi.get('campo_carica',0):.2f} oracolo={sc_pesi.get('oracolo_fp',0):.2f} signal={sc_pesi.get('signal_tracker',0):.2f}
TRADES_TOTALI: n={hb.get('m2_trades',0)} wins={hb.get('m2_wins',0)} pnl=${hb.get('m2_pnl',0):.2f}
TRADES_STATS: {trade_str}
ULTIMI_TRADE: {ultimi_trade}
SCORE_VS_RISULTATO: {score_corr}
ZAVORRA_ANALISI: {zavorra_str}
ANALISI_WIN: {analisi_win}
ANALISI_LOSS: {analisi_loss}
ANALISI_TRADE_RECENTI: {analisi_trade_str}
PHANTOM_CONTRADDITTORIO: {phantom_contraddittorio}
LATENCY: slip_medio={{hb.get('latency_stats',{{}}).get('slippage_medio',0):.3f}}% slip_explosive={hb.get('latency_stats',{}).get('slippage_medio_exp',0):.3f}% costo_usd=${hb.get('latency_stats',{}).get('costo_usd_tot',0):.2f} verdetto={hb.get('latency_stats',{}).get('verdetto','N/A')}"""
    except Exception as e:
        return f"Errore costruzione summary: {e}"

def narratore_thread():
    """Thread del Narratore AI — ciclo ogni 60 secondi."""
    log("[NARRATORE] 🎭 Narratore AI avviato — dialogo tra due AI ogni 60s")
    time.sleep(30)  # aspetta warmup iniziale

    while True:
        try:
            with heartbeat_lock:
                hb = dict(heartbeat_data)

            if hb.get("status") != "RUNNING":
                time.sleep(30)
                continue

            # ── LIVELLO 1: OSSERVATORE ────────────────────────────────
            summary = _build_status_summary(hb)
            domanda = _chiama_deepseek(
                PROMPT_OSSERVATORE,
                f"Analizza questo status e trova la domanda più importante:\n\n{summary}",
                max_tokens=120
            )

            if not domanda:
                time.sleep(NARRATORE_INTERVAL)
                continue

            # ── LIVELLO 2: RAGIONATORE — analisi narrativa ────────────
            risposta = _chiama_deepseek(
                PROMPT_RAGIONATORE,
                f"STATUS:\n{summary}\n\nDOMANDA:\n{domanda}",
                max_tokens=200
            )

            if not risposta:
                time.sleep(NARRATORE_INTERVAL)
                continue

            # ── LIVELLO 3: GENERATORE CAPSULA — JSON forzato ──────────
            capsula_iniettata = None
            try:
                cap_data = _chiama_deepseek_json(
                    PROMPT_CAPSULA_JSON,
                    f"STATUS:\n{summary}\n\nANOMALIA:\n{domanda}"
                )

                if cap_data and cap_data.get('id'):
                    # ID null = nessuna capsula necessaria
                    cap_id = cap_data['id']

                    if all(k in cap_data for k in ['azione', 'params', 'motivo']):
                        cap_data['fonte'] = 'RAGIONATORE_AI'
                        cap_data['ts']    = datetime.utcnow().isoformat()
                        cap_data.setdefault('vita',  300)
                        cap_data.setdefault('forza', 0.65)
                        if not cap_id.startswith('RA_'):
                            cap_data['id'] = 'RA_' + cap_id

                        with heartbeat_lock:
                            capsule_ra = heartbeat_data.get("capsule_ragionatore", [])
                            capsule_ra = [c for c in capsule_ra
                                          if c.get('id') != cap_data['id']]
                            capsule_ra.append(cap_data)
                            if len(capsule_ra) > 5:
                                capsule_ra = capsule_ra[-5:]
                            heartbeat_data["capsule_ragionatore"] = capsule_ra
                            heartbeat_data["narratore_ultima_capsula"] = {
                                "id":     cap_data['id'],
                                "ts":     cap_data['ts'],
                                "forza":  cap_data['forza'],
                                "motivo": cap_data['motivo'][:80],
                            }

                        capsula_iniettata = cap_data['id']
                        log(f"[NARRATORE] 💊 Capsula JSON: {cap_data['id']} "
                            f"forza={cap_data['forza']} vita={cap_data['vita']}s "
                            f"— {cap_data['motivo'][:50]}")
                    else:
                        log(f"[NARRATORE] ⚠️ JSON incompleto: {cap_data}")
                else:
                    log(f"[NARRATORE] 💭 Nessuna capsula necessaria")

            except Exception as _ce:
                log(f"[NARRATORE] Capsula JSON error: {_ce}")

            # ── SALVA NELLA NARRATIVA ─────────────────────────────────
            analisi_testo = risposta  # testo narrativo del Ragionatore
            ts = datetime.utcnow().strftime("%H:%M")
            narrativa_entry = {
                "ts":              ts,
                "domanda":         domanda,
                "risposta":        analisi_testo,
                "capsula":         capsula_iniettata,
            }

            with heartbeat_lock:
                storico = heartbeat_data.get("narrativa_ds", [])
                storico.append(narrativa_entry)
                if len(storico) > 10:
                    storico = storico[-10:]
                heartbeat_data["narrativa_ds"] = storico

            log(f"[NARRATORE] 💬 {ts} | Q: {domanda[:60]}..." + (f" | 💊 {capsula_iniettata}" if capsula_iniettata else ""))

        except Exception as e:
            log(f"[NARRATORE] Errore: {e}")

        time.sleep(NARRATORE_INTERVAL)

# ═══════════════════════════════════════════════════════════════════════════
# ANALIZZATORE TRADE — Perché abbiamo vinto o perso questo trade?
# ═══════════════════════════════════════════════════════════════════════════
# Quarto livello del Narratore. Si attiva dopo ogni trade chiuso.
# Analizza il singolo trade e risponde: perché abbiamo vinto/perso?
# La risposta appare in dashboard accanto al trade.

PROMPT_ANALIZZATORE_TRADE = """Sei l'Analizzatore Trade del sistema OVERTOP BASSANO.
Ricevi i dettagli di un singolo trade appena chiuso e devi spiegare in modo preciso e operativo:
1. PERCHÉ ha vinto o perso (causa principale)
2. COSA il sistema avrebbe dovuto fare diversamente (se loss)
3. COSA ha fatto bene (se win)
4. LEZIONE per i prossimi trade

Conosci l'architettura completa:
- CampoGravitazionale: score 0-100, soglia 48-55
- EXIT_E{N}_S{M} = energy score N vs soglia M al momento dell'exit
- WIN_+{N} nel motivo = il sistema ha raggiunto quel profitto durante il trade
- Se PnL negativo con WIN_+N = il sistema ha visto il profitto ma non l'ha catturato
- score basso (< 35) = momentum debole, non doveva entrare
- DIVORZIO = trigger di uscita forzata (volatilità, trend, drawdown, fingerprint)
- RANGING|ALTA|SIDEWAYS = contesto storicamente tossico
- EXPLOSIVE + OI FUOCO = contesto favorevole

Rispondi in italiano, massimo 3 righe, formato:
ESITO: [WIN/LOSS] — [causa principale in una frase]
LEZIONE: [cosa imparare da questo trade]
"""

_ultimo_trade_id_analizzato = 0

def analizzatore_trade_thread():
    """Analizza ogni trade chiuso e spiega perche ha vinto o perso."""
    global _ultimo_trade_id_analizzato
    log("[ANALIZZATORE] Analizzatore trade avviato")
    time.sleep(45)

    while True:
        try:
            # Legge dal DB direttamente — funziona anche dopo restart
            rows = db_execute("""
                SELECT id, timestamp, direction, pnl, reason, data_json
                FROM trades WHERE event_type='M2_EXIT'
                ORDER BY id DESC LIMIT 1
            """, fetch=True)

            if not rows:
                log("[ANALIZZATORE] DB vuoto o errore — attendo")
                time.sleep(20)
                continue

            r = rows[0] if isinstance(rows, list) else rows
            if not r or r[0] is None:
                time.sleep(20)
                continue
            trade_id = str(r[0])
            log(f"[ANALIZZATORE] Ultimo trade DB: id={trade_id} pnl={r[3]}")  # usa l'id DB come chiave univoca

            if trade_id == str(_ultimo_trade_id_analizzato):
                time.sleep(15)
                continue

            _ultimo_trade_id_analizzato = trade_id

            import json as _json2
            try:
                dj = _json2.loads(r[5]) if r[5] else {}
            except Exception:
                dj = {}

            pnl = float(r[3] or 0)
            ultimo = {
                'ts':        str(r[1] or '')[-8:][:5],
                'is_win':    pnl > 0,
                'pnl':       round(pnl, 2),
                'reason':    r[4] or '',
                'momentum':  dj.get('momentum', '?'),
                'volatility':dj.get('volatility', '?'),
                'trend':     dj.get('trend', '?'),
                'regime':    dj.get('regime', '?'),
                'score':     float(dj.get('score', 0)),
                'matrimonio':dj.get('matrimonio', '?'),
            }

            # Costruisce il contesto del trade
            esito     = "WIN" if ultimo.get('is_win') else "LOSS"
            pnl       = ultimo.get('pnl', 0)
            score     = ultimo.get('score', 0)
            motivo    = ultimo.get('reason', '?')
            ctx       = f"{ultimo.get('momentum','?')}|{ultimo.get('volatility','?')}|{ultimo.get('trend','?')}"
            regime    = ultimo.get('regime', '?')
            matrimon  = ultimo.get('matrimonio', '?')

            messaggio = f"""Trade appena chiuso:
ESITO: {esito} | PnL: ${pnl:.2f}
Score entry: {score:.1f} | Contesto: {ctx} | Regime: {regime}
Matrimonio: {matrimon} | Motivo exit: {motivo}

{"Il sistema ha raggiunto profitto durante il trade ma è uscito in perdita." if 'WIN_+' in motivo and pnl < 0 else ""}
{"Score molto basso — sistema entrato con momentum debole." if score < 38 else ""}
{"Score nella fascia buona." if score >= 43 else ""}

Spiega perché ha vinto/perso e cosa imparare."""

            risposta = _chiama_deepseek(PROMPT_ANALIZZATORE_TRADE, messaggio, max_tokens=150)

            if risposta:
                with heartbeat_lock:
                    analisi_list = heartbeat_data.get('trade_analisi', [])
                    analisi_list.append({
                        'ts':      ultimo.get('ts', '?'),
                        'esito':   esito,
                        'pnl':     round(pnl, 2),
                        'score':   score,
                        'ctx':     ctx,
                        'motivo':  motivo,
                        'analisi': risposta,
                    })
                    if len(analisi_list) > 20:
                        analisi_list = analisi_list[-20:]
                    heartbeat_data['trade_analisi'] = analisi_list

                log(f"[ANALIZZATORE] 🔬 {esito} ${pnl:.2f} — {risposta[:60]}...")

        except Exception as e:
            log(f"[ANALIZZATORE] ❌ {e}")

        time.sleep(15)

# Avvia narratore solo se DeepSeek è configurato
if DEEPSEEK_API_KEY:
    threading.Thread(target=narratore_thread, daemon=True, name='narratore_ai').start()
    threading.Thread(target=analizzatore_trade_thread, daemon=True, name='analizzatore_trade').start()
    log("[MAIN] ✅ Narratore AI + Analizzatore Trade avviati")
else:
    log("[MAIN] ⚠️ DEEPSEEK_API_KEY mancante — Narratore AI disabilitato")

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
      <a href="/supervisor" style="background:rgba(139,92,246,0.3); border:1px solid rgba(139,92,246,0.6); color:#a78bfa; padding:5px 12px; border-radius:6px; font-size:11px; font-weight:700; text-decoration:none; letter-spacing:1px;">⚡ COMMAND CENTER</a>
      <a href="/health" style="background:rgba(0,201,122,0.15); border:1px solid rgba(0,201,122,0.4); color:#00c97a; padding:5px 12px; border-radius:6px; font-size:11px; font-weight:700; text-decoration:none; letter-spacing:1px;">🩺 HEALTH</a>
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


    <!-- LATENCY TRACKER -->
    <div class="panel" id="latency-panel" style="margin-bottom:10px;">
      <div class="panel-head" id="latency-head" style="background:linear-gradient(90deg,#0a1628,#0d1f3c);border-left:3px solid var(--green);">
        ⏱ LATENCY TRACKER — Slippage decisione→esecuzione
        <span id="latency-verdetto" style="float:right;font-size:9px;color:var(--green)">IN ATTESA DATI</span>
      </div>
      <div class="panel-body">
        <div style="font-size:9px;color:var(--dim);margin-bottom:8px">
          Misura il costo della latenza Render. Quando slippage EXPLOSIVE > 0.05% → serve VPS Frankfurt (€6/mese).
        </div>
        <!-- KPI principali -->
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:10px">
          <div style="background:#0a1628;border-radius:4px;padding:6px;text-align:center">
            <div style="font-size:9px;color:var(--dim)">SLIP MEDIO</div>
            <div id="lat-slip-medio" style="font-size:16px;font-weight:bold;color:var(--green)">—</div>
            <div style="font-size:8px;color:var(--dim)">tutti i trade</div>
          </div>
          <div style="background:#0a1628;border-radius:4px;padding:6px;text-align:center">
            <div style="font-size:9px;color:var(--dim)">SLIP EXPLOSIVE</div>
            <div id="lat-slip-exp" style="font-size:16px;font-weight:bold;color:var(--green)">—</div>
            <div style="font-size:8px;color:var(--dim)">contesto critico</div>
          </div>
          <div style="background:#0a1628;border-radius:4px;padding:6px;text-align:center">
            <div style="font-size:9px;color:var(--dim)">COSTO USD</div>
            <div id="lat-costo" style="font-size:16px;font-weight:bold;color:var(--orange)">—</div>
            <div style="font-size:8px;color:var(--dim)">perso per latenza</div>
          </div>
          <div style="background:#0a1628;border-radius:4px;padding:6px;text-align:center">
            <div style="font-size:9px;color:var(--dim)">COSTO EXPLOSIVE</div>
            <div id="lat-costo-exp" style="font-size:16px;font-weight:bold;color:var(--orange)">—</div>
            <div style="font-size:8px;color:var(--dim)">dove conta di più</div>
          </div>
        </div>
        <!-- Barra allarme VPS -->
        <div id="lat-allarme-bar" style="display:none;background:#ff3355;color:#fff;font-size:10px;font-weight:bold;padding:8px 12px;border-radius:4px;text-align:center;margin-bottom:8px;letter-spacing:1px;animation:pulse 1s infinite">
          🔴 SLIPPAGE CRITICO — SERVE VPS FRANKFURT — €6/MESE RISOLVE IL PROBLEMA
        </div>
        <!-- Storia ultimi eventi -->
        <div style="font-size:9px;letter-spacing:2px;color:var(--dim);margin-bottom:4px">ULTIMI EVENTI LATENZA</div>
        <div id="lat-storia" style="font-size:10px;max-height:100px;overflow-y:auto"></div>
      </div>
    </div>

    <!-- NARRATORE AI — Dialogo tra due AI -->
    <div class="panel" style="margin-bottom:10px;border-color:#a855f7;border-width:2px;" id="narratore-panel">
      <div class="panel-head" style="background:linear-gradient(90deg,#1a0a2e,#2d1060);border-left:3px solid #a855f7;color:#a855f7;">
        🎭 NARRATORE AI — Dialogo tra due intelligenze
        <span id="narratore-ts" style="float:right;font-size:9px;color:#6b21a8">--:--</span>
      </div>
      <div id="narratore-capsula-bar" style="display:none;padding:6px 12px;background:#1a0a2e;border-bottom:1px solid #3b0764;font-size:10px;font-family:monospace;">
        <span style="color:#a855f7">💊 ULTIMA CAPSULA:</span>
        <span id="narratore-cap-id" style="color:#c4b5fd;margin-left:6px;font-weight:bold"></span>
        <span id="narratore-cap-forza" style="color:#7c3aed;margin-left:8px"></span>
        <span id="narratore-cap-ts" style="color:#4c1d95;margin-left:8px;float:right"></span>
        <div id="narratore-cap-motivo" style="color:#6b21a8;margin-top:2px;font-size:9px"></div>
      </div>
      <!-- Pannello diagnostica canale Narratore→Bot -->
      <div id="narratore-diag" style="padding:6px 12px;background:#0d0519;border-bottom:1px solid #2d1060;font-size:10px;font-family:monospace;">
        <span style="color:#6b21a8;letter-spacing:1px;font-size:9px">📡 CANALE NARRATORE→BOT</span>
        <span id="nd-iniettate" style="color:#00d97a;margin-left:12px">iniettate: 0</span>
        <span id="nd-bloccate" style="color:#f59e0b;margin-left:12px">bloccate: 0</span>
        <span id="nd-ultima" style="color:#4c1d95;margin-left:12px">ultima: mai</span>
        <div id="nd-storia" style="margin-top:4px;color:#3d1a6e;font-size:9px;max-height:40px;overflow:hidden"></div>
      </div>
      <div class="panel-body" id="narratore-body">
        <div style="color:#3d1a6e;font-size:10px;font-family:monospace;text-align:center;padding:16px 0">
          In ascolto del mercato...
        </div>
      </div>
    </div>

    <!-- V16 MOTORI -->
    <div class="panel">
      <div class="panel-head" style="background:linear-gradient(90deg,#0a1628,#0d1f3c);border-left:3px solid var(--green)">
        🏎️ V16 — ASSETTO MERCATO
        <span id="v16-comparto-nome" style="float:right;font-size:10px;color:var(--green)">—</span>
      </div>
      <div class="panel-body">
        <!-- COMPARTO -->
        <div style="margin-bottom:10px">
          <div style="font-size:9px;letter-spacing:2px;color:var(--dim);margin-bottom:5px">COMPARTO ATTIVO</div>
          <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:4px" id="v16-comparti-grid"></div>
        </div>
        <!-- GOMME / NERVOSISMO -->
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:10px">
          <div class="stat-item">
            <span class="stat-lbl">GOMME</span>
            <span class="stat-val" id="v16-gomme" style="font-size:14px">—</span>
          </div>
          <div class="stat-item">
            <span class="stat-lbl">NERVOSISMO</span>
            <span class="stat-val" id="v16-nerv" style="font-size:14px">—</span>
          </div>
          <div class="stat-item">
            <span class="stat-lbl">BREATH FASE</span>
            <span class="stat-val" id="v16-breath" style="font-size:14px">—</span>
          </div>
        </div>
        <!-- BARRA NERVOSISMO -->
        <div style="margin-bottom:8px">
          <div style="font-size:9px;color:var(--dim);margin-bottom:3px">TENSIONE MERCATO</div>
          <div style="height:8px;background:#0a1020;border-radius:4px;overflow:hidden;position:relative">
            <div id="v16-nerv-bar" style="height:100%;width:30%;border-radius:4px;transition:width 0.5s,background 0.5s;background:var(--green)"></div>
            <!-- soglie -->
            <div style="position:absolute;top:0;left:25%;width:1px;height:100%;background:rgba(255,255,255,0.2)"></div>
            <div style="position:absolute;top:0;left:60%;width:1px;height:100%;background:rgba(255,255,255,0.2)"></div>
          </div>
          <div style="display:flex;justify-content:space-between;font-size:8px;color:var(--dim);margin-top:2px">
            <span>SLICK</span><span>INTER</span><span>RAIN</span>
          </div>
        </div>
        <!-- SWITCH LOG -->
        <div style="font-size:9px;letter-spacing:2px;color:var(--dim);margin-bottom:4px">ULTIMI SWITCH ASSETTO</div>
        <div id="v16-switch-log" style="max-height:80px;overflow-y:auto;font-size:10px"></div>
      </div>
    </div>

    <!-- PHANTOM SUPERVISOR -->
    <div class="panel">
      <div class="panel-head" style="background:linear-gradient(90deg,#0a1628,#0d1f3c);border-left:3px solid var(--orange)">
        🧠 PHANTOM SUPERVISOR — Autocorrezione
        <span id="v16-sup-interventi" style="float:right;font-size:9px;color:var(--orange)">0 interventi</span>
      </div>
      <div class="panel-body">
        <div style="font-size:9px;color:var(--dim);margin-bottom:6px">
          Il sistema legge il Phantom e si autocorregge. Ogni componente che blocca troppo o troppo poco viene bilanciato automaticamente.
        </div>
        <div id="v16-phantom-sup-log" style="max-height:120px;overflow-y:auto"></div>
        <!-- Phantom per livello -->
        <div style="font-size:9px;letter-spacing:2px;color:var(--dim);margin:8px 0 4px">PHANTOM PER COMPONENTE</div>
        <div id="v16-phantom-livelli" style="font-size:10px"></div>
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

  <!-- ANALISI TRADE — Perché abbiamo vinto o perso -->
  <div class="panel" style="margin-bottom:10px; border-color:#00aaff; border-width:2px;">
    <div class="panel-head blue">🔬 ANALISI TRADE — Perché abbiamo vinto o perso?
      <span style="font-size:9px;color:var(--dim)">Analisi AI di ogni trade chiuso</span>
    </div>
    <div class="panel-body" id="trade-analisi-body">
      <div style="color:var(--dim);font-size:10px;text-align:center;padding:12px">
        In attesa del primo trade...
      </div>
    </div>
  </div>

  <!-- ANALISI TRADE -->
  <div class="panel" style="margin-bottom:10px">
    <div class="panel-head blue">ANALISI TRADE</div>
    <div class="panel-body" id="trade-analisi-body">
      <div style="color:var(--dim);font-size:10px;text-align:center;padding:12px">In attesa del primo trade...</div>
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
  
    // ── V16 AGGIORNAMENTO ─────────────────────────────────────
    // COMPARTO
    const comparto = hb.comparto || 'NEUTRO';
    const compartiTutti = hb.comparti_tutti || [];
    const COMP_COL = {
      DIFENSIVO:'#6b7280', NEUTRO:'#3b82f6',
      ATTACCO:'#00d97a', TRENDING_BULL:'#00ff88', TRENDING_BEAR:'#ff3355'
    };
    $('v16-comparto-nome').textContent = comparto;
    $('v16-comparto-nome').style.color = COMP_COL[comparto] || '#3b82f6';

    // Grid comparti
    const compGrid = $('v16-comparti-grid');
    if (compGrid) {
      const nomi = ['DIFENSIVO','NEUTRO','ATTACCO','TRENDING_BULL','TRENDING_BEAR'];
      compGrid.innerHTML = nomi.map(n => {
        const attivo = n === comparto;
        const col = COMP_COL[n] || '#3b82f6';
        return `<div style="padding:4px 2px;border-radius:3px;text-align:center;
          background:${attivo ? col+'22' : '#0a1020'};
          border:1px solid ${attivo ? col : '#1e3a5f'};
          font-size:8px;color:${attivo ? col : '#3d5a7a'};
          font-weight:${attivo ? '700' : '400'}">
          ${n.replace('_',' ')}
        </div>`;
      }).join('');
    }

    // GOMME / NERVOSISMO
    const gomme   = hb.gomme || 'INTER';
    const nervVal = typeof hb.nervosismo === 'number' ? hb.nervosismo : 0.3;
    const breath  = hb.breath || {};
    const breathFase = breath.fase || 'NEUTRO';
    const GOMME_COL = {SLICK:'#00d97a', INTER:'#f59e0b', RAIN:'#3b82f6'};
    const gcol = GOMME_COL[gomme] || '#f59e0b';
    $('v16-gomme').textContent = gomme;
    $('v16-gomme').style.color = gcol;
    const NERV_COL = nervVal > 0.6 ? '#3b82f6' : nervVal > 0.3 ? '#f59e0b' : '#00d97a';
    $('v16-nerv').textContent = (nervVal * 100).toFixed(0) + '%';
    $('v16-nerv').style.color = NERV_COL;
    const BREATH_COL = {INALAZIONE:'#00d97a', PICCO:'#f59e0b', ESALAZIONE:'#ff3355', NEUTRO:'#3d5a7a'};
    $('v16-breath').textContent = breathFase;
    $('v16-breath').style.color = BREATH_COL[breathFase] || '#3d5a7a';

    // Barra nervosismo
    const nervBar = $('v16-nerv-bar');
    if (nervBar) {
      nervBar.style.width = Math.min(100, nervVal * 100) + '%';
      nervBar.style.background = NERV_COL;
    }

    // Switch log comparti
    const switchLog = hb.switch_log || [];
    const slEl = $('v16-switch-log');
    if (slEl && switchLog.length) {
      slEl.innerHTML = switchLog.slice(0,5).map(s =>
        `<div style="display:grid;grid-template-columns:45px 70px 70px 1fr;gap:4px;
          padding:2px 0;border-bottom:1px solid #0a1020;color:#3d5a7a">
          <span style="color:#1e3a5f">${s.ts||''}</span>
          <span style="color:${COMP_COL[s.da]||'#3d5a7a'}">${s.da||''}</span>
          <span style="color:#1e3a5f">→</span>
          <span style="color:${COMP_COL[s.a]||'#00d97a'};font-weight:700">${s.a||''}</span>
        </div>`
      ).join('');
    } else if (slEl) {
      slEl.innerHTML = '<div style="color:#1e3a5f;font-size:10px">Nessun switch ancora</div>';
    }

    // Phantom supervisor
    const supLog = hb.phantom_sup_log || [];
    $('v16-sup-interventi').textContent = supLog.length + ' interventi';
    const supEl = $('v16-phantom-sup-log');
    if (supEl) {
      if (supLog.length) {
        supEl.innerHTML = supLog.slice().reverse().map(s => {
          const isAllenta = s.azione && s.azione.includes('ALLENTA');
          const col = isAllenta ? '#00d97a' : '#ff3355';
          return `<div style="display:grid;grid-template-columns:45px 1fr 80px;gap:4px;
            padding:3px 0;border-bottom:1px solid #0a1020">
            <span style="color:#1e3a5f;font-size:9px">${s.ts||''}</span>
            <span style="color:#3d5a7a;font-size:10px">${(s.blocco||'').slice(0,25)}</span>
            <span style="color:${col};font-size:9px;font-weight:700">${s.azione||''}</span>
          </div>`;
        }).join('');
      } else {
        supEl.innerHTML = '<div style="color:#1e3a5f;font-size:10px">Sistema in osservazione — nessun intervento ancora</div>';
      }
    }

    // Phantom per livello
    const perLiv = (hb.phantom||{}).per_livello || {};
    const livEl = $('v16-phantom-livelli');
    if (livEl && Object.keys(perLiv).length) {
      livEl.innerHTML = Object.entries(perLiv).map(([id, d]) => {
        const tot = (d.would_win||0) + (d.would_lose||0);
        const wr  = tot > 0 ? Math.round(d.would_win/tot*100) : 0;
        const col = wr > 45 ? '#f59e0b' : wr < 25 ? '#ff3355' : '#3d5a7a';
        const alert = wr > 45 ? '⚠️ ALLENTA' : wr < 25 ? '🛡️ OK' : '';
        return `<div style="display:grid;grid-template-columns:1fr 40px 40px 40px 70px;
          gap:4px;padding:3px 0;border-bottom:1px solid #0a1020;align-items:center">
          <span style="color:#3d5a7a;font-size:9px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${id}</span>
          <span style="color:#1e3a5f;font-size:9px;text-align:center">${d.blocked||0}</span>
          <span style="color:${col};font-size:9px;text-align:center;font-weight:700">${wr}%</span>
          <span style="color:#1e3a5f;font-size:9px;text-align:center">WR</span>
          <span style="color:${col};font-size:8px">${alert}</span>
        </div>`;
      }).join('');
    }
    // ── FINE V16 ─────────────────────────────────────────────

    // ── NARRATORE AI ──────────────────────────────────────────
    const narrativa = hb.narrativa_ds || [];
    const narratoreBody = $('narratore-body');
    const narratoreTs   = $('narratore-ts');

    // Barra ultima capsula generata
    const ultCap = hb.narratore_ultima_capsula;
    const capBar = $('narratore-capsula-bar');
    if (ultCap && capBar) {
      capBar.style.display = 'block';
      const el = id => document.getElementById(id);
      if (el('narratore-cap-id'))     el('narratore-cap-id').textContent    = ultCap.id || '';
      if (el('narratore-cap-forza'))  el('narratore-cap-forza').textContent = `forza=${ultCap.forza?.toFixed(2) || '?'}`;
      if (el('narratore-cap-ts'))     el('narratore-cap-ts').textContent    = (ultCap.ts || '').slice(11,16);
      if (el('narratore-cap-motivo')) el('narratore-cap-motivo').textContent = ultCap.motivo || '';
    }

    // Diagnostica canale Narratore→Bot
    const diag = hb.narratore_diagnostica || {};
    const ndI = document.getElementById('nd-iniettate');
    const ndB = document.getElementById('nd-bloccate');
    const ndU = document.getElementById('nd-ultima');
    const ndS = document.getElementById('nd-storia');
    if (ndI) {
      const tot = diag.iniettate_tot || 0;
      ndI.textContent = `iniettate: ${tot}`;
      ndI.style.color = tot > 0 ? '#00d97a' : '#4c1d95';
    }
    if (ndB) ndB.textContent = `bloccate: ${diag.bloccate_tot || 0}`;
    if (ndU) {
      const ult = diag.ultima_iniettata;
      ndU.textContent = ult ? `ultima: ${ult.ids?.join(',')} @ ${ult.ts}` : 'ultima: mai';
      ndU.style.color = ult ? '#c4b5fd' : '#4c1d95';
    }
    if (ndS) {
      const storia = (diag.storia || []).slice(-3).reverse();
      ndS.innerHTML = storia.map(s =>
        `<span style="color:#6b21a8">${s.ts}</span> ` +
        `<span style="color:#00d97a">+[${s.iniettate?.join(',')||''}]</span>` +
        (s.bloccate?.length ? ` <span style="color:#f59e0b">✗[${s.bloccate.join(',')}]</span>` : '')
      ).join(' &nbsp;·&nbsp; ');
    }

    if (narrativa.length && narratoreBody) {
      const ultimo = narrativa[narrativa.length - 1];
      narratoreTs.textContent = ultimo.ts || '--:--';

      var atHtml = '';
      var atList = hb.trade_analisi || [];
      if (atList.length > 0) {
        var atOut = '<div style="margin-bottom:10px;padding:8px;background:#0a1a0a;border-left:3px solid #00ff88;border-radius:3px">';
        atOut += '<div style="font-size:9px;color:#00ff88;letter-spacing:1px;margin-bottom:6px">ANALIZZATORE TRADER</div>';
        var atItems = atList.slice(-3).reverse();
        for (var ai = 0; ai < atItems.length; ai++) {
          var at = atItems[ai];
          var atWin = at.esito === 'WIN';
          var atCol = atWin ? '#00ff88' : '#ff3355';
          var atBg = atWin ? 'rgba(0,255,136,0.04)' : 'rgba(255,51,85,0.04)';
          var atPnl = (at.pnl >= 0 ? '+' : '') + '$' + parseFloat(at.pnl||0).toFixed(2);
          var atCap = at.capsula ? ' C:' + at.capsula : '';
          var atTesto = (at.analisi || '');
          atOut += '<div style="margin-bottom:5px;padding:4px 6px;border-left:2px solid ' + atCol + ';background:' + atBg + '">';
          atOut += '<div style="font-size:9px;color:' + atCol + ';font-weight:bold">' + (atWin ? 'WIN' : 'LOSS') + ' ' + (at.ts||'') + ' score=' + parseFloat(at.score||0).toFixed(1) + ' ' + atPnl + atCap + '</div>';
          atOut += '<div style="font-size:10px;color:#aaa;line-height:1.5;margin-top:2px">' + atTesto + '</div>';
          atOut += '</div>';
        }
        atOut += '</div><hr style="border:none;border-top:1px solid #1a2a1a;margin:6px 0">';
        atHtml = atOut;
      }

      // Max 5 dialoghi, dal piu recente
      narratoreBody.innerHTML = atHtml + narrativa.slice(-5).reverse().map((n, i) => {
        const isUltimo = i === 0;
        const opacita = isUltimo ? '1' : `${0.85 - i * 0.15}`;

        // Badge capsula con dettaglio azione
        const capBadge = n.capsula
          ? `<span style="background:#3b0764;color:#c4b5fd;padding:2px 8px;border-radius:3px;font-size:9px;margin-left:6px;font-weight:bold">💊 ${n.capsula} → BOT</span>`
          : `<span style="color:#4c1d95;font-size:9px;margin-left:6px">○ nessuna capsula</span>`;

        return `<div style="margin-bottom:10px;opacity:${opacita};
          border-left:2px solid ${isUltimo ? '#a855f7' : '#3b0764'};
          padding-left:8px;">
          <div style="font-size:9px;color:#7c3aed;margin-bottom:3px;letter-spacing:1px;display:flex;align-items:center;flex-wrap:wrap;gap:4px">
            🔍 OSSERVATORE · ${n.ts} ${capBadge}
          </div>
          <div style="font-size:10px;color:#c4b5fd;font-style:italic;margin-bottom:4px;line-height:1.4">
            "${n.domanda}"
          </div>
          <div style="font-size:9px;color:#9333ea;margin-bottom:2px;letter-spacing:1px">💭 RAGIONATORE</div>
          <div style="font-size:10px;color:#e9d5ff;line-height:1.5">
            ${n.risposta}
          </div>
        </div>`;
      }).join('<hr style="border:none;border-top:1px solid #1a0a2e;margin:6px 0">');

    } else if (narratoreBody && !narrativa.length) {
      const hasDsKey = typeof hb.narrativa_ds !== 'undefined';
      narratoreBody.innerHTML = hasDsKey
        ? '<div style="color:#4c1d95;font-size:10px;text-align:center;padding:12px">Primo ciclo in arrivo tra 30s...</div>'
        : '<div style="color:#4c1d95;font-size:10px;text-align:center;padding:12px">DEEPSEEK_API_KEY non configurata</div>';
    }
    // ── FINE NARRATORE AI ─────────────────────────────────────

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
      var tsPart = line.substring(0,8); var tsMatch = (tsPart.length===8 && tsPart[2]===':') ? [tsPart,tsPart] : null;
      if (!tsMatch) return;
      const lineTs = new Date().toDateString() + ' ' + tsMatch[1];
      const ts = new Date(lineTs).getTime();
      if (line.includes('ENTRY')) {
        const dir = line.includes('SHORT') ? 'SHORT' : 'LONG';
        var scoreIdx2 = line.indexOf('score='); var scoreM = scoreIdx2>=0 ? [null, line.substring(scoreIdx2+6).split(' ')[0].split('/')[0]] : null;
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


# ═══════════════════════════════════════════════════════════════════════════
# SUPERVISOR ROUTES
# ═══════════════════════════════════════════════════════════════════════════

SUPERVISOR_HTML = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Command Center</title>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@400;700&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Share Tech Mono',monospace;background:#060810;color:#8a9bb0;min-height:100vh;padding:16px}
.hdr{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;padding-bottom:12px;border-bottom:1px solid #0f1a28}
.hdr-title{font-family:'Orbitron',sans-serif;font-size:16px;color:#8b5cf6;letter-spacing:3px}
.back-btn{background:rgba(59,130,246,0.2);border:1px solid rgba(59,130,246,0.4);color:#60a5fa;padding:6px 14px;border-radius:6px;font-size:11px;text-decoration:none;font-family:'Share Tech Mono',monospace}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin-bottom:20px}
.card{background:#0c1020;border:1px solid #0f1a28;border-radius:8px;padding:14px}
.card-title{font-size:10px;letter-spacing:2px;color:#1e3a5f;margin-bottom:8px}
.card-regime{font-size:12px;font-weight:700;margin-bottom:4px}
.card-score{font-size:11px;color:#3d5a7a;margin-bottom:6px}
.card-oi{font-size:11px}
.bar-wrap{height:4px;background:#0a1020;border-radius:2px;margin-top:8px;overflow:hidden}
.bar-fill{height:100%;border-radius:2px;transition:width .5s}
.ai-box{background:#0c1020;border:1px solid #0f1a28;border-radius:8px;padding:16px;margin-bottom:16px}
.ai-title{font-size:10px;letter-spacing:2px;color:#1e3a5f;margin-bottom:10px}
.ai-stato{font-size:18px;font-weight:700;margin-bottom:8px}
.sm-opportunita{color:#00ff88} .sm-attesa{color:#3b82f6} .sm-pericoloso{color:#ff3355} .sm-fermo{color:#6b7280} .sm-errore{color:#f59e0b}
.ai-analisi{font-size:12px;line-height:1.6;color:#8a9bb0;margin-bottom:8px}
.ai-azione{font-size:11px;color:#60a5fa;margin-bottom:6px}
.ai-trigger{font-size:10px;color:#3d5a7a}
.hist{background:#0c1020;border:1px solid #0f1a28;border-radius:8px;padding:14px}
.hist-title{font-size:10px;letter-spacing:2px;color:#1e3a5f;margin-bottom:10px;display:flex;justify-content:space-between}
.hist-entry{padding:6px 0;border-bottom:1px solid #0a1020;font-size:11px;display:grid;grid-template-columns:80px 140px 1fr;gap:8px}
.hist-entry:last-child{border-bottom:none}
.hist-ts{color:#1e3a5f} .hist-stato{font-weight:700}
.timer-bar{background:#0a1020;height:3px;border-radius:2px;margin-bottom:16px;overflow:hidden}
.timer-fill{height:100%;background:#8b5cf6;transition:width 1s linear}
.badge-best{background:rgba(0,255,136,0.15);color:#00ff88;padding:2px 8px;border-radius:3px;font-size:10px}
.badge-worst{background:rgba(255,51,85,0.15);color:#ff3355;padding:2px 8px;border-radius:3px;font-size:10px}
</style>
</head>
<body>
<div class="hdr">
  <div>
    <div class="hdr-title">⚡ COMMAND CENTER</div>
    <div style="font-size:10px;color:#1e3a5f;margin-top:4px">OVERTOP BASSANO V15 — AI SUPERVISOR MULTI-ASSET</div>
  </div>
  <a href="/" class="back-btn">← MISSION CONTROL</a>
</div>

<div class="timer-bar"><div class="timer-fill" id="next-fill" style="width:0%"></div></div>

<div class="cards" id="asset-cards">
  <div class="card" id="card-BTC">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <div class="card-title">BTCUSDC</div>
      <span id="badge-BTC"></span>
    </div>
    <div class="card-regime" id="btc-regime">—</div>
    <div class="card-score" id="btc-score">—</div>
    <div class="card-oi" id="btc-oi">—</div>
    <div style="display:flex;justify-content:space-between;font-size:10px;color:#1e3a5f;margin-top:6px">
      <span id="btc-trades">—</span><span id="btc-pnl">—</span><span id="btc-phantom">—</span>
    </div>
    <div class="bar-wrap"><div class="bar-fill" id="btc-dist" style="width:2%"></div></div>
  </div>
  <div class="card" id="card-SOL">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <div class="card-title">SOL/USDC</div>
      <span id="badge-SOL"></span>
    </div>
    <div class="card-regime" id="sol-regime">—</div>
    <div class="card-score" id="sol-score">—</div>
    <div class="card-oi" id="sol-oi">—</div>
    <div style="display:flex;justify-content:space-between;font-size:10px;color:#1e3a5f;margin-top:6px">
      <span id="sol-trades">—</span><span id="sol-pnl">—</span><span id="sol-phantom">—</span>
    </div>
    <div class="bar-wrap"><div class="bar-fill" id="sol-dist" style="width:2%"></div></div>
  </div>
  <div class="card" id="card-GOLD">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <div class="card-title">GOLD/USDT</div>
      <span id="badge-GOLD"></span>
    </div>
    <div class="card-regime" id="gold-regime">—</div>
    <div class="card-score" id="gold-score">—</div>
    <div class="card-oi" id="gold-oi">—</div>
    <div style="display:flex;justify-content:space-between;font-size:10px;color:#1e3a5f;margin-top:6px">
      <span id="gold-trades">—</span><span id="gold-pnl">—</span><span id="gold-phantom">—</span>
    </div>
    <div class="bar-wrap"><div class="bar-fill" id="gold-dist" style="width:2%"></div></div>
  </div>
</div>

<div class="ai-box" id="ai-decision">
  <div class="ai-title">🤖 AI SUPERVISOR — ANALISI CROSS-ASSET <span id="dec-tokens" style="float:right;color:#1e3a5f"></span></div>
  <div class="ai-stato sm-attesa" id="dec-stato">IN ATTESA</div>
  <div class="ai-analisi" id="dec-analisi">Caricamento...</div>
  <div class="ai-azione" id="dec-azione" style="display:none"></div>
  <div class="ai-trigger" id="dec-trigger"></div>
  <div style="font-size:10px;color:#1e3a5f;margin-top:8px"><span id="dec-badge"></span> · prossimo aggiornamento <span id="next-secs">—</span>s</div>
</div>

<div class="hist">
  <div class="hist-title"><span>STORICO DECISIONI AI</span><span id="hist-count" style="color:#1e3a5f"></span></div>
  <div id="history-body"></div>
</div>

<script>
const ASSETS = {BTC:'BTCUSDC',SOL:'SOLUSDC',GOLD:'XAUUSDT'};
let _hbCache={}, _lastResult={}, _historyLog=[], _callTimer=300;

async function fetchSupervisor() {
  try {
    const r = await fetch('/supervisor/result');
    const d = await r.json();
    _lastResult = d.result || {};
    _historyLog = d.log || [];
    _callTimer  = d.next_call_in || 300;
    const snaps = d.snapshots || {};
    for (const [sym, hb] of Object.entries(snaps)) {
      const key = sym.includes('BTC')?'BTC':sym.includes('SOL')?'SOL':sym.includes('XAU')||sym.includes('GOLD')?'GOLD':sym;
      _hbCache[key] = hb;
    }
    for (const [asset, hb] of Object.entries(_hbCache)) renderAsset(asset, hb);
    updateAIDecision();
    updateHistory();
  } catch(e) {}
}

function renderAsset(asset, hb) {
  if (!hb) return;
  const pfx = asset.toLowerCase();
  const regime=hb.regime||'—', score=hb.m2_last_score||0, soglia=hb.m2_last_soglia||60;
  const trades=hb.m2_trades||0, wins=hb.m2_wins||0, pnl=hb.m2_pnl||0;
  const oi=hb.oi_stato||'—', carica=hb.oi_carica||0;
  const phantom=hb.phantom||{}, bil=phantom.bilancio||0;
  const wr=trades>0?Math.round(wins/trades*100):0;
  const dist=score-soglia;
  const set=(id,v)=>{const el=document.getElementById(id);if(el)el.textContent=v};
  const col=(id,c)=>{const el=document.getElementById(id);if(el)el.style.color=c};
  set(pfx+'-regime',regime);
  set(pfx+'-score',score.toFixed(1)+'/'+soglia.toFixed(1)+' ('+( dist>=0?'+':'')+dist.toFixed(1)+')');
  set(pfx+'-oi',oi+' '+carica.toFixed(2));
  set(pfx+'-trades',trades+'t '+wr+'%');
  set(pfx+'-pnl','$'+(pnl>=0?'+':'')+pnl.toFixed(2));
  set(pfx+'-phantom','ph$'+(bil>=0?'+':'')+bil.toFixed(0));
  col(pfx+'-regime',regime==='EXPLOSIVE'?'#f59e0b':regime==='TRENDING_BULL'?'#00ff88':regime==='RANGING'?'#3b82f6':'#ff3355');
  col(pfx+'-oi',oi==='FUOCO'?'#00ff88':oi==='CARICA'?'#f59e0b':'#3d5a7a');
  const pct=Math.min(100,Math.max(2,(score/Math.max(1,soglia))*100));
  const fillEl=document.getElementById(pfx+'-dist');
  if(fillEl){fillEl.style.width=pct+'%';fillEl.style.background=dist>=0?'#00ff88':pct>=80?'#f59e0b':'#ff3355';}
}

function updateAIDecision() {
  const r=_lastResult;
  if (!r.stato_mercato) return;
  const smClass={'OPPORTUNITA':'sm-opportunita','ATTESA':'sm-attesa','PERICOLOSO':'sm-pericoloso','FERMO':'sm-fermo','ERRORE':'sm-errore'}[r.stato_mercato]||'sm-attesa';
  const stato=document.getElementById('dec-stato');
  if(stato){stato.textContent=r.stato_mercato;stato.className='ai-stato '+smClass;}
  const analisi=document.getElementById('dec-analisi');
  if(analisi)analisi.textContent=r.analisi||'—';
  const azione=document.getElementById('dec-azione');
  if(azione&&r.azione){azione.textContent='→ '+r.azione;azione.style.display='block';}
  const trigger=document.getElementById('dec-trigger');
  if(trigger)trigger.textContent=r.prossimo_trigger?'⏳ '+r.prossimo_trigger:'';
  const badge=document.getElementById('dec-badge');
  if(badge)badge.textContent='🤖 AI — '+(r.ts||'');
  const tokens=document.getElementById('dec-tokens');
  if(tokens&&r.tokens)tokens.textContent=r.tokens+' token';
  const box=document.getElementById('ai-decision');
  if(box)box.style.borderColor={'green':'rgba(0,255,136,0.3)','yellow':'rgba(245,158,11,0.3)','red':'rgba(255,51,85,0.3)'}[r.alert_level]||'#0f1a28';
  ['BTC','SOL','GOLD'].forEach(a=>{
    const card=document.getElementById('card-'+a);
    const bdg=document.getElementById('badge-'+a);
    if(!card)return;
    card.style.borderColor='#0f1a28';
    if(bdg)bdg.textContent='';
    if(a===r.asset_migliore){card.style.borderColor='rgba(0,255,136,0.4)';if(bdg){bdg.textContent='▲ MIGLIORE';bdg.className='badge-best';}}
    else if(a===r.asset_peggiore){card.style.borderColor='rgba(255,51,85,0.3)';if(bdg){bdg.textContent='▼ PEGGIORE';bdg.className='badge-worst';}}
  });
}

function updateHistory() {
  const body=document.getElementById('history-body');
  const cnt=document.getElementById('hist-count');
  if(!_historyLog.length)return;
  if(cnt)cnt.textContent=_historyLog.length+' analisi';
  if(body)body.innerHTML=_historyLog.map(r=>{
    const col=r.stato_mercato==='OPPORTUNITA'?'#00ff88':r.stato_mercato==='PERICOLOSO'?'#ff3355':r.stato_mercato==='FERMO'?'#3d5a7a':'#3b82f6';
    return '<div class="hist-entry"><span class="hist-ts">'+(r.ts||'—')+'</span><span class="hist-stato" style="color:'+col+'">'+(r.stato_mercato||'?')+' '+(r.asset_migliore?'→'+r.asset_migliore:'')+'</span><span style="color:#3d5a7a">'+(r.analisi||'').substring(0,80)+'</span></div>';
  }).join('');
}

function updateTimer() {
  _callTimer=Math.max(0,_callTimer-1);
  const el=document.getElementById('next-secs');
  if(el)el.textContent=_callTimer;
  const fill=document.getElementById('next-fill');
  if(fill)fill.style.width=((300-_callTimer)/300*100).toFixed(1)+'%';
}

fetchSupervisor();
setInterval(fetchSupervisor,5000);
setInterval(updateTimer,1000);
</script>
</body>
</html>"""

@app.route('/supervisor')
def supervisor_page():
    return render_template_string(SUPERVISOR_HTML)

@app.route('/supervisor/result')
def supervisor_result():
    if not _sv_new_ok or not sv_new:
        with heartbeat_lock:
            local_hb = dict(heartbeat_data)
        from OVERTOP_BASSANO_V15_PRODUCTION import SYMBOL as _SYM
        return jsonify({"result":{}, "log":[], "next_call_in":300,
                        "assets":[], "snapshots":{_SYM: local_hb}})
    snaps = sv_new.get_asset_snapshots()
    with heartbeat_lock:
        local_hb = dict(heartbeat_data)
    from OVERTOP_BASSANO_V15_PRODUCTION import SYMBOL as _SYM
    snaps[_SYM] = local_hb
    return jsonify({
        "result":       sv_new.get_last_result(),
        "log":          sv_new.get_log(),
        "next_call_in": sv_new.get_next_call_in(),
        "assets":       list(snaps.keys()),
        "snapshots":    snaps,
    })

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    log(f"[MAIN] 🚀 MISSION CONTROL V6.0 + AI BRIDGE — porta {port}")
    app.run(host='0.0.0.0', port=port, debug=False)

"""
MISSION CONTROL V5.1 — OVERTOP BASSANO
PIT STOP BIDIREZIONALE — l'auto non si ferma mai.

[V5.0 NUOVO rispetto a V4.3]
  - /trading/heartbeat     → MC sa sempre se il bot è vivo, capitale, modalità
  - GET /trading/commands  → bot chiede ogni 15s: ci sono ordini per me?
  - POST /trading/commands → dashboard/operatore crea comandi live
  - /trading/commands/<id>/ack → bot conferma esecuzione
  - /trading/pitstop       → endpoint rapido per azioni dashboard con 1 click
  - Dashboard V5.0: pannello PIT STOP con bottoni live, slider CONFIG, status bot
  - Capsule generate dal brain vengono INIETTATE via INJECT_CAPSULE (non solo salvate)
  - Tutto V4.3 invariato: DB, analisi, brain_report, capsule engine
"""

from flask import Flask, request, jsonify
import openai
import os, json, time, uuid, threading, sqlite3
from datetime import datetime, timedelta
from collections import deque, defaultdict
from typing import List, Dict

app   = Flask(__name__)
# ============================================================
# [V5.1] AI BRAIN — OpenAI nel loop
# ============================================================

openai.api_key = os.environ.get("OPENAI_API_KEY")
AI_MODEL       = "gpt-4o"
AI_MAX_TOKENS  = 1500
AI_TRIGGER_N   = 10      # analisi automatica ogni N trade nuovi
AI_TRIGGER_MIN = 15      # o ogni N minuti
_ai_ultimo_trigger_trades = 0
_ai_ultimo_trigger_ts     = 0.0
AI_LOG = deque(maxlen=100)

SYSTEM_PROMPT = """Sei il cervello AI del sistema di trading OVERTOP BASSANO.
Ragiona come il miglior trader quantitativo al mondo con expertise su:
- Trading algoritmico BTC/USDC su Binance spot
- Analisi di momentum e impulsi di mercato  
- Gestione del rischio e position sizing dinamico
- Pattern recognition su timeframe 1-60 secondi

Il bot opera in due modalità:
- NORMAL: mercato con movimento (range alto)
- FLAT: mercato laterale (range basso)

Parametri modificabili:
- FANTASMA_WR: soglia WR minimo per entrare (default 0.40)
- SEED_THRESH_NORMAL: qualità minima segnale NORMAL (default 0.45)
- SEED_THRESH_FLAT: qualità minima segnale FLAT (default 0.50)

Comandi disponibili: SET_CONFIG, INJECT_CAPSULE, STOP, RESUME, RESET_LOSSES
Rispondi SEMPRE e SOLO con JSON valido, zero testo fuori dal JSON."""


_lock = threading.Lock()

# ============================================================
# DATABASE — SQLite (invariato da V4.3)
# ============================================================

DB_PATH = os.environ.get("DB_PATH", "/tmp/overtop_mission.db")

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset TEXT, pnl REAL, win INTEGER, regime TEXT, ora_utc INTEGER,
            forza REAL, seed REAL, modalita TEXT, duration REAL, reason TEXT,
            loss_consecutivi INTEGER DEFAULT 0, entry_ts REAL,
            funding_rate REAL DEFAULT 0, open_interest REAL DEFAULT 0,
            bid_wall REAL DEFAULT 0, ask_wall REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS capsule (
            capsule_id TEXT PRIMARY KEY, version INTEGER DEFAULT 1,
            descrizione TEXT, trigger_json TEXT, azione_json TEXT,
            priority INTEGER DEFAULT 2, enabled INTEGER DEFAULT 1,
            source TEXT DEFAULT 'server_analyzer',
            hits INTEGER DEFAULT 0, wins_after INTEGER DEFAULT 0,
            losses_after INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )""")
    # [V5.0] Tabella comandi bidirezionali
    conn.execute("""
        CREATE TABLE IF NOT EXISTS commands (
            id TEXT PRIMARY KEY, type TEXT, params_json TEXT,
            status TEXT DEFAULT 'PENDING',
            created_at TEXT DEFAULT (datetime('now')), acked_at TEXT
        )""")
    conn.commit(); conn.close()
    print("[DB] V5.0 — tabelle OK")

# ============================================================
# STORAGE IN-MEMORY
# ============================================================

TRADING_EVENTS  = deque(maxlen=5000)
TRADES_COMPLETI = deque(maxlen=5000)
CAPSULE_ATTIVE: List[dict] = []
ANALISI_LOG     = deque(maxlen=200)
COMMANDS: Dict[str, dict] = {}   # [V5.0] comandi bidirezionali

LAST_MARKET = {
    "funding_rate": 0.0, "open_interest": 0.0,
    "bid_wall": 0.0, "bid_wall_price": 0.0,
    "ask_wall": 0.0, "ask_wall_price": 0.0, "updated_at": None,
}

TRADING_CONFIG = {
    "RISK_PER_TRADE": 0.015, "NORMAL_MIN_FORZA": 0.55, "NORMAL_VETO_WR": 0.50,
    "FLAT_MIN_FORZA": 0.65, "FLAT_VETO_WR": 0.30,
    "SW_FLAT_THRESHOLD": 0.0028, "SEED_THRESH_NORMAL": 0.45, "SEED_THRESH_FLAT": 0.50,
    "FANTASMA_WR": 0.40, "FANTASMA_PNL": -0.05,
    "META_ACC_THRESHOLD": 0.55, "META_REDUCTION": 0.8, "TAKE_PROFIT_R": 0.65,
    "last_updated": None, "version": "5.0-PITSTOP", "capsules": [],
}

BOT_STATUS = {
    "is_running": False, "last_ping": None,
    "total_trades": 0, "total_pnl": 0.0, "wins": 0, "losses": 0,
    "ultima_analisi": None, "capsule_generate": 0,
}

# [V5.0] Heartbeat live dal bot
BOT_HEARTBEAT = {
    "status": "UNKNOWN", "capital": 0.0, "trades": 0,
    "wins": 0, "losses": 0, "win_rate": 0.0,
    "modalita": "?", "posizione_aperta": False,
    "last_seen": None, "secondi_fa": None,
}

# ============================================================
# DB HELPERS (invariati da V4.3)
# ============================================================

def db_salva_trade(t):
    try:
        conn = get_db()
        conn.execute("""INSERT INTO trades
            (asset,pnl,win,regime,ora_utc,forza,seed,modalita,duration,reason,
             loss_consecutivi,entry_ts,funding_rate,open_interest,bid_wall,ask_wall)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (t.get("asset","BTCUSDC"), t.get("pnl",0), 1 if t.get("win") else 0,
             t.get("regime","unknown"), t.get("ora_utc",0), t.get("forza",0),
             t.get("seed",0), t.get("modalita","NORMAL"), t.get("duration",0),
             t.get("reason",""), t.get("loss_consecutivi",0), t.get("entry_ts",time.time()),
             t.get("funding_rate",0), t.get("open_interest",0),
             t.get("bid_wall",0), t.get("ask_wall",0)))
        conn.commit(); conn.close()
    except Exception as e:
        print(f"[DB] trade: {e}")

def db_salva_capsula(cap):
    try:
        conn = get_db()
        conn.execute("""INSERT OR REPLACE INTO capsule
            (capsule_id,version,descrizione,trigger_json,azione_json,priority,
             enabled,source,hits,wins_after,losses_after,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (cap["capsule_id"], cap.get("version",1), cap.get("descrizione",""),
             json.dumps(cap.get("trigger",[])), json.dumps(cap.get("azione",{})),
             cap.get("priority",2), 1 if cap.get("enabled",True) else 0,
             cap.get("source","server_analyzer"),
             cap.get("hits",0), cap.get("wins_after",0), cap.get("losses_after",0)))
        conn.commit(); conn.close()
    except Exception as e:
        print(f"[DB] capsula: {e}")

def db_carica_capsule():
    try:
        conn = get_db()
        rows = conn.execute("SELECT * FROM capsule WHERE enabled=1 ORDER BY priority").fetchall()
        conn.close()
        return [{"capsule_id": r["capsule_id"], "version": r["version"],
                 "descrizione": r["descrizione"],
                 "trigger": json.loads(r["trigger_json"] or "[]"),
                 "azione": json.loads(r["azione_json"] or "{}"),
                 "priority": r["priority"], "enabled": bool(r["enabled"]),
                 "source": r["source"], "hits": r["hits"],
                 "wins_after": r["wins_after"], "losses_after": r["losses_after"]} for r in rows]
    except Exception as e:
        print(f"[DB] carica capsule: {e}"); return []

def db_carica_trades(limit=5000):
    try:
        conn = get_db()
        rows = conn.execute("SELECT * FROM trades ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in reversed(rows)]
    except Exception as e:
        print(f"[DB] carica trades: {e}"); return []

def db_disabilita_capsula(capsule_id):
    try:
        conn = get_db()
        conn.execute("UPDATE capsule SET enabled=0 WHERE capsule_id=?", (capsule_id,))
        conn.commit(); conn.close()
    except Exception as e:
        print(f"[DB] disabilita: {e}")

def db_conta_trades():
    try:
        conn = get_db()
        n = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        conn.close(); return n
    except: return 0

def _fonte_migliore():
    db = db_carica_trades(limit=10000)
    mem = list(TRADES_COMPLETI)
    return db if len(db) >= len(mem) else mem

# ============================================================
# [V5.0] GESTIONE COMANDI BIDIREZIONALI
# ============================================================

def _crea_comando(tipo: str, params: dict = None) -> dict:
    """Crea un comando per il bot e lo mette in coda."""
    cmd_id = str(uuid.uuid4())[:8]
    cmd = {
        "id": cmd_id, "type": tipo.upper(), "params": params or {},
        "status": "PENDING", "created_at": datetime.now().isoformat(), "acked_at": None,
    }
    with _lock:
        COMMANDS[cmd_id] = cmd
    try:
        conn = get_db()
        conn.execute("INSERT INTO commands (id,type,params_json,status) VALUES(?,?,?,?)",
                     (cmd_id, tipo.upper(), json.dumps(params or {}), "PENDING"))
        conn.commit(); conn.close()
    except: pass
    print(f"[V5.0] 📡 CMD → bot: {tipo} | {params}")
    return cmd

def _inietta_capsule_via_cmd(capsule: dict):
    """Quando il brain genera una capsule, la manda LIVE al bot."""
    return _crea_comando("INJECT_CAPSULE", {"capsule": capsule})

# ============================================================
# ANALISI — CERVELLO (invariato da V4.3, + iniezione live)
# ============================================================

def _wr_pnl(trades):
    if not trades: return 0.0, 0.0
    wins = sum(1 for t in trades if t.get('win', t.get('pnl', 0) > 0))
    return wins / len(trades), sum(t.get('pnl', 0) for t in trades)

def analizza_e_genera_capsule(trades):
    if len(trades) < 10: return []
    capsule = []
    wr_globale, _ = _wr_pnl(trades)
    ids_esistenti = {c['capsule_id'] for c in CAPSULE_ATTIVE}
    MIN_CAMP = 8; MIN_DELTA = 0.10

    fasce_forza = [
        ('f_sotto_20', lambda f: f < 0.20, 0, 0.20),
        ('f_20_35', lambda f: 0.20 <= f < 0.35, 0.20, 0.35),
        ('f_35_50', lambda f: 0.35 <= f < 0.50, 0.35, 0.50),
        ('f_50_65', lambda f: 0.50 <= f < 0.65, 0.50, 0.65),
        ('f_65_80', lambda f: 0.65 <= f < 0.80, 0.65, 0.80),
        ('f_sopra_80', lambda f: f >= 0.80, 0.80, 2.0),
    ]

    forza_rep = []
    for fname, check, fmin, fmax in fasce_forza:
        gruppo = [t for t in trades if check(t.get('forza', 0.5))]
        if len(gruppo) < MIN_CAMP: continue
        wr, pnl = _wr_pnl(gruppo)
        forza_rep.append((fname, wr, pnl, len(gruppo)))
        trig = ([{"param":"forza","op":"<","value":fmax}] if fmin == 0
                else [{"param":"forza","op":">=","value":fmin},{"param":"forza","op":"<","value":fmax}])
        if wr < 0.38 and pnl < -8:
            cid = f"AUTO_BLOCCO_FORZA_{fname.upper()}_001"
            if cid not in ids_esistenti:
                capsule.append({"capsule_id": cid, "version": 1,
                    "descrizione": f"AUTO-BRAIN: forza {fname} WR={wr:.0%} PnL={pnl:+.2f} su {len(gruppo)} trade — BLOCCO",
                    "trigger": trig, "azione": {"type":"blocca_entry","params":{"reason":f"auto_forza_{fname}"}},
                    "priority": 2, "enabled": True, "source": "brain_v50",
                    "hits": 0, "wins_after": 0, "losses_after": 0})
        elif wr > 0.68 and pnl > 8:
            cid = f"AUTO_BOOST_FORZA_{fname.upper()}_001"
            if cid not in ids_esistenti:
                capsule.append({"capsule_id": cid, "version": 1,
                    "descrizione": f"AUTO-BRAIN: forza {fname} WR={wr:.0%} — BOOST +20%",
                    "trigger": trig, "azione": {"type":"modifica_size","params":{"mult":1.20}},
                    "priority": 3, "enabled": True, "source": "brain_v50",
                    "hits": 0, "wins_after": 0, "losses_after": 0})

    if forza_rep:
        ANALISI_LOG.append("[BRAIN] Forza: " + " | ".join(f"{f[0]}={f[1]:.0%}({f[2]:+.1f}$,n={f[3]})" for f in forza_rep))

    for sname, check, smin, smax in [
        ('seed_basso', lambda s: s < 0.45, 0, 0.45),
        ('seed_medio', lambda s: 0.45 <= s < 0.55, 0.45, 0.55),
        ('seed_alto', lambda s: 0.55 <= s < 0.65, 0.55, 0.65),
        ('seed_ottimo', lambda s: s >= 0.65, 0.65, 2.0),
    ]:
        gruppo = [t for t in trades if check(t.get('seed', 0.5))]
        if len(gruppo) < MIN_CAMP: continue
        wr, pnl = _wr_pnl(gruppo)
        cid = f"AUTO_BLOCCO_SEED_{sname.upper()}_001"
        trig = ([{"param":"seed","op":"<","value":smax}] if smin == 0
                else [{"param":"seed","op":">=","value":smin},{"param":"seed","op":"<","value":smax}])
        if wr < 0.38 and pnl < -8 and cid not in ids_esistenti:
            capsule.append({"capsule_id": cid, "version": 1,
                "descrizione": f"AUTO-BRAIN: seed {sname} WR={wr:.0%} — BLOCCO",
                "trigger": trig, "azione": {"type":"blocca_entry","params":{"reason":f"auto_seed_{sname}"}},
                "priority": 2, "enabled": True, "source": "brain_v50",
                "hits": 0, "wins_after": 0, "losses_after": 0})

    regimi = defaultdict(list)
    for t in trades:
        if t.get('regime'): regimi[t['regime']].append(t)
    for regime, gruppo in regimi.items():
        if len(gruppo) < MIN_CAMP: continue
        wr, pnl = _wr_pnl(gruppo)
        if wr < 0.35 and wr < wr_globale - MIN_DELTA:
            cid = f"AUTO_BLOCCO_REGIME_{regime.upper()}_001"
            if cid not in ids_esistenti:
                capsule.append({"capsule_id": cid, "version": 1,
                    "descrizione": f"AUTO-BRAIN: regime {regime} WR={wr:.0%} PnL={pnl:+.2f} — BLOCCO",
                    "trigger": [{"param":"regime","op":"==","value":regime}],
                    "azione": {"type":"blocca_entry","params":{"reason":f"auto_regime_{regime}"}},
                    "priority": 1, "enabled": True, "source": "brain_v50",
                    "hits": 0, "wins_after": 0, "losses_after": 0})
        elif wr > 0.70 and wr > wr_globale + MIN_DELTA:
            cid = f"AUTO_BOOST_REGIME_{regime.upper()}_001"
            if cid not in ids_esistenti:
                capsule.append({"capsule_id": cid, "version": 1,
                    "descrizione": f"AUTO-BRAIN: regime {regime} WR={wr:.0%} — BOOST +25%",
                    "trigger": [{"param":"regime","op":"==","value":regime}],
                    "azione": {"type":"modifica_size","params":{"mult":1.25}},
                    "priority": 3, "enabled": True, "source": "brain_v50",
                    "hits": 0, "wins_after": 0, "losses_after": 0})

    for modalita in ['FLAT', 'NORMAL']:
        gm = [t for t in trades if t.get('modalita') == modalita]
        if len(gm) < MIN_CAMP: continue
        for fname, check, fmin, fmax in fasce_forza:
            gruppo = [t for t in gm if check(t.get('forza', 0.5))]
            if len(gruppo) < 5: continue
            wr, pnl = _wr_pnl(gruppo)
            cid = f"AUTO_BLOCCO_{modalita}_FORZA_{fname.upper()}_001"
            if wr < 0.35 and pnl < -5 and cid not in ids_esistenti:
                trig = ([{"param":"forza","op":"<","value":fmax}] if fmin == 0
                        else [{"param":"forza","op":">=","value":fmin},{"param":"forza","op":"<","value":fmax}])
                capsule.append({"capsule_id": cid, "version": 1,
                    "descrizione": f"AUTO-BRAIN: {modalita}+forza {fname} WR={wr:.0%} — BLOCCO",
                    "trigger": [{"param":"modalita","op":"==","value":modalita}] + trig,
                    "azione": {"type":"blocca_entry","params":{"reason":f"auto_{modalita.lower()}_forza_{fname}"}},
                    "priority": 2, "enabled": True, "source": "brain_v50",
                    "hits": 0, "wins_after": 0, "losses_after": 0})

    for fname, (h0, h1) in {
        'mattina':(8,12),'pomeriggio':(12,16),'sera':(16,20),
        'notte_eu':(20,24),'notte_tarda':(0,4),'alba':(4,8)
    }.items():
        gruppo = [t for t in trades if h0 <= t.get('ora_utc', 0) < h1]
        if len(gruppo) < MIN_CAMP: continue
        wr, pnl = _wr_pnl(gruppo)
        cid = f"AUTO_BLOCCO_ORA_{fname.upper()}_001"
        if wr < 0.35 and pnl < -10 and cid not in ids_esistenti:
            capsule.append({"capsule_id": cid, "version": 1,
                "descrizione": f"AUTO-BRAIN: fascia {fname} WR={wr:.0%} — BLOCCO",
                "trigger": [{"param":"ora_utc","op":">=","value":h0},{"param":"ora_utc","op":"<","value":h1}],
                "azione": {"type":"blocca_entry","params":{"reason":f"auto_ora_{fname}"}},
                "priority": 2, "enabled": True, "source": "brain_v50",
                "hits": 0, "wins_after": 0, "losses_after": 0})

    per_asset = defaultdict(list)
    for t in sorted(trades, key=lambda x: x.get('entry_ts', time.time())):
        per_asset[t.get('asset','ALL')].append(t)
    for asset, seq in per_asset.items():
        after3 = [seq[i] for i in range(3, len(seq))
                  if not seq[i-1].get('win') and not seq[i-2].get('win') and not seq[i-3].get('win')]
        if len(after3) >= 5:
            wr, _ = _wr_pnl(after3)
            cid = f"AUTO_BLOCCO_3LOSS_{asset}_001"
            if wr < 0.40 and cid not in ids_esistenti:
                capsule.append({"capsule_id": cid, "version": 1,
                    "descrizione": f"AUTO-BRAIN: dopo 3 loss su {asset} WR={wr:.0%} — PAUSA",
                    "trigger": [{"param":"loss_consecutivi","op":">=","value":3},{"param":"asset","op":"==","value":asset}],
                    "azione": {"type":"blocca_entry","params":{"reason":"auto_3loss_pausa"}},
                    "priority": 2, "enabled": True, "source": "brain_v50",
                    "hits": 0, "wins_after": 0, "losses_after": 0})

    for regime in ['lateral','choppy','normal','trending']:
        for modalita in ['FLAT','NORMAL']:
            gruppo = [t for t in trades if t.get('regime')==regime and t.get('modalita')==modalita]
            if len(gruppo) < 5: continue
            wr, pnl = _wr_pnl(gruppo)
            cid = f"AUTO_COMBO_{regime.upper()}_{modalita}_001"
            if wr < 0.33 and pnl < -8 and cid not in ids_esistenti:
                capsule.append({"capsule_id": cid, "version": 1,
                    "descrizione": f"AUTO-BRAIN: combo {regime}+{modalita} WR={wr:.0%} — BLOCCO",
                    "trigger": [{"param":"regime","op":"==","value":regime},{"param":"modalita","op":"==","value":modalita}],
                    "azione": {"type":"blocca_entry","params":{"reason":f"auto_combo_{regime}_{modalita.lower()}"}},
                    "priority": 2, "enabled": True, "source": "brain_v50",
                    "hits": 0, "wins_after": 0, "losses_after": 0})

    return capsule

def _applica_capsule_nuove(nuove):
    with _lock:
        aggiunte = 0
        for cap in nuove:
            if not any(c['capsule_id'] == cap['capsule_id'] for c in CAPSULE_ATTIVE):
                CAPSULE_ATTIVE.append(cap)
                db_salva_capsula(cap)
                # [V5.0] PIT STOP LIVE — capsule iniettata nel bot in tempo reale
                _inietta_capsule_via_cmd(cap)
                aggiunte += 1
        TRADING_CONFIG['capsules'] = list(CAPSULE_ATTIVE)
        BOT_STATUS['ultima_analisi'] = datetime.now().isoformat()
        BOT_STATUS['capsule_generate'] += aggiunte
    return aggiunte

_ultimo_trigger_ts = 0.0

def trigger_analisi_se_pronto(snap):
    global _ultimo_trigger_ts
    now = time.time()
    if now - _ultimo_trigger_ts < 60: return
    _ultimo_trigger_ts = now
    try:
        nuove = analizza_e_genera_capsule(snap)
        n = _applica_capsule_nuove(nuove)
        if n > 0:
            msg = f"[TRIGGER {datetime.now().strftime('%H:%M')}] +{n} capsule → inviate al bot via INJECT_CAPSULE"
            ANALISI_LOG.append(msg); print(msg)
    except Exception as e:
        print(f"[TRIGGER] {e}")

def thread_analisi_periodica():
    time.sleep(60)
    while True:
        time.sleep(300)
        try:
            source = _fonte_migliore()
            if len(source) < 10: continue
            nuove = analizza_e_genera_capsule(source)
            n = _applica_capsule_nuove(nuove)
            wr, pnl = _wr_pnl(source)
            msg = f"[{datetime.now().strftime('%H:%M')}] 🧠 {len(source)} trade | WR={wr:.0%} PnL={pnl:+.2f} | +{n} capsule al bot"
            ANALISI_LOG.append(msg); print(msg)
        except Exception as e:
            print(f"[ANALISI] {e}")

# ============================================================
# STARTUP
# ============================================================

def startup():
    try:
        init_db()
        caps = db_carica_capsule()
        with _lock:
            CAPSULE_ATTIVE.extend(caps)
            TRADING_CONFIG['capsules'] = list(CAPSULE_ATTIVE)
        trades = db_carica_trades(limit=2000)
        with _lock:
            TRADES_COMPLETI.extend(trades)
            if trades:
                BOT_STATUS['total_trades'] = db_conta_trades()
                BOT_STATUS['wins']   = sum(1 for t in trades if t.get('win'))
                BOT_STATUS['losses'] = sum(1 for t in trades if not t.get('win'))
                BOT_STATUS['total_pnl'] = sum(t.get('pnl', 0) for t in trades)
        msg = f"[STARTUP V5.0] ✅ {len(caps)} capsule, {len(trades)} trade"
        ANALISI_LOG.append(msg); print(msg)
    except Exception as e:
        print(f"[STARTUP] ⚠️ {e}")



# ============================================================
# [V5.1] FUNZIONI AI
# ============================================================

def _build_ai_prompt(trades, mercato, capsule, status, hb):
    ultimi = trades[-30:] if len(trades) >= 30 else trades
    if ultimi:
        wins   = sum(1 for t in ultimi if t.get('win'))
        wr     = wins / len(ultimi) * 100
        pnl    = sum(t.get('pnl', 0) for t in ultimi)
        seq    = ' '.join(['✅' if t.get('win') else '❌' for t in ultimi[-5:]])
        flat_t = [t for t in ultimi if t.get('modalita') == 'FLAT']
        norm_t = [t for t in ultimi if t.get('modalita') == 'NORMAL']
        wr_f   = sum(1 for t in flat_t if t.get('win')) / len(flat_t) * 100 if flat_t else 0
        wr_n   = sum(1 for t in norm_t if t.get('win')) / len(norm_t) * 100 if norm_t else 0
        reasons = {}
        for t in ultimi[-10:]:
            r = t.get('reason','?')
            reasons[r] = reasons.get(r, 0) + 1
    else:
        wins = wr = pnl = wr_f = wr_n = 0
        seq = 'N/A'; reasons = {}

    cap_str = chr(10).join([f"- {c['capsule_id']}: {c.get('descrizione','')[:50]}" for c in capsule[:6]])
    trade_str = chr(10).join([
        f"{'✅' if t.get('win') else '❌'} {t.get('modalita','?')} F:{t.get('forza',0):.2f} S:{t.get('seed',0):.2f} {t.get('reason','?')} {t.get('pnl',0):+.3f}$"
        for t in ultimi[-5:]
    ])

    return f"""Analizza la situazione attuale del bot OVERTOP BASSANO e decidi se intervenire.

STATO BOT: {hb.get('status','?')} | Capitale: ${hb.get('capital',0):.2f} | Modalità: {hb.get('modalita','?')} | Posizione: {'APERTA' if hb.get('posizione_aperta') else 'CHIUSA'}

PERFORMANCE ULTIMI {len(ultimi)} TRADE:
WR={wr:.1f}% W:{wins} L:{len(ultimi)-wins} | PnL={pnl:+.2f}$ | Sequenza: {seq}
WR FLAT={wr_f:.1f}% ({len(flat_t)} trade) | WR NORMAL={wr_n:.1f}% ({len(norm_t)} trade)
Exit reasons: {reasons}

MERCATO: FR={mercato.get('funding_rate',0):.4%} OI={mercato.get('open_interest',0)/1000:.1f}K
AskWall={mercato.get('ask_wall',0):.1f}BTC@${mercato.get('ask_wall_price',0):.0f}
BidWall={mercato.get('bid_wall',0):.1f}BTC@${mercato.get('bid_wall_price',0):.0f}

CAPSULE ATTIVE ({len(capsule)}):
{cap_str}

ULTIMI 5 TRADE:
{trade_str}

Rispondi con questo JSON esatto:
{{
  "valutazione": "OTTIMO|BUONO|ATTENZIONE|CRITICO",
  "ragionamento": "cosa vedi in 2-3 frasi",
  "intervieni": true/false,
  "comandi": [],
  "messaggio_operatore": "messaggio per Roberto in italiano"
}}
Se intervieni, popola comandi con SET_CONFIG o INJECT_CAPSULE.
Se non intervieni, comandi = []."""


def chiedi_ai(motivo: str = "automatico") -> dict:
    global _ai_ultimo_trigger_ts, _ai_ultimo_trigger_trades
    try:
        with _lock:
            trades  = list(TRADES_COMPLETI)
            mercato = dict(LAST_MARKET)
            capsule = list(CAPSULE_ATTIVE)
            status  = dict(BOT_STATUS)
            hb      = dict(BOT_HEARTBEAT)
        if len(trades) < 5:
            return {"error": "Troppo pochi trade", "n_trade": len(trades)}
        prompt   = _build_ai_prompt(trades, mercato, capsule, status, hb)
        client   = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model=AI_MODEL, max_tokens=AI_MAX_TOKENS, temperature=0.3,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ]
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
        risultato = json.loads(raw.strip())
        msg = (f"[AI {datetime.now().strftime('%H:%M')} | {motivo}] "
               f"{risultato.get('valutazione','?')} — "
               f"{risultato.get('ragionamento','')[:120]}")
        AI_LOG.append(msg); ANALISI_LOG.append(msg); print(msg)
        cmds = []
        if risultato.get("intervieni") and risultato.get("comandi"):
            for cmd in risultato["comandi"]:
                c = _crea_comando(cmd["type"], cmd.get("params", {}))
                cmds.append(c["id"])
                print(f"[AI] 📡 Comando: {cmd['type']}")
        _ai_ultimo_trigger_ts     = time.time()
        _ai_ultimo_trigger_trades = len(trades)
        return {
            "ok": True, "motivo": motivo,
            "valutazione":  risultato.get("valutazione"),
            "ragionamento": risultato.get("ragionamento"),
            "intervieni":   risultato.get("intervieni", False),
            "comandi_creati": cmds,
            "messaggio":    risultato.get("messaggio_operatore", ""),
            "n_trade":      len(trades),
            "timestamp":    datetime.now().isoformat(),
        }
    except Exception as e:
        err = f"[AI] Errore: {e}"; AI_LOG.append(err); print(err)
        return {"error": str(e)}


def _check_ai_trigger(n_trades_ora: int):
    global _ai_ultimo_trigger_trades, _ai_ultimo_trigger_ts
    nuovi  = n_trades_ora - _ai_ultimo_trigger_trades
    minuti = (time.time() - _ai_ultimo_trigger_ts) / 60 if _ai_ultimo_trigger_ts > 0 else 999
    if nuovi >= AI_TRIGGER_N or minuti >= AI_TRIGGER_MIN:
        threading.Thread(target=chiedi_ai, args=("automatico",), daemon=True).start()

startup()
threading.Thread(target=thread_analisi_periodica, daemon=True).start()

# ============================================================
# ENDPOINTS V4.3 — INVARIATI
# ============================================================

@app.route("/trading/log", methods=["POST"])
def trading_log():
    try:
        data = request.get_json()
        if not data: return jsonify({"error": "No JSON"}), 400
        event = {**data, "received_at": datetime.now().isoformat()}
        with _lock:
            TRADING_EVENTS.append(event)
            BOT_STATUS["last_ping"] = datetime.now().isoformat()
            BOT_STATUS["is_running"] = True
            if data.get("event_type") == "MARKET_DATA":
                LAST_MARKET.update({k: data.get(k, 0) for k in
                    ["funding_rate","open_interest","bid_wall","bid_wall_price","ask_wall","ask_wall_price"]})
                LAST_MARKET["updated_at"] = datetime.now().isoformat()
            if data.get("event_type") == "EXIT":
                pnl = data.get("pnl", 0)
                BOT_STATUS["total_trades"] += 1
                BOT_STATUS["total_pnl"] += pnl
                if pnl > 0: BOT_STATUS["wins"] += 1
                else: BOT_STATUS["losses"] += 1
                trade = {
                    "asset": data.get("asset","BTCUSDC"), "pnl": pnl, "win": pnl > 0,
                    "regime": data.get("regime","unknown"), "ora_utc": data.get("ora", datetime.now().hour),
                    "forza": data.get("forza",0), "seed": data.get("seed",0),
                    "modalita": data.get("modalita","NORMAL"), "duration": data.get("duration",0),
                    "reason": data.get("reason",""), "loss_consecutivi": data.get("loss_consecutivi",0),
                    "entry_ts": data.get("entry_ts", time.time()),
                    "funding_rate": LAST_MARKET["funding_rate"], "open_interest": LAST_MARKET["open_interest"],
                    "bid_wall": LAST_MARKET["bid_wall"], "ask_wall": LAST_MARKET["ask_wall"],
                }
                TRADES_COMPLETI.append(trade)
                threading.Thread(target=db_salva_trade, args=(trade,), daemon=True).start()
                if len(TRADES_COMPLETI) >= 10:
                    snap = list(TRADES_COMPLETI)
                    threading.Thread(target=trigger_analisi_se_pronto, args=(snap,), daemon=True).start()
                    # [V5.1] Check trigger AI automatico
                    _check_ai_trigger(len(TRADES_COMPLETI))
        return jsonify({"status": "logged", "total_events": len(TRADING_EVENTS), "capsule_attive": len(CAPSULE_ATTIVE)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/trading/config", methods=["GET"])
def trading_config_get():
    with _lock:
        cfg = dict(TRADING_CONFIG)
        cfg['capsules'] = list(CAPSULE_ATTIVE)
    return jsonify(cfg)

@app.route("/trading/config", methods=["POST"])
def trading_config_update():
    try:
        data = request.get_json()
        if not data: return jsonify({"error": "No JSON"}), 400
        updated = {}
        with _lock:
            for key, value in data.items():
                if key not in {"last_updated","version","capsules"} and key in TRADING_CONFIG:
                    updated[key] = {"old": TRADING_CONFIG[key], "new": value}
                    TRADING_CONFIG[key] = value
            if updated: TRADING_CONFIG["last_updated"] = datetime.now().isoformat()
        # [V5.0] Notifica il bot via comando live
        for key, change in updated.items():
            _crea_comando("SET_CONFIG", {"key": key, "value": change["new"]})
        return jsonify({"status": "updated", "changes": updated})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/trading/capsule", methods=["POST"])
def aggiungi_capsule():
    try:
        data = request.get_json()
        if not data: return jsonify({"error": "No JSON"}), 400
        with _lock:
            ids = [c['capsule_id'] for c in CAPSULE_ATTIVE]
            if data.get('capsule_id') in ids:
                idx = ids.index(data['capsule_id'])
                if data.get('version', 0) > CAPSULE_ATTIVE[idx].get('version', 0):
                    CAPSULE_ATTIVE[idx] = data
                    db_salva_capsula(data)
                    _inietta_capsule_via_cmd(data)
                    action = "updated"
                else:
                    action = "skipped_old_version"
            else:
                CAPSULE_ATTIVE.append(data)
                db_salva_capsula(data)
                _inietta_capsule_via_cmd(data)
                action = "added"
            TRADING_CONFIG['capsules'] = list(CAPSULE_ATTIVE)
        return jsonify({"status": action, "total_capsule": len(CAPSULE_ATTIVE)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/trading/capsule/<capsule_id>", methods=["DELETE"])
def rimuovi_capsule(capsule_id):
    with _lock:
        for c in CAPSULE_ATTIVE:
            if c['capsule_id'] == capsule_id:
                c['enabled'] = False
                TRADING_CONFIG['capsules'] = list(CAPSULE_ATTIVE)
                db_disabilita_capsula(capsule_id)
                return jsonify({"status": "disabled", "capsule_id": capsule_id})
    return jsonify({"error": "not found"}), 404

@app.route("/trading/analisi_ora", methods=["POST"])
def forza_analisi():
    source = _fonte_migliore()
    if len(source) < 5: return jsonify({"error": f"Solo {len(source)} trade"}), 400
    nuove = analizza_e_genera_capsule(source)
    n = _applica_capsule_nuove(nuove)
    wr, pnl = _wr_pnl(source)
    msg = f"[MANUALE {datetime.now().strftime('%H:%M')}] {len(source)} trade | WR={wr:.0%} | +{n} capsule → bot"
    ANALISI_LOG.append(msg)
    return jsonify({"status": "ok", "trade_in_db": len(source), "wr_globale": round(wr,3),
                    "pnl_globale": round(pnl,2), "capsule_nuove": n, "capsule_totali": len(CAPSULE_ATTIVE), "log": msg})

@app.route("/trading/brain_report", methods=["GET"])
def brain_report():
    try:
        trades = _fonte_migliore()
        if not trades: return jsonify({"error": "Nessun trade"}), 400
        def ap(key, labels, funcs):
            out = {}
            for label, check in zip(labels, funcs):
                g = [t for t in trades if check(t.get(key, 0))]
                if g:
                    wr, pnl = _wr_pnl(g)
                    out[label] = {"n": len(g), "wr": round(wr,3), "pnl": round(pnl,2)}
            return out
        wr, pnl = _wr_pnl(trades)
        return jsonify({
            "totale_trade": len(trades), "wr_globale": round(wr,3), "pnl_globale": round(pnl,2),
            "per_forza": ap('forza', ['<0.20','0.20-0.35','0.35-0.50','0.50-0.65','0.65-0.80','>0.80'],
                [lambda f:f<0.20, lambda f:0.20<=f<0.35, lambda f:0.35<=f<0.50,
                 lambda f:0.50<=f<0.65, lambda f:0.65<=f<0.80, lambda f:f>=0.80]),
            "per_seed": ap('seed', ['<0.45','0.45-0.55','0.55-0.65','>0.65'],
                [lambda s:s<0.45, lambda s:0.45<=s<0.55, lambda s:0.55<=s<0.65, lambda s:s>=0.65]),
            "per_regime": {r: {"n":len(g),"wr":round(_wr_pnl(g)[0],3),"pnl":round(_wr_pnl(g)[1],2)}
                           for r in ['lateral','choppy','normal','trending']
                           if (g:=[t for t in trades if t.get('regime')==r])},
            "per_modalita": {m: {"n":len(g),"wr":round(_wr_pnl(g)[0],3),"pnl":round(_wr_pnl(g)[1],2)}
                             for m in ['FLAT','NORMAL']
                             if (g:=[t for t in trades if t.get('modalita')==m])},
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/trading/status", methods=["GET"])
def trading_status():
    try:
        minutes = request.args.get("minutes", 60, type=int)
        cutoff  = datetime.now() - timedelta(minutes=minutes)
        with _lock:
            recent = [e for e in TRADING_EVENTS
                      if datetime.fromisoformat(e.get("received_at","2000-01-01")) > cutoff]
        exits     = [e for e in recent if e.get("event_type") == "EXIT"]
        exits_win = [e for e in exits if e.get("pnl",0) > 0]
        blocks    = [e for e in recent if e.get("event_type") == "BLOCK"]
        block_types = defaultdict(int)
        for b in blocks: block_types[str(b.get("block_reason","unknown"))] += 1
        with _lock:
            hb     = dict(BOT_HEARTBEAT)
            status = dict(BOT_STATUS)
            sample = list(TRADES_COMPLETI)[-10:]
            analisi = list(ANALISI_LOG)
            caps   = list(CAPSULE_ATTIVE)
            pending = len([c for c in COMMANDS.values() if c['status']=='PENDING'])
        # Aggiorna secondi_fa
        if hb.get("last_seen"):
            try: hb["secondi_fa"] = int((datetime.now()-datetime.fromisoformat(hb["last_seen"])).total_seconds())
            except: pass
        wr  = len(exits_win)/len(exits)*100 if exits else 0
        pnl = sum(e.get("pnl",0) for e in exits)/len(exits) if exits else 0
        return jsonify({
            "timestamp": datetime.now().isoformat(),
            "bot_status": status, "bot_heartbeat": hb, "comandi_pending": pending,
            "periodo_minuti": minutes, "db_trade_totali": db_conta_trades(),
            "memoria_trade": len(TRADES_COMPLETI),
            "metriche": {"entries": len([e for e in recent if e.get("event_type")=="ENTRY"]),
                         "exits": len(exits), "wins": len(exits_win),
                         "losses": len(exits)-len(exits_win),
                         "win_rate": round(wr,1), "pnl_medio": round(pnl,4),
                         "blocks": len(blocks), "block_types": dict(block_types)},
            "capsule_attive": caps, "ultimi_log_analisi": analisi[-15:],
            "ultimi_10_trade": sample, "mercato": dict(LAST_MARKET),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================
# [V5.0] NUOVI ENDPOINT — CANALE BIDIREZIONALE
# ============================================================

@app.route("/trading/heartbeat", methods=["POST"])
def heartbeat():
    """Bot manda heartbeat ogni 30s. MC sa sempre se è vivo."""
    data = request.get_json() or {}
    with _lock:
        BOT_HEARTBEAT.update({
            "status":    data.get("status","RUNNING"),
            "capital":   data.get("capital",0.0),
            "trades":    data.get("trades",0),
            "wins":      data.get("wins",0),
            "losses":    data.get("losses",0),
            "win_rate":  data.get("win_rate",0.0),
            "modalita":  data.get("modalita","?"),
            "posizione_aperta": data.get("posizione_aperta",False),
            "last_seen": datetime.now().isoformat(),
            "secondi_fa": 0,
        })
        BOT_STATUS["is_running"] = True
        BOT_STATUS["last_ping"]  = datetime.now().isoformat()
    return jsonify({"ok": True})

@app.route("/trading/commands", methods=["GET"])
def get_commands():
    """Bot chiede: ci sono comandi per me? Ritorna PENDING, li segna SENT."""
    with _lock:
        pending = [cmd for cmd in COMMANDS.values() if cmd["status"] == "PENDING"]
        for cmd in pending:
            COMMANDS[cmd["id"]]["status"] = "SENT"
    return jsonify({"commands": pending, "count": len(pending)})

@app.route("/trading/commands", methods=["POST"])
def create_command():
    """Dashboard crea un comando per il bot."""
    data = request.get_json() or {}
    cmd  = _crea_comando(data.get("type","NOP"), data.get("params",{}))
    return jsonify({"ok": True, "cmd": cmd})

@app.route("/trading/commands/<cmd_id>/ack", methods=["POST"])
def ack_command(cmd_id):
    """Bot conferma esecuzione comando."""
    data = request.get_json() or {}
    with _lock:
        if cmd_id in COMMANDS:
            COMMANDS[cmd_id]["status"]   = data.get("status","OK")
            COMMANDS[cmd_id]["acked_at"] = datetime.now().isoformat()
    try:
        conn = get_db()
        conn.execute("UPDATE commands SET status=?,acked_at=datetime('now') WHERE id=?",
                     (data.get("status","OK"), cmd_id))
        conn.commit(); conn.close()
    except: pass
    print(f"[V5.0] ✅ ACK {cmd_id}: {data.get('status','OK')}")
    return jsonify({"ok": True})

@app.route("/trading/commands/history", methods=["GET"])
def commands_history():
    with _lock:
        cmds = sorted(COMMANDS.values(), key=lambda c: c.get("created_at",""), reverse=True)
    return jsonify({"commands": list(cmds)[:50]})

@app.route("/trading/pitstop", methods=["POST"])
def pitstop():
    """Azioni rapide dalla dashboard — un click, un comando al bot."""
    data   = request.get_json() or {}
    action = data.get("action","").upper()
    value  = data.get("value")

    if action in ("STOP","RESUME","RESET_LOSSES","CLOSE_ALL"):
        cmd = _crea_comando(action)
        return jsonify({"ok": True, "cmd": cmd, "msg": f"✅ {action} inviato al bot"})

    elif action == "SET_FANTASMA_WR" and value is not None:
        TRADING_CONFIG["FANTASMA_WR"] = float(value)
        cmd = _crea_comando("SET_CONFIG", {"key":"FANTASMA_WR","value":float(value)})
        return jsonify({"ok": True, "cmd": cmd, "msg": f"FANTASMA_WR → {value}"})

    elif action == "SET_SEED_NORMAL" and value is not None:
        TRADING_CONFIG["SEED_THRESH_NORMAL"] = float(value)
        cmd = _crea_comando("SET_CONFIG", {"key":"SEED_THRESH_NORMAL","value":float(value)})
        return jsonify({"ok": True, "cmd": cmd, "msg": f"SEED_THRESH_NORMAL → {value}"})

    elif action == "SET_SEED_FLAT" and value is not None:
        TRADING_CONFIG["SEED_THRESH_FLAT"] = float(value)
        cmd = _crea_comando("SET_CONFIG", {"key":"SEED_THRESH_FLAT","value":float(value)})
        return jsonify({"ok": True, "cmd": cmd, "msg": f"SEED_THRESH_FLAT → {value}"})

    elif action == "ANALISI_ORA":
        source = _fonte_migliore()
        nuove  = analizza_e_genera_capsule(source)
        n      = _applica_capsule_nuove(nuove)
        return jsonify({"ok": True, "capsule_nuove": n,
                        "msg": f"🧠 {len(source)} trade → +{n} capsule inviate al bot"})

    return jsonify({"error": f"Azione non riconosciuta: {action}"}), 400

# ============================================================
# DASHBOARD V5.0 — PIT STOP LIVE
# ============================================================

@app.route("/trading/dashboard")
@app.route("/")
def dashboard():
    return r"""<!DOCTYPE html>
<html>
<head>
<title>Mission Control V5.1 — PIT STOP</title>
<meta charset="utf-8">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:monospace;background:#080808;color:#0f0}
.hdr{padding:10px 18px;border-bottom:1px solid #1a1a1a;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
h1{color:#0ff;font-size:16px}
.dot{width:10px;height:10px;border-radius:50%;background:#f44;display:inline-block;transition:background .5s}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:6px;padding:6px 18px}
.card{background:#111;padding:9px;border-radius:5px;border:1px solid #1a1a1a}
.lbl{color:#444;font-size:9px;text-transform:uppercase}
.val{font-size:19px;font-weight:bold;margin-top:1px}
.g{color:#0f0}.r{color:#f44}.w{color:#fa0}.b{color:#48f}
.sec{padding:5px 18px}.sec h2{color:#ff0;font-size:12px;margin-bottom:5px}

/* PIT STOP */
.pit{background:#090f09;border:1px solid #0a0;border-radius:7px;padding:10px 14px;margin:6px 18px}
.pit h2{color:#0f0;font-size:12px;margin-bottom:8px;letter-spacing:1px}
.row{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:6px}
.rlbl{color:#555;font-size:10px;width:110px;flex-shrink:0}
btn,button{padding:5px 12px;cursor:pointer;border-radius:4px;font-size:10px;font-family:monospace;border:1px solid;transition:opacity .15s}
button:hover{opacity:.75}
.bstp{background:#300;color:#f44;border-color:#f44}
.bgo {background:#030;color:#0f0;border-color:#0f0}
.bwrn{background:#320;color:#fa0;border-color:#fa0}
.binf{background:#003;color:#48f;border-color:#48f}
.bpur{background:#102;color:#c8f;border-color:#a4f}
input[type=range]{width:140px;accent-color:#0f0;vertical-align:middle}
.sv{color:#0f0;font-size:11px;width:38px;display:inline-block}
input.txt{background:#111;color:#0f0;border:1px solid #1a4;padding:3px 7px;border-radius:3px;font-family:monospace;font-size:10px}

/* capsule */
.cap{background:#0a130a;border:1px solid #0a0;padding:4px 8px;margin:2px 0;border-radius:3px;font-size:10px;display:flex;align-items:center;gap:6px}
.cap.bl{border-color:#f44;background:#130a0a}
.cap.au{border-color:#fa0}
pre{background:#111;padding:6px;border-radius:3px;font-size:10px;max-height:160px;overflow-y:auto}
.tentry{padding:2px 0;border-bottom:1px solid #111;font-size:10px}
.centry{padding:2px 5px;border-bottom:1px solid #0a0a0a;font-size:9px;color:#444}
.cok{color:#060}.cpnd{color:#960}.csnt{color:#336}
table{width:100%;border-collapse:collapse;font-size:10px}
th{color:#333;text-align:left;padding:2px 6px;border-bottom:1px solid #1a1a1a}
td{padding:2px 6px;border-bottom:1px solid #111}
.toast{position:fixed;bottom:16px;right:16px;background:#0a1a0a;color:#0f0;border:1px solid #0f0;padding:8px 14px;border-radius:5px;font-size:11px;display:none;z-index:999;max-width:320px}
</style>
</head>
<body>
<div class="hdr">
  <span class="dot" id="dot"></span>
  <h1>🏎️ MISSION CONTROL V5.1 — PIT STOP LIVE</h1>
  <span id="hbInfo" style="font-size:10px;color:#555"></span>
  <span id="upd" style="font-size:10px;color:#222;margin-left:auto"></span>
</div>

<div class="grid" id="grid"></div>

<div class="sec"><h2>🤖 BOT LIVE</h2><div class="grid" id="botgrid" style="padding:0"></div></div>

<div class="pit">
  <h2>🔧 PIT STOP — COMANDI LIVE AL BOT</h2>
  <div class="row">
    <span class="rlbl">Controllo Bot</span>
    <button class="bstp" onclick="ps('STOP')">⛔ STOP</button>
    <button class="bgo"  onclick="ps('RESUME')">▶ RESUME</button>
    <button class="bwrn" onclick="ps('RESET_LOSSES')">🔄 RESET LOSSES</button>
    <button class="bstp" onclick="ps('CLOSE_ALL')">🚨 CLOSE ALL</button>
  </div>
  <div class="row">
    <span class="rlbl">FANTASMA_WR</span>
    <input type="range" min="0.10" max="0.60" step="0.05" value="0.40" id="fwr"
           oninput="document.getElementById('fwrv').textContent=parseFloat(this.value).toFixed(2)">
    <span class="sv" id="fwrv">0.40</span>
    <button class="binf" onclick="psv('SET_FANTASMA_WR',document.getElementById('fwr').value)">✓ Applica</button>
  </div>
  <div class="row">
    <span class="rlbl">SEED NORMAL</span>
    <input type="range" min="0.30" max="0.70" step="0.05" value="0.45" id="sn"
           oninput="document.getElementById('snv').textContent=parseFloat(this.value).toFixed(2)">
    <span class="sv" id="snv">0.45</span>
    <button class="binf" onclick="psv('SET_SEED_NORMAL',document.getElementById('sn').value)">✓ Applica</button>
  </div>
  <div class="row">
    <span class="rlbl">SEED FLAT</span>
    <input type="range" min="0.30" max="0.70" step="0.05" value="0.50" id="sf"
           oninput="document.getElementById('sfv').textContent=parseFloat(this.value).toFixed(2)">
    <span class="sv" id="sfv">0.50</span>
    <button class="binf" onclick="psv('SET_SEED_FLAT',document.getElementById('sf').value)">✓ Applica</button>
  </div>
  <div class="row">
    <span class="rlbl">Cervello</span>
    <button class="bpur" onclick="ps('ANALISI_ORA')">🧠 ANALISI + INIETTA CAPSULE</button>
    <button class="binf" onclick="showBrain()">📊 BRAIN REPORT</button>
  </div>
  <div class="row">
    <span class="rlbl">Capsule custom</span>
    <input class="txt" id="cid" placeholder="capsule_id" style="width:160px">
    <input class="txt" id="cdesc" placeholder="descrizione" style="width:200px">
    <button class="bwrn" onclick="iniettaManuale()">💊 INIETTA VETO CUSTOM</button>
  </div>
</div>

<div class="sec">
  <h2>📡 COMANDI RECENTI <span id="pendBadge" style="color:#fa0"></span></h2>
  <div id="cmdlog" style="max-height:80px;overflow-y:auto"></div>
</div>

<div class="sec">
  <h2>💊 CAPSULE ATTIVE</h2>
  <div id="caplist"></div>
</div>

<div class="sec" id="brainSec" style="display:none">
  <h2>📊 BRAIN REPORT</h2>
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:6px">
    <div><div class="lbl" style="margin-bottom:3px">FORZA</div><table id="tbF"></table></div>
    <div><div class="lbl" style="margin-bottom:3px">SEED</div><table id="tbS"></table></div>
    <div><div class="lbl" style="margin-bottom:3px">REGIME</div><table id="tbR"></table></div>
    <div><div class="lbl" style="margin-bottom:3px">MODALITÀ</div><table id="tbM"></table></div>
  </div>
</div>

<div class="sec"><h2>📋 ULTIMI TRADE</h2><div id="tlog" style="max-height:120px;overflow-y:auto"></div></div>
<div class="sec" style="margin-bottom:20px"><h2>🧠 LOG ANALISI</h2><pre id="alog"></pre></div>

<div class="toast" id="toast"></div>

<script>
function toast(msg,c){
  const t=document.getElementById('toast');
  t.textContent=msg; t.style.color=c||'#0f0'; t.style.borderColor=c||'#0f0';
  t.style.display='block'; setTimeout(()=>t.style.display='none',3000);
}
function ps(action){
  fetch('/trading/pitstop',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action})})
  .then(r=>r.json()).then(d=>{ toast(d.msg||action, d.ok?'#0f0':'#f44'); tick(); })
  .catch(e=>toast('❌ '+e,'#f44'));
}
function psv(action,value){
  fetch('/trading/pitstop',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action,value:parseFloat(value)})})
  .then(r=>r.json()).then(d=>{ toast(d.msg||action,'#48f'); tick(); })
  .catch(e=>toast('❌ '+e,'#f44'));
}
function iniettaManuale(){
  const id=document.getElementById('cid').value.trim();
  const desc=document.getElementById('cdesc').value.trim();
  if(!id){ toast('Inserisci capsule_id','#f44'); return; }
  const cap={capsule_id:id,version:1,descrizione:desc||id,priority:1,enabled:true,
    source:'dashboard_manual',
    trigger:[{param:'modalita',op:'==',value:'NORMAL'}],
    azione:{type:'blocca_entry',params:{reason:'manual_veto'}},
    hits:0,wins_after:0,losses_after:0};
  fetch('/trading/commands',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({type:'INJECT_CAPSULE',params:{capsule:cap}})})
  .then(r=>r.json()).then(()=>{ toast('💊 Capsule inviata al bot!','#fa0'); tick(); });
}
function disabilita(id){
  if(!confirm('Disabilitare '+id+'?')) return;
  fetch('/trading/capsule/'+id,{method:'DELETE'}).then(()=>tick());
}

function chiediAI() {
  const btn = event.target;
  btn.textContent = '⏳ AI sta analizzando...';
  btn.disabled = true;
  fetch('/trading/ai_analysis', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'})
  .then(r=>r.json()).then(d=>{
    btn.textContent = "🤖 CHIEDI ALL'AI";
    btn.disabled = false;
    if(d.error){ toast('❌ '+d.error,'#f44'); return; }
    const col = d.valutazione==='OTTIMO'?'#0f0':d.valutazione==='BUONO'?'#48f':d.valutazione==='ATTENZIONE'?'#fa0':'#f44';
    toast('🤖 ' + d.valutazione + ' — ' + (d.messaggio||d.ragionamento||''), col);
    if(d.comandi_creati && d.comandi_creati.length > 0){
      toast('📡 ' + d.comandi_creati.length + ' comandi inviati al bot!', '#fa0');
    }
    tick();
  }).catch(e=>{ btn.textContent="🤖 CHIEDI ALL'AI"; btn.disabled=false; toast('❌ '+e,'#f44'); });
}
function showBrain(){
  const s=document.getElementById('brainSec');
  s.style.display=s.style.display==='none'?'block':'none';
  if(s.style.display==='none') return;
  fetch('/trading/brain_report').then(r=>r.json()).then(d=>{
    function tb(data,id){
      const el=document.getElementById(id);
      if(!data||!Object.keys(data).length){el.innerHTML='<tr><td style="color:#222">N/A</td></tr>';return;}
      let h='<tr><th>Range</th><th>N</th><th>WR</th><th>PnL</th></tr>';
      Object.entries(data).forEach(([k,v])=>{
        const c=v.wr>=0.55?'g':v.wr>=0.40?'w':'r';
        h+=`<tr><td>${k}</td><td>${v.n}</td><td class="${c}">${(v.wr*100).toFixed(0)}%</td>
            <td class="${v.pnl>=0?'g':'r'}">${v.pnl>=0?'+':''}${v.pnl.toFixed(1)}$</td></tr>`;
      });
      el.innerHTML=h;
    }
    tb(d.per_forza,'tbF'); tb(d.per_seed,'tbS'); tb(d.per_regime,'tbR'); tb(d.per_modalita,'tbM');
  });
}
function tick(){
  fetch('/trading/status?minutes=60').then(r=>r.json()).then(d=>{
    const m=d.metriche,b=d.bot_status,hb=d.bot_heartbeat||{};
    const alive=hb.last_seen&&(Date.now()-new Date(hb.last_seen).getTime())<70000;
    document.getElementById('dot').style.background=alive?'#0f0':'#f44';
    document.getElementById('upd').textContent=new Date().toLocaleTimeString();
    const fa=hb.secondi_fa!=null?hb.secondi_fa+'s fa':'mai';
    document.getElementById('hbInfo').textContent=
      alive?`🟢 vivo ${fa} | ${hb.modalita||'?'} | $${(hb.capital||0).toFixed(2)}`:'🔴 offline';

    const pc=b.total_pnl>=0?'g':'r';
    const wc=m.win_rate>=50?'g':m.win_rate>=40?'w':'r';
    document.getElementById('grid').innerHTML=`
      <div class="card"><div class="lbl">PnL Totale</div>
        <div class="val ${pc}">${b.total_pnl>=0?'+':''}${(b.total_pnl||0).toFixed(2)}$</div>
        <div class="lbl">Trade: ${b.total_trades}</div></div>
      <div class="card"><div class="lbl">Win Rate 60min</div>
        <div class="val ${wc}">${m.win_rate}%</div>
        <div class="lbl">W:${m.wins} L:${m.losses}</div></div>
      <div class="card"><div class="lbl">PnL Medio</div>
        <div class="val ${m.pnl_medio>=0?'g':'r'}">${m.pnl_medio>=0?'+':''}${(m.pnl_medio||0).toFixed(3)}$</div></div>
      <div class="card"><div class="lbl">Blocchi</div>
        <div class="val w">${m.blocks}</div></div>
      <div class="card"><div class="lbl">Capsule</div>
        <div class="val">${(d.capsule_attive||[]).length}</div>
        <div class="lbl">Auto: ${b.capsule_generate||0}</div></div>
      <div class="card"><div class="lbl">Cmd Pending</div>
        <div class="val ${d.comandi_pending>0?'w':'b'}">${d.comandi_pending||0}</div></div>
      <div class="card"><div class="lbl">DB Trade</div>
        <div class="val b">${d.db_trade_totali||0}</div></div>
      <div class="card"><div class="lbl">Mercato</div>
        <div class="val" style="font-size:11px">FR:${((d.mercato?.funding_rate||0)*100).toFixed(4)}%</div>
        <div class="lbl">OI:${((d.mercato?.open_interest||0)/1000).toFixed(1)}K</div></div>`;

    document.getElementById('botgrid').innerHTML=`
      <div class="card"><div class="lbl">Status</div>
        <div class="val ${hb.status==='RUNNING'?'g':hb.status==='PAUSED'?'w':'r'}">${hb.status||'?'}</div></div>
      <div class="card"><div class="lbl">Capitale</div>
        <div class="val g">$${(hb.capital||0).toFixed(2)}</div></div>
      <div class="card"><div class="lbl">Modalità</div>
        <div class="val b">${hb.modalita||'?'}</div></div>
      <div class="card"><div class="lbl">WR Sessione</div>
        <div class="val ${(hb.win_rate||0)>=50?'g':'r'}">${(hb.win_rate||0).toFixed(1)}%</div></div>
      <div class="card"><div class="lbl">Posizione</div>
        <div class="val ${hb.posizione_aperta?'w':'b'}">${hb.posizione_aperta?'APERTA':'CHIUSA'}</div></div>`;

    // Capsule
    let ch='';
    (d.capsule_attive||[]).forEach(c=>{
      const bl=c.azione?.type==='blocca_entry';
      const au=(c.source||'').includes('brain');
      ch+=`<div class="cap ${bl?'bl':''} ${au?'au':''}">${bl?'🔴':'🟢'}${au?'🧠':'👤'}
        <b>${c.capsule_id}</b> — ${c.descrizione}
        <button class="bstp" style="padding:1px 6px;font-size:9px;margin-left:auto" onclick="disabilita('${c.capsule_id}')">✕</button></div>`;
    });
    document.getElementById('caplist').innerHTML=ch||'<span style="color:#222">Nessuna capsule</span>';

    document.getElementById('alog').textContent=(d.ultimi_log_analisi||[]).join('\n');

    let th='';
    (d.ultimi_10_trade||[]).slice().reverse().forEach(t=>{
      const c=t.win?'#0f0':'#f44';
      th+=`<div class="tentry" style="color:${c}">${t.win?'🟢':'🔴'} ${t.modalita||'?'} F:${(t.forza||0).toFixed(2)} S:${(t.seed||0).toFixed(2)} ${t.reason||'?'} ${(t.pnl||0)>=0?'+':''}${(t.pnl||0).toFixed(3)}$</div>`;
    });
    document.getElementById('tlog').innerHTML=th||'<span style="color:#222">Nessun trade</span>';
  }).catch(console.error);

  // Comandi
  fetch('/trading/commands/history').then(r=>r.json()).then(d=>{
    const pend=(d.commands||[]).filter(c=>c.status==='PENDING').length;
    document.getElementById('pendBadge').textContent=pend>0?`(${pend} pending)`:'';
    let ch='';
    (d.commands||[]).slice(0,15).forEach(c=>{
      const cls=c.status==='OK'?'cok':c.status==='PENDING'?'cpnd':'csnt';
      ch+=`<div class="centry ${cls}">${c.created_at.substring(11,19)} ${c.type} ${JSON.stringify(c.params)} → ${c.status}</div>`;
    });
    document.getElementById('cmdlog').innerHTML=ch||'<span style="color:#222">Nessun comando</span>';
  }).catch(()=>{});
}
tick();
setInterval(tick,8000);
</script>
</body>
</html>"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"[MC] Mission Control V5.1 — PIT STOP BIDIREZIONALE — porta {port}")
    app.run(host="0.0.0.0", port=port)

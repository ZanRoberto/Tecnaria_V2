"""
MISSION CONTROL V4.3 — OVERTOP BASSANO
[V4.3] Fix critici:
  - Rimosso DB_URL non definita (causava crash al boot)
  - Soglia analisi abbassata 25→10 trade
  - Analisi si attiva su memoria in-memory (sopravvive restart Render)
  - Trigger analisi immediato dopo ogni EXIT (non aspetta 5 minuti)
  - MIN_CAMP abbassato 20→8
  - Dashboard aggiornata V4.3
"""

from flask import Flask, request, jsonify
import os
import json
import time
import threading
import sqlite3
from datetime import datetime, timedelta
from collections import deque, defaultdict
from typing import List, Dict, Optional

app = Flask(__name__)
_lock = threading.Lock()

# ============================================================
# DATABASE — SQLite locale
# NOTA: /tmp su Render si azzera ad ogni restart — è normale.
# I trade in memoria (TRADES_COMPLETI) sopravvivono finché
# il server è attivo. Il DB è solo un backup secondario.
# ============================================================

DB_PATH = os.environ.get("DB_PATH", "/tmp/overtop_mission.db")
print(f"[DB] SQLite path: {DB_PATH}")


def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            asset       TEXT,
            pnl         REAL,
            win         INTEGER,
            regime      TEXT,
            ora_utc     INTEGER,
            forza       REAL,
            seed        REAL,
            modalita    TEXT,
            duration    REAL,
            reason      TEXT,
            loss_consecutivi INTEGER DEFAULT 0,
            entry_ts    REAL,
            funding_rate REAL DEFAULT 0,
            open_interest REAL DEFAULT 0,
            bid_wall    REAL DEFAULT 0,
            ask_wall    REAL DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS capsule (
            capsule_id   TEXT PRIMARY KEY,
            version      INTEGER DEFAULT 1,
            descrizione  TEXT,
            trigger_json TEXT,
            azione_json  TEXT,
            priority     INTEGER DEFAULT 2,
            enabled      INTEGER DEFAULT 1,
            source       TEXT DEFAULT 'server_analyzer',
            hits         INTEGER DEFAULT 0,
            wins_after   INTEGER DEFAULT 0,
            losses_after INTEGER DEFAULT 0,
            created_at   TEXT DEFAULT (datetime('now')),
            updated_at   TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()
    print("[DB] Tabelle SQLite inizializzate OK")


# ============================================================
# STORAGE IN-MEMORY — FONTE PRIMARIA
# ============================================================

TRADING_EVENTS   = deque(maxlen=5000)
TRADES_COMPLETI  = deque(maxlen=5000)  # [V4.3] aumentato da 2000
CAPSULE_ATTIVE: List[dict] = []
ANALISI_LOG      = deque(maxlen=200)

LAST_MARKET = {
    "funding_rate": 0.0, "open_interest": 0.0,
    "bid_wall": 0.0, "bid_wall_price": 0.0,
    "ask_wall": 0.0, "ask_wall_price": 0.0,
    "updated_at": None,
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
    "version":               "4.3-BRAIN",
    "capsules":              [],
}

BOT_STATUS = {
    "is_running": False, "last_ping": None,
    "total_trades": 0, "total_pnl": 0.0,
    "wins": 0, "losses": 0,
    "ultima_analisi": None, "capsule_generate": 0,
}

# ============================================================
# DB HELPERS
# ============================================================

def db_salva_trade(trade: dict):
    try:
        conn = get_db()
        conn.execute("""
            INSERT INTO trades
                (asset, pnl, win, regime, ora_utc, forza, seed, modalita,
                 duration, reason, loss_consecutivi, entry_ts,
                 funding_rate, open_interest, bid_wall, ask_wall)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            trade.get("asset", "BTCUSDC"),
            trade.get("pnl", 0),
            1 if trade.get("win", False) else 0,
            trade.get("regime", "unknown"),
            trade.get("ora_utc", 0),
            trade.get("forza", 0),
            trade.get("seed", 0),
            trade.get("modalita", "NORMAL"),
            trade.get("duration", 0),
            trade.get("reason", ""),
            trade.get("loss_consecutivi", 0),
            trade.get("entry_ts", time.time()),
            trade.get("funding_rate", 0),
            trade.get("open_interest", 0),
            trade.get("bid_wall", 0),
            trade.get("ask_wall", 0),
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB] Errore salvataggio trade: {e}")


def db_salva_capsula(cap: dict):
    try:
        conn = get_db()
        conn.execute("""
            INSERT OR REPLACE INTO capsule
                (capsule_id, version, descrizione, trigger_json, azione_json,
                 priority, enabled, source, hits, wins_after, losses_after, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
        """, (
            cap["capsule_id"], cap.get("version", 1),
            cap.get("descrizione", ""),
            json.dumps(cap.get("trigger", [])),
            json.dumps(cap.get("azione", {})),
            cap.get("priority", 2),
            1 if cap.get("enabled", True) else 0,
            cap.get("source", "server_analyzer"),
            cap.get("hits", 0),
            cap.get("wins_after", 0),
            cap.get("losses_after", 0),
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB] Errore salvataggio capsula: {e}")


def db_carica_capsule() -> List[dict]:
    try:
        conn = get_db()
        rows = conn.execute("SELECT * FROM capsule WHERE enabled = 1 ORDER BY priority").fetchall()
        conn.close()
        return [{
            "capsule_id":   r["capsule_id"],
            "version":      r["version"],
            "descrizione":  r["descrizione"],
            "trigger":      json.loads(r["trigger_json"] or "[]"),
            "azione":       json.loads(r["azione_json"] or "{}"),
            "priority":     r["priority"],
            "enabled":      bool(r["enabled"]),
            "source":       r["source"],
            "hits":         r["hits"],
            "wins_after":   r["wins_after"],
            "losses_after": r["losses_after"],
        } for r in rows]
    except Exception as e:
        print(f"[DB] Errore caricamento capsule: {e}")
        return []


def db_carica_trades(limit: int = 5000) -> List[dict]:
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in reversed(rows)]
    except Exception as e:
        print(f"[DB] Errore caricamento trades: {e}")
        return []


def db_disabilita_capsula(capsule_id: str):
    try:
        conn = get_db()
        conn.execute("UPDATE capsule SET enabled=0 WHERE capsule_id=?", (capsule_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB] Errore disabilita capsula: {e}")


def db_conta_trades() -> int:
    try:
        conn = get_db()
        n = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        conn.close()
        return n
    except Exception as e:
        return 0


def _fonte_migliore() -> List[dict]:
    """Restituisce la fonte con più trade: DB o memoria in-memory."""
    trades_db  = db_carica_trades(limit=10000)
    trades_mem = list(TRADES_COMPLETI)
    return trades_db if len(trades_db) >= len(trades_mem) else trades_mem


# ============================================================
# ANALISI — IL VERO CERVELLO
# ============================================================

def _wr_pnl(trades):
    if not trades:
        return 0.0, 0.0
    wins = sum(1 for t in trades if t.get('win', t.get('pnl', 0) > 0))
    pnl  = sum(t.get('pnl', 0) for t in trades)
    return wins / len(trades), pnl


def analizza_e_genera_capsule(trades: list) -> list:
    # [V4.3] Soglia abbassata 25 → 10
    if len(trades) < 10:
        return []

    capsule = []
    wr_globale, _ = _wr_pnl(trades)
    ids_esistenti = {c['capsule_id'] for c in CAPSULE_ATTIVE}
    MIN_CAMP  = 8     # [V4.3] abbassato da 20/25 a 8
    MIN_DELTA = 0.10

    fasce_forza = [
        ('f_sotto_20',  lambda f: f < 0.20,          0,    0.20),
        ('f_20_35',     lambda f: 0.20 <= f < 0.35,  0.20, 0.35),
        ('f_35_50',     lambda f: 0.35 <= f < 0.50,  0.35, 0.50),
        ('f_50_65',     lambda f: 0.50 <= f < 0.65,  0.50, 0.65),
        ('f_65_80',     lambda f: 0.65 <= f < 0.80,  0.65, 0.80),
        ('f_sopra_80',  lambda f: f >= 0.80,          0.80, 2.0),
    ]

    forza_report = []
    for fname, check, fmin, fmax in fasce_forza:
        gruppo = [t for t in trades if check(t.get('forza', 0.5))]
        if len(gruppo) < MIN_CAMP:
            continue
        wr, pnl = _wr_pnl(gruppo)
        forza_report.append((fname, wr, pnl, len(gruppo)))

        cid_b    = f"AUTO_BLOCCO_FORZA_{fname.upper()}_001"
        cid_boost = f"AUTO_BOOST_FORZA_{fname.upper()}_001"

        trigger = [{"param": "forza", "op": ">=", "value": fmin},
                   {"param": "forza", "op": "<",  "value": fmax}]
        if fmin == 0:
            trigger = [{"param": "forza", "op": "<", "value": fmax}]

        if wr < 0.38 and pnl < -8 and cid_b not in ids_esistenti:
            capsule.append({
                "capsule_id": cid_b, "version": 1,
                "descrizione": f"AUTO-BRAIN: forza {fname} WR={wr:.0%} PnL={pnl:+.2f} su {len(gruppo)} trade — BLOCCO",
                "trigger": trigger,
                "azione": {"type": "blocca_entry", "params": {"reason": f"auto_forza_{fname}"}},
                "priority": 2, "enabled": True, "source": "brain_v43",
                "hits": 0, "wins_after": 0, "losses_after": 0, "created_at": time.time(),
            })
        elif wr > 0.68 and pnl > 8 and cid_boost not in ids_esistenti:
            capsule.append({
                "capsule_id": cid_boost, "version": 1,
                "descrizione": f"AUTO-BRAIN: forza {fname} WR={wr:.0%} PnL={pnl:+.2f} su {len(gruppo)} trade — BOOST +20%",
                "trigger": trigger,
                "azione": {"type": "modifica_size", "params": {"mult": 1.20}},
                "priority": 3, "enabled": True, "source": "brain_v43",
                "hits": 0, "wins_after": 0, "losses_after": 0, "created_at": time.time(),
            })

    if forza_report:
        msg = "[BRAIN] Forza: " + " | ".join(f"{f[0]}={f[1]:.0%}({f[2]:+.1f}$,n={f[3]})" for f in forza_report)
        ANALISI_LOG.append(msg)

    # --- SEED ---
    fasce_seed = [
        ('seed_basso',  lambda s: s < 0.45,          0,    0.45),
        ('seed_medio',  lambda s: 0.45 <= s < 0.55,  0.45, 0.55),
        ('seed_alto',   lambda s: 0.55 <= s < 0.65,  0.55, 0.65),
        ('seed_ottimo', lambda s: s >= 0.65,          0.65, 2.0),
    ]
    for sname, check, smin, smax in fasce_seed:
        gruppo = [t for t in trades if check(t.get('seed', 0.5))]
        if len(gruppo) < MIN_CAMP:
            continue
        wr, pnl = _wr_pnl(gruppo)
        cid = f"AUTO_BLOCCO_SEED_{sname.upper()}_001"
        trigger = [{"param": "seed", "op": ">=", "value": smin},
                   {"param": "seed", "op": "<",  "value": smax}]
        if smin == 0:
            trigger = [{"param": "seed", "op": "<", "value": smax}]
        if wr < 0.38 and pnl < -8 and cid not in ids_esistenti:
            capsule.append({
                "capsule_id": cid, "version": 1,
                "descrizione": f"AUTO-BRAIN: seed {sname} WR={wr:.0%} PnL={pnl:+.2f} su {len(gruppo)} trade — BLOCCO",
                "trigger": trigger,
                "azione": {"type": "blocca_entry", "params": {"reason": f"auto_seed_{sname}"}},
                "priority": 2, "enabled": True, "source": "brain_v43",
                "hits": 0, "wins_after": 0, "losses_after": 0, "created_at": time.time(),
            })

    # --- REGIME ---
    regimi = defaultdict(list)
    for t in trades:
        if t.get('regime'):
            regimi[t['regime']].append(t)

    for regime, gruppo in regimi.items():
        if len(gruppo) < MIN_CAMP:
            continue
        wr, pnl = _wr_pnl(gruppo)
        cid_b    = f"AUTO_BLOCCO_REGIME_{regime.upper()}_001"
        cid_boost = f"AUTO_BOOST_REGIME_{regime.upper()}_001"
        if wr < 0.35 and wr < wr_globale - MIN_DELTA and cid_b not in ids_esistenti:
            capsule.append({
                "capsule_id": cid_b, "version": 1,
                "descrizione": f"AUTO-BRAIN: regime {regime} WR={wr:.0%} PnL={pnl:+.2f} su {len(gruppo)} trade — BLOCCO",
                "trigger": [{"param": "regime", "op": "==", "value": regime}],
                "azione": {"type": "blocca_entry", "params": {"reason": f"auto_regime_{regime}"}},
                "priority": 1, "enabled": True, "source": "brain_v43",
                "hits": 0, "wins_after": 0, "losses_after": 0, "created_at": time.time(),
            })
        elif wr > 0.70 and wr > wr_globale + MIN_DELTA and cid_boost not in ids_esistenti:
            capsule.append({
                "capsule_id": cid_boost, "version": 1,
                "descrizione": f"AUTO-BRAIN: regime {regime} WR={wr:.0%} su {len(gruppo)} trade — BOOST +25%",
                "trigger": [{"param": "regime", "op": "==", "value": regime}],
                "azione": {"type": "modifica_size", "params": {"mult": 1.25}},
                "priority": 3, "enabled": True, "source": "brain_v43",
                "hits": 0, "wins_after": 0, "losses_after": 0, "created_at": time.time(),
            })

    # --- MODALITA + FORZA COMBO ---
    for modalita in ['FLAT', 'NORMAL']:
        gruppo_mod = [t for t in trades if t.get('modalita') == modalita]
        if len(gruppo_mod) < MIN_CAMP:
            continue
        for fname, check, fmin, fmax in fasce_forza:
            gruppo = [t for t in gruppo_mod if check(t.get('forza', 0.5))]
            if len(gruppo) < 5:
                continue
            wr, pnl = _wr_pnl(gruppo)
            cid = f"AUTO_BLOCCO_{modalita}_FORZA_{fname.upper()}_001"
            if wr < 0.35 and pnl < -5 and cid not in ids_esistenti:
                trigger = [
                    {"param": "modalita", "op": "==", "value": modalita},
                    {"param": "forza",    "op": ">=", "value": fmin},
                    {"param": "forza",    "op": "<",  "value": fmax},
                ]
                capsule.append({
                    "capsule_id": cid, "version": 1,
                    "descrizione": f"AUTO-BRAIN: {modalita}+forza {fname} WR={wr:.0%} PnL={pnl:+.2f} — BLOCCO",
                    "trigger": trigger,
                    "azione": {"type": "blocca_entry", "params": {"reason": f"auto_{modalita.lower()}_forza_{fname}"}},
                    "priority": 2, "enabled": True, "source": "brain_v43",
                    "hits": 0, "wins_after": 0, "losses_after": 0, "created_at": time.time(),
                })

    # --- FASCIA ORARIA ---
    fasce_ore = {
        'mattina':     (8,  12),
        'pomeriggio':  (12, 16),
        'sera':        (16, 20),
        'notte_eu':    (20, 24),
        'notte_tarda': (0,  4),
        'alba':        (4,  8),
    }
    for fname, (h_start, h_end) in fasce_ore.items():
        gruppo = [t for t in trades if h_start <= t.get('ora_utc', 0) < h_end]
        if len(gruppo) < MIN_CAMP:
            continue
        wr, pnl = _wr_pnl(gruppo)
        cid = f"AUTO_BLOCCO_ORA_{fname.upper()}_001"
        if wr < 0.35 and pnl < -10 and cid not in ids_esistenti:
            capsule.append({
                "capsule_id": cid, "version": 1,
                "descrizione": f"AUTO-BRAIN: fascia {fname} ({h_start}-{h_end}h UTC) WR={wr:.0%} PnL={pnl:+.2f} — BLOCCO",
                "trigger": [{"param": "ora_utc", "op": ">=", "value": h_start},
                            {"param": "ora_utc", "op": "<",  "value": h_end}],
                "azione": {"type": "blocca_entry", "params": {"reason": f"auto_ora_{fname}"}},
                "priority": 2, "enabled": True, "source": "brain_v43",
                "hits": 0, "wins_after": 0, "losses_after": 0, "created_at": time.time(),
            })

    # --- SEQUENZE 3 LOSS ---
    per_asset = defaultdict(list)
    for t in sorted(trades, key=lambda x: x.get('entry_ts', time.time())):
        per_asset[t.get('asset', 'ALL')].append(t)

    for asset, seq in per_asset.items():
        after_3loss = []
        for i in range(3, len(seq)):
            if (not seq[i-1].get('win', False) and
                not seq[i-2].get('win', False) and
                not seq[i-3].get('win', False)):
                after_3loss.append(seq[i])
        if len(after_3loss) >= 5:
            wr, _ = _wr_pnl(after_3loss)
            cid = f"AUTO_BLOCCO_3LOSS_{asset}_001"
            if wr < 0.40 and cid not in ids_esistenti:
                capsule.append({
                    "capsule_id": cid, "version": 1,
                    "descrizione": f"AUTO-BRAIN: dopo 3 loss su {asset} WR={wr:.0%} su {len(after_3loss)} casi — BLOCCO PAUSA",
                    "trigger": [{"param": "loss_consecutivi", "op": ">=", "value": 3},
                                {"param": "asset", "op": "==", "value": asset}],
                    "azione": {"type": "blocca_entry", "params": {"reason": "auto_3loss_pausa"}},
                    "priority": 2, "enabled": True, "source": "brain_v43",
                    "hits": 0, "wins_after": 0, "losses_after": 0, "created_at": time.time(),
                })

    # --- COMBO REGIME + MODALITA ---
    for regime in ['lateral', 'choppy', 'normal', 'trending']:
        for modalita in ['FLAT', 'NORMAL']:
            gruppo = [t for t in trades if t.get('regime') == regime and t.get('modalita') == modalita]
            if len(gruppo) < 5:
                continue
            wr, pnl = _wr_pnl(gruppo)
            cid = f"AUTO_COMBO_{regime.upper()}_{modalita}_001"
            if wr < 0.33 and pnl < -8 and cid not in ids_esistenti:
                capsule.append({
                    "capsule_id": cid, "version": 1,
                    "descrizione": f"AUTO-BRAIN: combo {regime}+{modalita} WR={wr:.0%} PnL={pnl:+.2f} su {len(gruppo)} trade — BLOCCO",
                    "trigger": [{"param": "regime",   "op": "==", "value": regime},
                                {"param": "modalita", "op": "==", "value": modalita}],
                    "azione": {"type": "blocca_entry", "params": {"reason": f"auto_combo_{regime}_{modalita.lower()}"}},
                    "priority": 2, "enabled": True, "source": "brain_v43",
                    "hits": 0, "wins_after": 0, "losses_after": 0, "created_at": time.time(),
                })

    return capsule


def _applica_capsule_nuove(nuove: list):
    """Applica capsule nuove alla lista attiva e al DB."""
    with _lock:
        aggiunte = 0
        for cap in nuove:
            if not any(c['capsule_id'] == cap['capsule_id'] for c in CAPSULE_ATTIVE):
                CAPSULE_ATTIVE.append(cap)
                db_salva_capsula(cap)
                aggiunte += 1
        TRADING_CONFIG['capsules'] = list(CAPSULE_ATTIVE)
        BOT_STATUS['ultima_analisi'] = datetime.now().isoformat()
        BOT_STATUS['capsule_generate'] += aggiunte
    return aggiunte


# ============================================================
# [V4.3] TRIGGER IMMEDIATO — analisi dopo ogni EXIT
# Non aspetta il ciclo periodico di 5 minuti.
# ============================================================

_ultimo_trigger_ts = 0.0

def trigger_analisi_se_pronto(trades_snap: list):
    """Analisi immediata. Throttle: max 1 volta ogni 60 secondi."""
    global _ultimo_trigger_ts
    now = time.time()
    if now - _ultimo_trigger_ts < 60:
        return
    _ultimo_trigger_ts = now
    try:
        nuove = analizza_e_genera_capsule(trades_snap)
        aggiunte = _applica_capsule_nuove(nuove)
        if aggiunte > 0:
            msg = (f"[TRIGGER {datetime.now().strftime('%H:%M')}] "
                   f"+{aggiunte} capsule AUTO da {len(trades_snap)} trade in memoria")
            ANALISI_LOG.append(msg)
            print(msg)
    except Exception as e:
        print(f"[TRIGGER] Errore: {e}")


# ============================================================
# THREAD ANALISI PERIODICA (ogni 5 minuti, usa fonte migliore)
# ============================================================

def thread_analisi_periodica():
    time.sleep(60)  # attendi 1 minuto al boot
    while True:
        time.sleep(300)
        try:
            source = _fonte_migliore()
            if len(source) < 10:
                msg = f"[{datetime.now().strftime('%H:%M')}] Analisi saltata — {len(source)} trade (min 10)"
                ANALISI_LOG.append(msg)
                print(msg)
                continue
            nuove  = analizza_e_genera_capsule(source)
            aggiunte = _applica_capsule_nuove(nuove)
            wr, pnl = _wr_pnl(source)
            msg = (f"[{datetime.now().strftime('%H:%M')}] 🧠 Analisi {len(source)} trade "
                   f"| WR={wr:.0%} PnL={pnl:+.2f} | +{aggiunte} capsule (tot={len(CAPSULE_ATTIVE)})")
            ANALISI_LOG.append(msg)
            print(msg)
        except Exception as e:
            print(f"[ANALISI] Errore: {e}")


# ============================================================
# STARTUP — [V4.3] senza DB_URL non definita
# ============================================================

def startup():
    try:
        print(f"[STARTUP] DB_PATH: {DB_PATH}")
        init_db()

        capsule_db = db_carica_capsule()
        with _lock:
            CAPSULE_ATTIVE.extend(capsule_db)
            TRADING_CONFIG['capsules'] = list(CAPSULE_ATTIVE)

        trade_db = db_carica_trades(limit=2000)
        with _lock:
            TRADES_COMPLETI.extend(trade_db)
            if trade_db:
                BOT_STATUS['total_trades'] = db_conta_trades()
                BOT_STATUS['wins']         = sum(1 for t in trade_db if t.get('win'))
                BOT_STATUS['losses']       = sum(1 for t in trade_db if not t.get('win'))
                BOT_STATUS['total_pnl']    = sum(t.get('pnl', 0) for t in trade_db)

        msg = (f"[STARTUP] ✅ {len(capsule_db)} capsule, "
               f"{len(trade_db)} trade caricati (DB totale: {db_conta_trades()})")
        ANALISI_LOG.append(msg)
        print(msg)
    except Exception as e:
        print(f"[STARTUP] ⚠️ Errore: {e} — modalità memoria pura")
        ANALISI_LOG.append(f"[STARTUP] ⚠️ DB non raggiungibile — modalità memoria")


print("[MAIN] Avvio startup...")
startup()
print("[MAIN] Avvio thread analisi...")
threading.Thread(target=thread_analisi_periodica, daemon=True).start()


# ============================================================
# ENDPOINTS
# ============================================================

@app.route("/trading/log", methods=["POST"])
def trading_log():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON"}), 400

        event = {**data, "received_at": datetime.now().isoformat()}
        with _lock:
            TRADING_EVENTS.append(event)
            BOT_STATUS["last_ping"]   = datetime.now().isoformat()
            BOT_STATUS["is_running"]  = True

            if data.get("event_type") == "MARKET_DATA":
                LAST_MARKET.update({
                    "funding_rate":   data.get("funding_rate", 0.0),
                    "open_interest":  data.get("open_interest", 0.0),
                    "bid_wall":       data.get("bid_wall", 0.0),
                    "bid_wall_price": data.get("bid_wall_price", 0.0),
                    "ask_wall":       data.get("ask_wall", 0.0),
                    "ask_wall_price": data.get("ask_wall_price", 0.0),
                    "updated_at":     datetime.now().isoformat(),
                })

            if data.get("event_type") == "EXIT":
                pnl = data.get("pnl", 0)
                BOT_STATUS["total_trades"] += 1
                BOT_STATUS["total_pnl"]    += pnl
                if pnl > 0: BOT_STATUS["wins"]   += 1
                else:        BOT_STATUS["losses"] += 1

                trade = {
                    "asset":            data.get("asset", "BTCUSDC"),
                    "pnl":              pnl,
                    "win":              pnl > 0,
                    "regime":           data.get("regime", data.get("modalita", "unknown")),
                    "ora_utc":          data.get("ora", datetime.now().hour),
                    "forza":            data.get("forza", 0.0),
                    "seed":             data.get("seed", 0.0),
                    "modalita":         data.get("modalita", "NORMAL"),
                    "duration":         data.get("duration", 0),
                    "reason":           data.get("reason", ""),
                    "loss_consecutivi": data.get("loss_consecutivi", 0),
                    "entry_ts":         data.get("entry_ts", time.time()),
                    "funding_rate":     LAST_MARKET["funding_rate"],
                    "open_interest":    LAST_MARKET["open_interest"],
                    "bid_wall":         LAST_MARKET["bid_wall"],
                    "ask_wall":         LAST_MARKET["ask_wall"],
                }
                TRADES_COMPLETI.append(trade)
                threading.Thread(target=db_salva_trade, args=(trade,), daemon=True).start()

                # [V4.3] Trigger analisi immediato
                if len(TRADES_COMPLETI) >= 10:
                    snap = list(TRADES_COMPLETI)
                    threading.Thread(target=trigger_analisi_se_pronto, args=(snap,), daemon=True).start()

        return jsonify({
            "status":         "logged",
            "total_events":   len(TRADING_EVENTS),
            "capsule_attive": len(CAPSULE_ATTIVE),
        })
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
        return jsonify({"status": "updated", "changes": updated})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/trading/capsule", methods=["POST"])
def aggiungi_capsule():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON"}), 400
        with _lock:
            ids = [c['capsule_id'] for c in CAPSULE_ATTIVE]
            if data.get('capsule_id') in ids:
                idx = ids.index(data['capsule_id'])
                if data.get('version', 0) > CAPSULE_ATTIVE[idx].get('version', 0):
                    CAPSULE_ATTIVE[idx] = data
                    db_salva_capsula(data)
                    action = "updated"
                else:
                    action = "skipped_old_version"
            else:
                CAPSULE_ATTIVE.append(data)
                db_salva_capsula(data)
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


@app.route("/trading/status", methods=["GET"])
def trading_status():
    try:
        minutes = request.args.get("minutes", 60, type=int)
        cutoff  = datetime.now() - timedelta(minutes=minutes)
        with _lock:
            recent = [e for e in TRADING_EVENTS
                      if datetime.fromisoformat(e.get("received_at", "2000-01-01")) > cutoff]
        exits      = [e for e in recent if e.get("event_type") == "EXIT"]
        exits_win  = [e for e in exits if e.get("pnl", 0) > 0]
        exits_loss = [e for e in exits if e.get("pnl", 0) <= 0]
        wr         = len(exits_win) / len(exits) * 100 if exits else 0
        pnl_medio  = sum(e.get("pnl", 0) for e in exits) / len(exits) if exits else 0
        blocks     = [e for e in recent if e.get("event_type") == "BLOCK"]
        block_types = defaultdict(int)
        for b in blocks:
            block_types[str(b.get("block_reason", "unknown"))] += 1

        with _lock:
            status        = dict(BOT_STATUS)
            trades_sample = list(TRADES_COMPLETI)[-10:]
            analisi       = list(ANALISI_LOG)
            capsule       = list(CAPSULE_ATTIVE)

        return jsonify({
            "timestamp":          datetime.now().isoformat(),
            "bot_status":         status,
            "periodo_minuti":     minutes,
            "db_trade_totali":    db_conta_trades(),
            "memoria_trade":      len(TRADES_COMPLETI),
            "metriche": {
                "entries":    len([e for e in recent if e.get("event_type") == "ENTRY"]),
                "exits":      len(exits),
                "wins":       len(exits_win),
                "losses":     len(exits_loss),
                "win_rate":   round(wr, 1),
                "pnl_medio":  round(pnl_medio, 4),
                "blocks":     len(blocks),
                "block_types": dict(block_types),
            },
            "capsule_attive":     capsule,
            "ultimi_log_analisi": analisi[-15:],
            "ultimi_10_trade":    trades_sample,
            "mercato":            dict(LAST_MARKET),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/trading/analisi_ora", methods=["POST"])
def forza_analisi():
    source = _fonte_migliore()
    if len(source) < 5:
        return jsonify({"error": f"Solo {len(source)} trade — minimo 5"}), 400
    nuove   = analizza_e_genera_capsule(source)
    aggiunte = _applica_capsule_nuove(nuove)
    wr, pnl = _wr_pnl(source)
    msg = (f"[MANUALE {datetime.now().strftime('%H:%M')}] "
           f"{len(source)} trade | WR={wr:.0%} PnL={pnl:+.2f} | "
           f"+{aggiunte} capsule (tot={len(CAPSULE_ATTIVE)})")
    ANALISI_LOG.append(msg)
    return jsonify({
        "status":         "analisi_completata",
        "trade_in_db":    len(source),
        "wr_globale":     round(wr, 3),
        "pnl_globale":    round(pnl, 2),
        "capsule_nuove":  aggiunte,
        "capsule_totali": len(CAPSULE_ATTIVE),
        "nuove_capsule":  nuove,
        "log":            msg,
    })


@app.route("/trading/brain_report", methods=["GET"])
def brain_report():
    try:
        trades = _fonte_migliore()
        if not trades:
            return jsonify({"error": "Nessun trade disponibile"}), 400

        def analisi_per(key, labels, funcs):
            out = {}
            for label, check in zip(labels, funcs):
                gruppo = [t for t in trades if check(t.get(key, 0))]
                if not gruppo: continue
                wr, pnl = _wr_pnl(gruppo)
                out[label] = {"n": len(gruppo), "wr": round(wr, 3), "pnl": round(pnl, 2)}
            return out

        wr_tot, pnl_tot = _wr_pnl(trades)
        return jsonify({
            "totale_trade": len(trades),
            "wr_globale":   round(wr_tot, 3),
            "pnl_globale":  round(pnl_tot, 2),
            "per_forza": analisi_per('forza',
                ['<0.20','0.20-0.35','0.35-0.50','0.50-0.65','0.65-0.80','>0.80'],
                [lambda f:f<0.20, lambda f:0.20<=f<0.35, lambda f:0.35<=f<0.50,
                 lambda f:0.50<=f<0.65, lambda f:0.65<=f<0.80, lambda f:f>=0.80]),
            "per_seed": analisi_per('seed',
                ['<0.45','0.45-0.55','0.55-0.65','>0.65'],
                [lambda s:s<0.45, lambda s:0.45<=s<0.55,
                 lambda s:0.55<=s<0.65, lambda s:s>=0.65]),
            "per_regime": {r: {"n": len(g), "wr": round(_wr_pnl(g)[0],3), "pnl": round(_wr_pnl(g)[1],2)}
                           for r in ['lateral','choppy','normal','trending']
                           if (g := [t for t in trades if t.get('regime')==r])},
            "per_modalita": {m: {"n": len(g), "wr": round(_wr_pnl(g)[0],3), "pnl": round(_wr_pnl(g)[1],2)}
                             for m in ['FLAT','NORMAL']
                             if (g := [t for t in trades if t.get('modalita')==m])},
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/trading/candles")
def candles():
    with _lock:
        events = list(TRADING_EVENTS)
    markers = []
    for e in events:
        et = e.get('event_type')
        ts_raw = e.get('timestamp', e.get('received_at', ''))
        try:
            ts = datetime.fromisoformat(ts_raw).timestamp() if isinstance(ts_raw, str) else float(ts_raw)
        except:
            ts = time.time()
        if et == 'ENTRY':
            markers.append({"ts": ts*1000, "type": "buy",
                "price": e.get('entry_price', 0), "label": f"ENTRY F:{e.get('forza',0):.2f}"})
        elif et == 'EXIT':
            pnl = e.get('pnl', 0)
            markers.append({"ts": ts*1000, "type": "sell",
                "price": e.get('exit_price', 0), "pnl": pnl, "win": pnl > 0,
                "label": f"EXIT {e.get('reason','')} {pnl:+.2f}$"})
    return jsonify({"markers": markers[-100:], "last_market": dict(LAST_MARKET), "bot_status": dict(BOT_STATUS)})


@app.route("/trading/dashboard")
def dashboard():
    return """<!DOCTYPE html>
<html>
<head>
    <title>Mission Control V4.3 — OVERTOP BASSANO</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: monospace; background: #0a0a0a; color: #00ff00; }
        .header { padding: 15px 20px; border-bottom: 1px solid #1a1a1a; display:flex; align-items:center; gap:20px; flex-wrap:wrap; }
        h1 { color: #00ffff; font-size: 18px; }
        .status-dot { width:10px; height:10px; border-radius:50%; background:#ff4444; display:inline-block; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 8px; padding: 10px 20px; }
        .card { background: #111; padding: 10px; border-radius: 6px; border: 1px solid #1a1a1a; }
        .label { color: #555; font-size: 10px; text-transform:uppercase; }
        .value { font-size: 20px; font-weight: bold; margin-top:2px; }
        .profit { color: #00ff00; } .loss { color: #ff4444; } .warn { color: #ffaa00; }
        .section { padding: 8px 20px; }
        .section h2 { color: #ffff00; font-size: 13px; margin-bottom: 6px; }
        .capsule { background: #0a1a0a; border: 1px solid #0f5; padding: 6px 10px; margin: 3px 0; border-radius: 4px; font-size: 11px; }
        .capsule.blocca { border-color: #f44; background: #1a0a0a; }
        pre { background: #111; padding: 8px; border-radius: 4px; font-size: 11px; max-height:220px; overflow-y:auto; }
        button { background: #1a3a1a; color: #0f0; border: 1px solid #0f0; padding: 6px 12px; cursor: pointer; border-radius: 4px; margin: 2px; font-size:11px; }
        button.danger { background: #3a1a1a; color: #f44; border-color: #f44; }
        button.info { background: #1a1a3a; color: #44f; border-color: #44f; }
        .trade-log { max-height: 160px; overflow-y:auto; }
        .trade-entry { padding: 3px 0; border-bottom: 1px solid #111; font-size: 11px; }
        table { width:100%; border-collapse:collapse; font-size:11px; }
        th { color:#555; text-align:left; padding:4px 8px; border-bottom:1px solid #1a1a1a; }
        td { padding:4px 8px; border-bottom:1px solid #111; }
    </style>
</head>
<body>
    <div class="header">
        <span class="status-dot" id="dot"></span>
        <h1>🧠 MISSION CONTROL V4.3 — OVERTOP BASSANO</h1>
        <span id="dbCount" style="color:#555;font-size:11px;"></span>
        <span id="lastUpdate" style="color:#555;font-size:11px;margin-left:auto"></span>
    </div>
    <div class="grid" id="metriche"></div>
    <div class="section">
        <h2>📡 MERCATO LIVE</h2>
        <div class="grid" id="mercatoGrid" style="padding:0"></div>
    </div>
    <div class="section">
        <h2>💊 CAPSULE ATTIVE
            <button onclick="forzaAnalisi()">🔬 ANALISI ORA</button>
            <button class="info" onclick="brainReport()">🧠 BRAIN REPORT</button>
        </h2>
        <div id="capsuleList"></div>
    </div>
    <div class="section">
        <h2>📊 BRAIN REPORT — Analisi per parametro</h2>
        <div id="brainDiv" style="display:none">
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:8px">
                <div><h3 style="color:#aaa;font-size:11px;margin-bottom:4px">FORZA</h3><table id="tForza"></table></div>
                <div><h3 style="color:#aaa;font-size:11px;margin-bottom:4px">SEED</h3><table id="tSeed"></table></div>
                <div><h3 style="color:#aaa;font-size:11px;margin-bottom:4px">REGIME</h3><table id="tRegime"></table></div>
                <div><h3 style="color:#aaa;font-size:11px;margin-bottom:4px">MODALITÀ</h3><table id="tModalita"></table></div>
            </div>
        </div>
    </div>
    <div class="section">
        <h2>📋 ULTIMI TRADE</h2>
        <div class="trade-log" id="tradeLog"></div>
    </div>
    <div class="section">
        <h2>📋 LOG CERVELLO</h2>
        <pre id="logAnalisi"></pre>
    </div>
<script>
function aggiorna() {
    fetch('/trading/status?minutes=60')
    .then(r => r.json())
    .then(d => {
        const m = d.metriche, b = d.bot_status;
        document.getElementById('dot').style.background = b.is_running ? '#00ff00' : '#ff4444';
        document.getElementById('lastUpdate').textContent = 'aggiornato: ' + new Date().toLocaleTimeString();
        document.getElementById('dbCount').textContent =
            '🗄️ DB: ' + (d.db_trade_totali||0) + ' | 🧠 Mem: ' + (d.memoria_trade||0) + ' trade';
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
                <div class="value warn">${m.blocks}</div></div>
            <div class="card"><div class="label">CAPSULE AUTO</div>
                <div class="value">${d.capsule_attive ? d.capsule_attive.length : 0}</div>
                <div class="label">Generate: ${b.capsule_generate||0}</div></div>
            <div class="card"><div class="label">ULTIMO PING</div>
                <div class="value" style="font-size:13px">${b.last_ping ? b.last_ping.substring(11,19) : 'N/A'}</div></div>
            <div class="card"><div class="label">ULTIMA ANALISI</div>
                <div class="value" style="font-size:12px">${b.ultima_analisi ? b.ultima_analisi.substring(11,19) : 'MAI'}</div></div>
        `;
        if (d.mercato) {
            const fr = d.mercato.funding_rate || 0;
            const fr_class = Math.abs(fr) > 0.0003 ? 'warn' : 'profit';
            document.getElementById('mercatoGrid').innerHTML = `
                <div class="card"><div class="label">FUNDING RATE</div>
                    <div class="value ${fr_class}">${(fr*100).toFixed(4)}%</div></div>
                <div class="card"><div class="label">OPEN INTEREST</div>
                    <div class="value">${((d.mercato.open_interest||0)/1000).toFixed(1)}K</div></div>
                <div class="card"><div class="label">ASK WALL</div>
                    <div class="value loss">${(d.mercato.ask_wall||0).toFixed(1)} BTC</div></div>
                <div class="card"><div class="label">BID WALL</div>
                    <div class="value profit">${(d.mercato.bid_wall||0).toFixed(1)} BTC</div></div>
            `;
        }
        let chtml = '';
        (d.capsule_attive || []).forEach(c => {
            const tipo = c.azione && c.azione.type === 'blocca_entry' ? 'blocca' : '';
            const icona = tipo === 'blocca' ? '🔴' : '🟢';
            const src = (c.source||'').includes('brain') ? '🧠' : '👤';
            chtml += `<div class="capsule ${tipo}">${icona}${src} <b>${c.capsule_id}</b> — ${c.descrizione}
                <button class="danger" onclick="disabilita('${c.capsule_id}')">✕</button></div>`;
        });
        document.getElementById('capsuleList').innerHTML = chtml ||
            '<span style="color:#555">Nessuna capsula — accumula 10+ trade per prima analisi</span>';
        document.getElementById('logAnalisi').textContent = (d.ultimi_log_analisi || []).join('\\n');
        let thtml = '';
        (d.ultimi_10_trade || []).slice().reverse().forEach(t => {
            const col = t.win ? '#00ff00' : '#ff4444';
            thtml += `<div class="trade-entry" style="color:${col}">
                ${t.win ? '🟢' : '🔴'} ${t.modalita||'?'} | F:${(t.forza||0).toFixed(2)} | S:${(t.seed||0).toFixed(2)} | ${t.reason||'?'} | ${(t.pnl||0) >= 0 ? '+' : ''}${(t.pnl||0).toFixed(3)}$
            </div>`;
        });
        document.getElementById('tradeLog').innerHTML = thtml || '<span style="color:#333">Nessun trade</span>';
    }).catch(e => console.error('Status error:', e));
}
function brainReport() {
    document.getElementById('brainDiv').style.display = 'block';
    fetch('/trading/brain_report').then(r => r.json()).then(d => {
        function buildTable(data, elId) {
            const el = document.getElementById(elId);
            if (!data || !Object.keys(data).length) { el.innerHTML = '<tr><td style="color:#333">Dati insufficienti</td></tr>'; return; }
            let html = '<tr><th>Range</th><th>N</th><th>WR</th><th>PnL</th></tr>';
            Object.entries(data).forEach(([k, v]) => {
                const wrClass = v.wr >= 0.55 ? 'profit' : v.wr >= 0.40 ? 'warn' : 'loss';
                html += `<tr><td>${k}</td><td>${v.n}</td>
                    <td class="${wrClass}">${(v.wr*100).toFixed(0)}%</td>
                    <td class="${v.pnl>=0?'profit':'loss'}">${v.pnl>=0?'+':''}${v.pnl.toFixed(1)}$</td></tr>`;
            });
            el.innerHTML = html;
        }
        buildTable(d.per_forza, 'tForza');
        buildTable(d.per_seed, 'tSeed');
        buildTable(d.per_regime, 'tRegime');
        buildTable(d.per_modalita, 'tModalita');
    }).catch(e => console.error('Brain report error:', e));
}
function disabilita(id) {
    if (!confirm('Disabilitare ' + id + '?')) return;
    fetch('/trading/capsule/' + id, {method:'DELETE'}).then(() => aggiorna());
}
function forzaAnalisi() {
    fetch('/trading/analisi_ora', {method:'POST'}).then(r => r.json()).then(d => {
        if (d.error) { alert('Errore: ' + d.error); return; }
        alert('🧠 Analisi su ' + d.trade_in_db + ' trade\\n+' + d.capsule_nuove + ' capsule nuove\\nWR=' + (d.wr_globale*100).toFixed(1) + '%  PnL=' + d.pnl_globale.toFixed(2) + '$');
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"[MC] Mission Control V4.3 — BRAIN AUTONOMO — porta {port}")
    app.run(host="0.0.0.0", port=port)

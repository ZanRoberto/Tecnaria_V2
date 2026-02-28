"""
MISSION CONTROL V4.1 — OVERTOP BASSANO
Cervello persistente con Supabase.
[FIX V4.1] Porta 6543 connection pooler Supabase Free
"""

from flask import Flask, request, jsonify
import os
import json
import time
import threading
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from collections import deque, defaultdict
from typing import List, Dict, Optional

app = Flask(__name__)
_lock = threading.Lock()

# ============================================================
# CONNESSIONE SUPABASE — FIX PORTA 6543 (connection pooler)
# ============================================================

_raw_url = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:vivalafiga@db.cqtderlxqiffwjxofvux.supabase.co:5432/postgres?sslmode=require"
)
# Supabase Free blocca porta 5432 diretta — usa 6543 (connection pooler)
DB_URL = _raw_url.replace(":5432/", ":6543/")
print(f"[DB] URL configurato: {DB_URL[:80]}...")


def get_db():
    """Connessione al database con retry automatico."""
    for attempt in range(3):
        try:
            return psycopg2.connect(DB_URL, connect_timeout=10)
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(1)


def init_db():
    """Crea le tabelle se non esistono."""
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id          SERIAL PRIMARY KEY,
            asset       TEXT,
            pnl         FLOAT,
            win         BOOLEAN,
            regime      TEXT,
            ora_utc     INT,
            forza       FLOAT,
            seed        FLOAT,
            modalita    TEXT,
            duration    FLOAT,
            reason      TEXT,
            loss_consecutivi INT DEFAULT 0,
            entry_ts    FLOAT,
            funding_rate FLOAT DEFAULT 0,
            open_interest FLOAT DEFAULT 0,
            bid_wall    FLOAT DEFAULT 0,
            ask_wall    FLOAT DEFAULT 0,
            created_at  TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS capsule (
            capsule_id  TEXT PRIMARY KEY,
            version     INT DEFAULT 1,
            descrizione TEXT,
            trigger_json TEXT,
            azione_json  TEXT,
            priority    INT DEFAULT 2,
            enabled     BOOLEAN DEFAULT TRUE,
            source      TEXT DEFAULT 'server_analyzer',
            hits        INT DEFAULT 0,
            wins_after  INT DEFAULT 0,
            losses_after INT DEFAULT 0,
            created_at  TIMESTAMPTZ DEFAULT NOW(),
            updated_at  TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS analisi_log (
            id         SERIAL PRIMARY KEY,
            messaggio  TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    conn.commit()
    cur.close()
    conn.close()
    print("[DB] Tabelle inizializzate OK")


# ============================================================
# STORAGE IN-MEMORY
# ============================================================

TRADING_EVENTS   = deque(maxlen=5000)
TRADES_COMPLETI  = deque(maxlen=5000)
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
    "version":               "4.1-BRAIN",
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

_pending_trades = deque(maxlen=500)


def db_salva_trade(trade: dict):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO trades
                (asset, pnl, win, regime, ora_utc, forza, seed, modalita,
                 duration, reason, loss_consecutivi, entry_ts,
                 funding_rate, open_interest, bid_wall, ask_wall)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            trade.get("asset", "BTCUSDC"),
            trade.get("pnl", 0),
            trade.get("win", False),
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
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[DB] Errore salvataggio trade: {e} — messo in coda locale")
        _pending_trades.append(trade)


def db_salva_capsula(cap: dict):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO capsule
                (capsule_id, version, descrizione, trigger_json, azione_json,
                 priority, enabled, source, hits, wins_after, losses_after)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (capsule_id) DO UPDATE SET
                version      = EXCLUDED.version,
                descrizione  = EXCLUDED.descrizione,
                trigger_json = EXCLUDED.trigger_json,
                azione_json  = EXCLUDED.azione_json,
                priority     = EXCLUDED.priority,
                enabled      = EXCLUDED.enabled,
                updated_at   = NOW()
        """, (
            cap["capsule_id"], cap.get("version", 1),
            cap.get("descrizione", ""),
            json.dumps(cap.get("trigger", [])),
            json.dumps(cap.get("azione", {})),
            cap.get("priority", 2),
            cap.get("enabled", True),
            cap.get("source", "server_analyzer"),
            cap.get("hits", 0),
            cap.get("wins_after", 0),
            cap.get("losses_after", 0),
        ))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[DB] Errore salvataggio capsula: {e}")


def db_carica_capsule() -> List[dict]:
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM capsule WHERE enabled = TRUE ORDER BY priority")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        result = []
        for r in rows:
            result.append({
                "capsule_id":   r["capsule_id"],
                "version":      r["version"],
                "descrizione":  r["descrizione"],
                "trigger":      json.loads(r["trigger_json"] or "[]"),
                "azione":       json.loads(r["azione_json"] or "{}"),
                "priority":     r["priority"],
                "enabled":      r["enabled"],
                "source":       r["source"],
                "hits":         r["hits"],
                "wins_after":   r["wins_after"],
                "losses_after": r["losses_after"],
            })
        return result
    except Exception as e:
        print(f"[DB] Errore caricamento capsule: {e}")
        return []


def db_carica_trades(limit: int = 5000) -> List[dict]:
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM trades ORDER BY created_at DESC LIMIT %s", (limit,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in reversed(rows)]
    except Exception as e:
        print(f"[DB] Errore caricamento trades: {e}")
        return []


def db_disabilita_capsula(capsule_id: str):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE capsule SET enabled=FALSE WHERE capsule_id=%s", (capsule_id,))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[DB] Errore disabilita capsula: {e}")


def db_conta_trades() -> int:
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM trades")
        n = cur.fetchone()[0]
        cur.close()
        conn.close()
        return n
    except Exception as e:
        print(f"[DB] Errore conta trades: {e}")
        return -1


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
    if len(trades) < 25:
        return []

    capsule = []
    wr_globale, pnl_globale = _wr_pnl(trades)
    ids_esistenti = {c['capsule_id'] for c in CAPSULE_ATTIVE}
    MIN_CAMP  = 20
    MIN_DELTA = 0.10

    fasce_forza = [
        ('f_sotto_20',  lambda f: f < 0.20),
        ('f_20_35',     lambda f: 0.20 <= f < 0.35),
        ('f_35_50',     lambda f: 0.35 <= f < 0.50),
        ('f_50_65',     lambda f: 0.50 <= f < 0.65),
        ('f_65_80',     lambda f: 0.65 <= f < 0.80),
        ('f_sopra_80',  lambda f: f >= 0.80),
    ]
    soglie_forza = [0, 0.20, 0.35, 0.50, 0.65, 0.80, 2.0]

    forza_report = []
    for i, (fname, check) in enumerate(fasce_forza):
        gruppo = [t for t in trades if check(t.get('forza', 0.5))]
        if len(gruppo) < MIN_CAMP:
            continue
        wr, pnl = _wr_pnl(gruppo)
        forza_report.append((fname, wr, pnl, len(gruppo), soglie_forza[i], soglie_forza[i+1]))

        cid_b    = f"AUTO_BLOCCO_FORZA_{fname.upper()}_001"
        cid_boost = f"AUTO_BOOST_FORZA_{fname.upper()}_001"

        if wr < 0.38 and pnl < -10 and cid_b not in ids_esistenti:
            trigger = [{"param": "forza", "op": ">=", "value": soglie_forza[i]},
                       {"param": "forza", "op": "<",  "value": soglie_forza[i+1]}]
            if soglie_forza[i] == 0:
                trigger = [{"param": "forza", "op": "<", "value": soglie_forza[i+1]}]
            capsule.append({
                "capsule_id":  cid_b, "version": 1,
                "descrizione": f"AUTO-BRAIN: forza {fname} WR={wr:.0%} PnL={pnl:+.2f} su {len(gruppo)} trade — BLOCCO",
                "trigger":     trigger,
                "azione":      {"type": "blocca_entry", "params": {"reason": f"auto_forza_{fname}"}},
                "priority": 2, "enabled": True, "source": "brain_v4",
                "hits": 0, "wins_after": 0, "losses_after": 0, "created_at": time.time(),
            })
        elif wr > 0.68 and pnl > 10 and cid_boost not in ids_esistenti:
            trigger = [{"param": "forza", "op": ">=", "value": soglie_forza[i]},
                       {"param": "forza", "op": "<",  "value": soglie_forza[i+1]}]
            capsule.append({
                "capsule_id":  cid_boost, "version": 1,
                "descrizione": f"AUTO-BRAIN: forza {fname} WR={wr:.0%} PnL={pnl:+.2f} su {len(gruppo)} trade — BOOST +20%",
                "trigger":     trigger,
                "azione":      {"type": "modifica_size", "params": {"mult": 1.20}},
                "priority": 3, "enabled": True, "source": "brain_v4",
                "hits": 0, "wins_after": 0, "losses_after": 0, "created_at": time.time(),
            })

    if forza_report:
        msg = "[BRAIN] Analisi forza: " + " | ".join(
            f"{f[0]}={f[1]:.0%}({f[2]:+.1f}$)" for f in forza_report
        )
        ANALISI_LOG.append(msg)

    fasce_seed = [
        ('seed_basso',   lambda s: s < 0.45),
        ('seed_medio',   lambda s: 0.45 <= s < 0.55),
        ('seed_alto',    lambda s: 0.55 <= s < 0.65),
        ('seed_ottimo',  lambda s: s >= 0.65),
    ]
    soglie_seed = [0, 0.45, 0.55, 0.65, 2.0]

    for i, (sname, check) in enumerate(fasce_seed):
        gruppo = [t for t in trades if check(t.get('seed', 0.5))]
        if len(gruppo) < MIN_CAMP:
            continue
        wr, pnl = _wr_pnl(gruppo)
        cid = f"AUTO_BLOCCO_SEED_{sname.upper()}_001"
        if wr < 0.38 and pnl < -8 and cid not in ids_esistenti:
            trigger = [{"param": "seed", "op": ">=", "value": soglie_seed[i]},
                       {"param": "seed", "op": "<",  "value": soglie_seed[i+1]}]
            if soglie_seed[i] == 0:
                trigger = [{"param": "seed", "op": "<", "value": soglie_seed[i+1]}]
            capsule.append({
                "capsule_id":  cid, "version": 1,
                "descrizione": f"AUTO-BRAIN: seed {sname} WR={wr:.0%} PnL={pnl:+.2f} su {len(gruppo)} trade — BLOCCO",
                "trigger":     trigger,
                "azione":      {"type": "blocca_entry", "params": {"reason": f"auto_seed_{sname}"}},
                "priority": 2, "enabled": True, "source": "brain_v4",
                "hits": 0, "wins_after": 0, "losses_after": 0, "created_at": time.time(),
            })

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
                "trigger":    [{"param": "regime", "op": "==", "value": regime}],
                "azione":     {"type": "blocca_entry", "params": {"reason": f"auto_regime_{regime}"}},
                "priority": 1, "enabled": True, "source": "brain_v4",
                "hits": 0, "wins_after": 0, "losses_after": 0, "created_at": time.time(),
            })
        elif wr > 0.70 and wr > wr_globale + MIN_DELTA and cid_boost not in ids_esistenti:
            capsule.append({
                "capsule_id": cid_boost, "version": 1,
                "descrizione": f"AUTO-BRAIN: regime {regime} WR={wr:.0%} su {len(gruppo)} trade — BOOST +25%",
                "trigger":    [{"param": "regime", "op": "==", "value": regime}],
                "azione":     {"type": "modifica_size", "params": {"mult": 1.25}},
                "priority": 3, "enabled": True, "source": "brain_v4",
                "hits": 0, "wins_after": 0, "losses_after": 0, "created_at": time.time(),
            })

    for modalita in ['FLAT', 'NORMAL']:
        gruppo_mod = [t for t in trades if t.get('modalita') == modalita]
        if len(gruppo_mod) < MIN_CAMP:
            continue
        for i, (fname, check) in enumerate(fasce_forza):
            gruppo = [t for t in gruppo_mod if check(t.get('forza', 0.5))]
            if len(gruppo) < 15:
                continue
            wr, pnl = _wr_pnl(gruppo)
            cid = f"AUTO_BLOCCO_{modalita}_FORZA_{fname.upper()}_001"
            if wr < 0.35 and pnl < -5 and cid not in ids_esistenti:
                trigger = [
                    {"param": "modalita", "op": "==",  "value": modalita},
                    {"param": "forza",    "op": ">=",   "value": soglie_forza[i]},
                    {"param": "forza",    "op": "<",    "value": soglie_forza[i+1]},
                ]
                capsule.append({
                    "capsule_id": cid, "version": 1,
                    "descrizione": f"AUTO-BRAIN: {modalita}+forza {fname} WR={wr:.0%} PnL={pnl:+.2f} — BLOCCO",
                    "trigger":    trigger,
                    "azione":     {"type": "blocca_entry", "params": {"reason": f"auto_{modalita.lower()}_forza_{fname}"}},
                    "priority": 2, "enabled": True, "source": "brain_v4",
                    "hits": 0, "wins_after": 0, "losses_after": 0, "created_at": time.time(),
                })

    fasce_ore = {
        'mattina':    (8, 12),
        'pomeriggio': (12, 16),
        'sera':       (16, 20),
        'notte_eu':   (20, 24),
        'notte_tarda':(0, 4),
        'alba':       (4, 8),
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
                "trigger":    [{"param": "ora_utc", "op": ">=", "value": h_start},
                               {"param": "ora_utc", "op": "<",  "value": h_end}],
                "azione":     {"type": "blocca_entry", "params": {"reason": f"auto_ora_{fname}"}},
                "priority": 2, "enabled": True, "source": "brain_v4",
                "hits": 0, "wins_after": 0, "losses_after": 0, "created_at": time.time(),
            })

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
        if len(after_3loss) >= 8:
            wr, _ = _wr_pnl(after_3loss)
            cid = f"AUTO_BLOCCO_3LOSS_{asset}_001"
            if wr < 0.40 and cid not in ids_esistenti:
                capsule.append({
                    "capsule_id": cid, "version": 1,
                    "descrizione": f"AUTO-BRAIN: dopo 3 loss su {asset} WR={wr:.0%} su {len(after_3loss)} casi — BLOCCO PAUSA",
                    "trigger":    [{"param": "loss_consecutivi", "op": ">=", "value": 3},
                                   {"param": "asset", "op": "==", "value": asset}],
                    "azione":     {"type": "blocca_entry", "params": {"reason": "auto_3loss_pausa"}},
                    "priority": 2, "enabled": True, "source": "brain_v4",
                    "hits": 0, "wins_after": 0, "losses_after": 0, "created_at": time.time(),
                })

    for regime in ['lateral', 'choppy', 'normal', 'trending']:
        for modalita in ['FLAT', 'NORMAL']:
            gruppo = [t for t in trades
                      if t.get('regime') == regime and t.get('modalita') == modalita]
            if len(gruppo) < 15:
                continue
            wr, pnl = _wr_pnl(gruppo)
            cid = f"AUTO_COMBO_{regime.upper()}_{modalita}_001"
            if wr < 0.33 and pnl < -8 and cid not in ids_esistenti:
                capsule.append({
                    "capsule_id": cid, "version": 1,
                    "descrizione": f"AUTO-BRAIN: combo {regime}+{modalita} WR={wr:.0%} PnL={pnl:+.2f} su {len(gruppo)} trade — BLOCCO",
                    "trigger":    [{"param": "regime",   "op": "==", "value": regime},
                                   {"param": "modalita", "op": "==", "value": modalita}],
                    "azione":     {"type": "blocca_entry", "params": {"reason": f"auto_combo_{regime}_{modalita.lower()}"}},
                    "priority": 2, "enabled": True, "source": "brain_v4",
                    "hits": 0, "wins_after": 0, "losses_after": 0, "created_at": time.time(),
                })

    return capsule


# ============================================================
# THREAD ANALISI PERIODICA
# ============================================================

def thread_analisi_periodica():
    time.sleep(30)
    while True:
        time.sleep(300)
        try:
            tutti_i_trade = db_carica_trades(limit=10000)
            n_totale = db_conta_trades()
            if len(tutti_i_trade) < 25:
                msg = f"[{datetime.now().strftime('%H:%M')}] Analisi saltata — solo {len(tutti_i_trade)} trade in DB"
                ANALISI_LOG.append(msg)
                continue
            nuove = analizza_e_genera_capsule(tutti_i_trade)
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
            wr, pnl = _wr_pnl(tutti_i_trade)
            msg = (f"[{datetime.now().strftime('%H:%M')}] 🧠 Analisi su {n_totale} trade totali "
                   f"| WR={wr:.0%} PnL={pnl:+.2f} | +{aggiunte} capsule nuove (tot={len(CAPSULE_ATTIVE)})")
            ANALISI_LOG.append(msg)
            print(msg)
        except Exception as e:
            print(f"[ANALISI] Errore: {e}")


# ============================================================
# AVVIO: carica stato da Supabase
# ============================================================

def startup():
    try:
        print(f"[STARTUP] DB_URL in uso: {DB_URL[:80]}...")
        print(f"[STARTUP] DATABASE_URL env presente: {'SI' if os.environ.get('DATABASE_URL') else 'NO — uso default hardcoded'}")
        init_db()

        capsule_db = db_carica_capsule()
        with _lock:
            CAPSULE_ATTIVE.extend(capsule_db)
            TRADING_CONFIG['capsules'] = list(CAPSULE_ATTIVE)

        trade_db = db_carica_trades(limit=2000)
        with _lock:
            TRADES_COMPLETI.extend(trade_db)

        if trade_db:
            with _lock:
                BOT_STATUS['total_trades'] = db_conta_trades()
                BOT_STATUS['wins']   = sum(1 for t in trade_db if t.get('win'))
                BOT_STATUS['losses'] = sum(1 for t in trade_db if not t.get('win'))
                BOT_STATUS['total_pnl'] = sum(t.get('pnl', 0) for t in trade_db)

        msg = (f"[STARTUP] ✅ DB caricato — {len(capsule_db)} capsule, "
               f"{len(trade_db)} trade in cache (totale DB: {db_conta_trades()})")
        ANALISI_LOG.append(msg)
        print(msg)

    except Exception as e:
        print(f"[STARTUP] ⚠️ Supabase non raggiungibile: {e}")
        ANALISI_LOG.append(f"[STARTUP] ⚠️ DB non raggiungibile — modalità memoria")


# Startup SINCRONO — deve completare prima che Flask parta
print("[MAIN] Avvio startup sincrono DB...")
startup()
print("[MAIN] Startup completato — avvio thread analisi...")
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
            BOT_STATUS["last_ping"] = datetime.now().isoformat()
            BOT_STATUS["is_running"] = True

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
                if pnl > 0:
                    BOT_STATUS["wins"] += 1
                else:
                    BOT_STATUS["losses"] += 1

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

        return jsonify({
            "status": "logged",
            "total_events": len(TRADING_EVENTS),
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
    tutti = db_carica_trades(limit=10000)
    if len(tutti) < 10:
        return jsonify({"error": f"Solo {len(tutti)} trade — minimo 10"}), 400
    nuove = analizza_e_genera_capsule(tutti)
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
    wr, pnl = _wr_pnl(tutti)
    msg = (f"[MANUALE {datetime.now().strftime('%H:%M')}] "
           f"{len(tutti)} trade DB | WR={wr:.0%} PnL={pnl:+.2f} | "
           f"+{aggiunte} capsule (tot={len(CAPSULE_ATTIVE)})")
    ANALISI_LOG.append(msg)
    return jsonify({
        "status":         "analisi_completata",
        "trade_in_db":    len(tutti),
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
        trades = db_carica_trades(limit=10000)
        if not trades:
            return jsonify({"error": "Nessun trade in DB"}), 400

        def _analisi_per(key, fasce_labels, fasce_funcs):
            out = {}
            for label, check in zip(fasce_labels, fasce_funcs):
                gruppo = [t for t in trades if check(t.get(key, 0))]
                if not gruppo:
                    continue
                wr, pnl = _wr_pnl(gruppo)
                out[label] = {"n": len(gruppo), "wr": round(wr, 3), "pnl": round(pnl, 2)}
            return out

        forza_labels = ['<0.20','0.20-0.35','0.35-0.50','0.50-0.65','0.65-0.80','>0.80']
        forza_funcs  = [lambda f: f<0.20, lambda f: 0.20<=f<0.35,
                        lambda f: 0.35<=f<0.50, lambda f: 0.50<=f<0.65,
                        lambda f: 0.65<=f<0.80, lambda f: f>=0.80]
        seed_labels  = ['<0.45','0.45-0.55','0.55-0.65','>0.65']
        seed_funcs   = [lambda s: s<0.45, lambda s: 0.45<=s<0.55,
                        lambda s: 0.55<=s<0.65, lambda s: s>=0.65]

        regimi = {}
        for regime in ['lateral','choppy','normal','trending']:
            gruppo = [t for t in trades if t.get('regime') == regime]
            if gruppo:
                wr, pnl = _wr_pnl(gruppo)
                regimi[regime] = {"n": len(gruppo), "wr": round(wr,3), "pnl": round(pnl,2)}

        modalita_report = {}
        for mod in ['FLAT','NORMAL']:
            gruppo = [t for t in trades if t.get('modalita') == mod]
            if gruppo:
                wr, pnl = _wr_pnl(gruppo)
                modalita_report[mod] = {"n": len(gruppo), "wr": round(wr,3), "pnl": round(pnl,2)}

        wr_tot, pnl_tot = _wr_pnl(trades)
        return jsonify({
            "totale_trade": len(trades),
            "wr_globale":   round(wr_tot, 3),
            "pnl_globale":  round(pnl_tot, 2),
            "per_forza":    _analisi_per('forza', forza_labels, forza_funcs),
            "per_seed":     _analisi_per('seed', seed_labels, seed_funcs),
            "per_regime":   regimi,
            "per_modalita": modalita_report,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/trading/candles")
def candles():
    with _lock:
        events = list(TRADING_EVENTS)
    trade_markers = []
    for e in events:
        et     = e.get('event_type')
        ts_raw = e.get('timestamp', e.get('received_at', ''))
        try:
            ts = datetime.fromisoformat(ts_raw).timestamp() if isinstance(ts_raw, str) else float(ts_raw)
        except:
            ts = time.time()
        if et == 'ENTRY':
            trade_markers.append({"ts": ts*1000, "type": "buy",
                "price": e.get('entry_price', 0), "label": f"ENTRY F:{e.get('forza',0):.2f}"})
        elif et == 'EXIT':
            pnl = e.get('pnl', 0)
            trade_markers.append({"ts": ts*1000, "type": "sell",
                "price": e.get('exit_price', 0), "pnl": pnl, "win": pnl > 0,
                "label": f"EXIT {e.get('reason','')} {pnl:+.2f}$"})
    return jsonify({"markers": trade_markers[-100:], "last_market": dict(LAST_MARKET), "bot_status": dict(BOT_STATUS)})


@app.route("/trading/dashboard")
def dashboard():
    return """<!DOCTYPE html>
<html>
<head>
    <title>Mission Control V4.1 — OVERTOP BASSANO</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: monospace; background: #0a0a0a; color: #00ff00; }
        .header { padding: 15px 20px; border-bottom: 1px solid #1a1a1a; display:flex; align-items:center; gap:20px; }
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
        pre { background: #111; padding: 8px; border-radius: 4px; font-size: 11px; max-height:200px; overflow-y:auto; }
        button { background: #1a3a1a; color: #0f0; border: 1px solid #0f0; padding: 6px 12px; cursor: pointer; border-radius: 4px; margin: 2px; font-size:11px; }
        button.danger { background: #3a1a1a; color: #f44; border-color: #f44; }
        button.info { background: #1a1a3a; color: #44f; border-color: #44f; }
        .trade-log { max-height: 150px; overflow-y:auto; }
        .trade-entry { padding: 3px 0; border-bottom: 1px solid #111; font-size: 11px; }
        table { width:100%; border-collapse:collapse; font-size:11px; }
        th { color:#555; text-align:left; padding:4px 8px; border-bottom:1px solid #1a1a1a; }
        td { padding:4px 8px; border-bottom:1px solid #111; }
    </style>
</head>
<body>
    <div class="header">
        <span class="status-dot" id="dot"></span>
        <h1>🧠 MISSION CONTROL V4.1 — OVERTOP BASSANO</h1>
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
        document.getElementById('dbCount').textContent = '🗄️ DB: ' + (d.db_trade_totali || 0) + ' trade totali';
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
            <div class="card"><div class="label">CAPSULE</div>
                <div class="value">${d.capsule_attive ? d.capsule_attive.length : 0}</div>
                <div class="label">Generate: ${b.capsule_generate||0}</div></div>
            <div class="card"><div class="label">ULTIMO PING</div>
                <div class="value" style="font-size:13px">${b.last_ping ? b.last_ping.substring(11,19) : 'N/A'}</div></div>
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
            const src = c.source === 'brain_v4' ? '🧠' : '👤';
            chtml += `<div class="capsule ${tipo}">${icona}${src} <b>${c.capsule_id}</b> — ${c.descrizione}
                <button class="danger" onclick="disabilita('${c.capsule_id}')">✕</button></div>`;
        });
        document.getElementById('capsuleList').innerHTML = chtml || '<span style="color:#333">Nessuna capsula — accumula trade per prima analisi</span>';
        document.getElementById('logAnalisi').textContent = (d.ultimi_log_analisi || []).join('\\n');
        let thtml = '';
        (d.ultimi_10_trade || []).slice().reverse().forEach(t => {
            const col = t.win ? '#00ff00' : '#ff4444';
            thtml += `<div class="trade-entry" style="color:${col}">
                ${t.win ? '🟢' : '🔴'} ${t.modalita} | F:${(t.forza||0).toFixed(2)} | S:${(t.seed||0).toFixed(2)} | ${t.reason} | ${t.pnl >= 0 ? '+' : ''}${(t.pnl||0).toFixed(3)}$
            </div>`;
        });
        document.getElementById('tradeLog').innerHTML = thtml;
    });
}
function brainReport() {
    document.getElementById('brainDiv').style.display = 'block';
    fetch('/trading/brain_report').then(r => r.json()).then(d => {
        function buildTable(data, elId) {
            const el = document.getElementById(elId);
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
    });
}
function disabilita(id) {
    if (!confirm('Disabilitare ' + id + '?')) return;
    fetch('/trading/capsule/' + id, {method:'DELETE'}).then(() => aggiorna());
}
function forzaAnalisi() {
    fetch('/trading/analisi_ora', {method:'POST'}).then(r => r.json()).then(d => {
        alert('🧠 Analisi su ' + d.trade_in_db + ' trade in DB\\n+' + d.capsule_nuove + ' capsule nuove\\nWR=' + (d.wr_globale*100).toFixed(1) + '%  PnL=' + d.pnl_globale.toFixed(2) + '$');
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
    print(f"[MC] Mission Control V4.1 — CERVELLO SUPABASE — porta {port}")
    app.run(host="0.0.0.0", port=port)

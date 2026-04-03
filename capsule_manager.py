"""
CAPSULE MANAGER — Sistema Unificato
====================================
Sostituisce:
  - CapsuleRuntime (capsule_attive.json)
  - ConfigHotReloader
  - IntelligenzaAutonoma
  - VETI_LONG / VETI_SHORT hardcodati nel codice
  - OC1-OC5 statiche

UN SOLO sistema. UN SOLO store (SQLite). Asset-aware.
Ogni capsule visibile, modificabile da dashboard, taggata per asset.

LIVELLI:
  STATIC   — veti fondamentali (ex hardcodati), modificabili da dash
  LEARNED  — generate da pattern statistici (ex L2 IntelligenzaAutonoma)
  AUTO     — generate da anomalie evento (ex L3 streak/regime)

AZIONI:
  blocca_entry   — blocca ingresso
  modifica_size  — moltiplica size
  boost_soglia   — alza soglia ingresso
  boost_entry    — abbassa soglia (opportunità)
  + azioni auto-correttive passthrough
"""

import sqlite3
import json
import time
import logging
import os
from collections import deque, defaultdict

log = logging.getLogger(__name__)

# ===========================================================================
# SCHEMA DB
# ===========================================================================

SCHEMA = """
CREATE TABLE IF NOT EXISTS capsule (
    id           TEXT PRIMARY KEY,
    asset        TEXT NOT NULL DEFAULT 'ALL',
    livello      TEXT NOT NULL,
    tipo         TEXT NOT NULL,
    descrizione  TEXT,
    trigger_json TEXT NOT NULL DEFAULT '[]',
    azione_json  TEXT NOT NULL,
    priority     INTEGER DEFAULT 5,
    enabled      INTEGER DEFAULT 1,
    samples      INTEGER DEFAULT 0,
    wr           REAL DEFAULT 0.0,
    pnl_avg      REAL DEFAULT 0.0,
    created_ts   REAL NOT NULL,
    scade_ts     REAL,
    hits         INTEGER DEFAULT 0,
    hits_saved   REAL DEFAULT 0.0,
    note         TEXT
);
CREATE TABLE IF NOT EXISTS capsule_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL NOT NULL,
    capsule_id   TEXT NOT NULL,
    asset        TEXT,
    event        TEXT,
    context_json TEXT,
    pnl_impact   REAL DEFAULT 0.0
);
"""

OPS = {
    '>':      lambda a, b: a > b,
    '>=':     lambda a, b: a >= b,
    '<':      lambda a, b: a < b,
    '<=':     lambda a, b: a <= b,
    '==':     lambda a, b: a == b,
    '!=':     lambda a, b: a != b,
    'in':     lambda a, b: a in b,
    'not_in': lambda a, b: a not in b,
}

# ===========================================================================
# CAPSULE STATICHE BTC — migrate da VETI_LONG/SHORT hardcodati
# ===========================================================================

STATIC_BTC = [
    # VETI LONG BTC
    {"id": "STATIC_LONG_DEBOLE_ALTA_DOWN_BTC",     "asset": "BTCUSDC", "livello": "STATIC", "tipo": "VETO_LONG",
     "descrizione": "TRAP: WR 5% su dati BTC reali",
     "trigger": [{"param":"momentum","op":"==","value":"DEBOLE"},{"param":"volatility","op":"==","value":"ALTA"},
                 {"param":"trend","op":"==","value":"DOWN"},{"param":"direction","op":"==","value":"LONG"}],
     "azione": {"type":"blocca_entry","params":{"reason":"STATIC_TOSSICO_LONG_DEBOLE_ALTA_DOWN"}},
     "priority":1, "samples":74, "wr":0.05, "note":"WR 5% TRAP BTC"},

    {"id": "STATIC_LONG_FORTE_ALTA_DOWN_BTC",      "asset": "BTCUSDC", "livello": "STATIC", "tipo": "VETO_LONG",
     "descrizione": "PANIC: WR 15% su dati BTC reali",
     "trigger": [{"param":"momentum","op":"==","value":"FORTE"},{"param":"volatility","op":"==","value":"ALTA"},
                 {"param":"trend","op":"==","value":"DOWN"},{"param":"direction","op":"==","value":"LONG"}],
     "azione": {"type":"blocca_entry","params":{"reason":"STATIC_TOSSICO_LONG_FORTE_ALTA_DOWN"}},
     "priority":1, "samples":52, "wr":0.15, "note":"WR 15% PANIC BTC"},

    {"id": "STATIC_LONG_DEBOLE_ALTA_SIDEWAYS_BTC", "asset": "BTCUSDC", "livello": "STATIC", "tipo": "VETO_LONG",
     "descrizione": "RANGE_VOL_W: WR 19% su dati BTC reali",
     "trigger": [{"param":"momentum","op":"==","value":"DEBOLE"},{"param":"volatility","op":"==","value":"ALTA"},
                 {"param":"trend","op":"==","value":"SIDEWAYS"},{"param":"direction","op":"==","value":"LONG"}],
     "azione": {"type":"blocca_entry","params":{"reason":"STATIC_TOSSICO_LONG_DEBOLE_ALTA_SIDEWAYS"}},
     "priority":1, "samples":74, "wr":0.19, "note":"WR 19% RANGE_VOL_W BTC"},

    {"id": "STATIC_LONG_FORTE_ALTA_SIDEWAYS_BTC",  "asset": "BTCUSDC", "livello": "STATIC", "tipo": "VETO_LONG",
     "descrizione": "RANGE_VOL_F: WR 34% su dati BTC reali",
     "trigger": [{"param":"momentum","op":"==","value":"FORTE"},{"param":"volatility","op":"==","value":"ALTA"},
                 {"param":"trend","op":"==","value":"SIDEWAYS"},{"param":"direction","op":"==","value":"LONG"}],
     "azione": {"type":"blocca_entry","params":{"reason":"STATIC_TOSSICO_LONG_FORTE_ALTA_SIDEWAYS"}},
     "priority":1, "samples":48, "wr":0.34, "note":"WR 34% RANGE_VOL_F BTC"},

    {"id": "STATIC_LONG_MEDIO_ALTA_SIDEWAYS_BTC",  "asset": "BTCUSDC", "livello": "STATIC", "tipo": "VETO_LONG",
     "descrizione": "RANGE_VOL_M: WR 28% su dati BTC reali",
     "trigger": [{"param":"momentum","op":"==","value":"MEDIO"},{"param":"volatility","op":"==","value":"ALTA"},
                 {"param":"trend","op":"==","value":"SIDEWAYS"},{"param":"direction","op":"==","value":"LONG"}],
     "azione": {"type":"blocca_entry","params":{"reason":"STATIC_TOSSICO_LONG_MEDIO_ALTA_SIDEWAYS"}},
     "priority":1, "samples":41, "wr":0.28, "note":"WR 28% RANGE_VOL_M BTC"},

    # VETI SHORT BTC
    {"id": "STATIC_SHORT_FORTE_BASSA_UP_BTC",      "asset": "BTCUSDC", "livello": "STATIC", "tipo": "VETO_SHORT",
     "descrizione": "STRONG_BULL SHORT: WR 5% su BTC",
     "trigger": [{"param":"momentum","op":"==","value":"FORTE"},{"param":"volatility","op":"==","value":"BASSA"},
                 {"param":"trend","op":"==","value":"UP"},{"param":"direction","op":"==","value":"SHORT"}],
     "azione": {"type":"blocca_entry","params":{"reason":"STATIC_TOSSICO_SHORT_FORTE_BASSA_UP"}},
     "priority":1, "samples":38, "wr":0.05, "note":"WR 5% SHORT STRONG_BULL BTC"},

    {"id": "STATIC_SHORT_FORTE_MEDIA_UP_BTC",      "asset": "BTCUSDC", "livello": "STATIC", "tipo": "VETO_SHORT",
     "descrizione": "STRONG_MED SHORT: WR 12% su BTC",
     "trigger": [{"param":"momentum","op":"==","value":"FORTE"},{"param":"volatility","op":"==","value":"MEDIA"},
                 {"param":"trend","op":"==","value":"UP"},{"param":"direction","op":"==","value":"SHORT"}],
     "azione": {"type":"blocca_entry","params":{"reason":"STATIC_TOSSICO_SHORT_FORTE_MEDIA_UP"}},
     "priority":1, "samples":29, "wr":0.12, "note":"WR 12% SHORT STRONG_MED BTC"},

    {"id": "STATIC_SHORT_DEBOLE_ALTA_SIDEWAYS_BTC","asset": "BTCUSDC", "livello": "STATIC", "tipo": "VETO_SHORT",
     "descrizione": "RANGE_VOL_W SHORT: WR 10% su BTC",
     "trigger": [{"param":"momentum","op":"==","value":"DEBOLE"},{"param":"volatility","op":"==","value":"ALTA"},
                 {"param":"trend","op":"==","value":"SIDEWAYS"},{"param":"direction","op":"==","value":"SHORT"}],
     "azione": {"type":"blocca_entry","params":{"reason":"STATIC_TOSSICO_SHORT_DEBOLE_ALTA_SIDEWAYS"}},
     "priority":1, "samples":21, "wr":0.10, "note":"WR 10% SHORT RANGE_VOL_W BTC"},
]

# SOL: zero veti statici — impara dai dati reali propri
STATIC_SOL = []


# ===========================================================================
# CAPSULE MANAGER
# ===========================================================================

class CapsuleManager:
    """
    Sistema unificato capsule. Asset-aware. SQLite. Dashboard-ready.
    Drop-in replacement per CapsuleRuntime + IntelligenzaAutonoma.
    """

    MIN_SAMPLES_L2   = 8
    MIN_SAMPLES_L3   = 3
    MAX_AGE_L2       = 86400   # 24h
    MAX_AGE_L3       = 3600    # 1h
    ANALISI_INTERVAL = 30

    def __init__(self, db_path: str, asset: str = "BTCUSDC"):
        self.db_path         = db_path
        self.asset           = asset
        self._cache          = []
        self._cache_ts       = 0.0
        self._trade_buffer   = deque(maxlen=200)
        self._trade_count    = 0
        self._ctx            = {}
        self._init_db()
        self._seed_static()
        self._refresh_cache()
        log.info(f"[CM] ✅ CapsuleManager pronto — asset={asset}")

    # -------------------------------------------------------------------------
    # INIT
    # -------------------------------------------------------------------------

    def _init_db(self):
        with sqlite3.connect(self.db_path) as c:
            c.executescript(SCHEMA)

    def _seed_static(self):
        seeds = STATIC_BTC if "BTC" in self.asset else STATIC_SOL
        with sqlite3.connect(self.db_path) as c:
            for cap in seeds:
                if not c.execute("SELECT id FROM capsule WHERE id=?", (cap["id"],)).fetchone():
                    c.execute("""INSERT INTO capsule
                        (id,asset,livello,tipo,descrizione,trigger_json,azione_json,
                         priority,enabled,samples,wr,pnl_avg,created_ts,scade_ts,note)
                        VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?,NULL,?)""",
                        (cap["id"], cap["asset"], cap["livello"], cap["tipo"],
                         cap.get("descrizione",""),
                         json.dumps(cap.get("trigger",[])),
                         json.dumps(cap["azione"]),
                         cap.get("priority",5),
                         cap.get("samples",0), cap.get("wr",0.0), 0.0,
                         time.time(), cap.get("note","")))
                    log.info(f"[CM] 🌱 Statica inserita: {cap['id']}")

    def _refresh_cache(self):
        try:
            ora = time.time()
            with sqlite3.connect(self.db_path) as c:
                rows = c.execute("""
                    SELECT id,asset,livello,tipo,trigger_json,azione_json,
                           priority,enabled,samples,wr,pnl_avg,scade_ts,note
                    FROM capsule
                    WHERE enabled=1 AND (asset=? OR asset='ALL')
                      AND (scade_ts IS NULL OR scade_ts > ?)
                    ORDER BY priority ASC
                """, (self.asset, ora)).fetchall()
            self._cache = [{
                "id":r[0],"asset":r[1],"livello":r[2],"tipo":r[3],
                "trigger":json.loads(r[4]),"azione":json.loads(r[5]),
                "priority":r[6],"enabled":r[7],"samples":r[8],
                "wr":r[9],"pnl_avg":r[10],"scade_ts":r[11],"note":r[12],
            } for r in rows]
            self._cache_ts = ora
        except Exception as e:
            log.error(f"[CM] refresh_cache: {e}")

    # -------------------------------------------------------------------------
    # VALUTAZIONE
    # -------------------------------------------------------------------------

    def valuta(self, contesto: dict) -> dict:
        if time.time() - self._cache_ts > 10:
            self._refresh_cache()

        res = {"blocca":False,"size_mult":1.0,"soglia_boost":0.0,
               "reason":"","capsule_id":""}

        for cap in self._cache:
            if not self._check_triggers(cap["trigger"], contesto):
                continue
            act  = cap["azione"]
            atype = act.get("type","")

            if atype == "blocca_entry":
                res["blocca"]     = True
                res["reason"]     = act.get("params",{}).get("reason", cap["id"])
                res["capsule_id"] = cap["id"]
                self._fire(cap["id"], contesto)
                log.info(f"[CM] 🚫 BLOCCO {cap['id']} ({cap['livello']}) WR={cap['wr']:.0%}")
                break

            elif atype == "modifica_size":
                res["size_mult"] *= act.get("params",{}).get("mult",1.0)
                self._fire(cap["id"], contesto)

            elif atype == "boost_soglia":
                res["soglia_boost"] += act.get("params",{}).get("delta",0.0)
                self._fire(cap["id"], contesto)

            elif atype == "boost_entry":
                res["soglia_boost"] -= abs(act.get("params",{}).get("delta",0.0))
                self._fire(cap["id"], contesto)

            elif atype in ("ripristina_pesi_sc","sblocca_short_ranging",
                           "oracolo_override","blocca_long",
                           "set_soglia_ranging","set_cap2_soglia"):
                res[atype] = act.get("params", True)
                self._fire(cap["id"], contesto)

        return res

    def _check_triggers(self, triggers: list, ctx: dict) -> bool:
        if not triggers:
            return True
        for t in triggers:
            p, op, v = t.get("param"), t.get("op"), t.get("value")
            if p not in ctx or op not in OPS:
                return False
            try:
                if not OPS[op](ctx[p], v):
                    return False
            except Exception:
                return False
        return True

    def _fire(self, cap_id: str, ctx: dict):
        try:
            with sqlite3.connect(self.db_path) as c:
                c.execute("UPDATE capsule SET hits=hits+1 WHERE id=?", (cap_id,))
                c.execute("INSERT INTO capsule_log (ts,capsule_id,asset,event,context_json) VALUES (?,?,?,?,?)",
                    (time.time(), cap_id, self.asset, "FIRED",
                     json.dumps({k:v for k,v in ctx.items() if isinstance(v,(str,int,float,bool))})))
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # APPRENDIMENTO
    # -------------------------------------------------------------------------

    def registra_trade(self, trade: dict):
        trade["_ts"]   = time.time()
        trade["asset"] = self.asset
        self._trade_buffer.append(trade)
        self._trade_count += 1
        is_critico = not trade.get("is_win") and abs(trade.get("pnl",0)) > 5
        if self._trade_count % self.ANALISI_INTERVAL == 0 or is_critico:
            self.analizza_e_genera()
        if self._trade_count % 10 == 0:
            self._pulisci_scadute()

    def analizza_e_genera(self) -> list:
        nuove = []
        trades = list(self._trade_buffer)
        if len(trades) < self.MIN_SAMPLES_L3:
            return nuove
        nuove += self._l2_matrimoni(trades)
        nuove += self._l2_contesto(trades)
        nuove += self._l2_drift(trades)
        nuove += self._l3_streak(trades)
        nuove += self._l3_regime(trades)
        nuove += self._l3_opportunita(trades)
        if nuove:
            self._persisti(nuove)
            self._refresh_cache()
        return nuove

    def _l2_matrimoni(self, trades):
        caps = []
        per_mat = defaultdict(list)
        for t in trades:
            m = t.get("matrimonio","")
            if m: per_mat[m].append(t)
        for mat, tt in per_mat.items():
            if len(tt) < self.MIN_SAMPLES_L2: continue
            wins = sum(1 for t in tt if t.get("is_win"))
            wr   = wins / len(tt)
            pnl  = sum(t.get("pnl",0) for t in tt) / len(tt)
            if wr < 0.35 and pnl < -0.5:
                caps.append(self._build(
                    f"LEARNED_MAT_TOSSICO_{mat}_{self.asset}", "LEARNED", "MATRIMONIO_TOSSICO",
                    f"Matrimonio {mat} tossico su {self.asset}: WR {wr:.0%} n={len(tt)}",
                    [{"param":"matrimonio","op":"==","value":mat}],
                    {"type":"blocca_entry","params":{"reason":f"MAT_TOSSICO_{mat}"}},
                    len(tt), wr, pnl, time.time()+self.MAX_AGE_L2))
                log.info(f"[CM] 🧬 L2 MAT TOSSICO: {mat} WR={wr:.0%} n={len(tt)}")
            elif wr > 0.70 and pnl > 0.5:
                caps.append(self._build(
                    f"LEARNED_MAT_OPP_{mat}_{self.asset}", "LEARNED", "MATRIMONIO_OPP",
                    f"Matrimonio {mat} opportunità su {self.asset}: WR {wr:.0%}",
                    [{"param":"matrimonio","op":"==","value":mat}],
                    {"type":"boost_entry","params":{"delta":5.0,"reason":f"OPP_{mat}"}},
                    len(tt), wr, pnl, time.time()+self.MAX_AGE_L2))
                log.info(f"[CM] 🌟 L2 MAT OPP: {mat} WR={wr:.0%} n={len(tt)}")
        return caps

    def _l2_contesto(self, trades):
        caps = []
        per_ctx = defaultdict(list)
        for t in trades:
            ctx = (t.get("momentum",""), t.get("volatility",""), t.get("trend",""))
            if all(ctx): per_ctx[ctx].append(t)
        for ctx, tt in per_ctx.items():
            if len(tt) < self.MIN_SAMPLES_L2: continue
            wins = sum(1 for t in tt if t.get("is_win"))
            wr   = wins / len(tt)
            pnl  = sum(t.get("pnl",0) for t in tt) / len(tt)
            mom, vol, trend = ctx
            if wr < 0.30:
                caps.append(self._build(
                    f"LEARNED_CTX_{mom}_{vol}_{trend}_{self.asset}", "LEARNED", "CONTESTO_TOSSICO",
                    f"Contesto {mom}/{vol}/{trend} tossico su {self.asset}: WR {wr:.0%}",
                    [{"param":"momentum","op":"==","value":mom},
                     {"param":"volatility","op":"==","value":vol},
                     {"param":"trend","op":"==","value":trend}],
                    {"type":"blocca_entry","params":{"reason":f"CTX_TOSSICO_{mom}_{vol}_{trend}"}},
                    len(tt), wr, pnl, time.time()+self.MAX_AGE_L2))
                log.info(f"[CM] 🧬 L2 CTX TOSSICO: {mom}/{vol}/{trend} WR={wr:.0%}")
        return caps

    def _l2_drift(self, trades):
        caps = []
        long_loss = [t for t in trades if not t.get("is_win") and t.get("direction","")=="LONG" and "drift" in t]
        long_win  = [t for t in trades if t.get("is_win")     and t.get("direction","")=="LONG" and "drift" in t]
        if len(long_loss) >= self.MIN_SAMPLES_L2 and len(long_win) >= 3:
            avg_loss = sum(t["drift"] for t in long_loss) / len(long_loss)
            avg_win  = sum(t["drift"] for t in long_win)  / len(long_win)
            std = (sum((t["drift"]-avg_loss)**2 for t in long_loss)/len(long_loss))**0.5
            soglia = min(-0.05, avg_loss + std)
            if avg_loss < -0.03 and avg_win > avg_loss + 0.02:
                caps.append(self._build(
                    f"LEARNED_DRIFT_LONG_{self.asset}", "LEARNED", "DRIFT_VETO",
                    f"LONG con drift<{soglia:+.3f}% sistematicamente in loss su {self.asset}",
                    [{"param":"drift_pct","op":"<","value":round(soglia,4)},
                     {"param":"direction","op":"==","value":"LONG"}],
                    {"type":"blocca_entry","params":{"reason":f"DRIFT_VETO_LONG_{soglia:+.3f}"}},
                    len(long_loss), 0.0, avg_loss, time.time()+self.MAX_AGE_L2))
                log.info(f"[CM] 🧭 L2 DRIFT VETO LONG soglia={soglia:+.3f}%")
        return caps

    def _l3_streak(self, trades):
        caps = []
        streak = 0
        for t in reversed(list(trades)[-10:]):
            if not t.get("is_win"): streak += 1
            else: break
        if streak >= 3:
            pnl = sum(t.get("pnl",0) for t in list(trades)[-streak:])
            caps.append(self._build(
                f"AUTO_LOSS_STREAK_{self.asset}", "AUTO", "LOSS_STREAK",
                f"Loss streak {streak} su {self.asset} (PnL={pnl:+.2f}$)",
                [],
                {"type":"boost_soglia","params":{"delta":min(15,streak*4),"reason":f"STREAK_{streak}"}},
                streak, 0.0, pnl/max(1,streak), time.time()+1800))
            log.info(f"[CM] ⚠️ L3 LOSS STREAK {streak}")
        return caps

    def _l3_regime(self, trades):
        caps = []
        regime = self._ctx.get("regime","")
        if not regime: return caps
        rt = [t for t in trades if t.get("regime")==regime]
        if len(rt) < self.MIN_SAMPLES_L3: return caps
        wins = sum(1 for t in rt if t.get("is_win"))
        wr   = wins / len(rt)
        if wr < 0.30:
            caps.append(self._build(
                f"AUTO_REGIME_TOSSICO_{regime}_{self.asset}", "AUTO", "REGIME_TOSSICO",
                f"Regime {regime} tossico su {self.asset}: WR {wr:.0%} n={len(rt)}",
                [{"param":"regime","op":"==","value":regime}],
                {"type":"blocca_entry","params":{"reason":f"REGIME_TOSSICO_{regime}"}},
                len(rt), wr, 0.0, time.time()+self.MAX_AGE_L3))
            log.info(f"[CM] 🚨 L3 REGIME TOSSICO {regime} WR={wr:.0%}")
        return caps

    def _l3_opportunita(self, trades):
        caps = []
        regime = self._ctx.get("regime","")
        recent = [t for t in list(trades)[-20:] if t.get("regime")==regime]
        if len(recent) < self.MIN_SAMPLES_L3: return caps
        wins = sum(1 for t in recent if t.get("is_win"))
        wr   = wins / len(recent)
        pnl  = sum(t.get("pnl",0) for t in recent) / len(recent)
        if wr >= 0.75 and pnl > 0.5:
            boost = min(1.4, 1.0 + (wr-0.65)*2.0)
            caps.append(self._build(
                f"AUTO_OPP_{regime}_{self.asset}", "AUTO", "OPPORTUNITA",
                f"Opportunità {regime} su {self.asset}: WR {wr:.0%} boost={boost:.2f}x",
                [{"param":"regime","op":"==","value":regime}],
                {"type":"modifica_size","params":{"mult":boost,"reason":f"OPP_{regime}"}},
                len(recent), wr, pnl, time.time()+self.MAX_AGE_L3))
            log.info(f"[CM] ⭐ L3 OPP {regime} WR={wr:.0%} boost={boost:.2f}x")
        return caps

    def _build(self, cap_id, livello, tipo, descrizione, trigger, azione,
               samples=0, wr=0.0, pnl_avg=0.0, scade_ts=None):
        return {"id":cap_id,"asset":self.asset,"livello":livello,"tipo":tipo,
                "descrizione":descrizione,"trigger":trigger,"azione":azione,
                "priority":2 if livello=="AUTO" else 3,
                "samples":samples,"wr":round(wr,3),"pnl_avg":round(pnl_avg,3),
                "scade_ts":scade_ts}

    def _persisti(self, nuove):
        try:
            with sqlite3.connect(self.db_path) as c:
                existing = {r[0] for r in c.execute("SELECT id FROM capsule").fetchall()}
                for cap in nuove:
                    if cap["id"] in existing:
                        c.execute("UPDATE capsule SET samples=?,wr=?,pnl_avg=?,scade_ts=? WHERE id=?",
                            (cap["samples"],cap["wr"],cap["pnl_avg"],cap.get("scade_ts"),cap["id"]))
                    else:
                        c.execute("""INSERT INTO capsule
                            (id,asset,livello,tipo,descrizione,trigger_json,azione_json,
                             priority,enabled,samples,wr,pnl_avg,created_ts,scade_ts)
                            VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?,?)""",
                            (cap["id"],cap["asset"],cap["livello"],cap["tipo"],
                             cap.get("descrizione",""),
                             json.dumps(cap.get("trigger",[])),
                             json.dumps(cap["azione"]),
                             cap.get("priority",5),
                             cap["samples"],cap["wr"],cap["pnl_avg"],
                             time.time(),cap.get("scade_ts")))
                        log.info(f"[CM] 💾 Nuova: {cap['id']} ({cap['livello']})")
        except Exception as e:
            log.error(f"[CM] persisti: {e}")

    def _pulisci_scadute(self):
        try:
            with sqlite3.connect(self.db_path) as c:
                c.execute("UPDATE capsule SET enabled=0 WHERE scade_ts IS NOT NULL AND scade_ts<=? AND enabled=1",
                    (time.time(),))
                n = c.execute("SELECT changes()").fetchone()[0]
                if n: log.info(f"[CM] 🗑️ {n} capsule scadute disabilitate")
        except Exception as e:
            log.error(f"[CM] pulisci: {e}")

    # -------------------------------------------------------------------------
    # API DASHBOARD
    # -------------------------------------------------------------------------

    def get_all_for_dashboard(self) -> list:
        try:
            ora = time.time()
            with sqlite3.connect(self.db_path) as c:
                rows = c.execute("""
                    SELECT id,asset,livello,tipo,descrizione,enabled,samples,wr,pnl_avg,
                           hits,hits_saved,created_ts,scade_ts,note,trigger_json,azione_json
                    FROM capsule WHERE asset=? OR asset='ALL'
                    ORDER BY enabled DESC, priority ASC
                """, (self.asset,)).fetchall()
            result = []
            for r in rows:
                scade_in = int(max(0, r[12]-ora)) if r[12] else None
                result.append({
                    "id":r[0],"asset":r[1],"livello":r[2],"tipo":r[3],
                    "descrizione":r[4],"enabled":bool(r[5]),
                    "samples":r[6],"wr":r[7],"pnl_avg":r[8],
                    "hits":r[9],"hits_saved":r[10],
                    "created_ago":int(ora-r[11]),"scade_in":scade_in,
                    "note":r[13],
                    "trigger":json.loads(r[14]),"azione":json.loads(r[15]),
                })
            return result
        except Exception as e:
            log.error(f"[CM] dashboard: {e}")
            return []

    def toggle_capsule(self, capsule_id: str, enabled: bool) -> bool:
        try:
            with sqlite3.connect(self.db_path) as c:
                c.execute("UPDATE capsule SET enabled=? WHERE id=?", (1 if enabled else 0, capsule_id))
                changed = c.execute("SELECT changes()").fetchone()[0]
            if changed:
                self._refresh_cache()
                log.info(f"[CM] {'✅' if enabled else '🔴'} {'ON' if enabled else 'OFF'}: {capsule_id}")
                return True
            return False
        except Exception as e:
            log.error(f"[CM] toggle: {e}")
            return False

    def delete_capsule(self, capsule_id: str) -> bool:
        """Elimina LEARNED/AUTO. Le STATIC sono inviolabili — usa toggle per disabilitarle."""
        try:
            with sqlite3.connect(self.db_path) as c:
                row = c.execute("SELECT livello FROM capsule WHERE id=?", (capsule_id,)).fetchone()
                if not row: return False
                if row[0] == "STATIC":
                    log.warning(f"[CM] ⚠️ STATIC non eliminabile: usa toggle per disabilitare {capsule_id}")
                    return False
                c.execute("DELETE FROM capsule WHERE id=?", (capsule_id,))
            self._refresh_cache()
            log.info(f"[CM] 🗑️ Eliminata: {capsule_id}")
            return True
        except Exception as e:
            log.error(f"[CM] delete: {e}")
            return False

    def get_stats(self) -> dict:
        try:
            caps = self.get_all_for_dashboard()
            attive = [c for c in caps if c["enabled"]]
            return {
                "attive":          len(attive),
                "static":          len([c for c in attive if c["livello"]=="STATIC"]),
                "learned":         len([c for c in attive if c["livello"]=="LEARNED"]),
                "auto":            len([c for c in attive if c["livello"]=="AUTO"]),
                "scadono_presto":  len([c for c in attive if c["scade_in"] is not None and c["scade_in"]<300]),
                "trade_osservati": self._trade_count,
                "capsule_list":    caps[:30],
            }
        except Exception:
            return {"attive":0,"static":0,"learned":0,"auto":0,"trade_osservati":0}

    # Compatibility
    def reload(self) -> bool:
        self._refresh_cache()
        return True

    @property
    def capsules(self):
        return self._cache

    def _check_trigger(self, trigger: dict, ctx: dict) -> bool:
        return self._check_triggers([trigger], ctx)

# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════════
 CAPSULA CANVAS — Skill esterna che vive accanto al motore V16
═══════════════════════════════════════════════════════════════════════

ARCHITETTURA:
  Questo file vive separato da OVERTOP_BASSANO_V16_PRODUCTION.py.
  Il motore lo importa con 5 righe di hook.
  Tutto il cervello della capsula è qui dentro.

LIVELLI (armabili via env var Render):
  L1 OCCHI       — vede entry/exit/deposizione capsule (sempre ON)
  L2 MEMORIA     — ricorda snapshot in DB (sempre ON)
  L3 RAGIONA     — confronta presente con passato (env CANVAS_L3=on)
  L4 DIALOGA AI  — chiama DeepSeek/Claude (env CANVAS_L4=on)
  L5 GENERA      — crea capsule-figlie in quarantena (env CANVAS_L5=on)
  L6 SE STESSA   — auto-osservazione, auto-correzione (env CANVAS_L6=on)

KILL SWITCH:
  env CANVAS_DEAD=true → tutti i metodi diventano no-op, capsula silente.

SICUREZZA:
  - Nessun metodo modifica direttamente trade/decisioni del bot
  - Solo OSSERVA + RICORDA + (livelli superiori) PROPONE
  - Roberto promuove/disabilita via endpoint Canvas Vivo
  - In caso di errore: try/except totale, mai blocca il motore

NOTE DEPLOY:
  - File da mettere nella stessa cartella di OVERTOP_BASSANO_V16_PRODUCTION.py
  - Import in V16: `from capsula_canvas import CapsulaCanvas`
  - 3 hook in V16 (entry, exit, log) — tutti try/except
  - DB tabella creata automaticamente al primo avvio
"""

import os
import json
import time
import sqlite3
import logging
from typing import Optional, Dict, Any, List

log = logging.getLogger("CAPSULA_CANVAS")

# ─────────────────────────────────────────────────────────────────────
# FLAGS LIVELLI (controllati da env Render)
# ─────────────────────────────────────────────────────────────────────

def _env_bool(name: str, default: bool = False) -> bool:
    """Legge env var come bool. 'true', '1', 'on', 'yes' = True."""
    val = os.environ.get(name, "").strip().lower()
    if not val:
        return default
    return val in ("true", "1", "on", "yes", "si", "sì")

CANVAS_DEAD = _env_bool("CANVAS_DEAD", False)          # kill switch
CANVAS_L1   = _env_bool("CANVAS_L1", True)             # OCCHI (default ON)
CANVAS_L2   = _env_bool("CANVAS_L2", True)             # MEMORIA (default ON)
CANVAS_L3   = _env_bool("CANVAS_L3", False)            # RAGIONA (default OFF)
CANVAS_L4   = _env_bool("CANVAS_L4", False)            # DIALOGA AI (default OFF)
CANVAS_L5   = _env_bool("CANVAS_L5", False)            # GENERA (default OFF)
CANVAS_L6   = _env_bool("CANVAS_L6", False)            # SE STESSA (default OFF)


# ─────────────────────────────────────────────────────────────────────
# CLASSE PRINCIPALE
# ─────────────────────────────────────────────────────────────────────

class CapsulaCanvas:
    """
    La Skill che vive accanto al motore V16.

    Vita della capsula:
      1. All'avvio del bot, V16 fa: self.canvas = CapsulaCanvas(bot_ref=self, db_path=DB_PATH)
      2. Ad ogni entry M2: V16 chiama self.canvas.observe_entry(snapshot)
      3. Ad ogni exit M2:  V16 chiama self.canvas.observe_exit(trade_id, outcome)
      4. Ad ogni deposizione capsule: V16 chiama self.canvas.observe_capsule_vote(voto)

    La capsula non decide niente. Osserva, ricorda, in futuro consiglia.
    """

    # Versione skill (cambierà ad ogni evoluzione auto-correttiva)
    VERSION = "1.1.0"

    # Schema tabella DB
    SCHEMA_SNAPSHOTS = """
    CREATE TABLE IF NOT EXISTS canvas_snapshots (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ts              REAL NOT NULL DEFAULT (strftime('%s','now')),
        evento          TEXT NOT NULL,
        trade_id        TEXT,
        fingerprint     TEXT,
        sensori_json    TEXT,
        capsule_voto    TEXT,
        sc_decision     TEXT,
        outcome         TEXT,
        pnl_netto       REAL,
        durata_s        REAL,
        reason          TEXT,
        note            TEXT
    );
    """

    SCHEMA_SCOPERTE = """
    CREATE TABLE IF NOT EXISTS canvas_scoperte (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ts              REAL NOT NULL DEFAULT (strftime('%s','now')),
        livello         INTEGER NOT NULL,
        tipo            TEXT NOT NULL,
        titolo          TEXT NOT NULL,
        descrizione     TEXT,
        evidenza_json   TEXT,
        confidence      REAL DEFAULT 0.0,
        promossa        INTEGER DEFAULT 0,
        roberto_note    TEXT
    );
    """

    SCHEMA_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_canvas_snap_ts ON canvas_snapshots(ts);
    CREATE INDEX IF NOT EXISTS idx_canvas_snap_evento ON canvas_snapshots(evento);
    CREATE INDEX IF NOT EXISTS idx_canvas_snap_trade ON canvas_snapshots(trade_id);
    CREATE INDEX IF NOT EXISTS idx_canvas_scop_ts ON canvas_scoperte(ts);
    """

    def __init__(self, bot_ref=None, db_path: Optional[str] = None):
        """
        Args:
            bot_ref: riferimento all'oggetto bot V16 (per leggere stato)
            db_path: percorso DB SQLite (default: /var/data/trading_data.db)
        """
        self.bot = bot_ref
        self.db_path = db_path or os.environ.get("DB_PATH", "/var/data/trading_data.db")
        self._initialized = False
        self._error_count = 0
        self._observe_count = 0

        # Pending entries: aspettano l'exit per chiudere il cerchio
        # { trade_id: {entry_snapshot, ts_entry} }
        self._pending_entries: Dict[str, Dict[str, Any]] = {}

        # Stato livelli (snapshot all'inizio per debug)
        self.levels_armed = {
            "L1_OCCHI":      CANVAS_L1 and not CANVAS_DEAD,
            "L2_MEMORIA":    CANVAS_L2 and not CANVAS_DEAD,
            "L3_RAGIONA":    CANVAS_L3 and not CANVAS_DEAD,
            "L4_DIALOGA_AI": CANVAS_L4 and not CANVAS_DEAD,
            "L5_GENERA":     CANVAS_L5 and not CANVAS_DEAD,
            "L6_SE_STESSA":  CANVAS_L6 and not CANVAS_DEAD,
        }

        if CANVAS_DEAD:
            log.warning("[CAPSULA_CANVAS] KILL SWITCH attivo (CANVAS_DEAD=true). Tutte le funzioni silenziate.")
            return

        try:
            self._init_db()
            self._initialized = True
            log.info(f"[CAPSULA_CANVAS] v{self.VERSION} inizializzata. "
                     f"Livelli armati: {[k for k,v in self.levels_armed.items() if v]}")
        except Exception as e:
            log.error(f"[CAPSULA_CANVAS] init fallita: {e}")

    # ═══════════════════════════════════════════════════════════════
    # DB SETUP
    # ═══════════════════════════════════════════════════════════════

    def _init_db(self):
        """Crea le tabelle se non esistono. Idempotente."""
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            cur = conn.cursor()
            cur.executescript(self.SCHEMA_SNAPSHOTS)
            cur.executescript(self.SCHEMA_SCOPERTE)
            cur.executescript(self.SCHEMA_INDEX)
            conn.commit()

    def _db_write(self, sql: str, params: tuple):
        """Scrittura DB sicura con timeout e gestione errori."""
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                conn.execute(sql, params)
                conn.commit()
            return True
        except Exception as e:
            self._error_count += 1
            log.debug(f"[CANVAS_DB_WRITE_ERR] {e}")
            return False

    # ═══════════════════════════════════════════════════════════════
    # LIVELLO 1 — OCCHI (sempre ON di default)
    # ═══════════════════════════════════════════════════════════════

    def observe_entry(self, verbale: Dict[str, Any], trade_id: str = None) -> None:
        """
        Hook chiamato da V16 alla DEPOSIZIONE CAPSULE.
        v1.1: registra come ENTRY_VALUTAZIONE (può essere bloccata dopo).
        L'apertura vera è registrata da observe_entry_open() chiamato
        dal motore SOLO quando self._shadow viene creato.

        Args:
            verbale: dict con tutti i sensori, capsule_voto, sc_decision, ecc.
            trade_id: identificatore unico (dovrebbe arrivare anche a observe_entry_open)
        """
        if CANVAS_DEAD or not self.levels_armed["L1_OCCHI"] or not self._initialized:
            return

        try:
            tid = trade_id or f"v_{int(time.time()*1000)}"

            # Estraggo fingerprint (v1.1: leggo direction dal verbale o dai sensori)
            _dir = verbale.get('direction') or verbale.get('_dir') or '?'
            fp = f"{verbale.get('momentum','?')}|{verbale.get('volatility','?')}|" \
                 f"{verbale.get('trend','?')}|{_dir}"

            # Snapshot sensori
            sensori = {
                "score":        verbale.get("score"),
                "soglia":       verbale.get("soglia"),
                "fp_wr":        verbale.get("fp_wr"),
                "drift":        verbale.get("drift"),
                "oi_carica":    verbale.get("oi_carica"),
                "oi_carica_short": verbale.get("oi_carica_short"),
                "breath_fase":  verbale.get("breath_fase"),
                "breath_energia": verbale.get("breath_energia"),
                "regime":       verbale.get("regime"),
            }

            capsule_voto = {
                "block_score":     verbale.get("capsule_block_score"),
                "boost_score":     verbale.get("capsule_boost_score"),
                "threshold_delta": verbale.get("capsule_threshold_delta"),
                "reasons":         verbale.get("capsule_reasons", []),
                "oracolo_override": verbale.get("capsule_oracolo_override"),
            }

            sc_decision = verbale.get("sc_decision") or ""
            blocked_by = verbale.get("blocked_by") or ""

            # v1.1: evento differenziato
            # Se blocked_by è già settato → la valutazione è bloccata da una patch
            evento = "ENTRY_VALUTAZIONE_BLOCCATA" if blocked_by else "ENTRY_VALUTAZIONE"

            # Salvo nello stato pending (aspetta l'apertura vera per promuoverlo)
            self._pending_entries[tid] = {
                "entry_ts": time.time(),
                "fingerprint": fp,
                "sensori": sensori,
                "capsule_voto": capsule_voto,
                "promosso_a_open": False,
            }

            if self.levels_armed["L2_MEMORIA"]:
                self._db_write(
                    """INSERT INTO canvas_snapshots
                       (evento, trade_id, fingerprint, sensori_json, capsule_voto, sc_decision, note)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        evento, tid, fp,
                        json.dumps(sensori, default=str),
                        json.dumps(capsule_voto, default=str),
                        sc_decision,
                        blocked_by,
                    )
                )

            self._observe_count += 1

            if self.levels_armed["L3_RAGIONA"]:
                self._reason_on_entry(tid, fp, sensori, capsule_voto)

        except Exception as e:
            self._error_count += 1
            log.debug(f"[CANVAS_OBSERVE_ENTRY_ERR] {e}")

    def observe_entry_open(self, trade_id: str, shadow_dict: Dict[str, Any]) -> None:
        """
        v1.1: Hook chiamato da V16 SUBITO DOPO `self._shadow = {...}`.
        Questa è l'apertura REALE del trade. Promuove la valutazione precedente
        (se trovata) e salva un evento ENTRY_OPEN_REALE per chiusura del cerchio.

        Args:
            trade_id: identificatore unico (stesso usato in observe_entry)
            shadow_dict: contenuto di self._shadow (sensori finali dell'apertura)
        """
        if CANVAS_DEAD or not self.levels_armed["L1_OCCHI"] or not self._initialized:
            return

        try:
            tid = trade_id or f"o_{int(time.time()*1000)}"

            # Promuovo la valutazione pending (se esiste)
            pending = self._pending_entries.get(tid)
            if pending:
                pending["promosso_a_open"] = True
                pending["open_ts"] = time.time()
                pending["shadow_open"] = {
                    "price_entry":  shadow_dict.get("price_entry"),
                    "direction":    shadow_dict.get("direction"),
                    "score":        shadow_dict.get("score"),
                    "soglia":       shadow_dict.get("soglia"),
                    "size":         shadow_dict.get("size"),
                    "fingerprint_wr": shadow_dict.get("fingerprint_wr"),
                    "seed":         shadow_dict.get("seed"),
                    "regime_entry": shadow_dict.get("regime_entry"),
                    "matrimonio":   shadow_dict.get("matrimonio"),
                }
                fp = pending["fingerprint"]
            else:
                # Nessuna valutazione pending: registro comunque
                fp = f"{shadow_dict.get('momentum_entry','?')}|" \
                     f"{shadow_dict.get('volatility_entry','?')}|" \
                     f"{shadow_dict.get('trend_entry','?')}|" \
                     f"{shadow_dict.get('direction','?')}"

            # Scrivo evento ENTRY_OPEN_REALE in DB
            if self.levels_armed["L2_MEMORIA"]:
                self._db_write(
                    """INSERT INTO canvas_snapshots
                       (evento, trade_id, fingerprint, sensori_json, note)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        "ENTRY_OPEN_REALE", tid, fp,
                        json.dumps({
                            "price_entry":  shadow_dict.get("price_entry"),
                            "direction":    shadow_dict.get("direction"),
                            "score":        shadow_dict.get("score"),
                            "soglia":       shadow_dict.get("soglia"),
                            "size":         shadow_dict.get("size"),
                            "fingerprint_wr": shadow_dict.get("fingerprint_wr"),
                            "seed":         shadow_dict.get("seed"),
                            "regime_entry": shadow_dict.get("regime_entry"),
                            "matrimonio":   shadow_dict.get("matrimonio"),
                        }, default=str),
                        "promosso_da_valutazione" if pending else "open_senza_valutazione_pending",
                    )
                )

        except Exception as e:
            self._error_count += 1
            log.debug(f"[CANVAS_OBSERVE_OPEN_ERR] {e}")

    def observe_exit(self, trade_id: str, outcome: str, pnl_netto: float,
                     durata_s: float, reason: str = "") -> None:
        """
        Hook chiamato da V16 quando un trade si chiude.

        Args:
            trade_id: identificatore (stesso usato in observe_entry)
            outcome: WIN_NET / LOSS_REAL / LOSS_FEE / ZONA_MORTA / ...
            pnl_netto: PnL al netto delle fee
            durata_s: durata trade in secondi
            reason: motivo chiusura (es. EXIT_E15_S35_WIN_NET_+1)
        """
        if CANVAS_DEAD or not self.levels_armed["L1_OCCHI"] or not self._initialized:
            return

        try:
            pending = self._pending_entries.pop(trade_id, None)
            fp = pending["fingerprint"] if pending else "?|?|?|?"

            # Scrivo exit + chiudo cerchio con entry
            if self.levels_armed["L2_MEMORIA"]:
                self._db_write(
                    """INSERT INTO canvas_snapshots
                       (evento, trade_id, fingerprint, outcome, pnl_netto, durata_s, reason)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    ("EXIT", trade_id, fp, outcome, pnl_netto, durata_s, reason)
                )

            # L3 RAGIONA — chiudo cerchio (confronto previsto vs reale)
            if self.levels_armed["L3_RAGIONA"] and pending:
                self._reason_on_exit(trade_id, pending, outcome, pnl_netto, durata_s)

        except Exception as e:
            self._error_count += 1
            log.debug(f"[CANVAS_OBSERVE_EXIT_ERR] {e}")

    # ═══════════════════════════════════════════════════════════════
    # LIVELLO 3 — RAGIONA (env CANVAS_L3=on)
    # Placeholder: implementato quando armiamo L3
    # ═══════════════════════════════════════════════════════════════

    def _reason_on_entry(self, tid, fp, sensori, capsule_voto):
        """Confronta entry corrente con snapshot passati simili."""
        # PLACEHOLDER L3 — implementazione completa nella prossima patch
        pass

    def _reason_on_exit(self, tid, pending, outcome, pnl, durata):
        """Confronta outcome reale con previsione delle capsule."""
        # PLACEHOLDER L3 — implementazione completa nella prossima patch
        pass

    # ═══════════════════════════════════════════════════════════════
    # LIVELLO 4 — DIALOGA AI (env CANVAS_L4=on)
    # Placeholder: implementato quando armiamo L4
    # ═══════════════════════════════════════════════════════════════

    def ask_deepseek(self, prompt: str, context: dict) -> Optional[dict]:
        """Chiama DeepSeek API con prompt + memoria capsula."""
        if not self.levels_armed["L4_DIALOGA_AI"]:
            return None
        # PLACEHOLDER L4 — implementazione completa nella prossima patch
        return None

    # ═══════════════════════════════════════════════════════════════
    # LIVELLO 5 — GENERA capsule-figlie (env CANVAS_L5=on)
    # Placeholder
    # ═══════════════════════════════════════════════════════════════

    def generate_child_capsule(self, scoperta_id: int) -> Optional[str]:
        """Genera capsula-figlia da scoperta. Mette in quarantena."""
        if not self.levels_armed["L5_GENERA"]:
            return None
        # PLACEHOLDER L5
        return None

    # ═══════════════════════════════════════════════════════════════
    # LIVELLO 6 — AUTO-OSSERVAZIONE (env CANVAS_L6=on)
    # Placeholder
    # ═══════════════════════════════════════════════════════════════

    def self_check_metrics(self) -> dict:
        """Misura performance della capsula stessa."""
        if not self.levels_armed["L6_SE_STESSA"]:
            return {"l6": "disarmato"}
        # PLACEHOLDER L6
        return {}

    # ═══════════════════════════════════════════════════════════════
    # API DI INTROSPEZIONE (usabile dall'endpoint Canvas)
    # ═══════════════════════════════════════════════════════════════

    def status(self) -> dict:
        """Stato della capsula per dashboard."""
        return {
            "version":         self.VERSION,
            "initialized":     self._initialized,
            "kill_switch":     CANVAS_DEAD,
            "levels_armed":    self.levels_armed,
            "observe_count":   self._observe_count,
            "error_count":     self._error_count,
            "pending_entries": len(self._pending_entries),
            "db_path":         self.db_path,
        }

    def recent_snapshots(self, n: int = 20) -> List[dict]:
        """Ultimi N snapshot per dashboard."""
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM canvas_snapshots ORDER BY id DESC LIMIT ?",
                    (n,)
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            log.debug(f"[CANVAS_RECENT_ERR] {e}")
            return []

    def recent_scoperte(self, n: int = 20) -> List[dict]:
        """Ultime N scoperte per dashboard."""
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM canvas_scoperte ORDER BY id DESC LIMIT ?",
                    (n,)
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            log.debug(f"[CANVAS_SCOPERTE_ERR] {e}")
            return []


# ─────────────────────────────────────────────────────────────────────
# SINGLETON GLOBALE (opzionale - utile per accesso da app.py)
# ─────────────────────────────────────────────────────────────────────

_canvas_singleton: Optional[CapsulaCanvas] = None

def get_canvas() -> Optional[CapsulaCanvas]:
    """Restituisce il singleton se inizializzato."""
    return _canvas_singleton

def set_canvas(canvas: CapsulaCanvas) -> None:
    """Imposta il singleton (chiamato da V16 all'avvio)."""
    global _canvas_singleton
    _canvas_singleton = canvas


# ─────────────────────────────────────────────────────────────────────
# FINE FILE capsula_canvas.py
# ─────────────────────────────────────────────────────────────────────

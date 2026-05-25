# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════════
 CAPSULA FASE — Riconoscimento Adattivo delle Fasi di Mercato
═══════════════════════════════════════════════════════════════════════

AUTORE INTELLETTUALE: Roberto Zan (25 maggio 2026)
SCOPERTA FONDANTE: I trade WIN e LOSS arrivano a CLUSTER TEMPORALI.
                   Il discriminante non è il singolo testimone all'entry,
                   ma la FASE del mercato negli ultimi 5-10 minuti.

ARCHITETTURA:
  File separato che vive accanto al motore V16.
  Il motore lo importa con 4 hook minimali (3-5 righe ognuno).
  TUTTO il cervello della capsula è qui dentro.

LIVELLI (armabili via env Render):
  L1 OCCHI         — registra snapshot fase ad ogni entry (default ON)
  L2 MEMORIA       — collega fase entry → esito trade (default ON)
  L3 PROPONE       — modalità OBSERVER: suggerisce blocchi ma NON blocca
                     (default ON)
  L4 DECIDE        — modalità BLOCCANTE: il bot rispetta il verdetto
                     (default OFF — armare solo dopo che L3 dimostra
                      precisione ≥70% su almeno 50 osservazioni)
  L5 IMPARA        — ricalcola soglie ottimali dai dati ogni 50 trade
                     (default OFF — armare dopo 200+ osservazioni)
  L6 AUTO-QUARANTENA — si disattiva da sola se danneggia per 24h consecutive
                       (default OFF — sicurezza ultima)

KILL SWITCH GENERALE: env CAPSULA_FASE_DEAD=true

SOGLIE INIZIALI: derivate dalle osservazioni di Roberto del 25/05/2026:
  - 13:10-13:28: prezzo -$79 in 18min mentre bot apriva 4 LOSS LONG
  - 06:09-06:36: prezzo +$50 in 27min con 3 WIN LONG consecutivi
  Soglie partono da: d10=35, d5=22, d2=8 USD (calibrabili via L5)

SICUREZZA:
  - Nessun metodo modifica direttamente trade/decisioni del bot
  - L3 SUGGERISCE: scrive verdetto in DB ma NON blocca
  - L4 BLOCCA: ma SOLO se armato esplicitamente
  - In caso di errore: try/except totale, ritorna sempre "lascia passare"
  - Fail-open: se la capsula crasha, il bot continua come se non esistesse

NOTE DEPLOY:
  - File da mettere accanto a OVERTOP_BASSANO_V16_PRODUCTION.py
  - Import in V16: from capsula_fase import CapsulaFase
  - 4 hook in V16 (init, feed_tick, consulta, observe_outcome)
  - DB tabelle create automaticamente al primo avvio
═══════════════════════════════════════════════════════════════════════
"""

import os
import json
import time
import sqlite3
import logging
from collections import deque
from typing import Optional, Dict, Any, Tuple, List

log = logging.getLogger("CAPSULA_FASE")

# ─────────────────────────────────────────────────────────────────────
# FLAGS LIVELLI (controllati da env Render)
# ─────────────────────────────────────────────────────────────────────

def _env_bool(name: str, default: bool = False) -> bool:
    """Legge env var come bool. 'true', '1', 'on', 'yes' = True."""
    val = os.environ.get(name, "").strip().lower()
    if not val:
        return default
    return val in ("true", "1", "on", "yes", "si", "sì")


def _env_float(name: str, default: float) -> float:
    """Legge env var come float. Se non valida, ritorna default."""
    try:
        val = os.environ.get(name, "").strip()
        if not val:
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


def _env_int(name: str, default: int) -> int:
    """Legge env var come int. Se non valida, ritorna default."""
    try:
        val = os.environ.get(name, "").strip()
        if not val:
            return default
        return int(val)
    except (ValueError, TypeError):
        return default


CAPSULA_FASE_DEAD = _env_bool("CAPSULA_FASE_DEAD", False)         # kill switch generale
CAPSULA_FASE_L1   = _env_bool("CAPSULA_FASE_L1",   True)          # OCCHI (default ON)
CAPSULA_FASE_L2   = _env_bool("CAPSULA_FASE_L2",   True)          # MEMORIA (default ON)
CAPSULA_FASE_L3   = _env_bool("CAPSULA_FASE_L3",   True)          # PROPONE (default ON)
CAPSULA_FASE_L4   = _env_bool("CAPSULA_FASE_L4",   False)         # DECIDE (default OFF)
CAPSULA_FASE_L5   = _env_bool("CAPSULA_FASE_L5",   False)         # IMPARA (default OFF)
CAPSULA_FASE_L6   = _env_bool("CAPSULA_FASE_L6",   False)         # AUTO-QUARANTENA (default OFF)


# ─────────────────────────────────────────────────────────────────────
# CLASSE PRINCIPALE
# ─────────────────────────────────────────────────────────────────────

class CapsulaFase:
    """
    La Skill che riconosce la FASE del mercato e la usa per decidere
    se aprire un trade è coerente con la fisica del momento.

    Vita della capsula:
      1. All'avvio del bot: self.capsula_fase = CapsulaFase(bot_ref=self, db_path=DB_PATH)
      2. Ad ogni tick: self.capsula_fase.feed_tick(now, price)  # alimentazione
      3. Pre-entry: ok, motivo = self.capsula_fase.consulta(direction)
                    if not ok and self.capsula_fase.is_blocking(): return
      4. Post-exit: self.capsula_fase.observe_outcome(trade_id, outcome, pnl_netto)

    La capsula:
      - L1: VEDE ogni proposta di entry e la registra
      - L2: RICORDA legando fase entry → esito reale
      - L3: PROPONE un verdetto ma NON blocca (default)
      - L4: DECIDE bloccando per davvero (solo se armato)
      - L5: IMPARA le soglie ottimali dai dati storici
      - L6: AUTO-QUARANTINA se sta danneggiando
    """

    VERSION = "1.0.0"

    # ═══════════════════════════════════════════════════════════════
    # SCHEMA DB
    # ═══════════════════════════════════════════════════════════════

    SCHEMA_FASE_OSSERVAZIONI = """
    CREATE TABLE IF NOT EXISTS capsula_fase_osservazioni (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ts              REAL NOT NULL DEFAULT (strftime('%s','now')),
        trade_id        TEXT,
        direction       TEXT,
        price           REAL,
        d2              REAL,
        d5              REAL,
        d10             REAL,
        range10         REAL,
        pos10           REAL,
        coverage_s      REAL,
        n_ticks         INTEGER,
        proposta        TEXT,
        motivo          TEXT,
        bloccante       INTEGER DEFAULT 0,
        outcome         TEXT,
        pnl_netto       REAL,
        durata_s        REAL
    );
    """

    SCHEMA_FASE_VERDETTI = """
    CREATE TABLE IF NOT EXISTS capsula_fase_verdetti (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ts              REAL NOT NULL DEFAULT (strftime('%s','now')),
        stato           TEXT,
        eta_oss         INTEGER,
        blocchi_prop    INTEGER,
        blocchi_giusti  INTEGER,
        blocchi_sbag    INTEGER,
        precisione      REAL,
        soglia_d10      REAL,
        soglia_d5       REAL,
        soglia_d2       REAL,
        note            TEXT
    );
    """

    SCHEMA_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_fase_oss_ts ON capsula_fase_osservazioni(ts);
    CREATE INDEX IF NOT EXISTS idx_fase_oss_trade ON capsula_fase_osservazioni(trade_id);
    CREATE INDEX IF NOT EXISTS idx_fase_oss_outcome ON capsula_fase_osservazioni(outcome);
    CREATE INDEX IF NOT EXISTS idx_fase_ver_ts ON capsula_fase_verdetti(ts);
    """

    # ═══════════════════════════════════════════════════════════════
    # SOGLIE INIZIALI (calibrate sui dati del 25/05/2026)
    # ═══════════════════════════════════════════════════════════════

    SOGLIA_D10_DEFAULT = 35.0   # USD: scarico/carico chiaro a 10 min
    SOGLIA_D5_DEFAULT  = 22.0   # USD: scarico/carico chiaro a 5 min
    SOGLIA_D2_DEFAULT  = 8.0    # USD: conferma micro a 2 min
    MIN_COVERAGE_SEC   = 300.0  # warmup: 5 min minimi di storia
    MIN_TICKS          = 30     # warmup: almeno 30 tick

    # Sicurezza: se eccede questi limiti, ignora il dato
    MAX_DELTA_REASONABLE = 1000.0  # USD: oltre $1000 in 10 min = anomalia

    def __init__(self, bot_ref=None, db_path: Optional[str] = None):
        """
        Args:
            bot_ref: riferimento al bot V16 (per leggere stato se serve)
            db_path: percorso DB SQLite (default: /var/data/trading_data.db)
        """
        self.bot = bot_ref
        self.db_path = db_path or os.environ.get("DB_PATH", "/var/data/trading_data.db")
        self._initialized = False
        self._error_count = 0

        # Stato livelli
        self.levels_armed = {
            "L1_OCCHI":         CAPSULA_FASE_L1 and not CAPSULA_FASE_DEAD,
            "L2_MEMORIA":       CAPSULA_FASE_L2 and not CAPSULA_FASE_DEAD,
            "L3_PROPONE":       CAPSULA_FASE_L3 and not CAPSULA_FASE_DEAD,
            "L4_DECIDE":        CAPSULA_FASE_L4 and not CAPSULA_FASE_DEAD,
            "L5_IMPARA":        CAPSULA_FASE_L5 and not CAPSULA_FASE_DEAD,
            "L6_AUTO_QUAR":     CAPSULA_FASE_L6 and not CAPSULA_FASE_DEAD,
        }

        # Buffer prezzi temporale: deque di tuple (timestamp, prezzo).
        # maxlen alto per non saturare anche con tick veloci; pruning temporale
        # nel feed_tick mantiene solo ultimi 15 minuti reali.
        self._price_buffer: deque = deque(maxlen=30000)

        # Soglie correnti (modificabili da L5 IMPARA)
        # Permette override iniziale via env per esperimenti, ma di default usa
        # i valori scoperti sui dati del 25/05.
        self.soglia_d10 = _env_float("CAPSULA_FASE_SOGLIA_D10", self.SOGLIA_D10_DEFAULT)
        self.soglia_d5  = _env_float("CAPSULA_FASE_SOGLIA_D5",  self.SOGLIA_D5_DEFAULT)
        self.soglia_d2  = _env_float("CAPSULA_FASE_SOGLIA_D2",  self.SOGLIA_D2_DEFAULT)

        # Pending: snapshot ad entry, in attesa di outcome
        self._pending_entries: Dict[str, Dict[str, Any]] = {}

        # Statistiche runtime
        self._stat_osservazioni = 0
        self._stat_blocchi_proposti = 0
        self._stat_blocchi_giusti = 0   # blocco proposto + trade reale sarebbe stato LOSS
        self._stat_blocchi_sbagliati = 0  # blocco proposto + trade reale è stato WIN
        self._stat_passati = 0

        # Auto-quarantena: stato corrente
        self._quarantena_attiva = False
        self._ultimo_check_quarantena_ts = 0

        if CAPSULA_FASE_DEAD:
            log.warning("[CAPSULA_FASE] KILL SWITCH ATTIVO (CAPSULA_FASE_DEAD=true). Tutte le funzioni silenziate.")
            return

        try:
            self._init_db()
            self._initialized = True
            log.info(f"[CAPSULA_FASE] v{self.VERSION} inizializzata. "
                     f"Livelli armati: {[k for k,v in self.levels_armed.items() if v]} | "
                     f"Soglie iniziali d10={self.soglia_d10} d5={self.soglia_d5} d2={self.soglia_d2}")
        except Exception as e:
            log.error(f"[CAPSULA_FASE] init fallita: {e}")

    # ═══════════════════════════════════════════════════════════════
    # DB SETUP
    # ═══════════════════════════════════════════════════════════════

    def _init_db(self):
        """Crea le tabelle se non esistono. Idempotente."""
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            cur = conn.cursor()
            cur.executescript(self.SCHEMA_FASE_OSSERVAZIONI)
            cur.executescript(self.SCHEMA_FASE_VERDETTI)
            cur.executescript(self.SCHEMA_INDEX)
            conn.commit()

    def _db_write(self, sql: str, params: tuple) -> bool:
        """Scrittura DB sicura con timeout. Mai blocca il chiamante."""
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                conn.execute(sql, params)
                conn.commit()
            return True
        except Exception as e:
            self._error_count += 1
            log.debug(f"[FASE_DB_WRITE_ERR] {e}")
            return False

    def _db_read(self, sql: str, params: tuple = ()) -> List[tuple]:
        """Lettura DB sicura. Ritorna [] su errore."""
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                cur = conn.execute(sql, params)
                return cur.fetchall()
        except Exception as e:
            self._error_count += 1
            log.debug(f"[FASE_DB_READ_ERR] {e}")
            return []

    # ═══════════════════════════════════════════════════════════════
    # ALIMENTAZIONE BUFFER PREZZI (chiamata ad ogni tick)
    # ═══════════════════════════════════════════════════════════════

    def feed_tick(self, now: float, price: float) -> None:
        """
        Hook chiamato da V16 ad ogni tick di prezzo.

        Mantiene una finestra temporale REALE di 15 minuti (cutoff temporale,
        non solo maxlen). Questo garantisce 15 min anche con tick a velocità
        variabile.
        """
        if CAPSULA_FASE_DEAD or not self._initialized:
            return

        try:
            self._price_buffer.append((float(now), float(price)))

            # Pruning temporale: teniamo massimo 15 minuti reali (900 sec)
            cutoff = now - 900.0
            while len(self._price_buffer) > 2 and self._price_buffer[0][0] < cutoff:
                self._price_buffer.popleft()

        except Exception as e:
            self._error_count += 1
            log.debug(f"[FASE_FEED_ERR] {e}")

    # ═══════════════════════════════════════════════════════════════
    # SNAPSHOT DELLA FASE (lettura del buffer)
    # ═══════════════════════════════════════════════════════════════

    def _phase_snapshot(self) -> Dict[str, Any]:
        """
        Costruisce snapshot fase corrente: delta 2/5/10 min, range, posizione.

        Ritorna sempre un dict. Se warmup, contiene 'ready'=False e 'reason'.
        """
        try:
            n = len(self._price_buffer)
            if n < self.MIN_TICKS:
                return {"ready": False, "reason": "PHASE_INSUFFICIENT_TICKS", "n": n}

            now_ts, price_now = self._price_buffer[-1]
            coverage = now_ts - self._price_buffer[0][0]

            if coverage < self.MIN_COVERAGE_SEC:
                return {
                    "ready": False,
                    "reason": f"PHASE_INSUFFICIENT_COVERAGE_{coverage:.0f}s",
                    "coverage_s": round(coverage, 1),
                    "n": n,
                }

            # Cerco il prezzo più vicino disponibile prima di target_ts
            def _price_before(target_ts: float) -> Optional[float]:
                for ts, p in reversed(self._price_buffer):
                    if ts <= target_ts:
                        return float(p)
                # Buffer non abbastanza vecchio: usa il più vecchio disponibile
                return float(self._price_buffer[0][1]) if self._price_buffer else None

            p2  = _price_before(now_ts - 120.0)
            p5  = _price_before(now_ts - 300.0)
            p10 = _price_before(now_ts - 600.0)

            # Fallback al più vecchio se non c'è ancora abbastanza storia
            oldest_price = float(self._price_buffer[0][1])
            if p10 is None: p10 = oldest_price
            if p5  is None: p5  = oldest_price
            if p2  is None: p2  = oldest_price

            # Range del prezzo negli ultimi 10 min
            recent10 = [p for ts, p in self._price_buffer if ts >= now_ts - 600.0]
            if not recent10:
                recent10 = [p for _, p in self._price_buffer]
            hi = max(recent10)
            lo = min(recent10)
            rng = max(0.0001, hi - lo)
            pos = max(0.0, min(1.0, (price_now - lo) / rng))

            d2  = price_now - p2
            d5  = price_now - p5
            d10 = price_now - p10

            # Sanity check: se i delta sono assurdi, scarta
            if (abs(d2)  > self.MAX_DELTA_REASONABLE or
                abs(d5)  > self.MAX_DELTA_REASONABLE or
                abs(d10) > self.MAX_DELTA_REASONABLE):
                return {"ready": False, "reason": "PHASE_DELTA_ANOMALY", "d2": d2, "d5": d5, "d10": d10}

            return {
                "ready":      True,
                "price":      round(price_now, 2),
                "d2":         round(d2, 2),
                "d5":         round(d5, 2),
                "d10":        round(d10, 2),
                "range10":    round(rng, 2),
                "pos10":      round(pos, 3),
                "coverage_s": round(coverage, 1),
                "n":          n,
            }
        except Exception as e:
            self._error_count += 1
            log.debug(f"[FASE_SNAPSHOT_ERR] {e}")
            return {"ready": False, "reason": f"PHASE_ERROR:{e}"}

    # ═══════════════════════════════════════════════════════════════
    # LOGICA DI VERDETTO (cuore della capsula)
    # ═══════════════════════════════════════════════════════════════

    def _verdetto_logico(self, direction: str, snap: Dict[str, Any]) -> Tuple[str, str]:
        """
        Decide il verdetto SUL solo snapshot. Non scrive niente, non muta stato.

        Ritorna (verdetto, motivo):
          - verdetto: "BLOCCA" | "PASSA" | "NEUTRO"
          - motivo: stringa descrittiva
        """
        if not snap.get("ready"):
            return "NEUTRO", snap.get("reason", "NOT_READY")

        d10 = float(snap.get("d10", 0.0))
        d5  = float(snap.get("d5", 0.0))
        d2  = float(snap.get("d2", 0.0))
        pos = float(snap.get("pos10", 0.5))

        direction = (direction or "LONG").upper()

        # ── REGOLA SCARICO (LONG contro corrente) ─────────────────────
        # Tre forme di scarico, basta una per innescare il blocco LONG:
        #
        # FORMA A: scarico lento ma esteso (d10 grande, conferma micro d2)
        # FORMA B: scarico veloce (d5 grande, conferma micro d2)
        # FORMA C: scarico moderato da posizione bassa nel range
        scarico_long = (
            (d10 <= -self.soglia_d10 and d2 <= -self.soglia_d2) or
            (d5  <= -self.soglia_d5  and d2 <= -self.soglia_d2) or
            (d10 <= -self.soglia_d5  and d5 < 0 and pos <= 0.25)
        )

        # ── REGOLA CARICO (SHORT contro corrente) ─────────────────────
        # Specchiata della precedente.
        carico_short = (
            (d10 >= self.soglia_d10 and d2 >= self.soglia_d2) or
            (d5  >= self.soglia_d5  and d2 >= self.soglia_d2) or
            (d10 >= self.soglia_d5  and d5 > 0 and pos >= 0.75)
        )

        if direction == "LONG" and scarico_long:
            return "BLOCCA", f"FASE_SCARICO d10={d10:+.1f} d5={d5:+.1f} d2={d2:+.1f} pos={pos:.2f}"

        if direction == "SHORT" and carico_short:
            return "BLOCCA", f"FASE_CARICO d10={d10:+.1f} d5={d5:+.1f} d2={d2:+.1f} pos={pos:.2f}"

        return "PASSA", f"FASE_OK d10={d10:+.1f} d5={d5:+.1f} d2={d2:+.1f} pos={pos:.2f}"

    # ═══════════════════════════════════════════════════════════════
    # L1 + L3 — CONSULTA (chiamato pre-entry dal V16)
    # ═══════════════════════════════════════════════════════════════

    def consulta(self, direction: str, trade_id: Optional[str] = None) -> Tuple[bool, str]:
        """
        Hook chiamato da V16 prima di aprire un trade.

        Ritorna (ok, motivo):
          - ok=True  → la fase è compatibile (o capsula in warmup/dead)
          - ok=False → la fase è contraria (verdetto BLOCCA)

        IMPORTANTE: il V16 deve poi chiamare is_blocking() per sapere
        SE il verdetto deve essere RISPETTATO o solo OSSERVATO.
        Se L4_DECIDE è OFF, il verdetto è solo log (modalità OBSERVER).
        """
        # Fail-safe: capsula morta o non inizializzata → lascia passare
        if CAPSULA_FASE_DEAD or not self._initialized:
            return True, "CAPSULA_DEAD_OR_NOT_INIT"

        # Auto-quarantena attiva → lascia passare
        if self._quarantena_attiva:
            return True, "CAPSULA_QUARANTENA"

        try:
            snap = self._phase_snapshot()
            verdetto, motivo = self._verdetto_logico(direction, snap)

            self._stat_osservazioni += 1

            # L1 OCCHI: registra l'osservazione
            if self.levels_armed["L1_OCCHI"]:
                bloccante = 1 if (verdetto == "BLOCCA" and self.is_blocking()) else 0
                self._db_write(
                    """INSERT INTO capsula_fase_osservazioni
                       (trade_id, direction, price, d2, d5, d10, range10, pos10,
                        coverage_s, n_ticks, proposta, motivo, bloccante)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        trade_id, direction,
                        snap.get("price"),
                        snap.get("d2"), snap.get("d5"), snap.get("d10"),
                        snap.get("range10"), snap.get("pos10"),
                        snap.get("coverage_s"), snap.get("n"),
                        verdetto, motivo, bloccante,
                    )
                )

            # Conta blocchi proposti
            if verdetto == "BLOCCA":
                self._stat_blocchi_proposti += 1

                # Conserva pending per memoria L2 (collegamento con outcome)
                if trade_id:
                    self._pending_entries[trade_id] = {
                        "ts_entry":  time.time(),
                        "snap":      snap,
                        "verdetto":  verdetto,
                        "motivo":    motivo,
                        "direction": direction,
                    }
            else:
                self._stat_passati += 1

            # Decisione effettiva:
            #   - L4 DECIDE OFF (default): observer-only, lascia sempre passare
            #   - L4 DECIDE ON:           rispetta il verdetto BLOCCA
            if verdetto == "BLOCCA" and self.is_blocking():
                return False, motivo

            return True, motivo

        except Exception as e:
            self._error_count += 1
            log.debug(f"[FASE_CONSULTA_ERR] {e}")
            # FAIL-OPEN: in caso di errore, lascia passare
            return True, f"CAPSULA_ERROR:{e}"

    # ═══════════════════════════════════════════════════════════════
    # L2 — OSSERVA OUTCOME (chiusura del cerchio)
    # ═══════════════════════════════════════════════════════════════

    def observe_outcome(self, trade_id: str, outcome: str, pnl_netto: float,
                        durata_s: float = 0.0) -> None:
        """
        Hook chiamato da V16 alla chiusura di un trade.

        Lega l'outcome alla fase osservata all'entry. Aggiorna statistiche
        di precisione: i blocchi proposti su trade che sono poi diventati LOSS
        sono "blocchi giusti"; quelli che sono diventati WIN sono "sbagliati".
        """
        if CAPSULA_FASE_DEAD or not self._initialized or not self.levels_armed["L2_MEMORIA"]:
            return

        try:
            # Aggiorna l'osservazione esistente con outcome
            self._db_write(
                """UPDATE capsula_fase_osservazioni
                   SET outcome=?, pnl_netto=?, durata_s=?
                   WHERE trade_id=? AND outcome IS NULL""",
                (outcome, pnl_netto, durata_s, trade_id)
            )

            # Aggiorna statistiche runtime per blocchi proposti
            pending = self._pending_entries.pop(trade_id, None)
            if pending and pending.get("verdetto") == "BLOCCA":
                is_loss = ("LOSS" in (outcome or "").upper()) or (pnl_netto is not None and pnl_netto < 0)
                if is_loss:
                    self._stat_blocchi_giusti += 1
                else:
                    self._stat_blocchi_sbagliati += 1

            # L6 AUTO-QUARANTENA: check periodico
            if self.levels_armed["L6_AUTO_QUAR"]:
                self._check_auto_quarantena()

        except Exception as e:
            self._error_count += 1
            log.debug(f"[FASE_OBSERVE_OUTCOME_ERR] {e}")

    # ═══════════════════════════════════════════════════════════════
    # L4 — MODALITÀ BLOCCANTE
    # ═══════════════════════════════════════════════════════════════

    def is_blocking(self) -> bool:
        """
        Ritorna True se la capsula è autorizzata a BLOCCARE davvero i trade.
        False = modalità OBSERVER (suggerisce ma non blocca).

        Il V16 chiama questo metodo per decidere se rispettare il verdetto.
        """
        if CAPSULA_FASE_DEAD or self._quarantena_attiva:
            return False
        return bool(self.levels_armed.get("L4_DECIDE", False))

    # ═══════════════════════════════════════════════════════════════
    # L5 — IMPARA (ricalibra soglie dai dati)
    # ═══════════════════════════════════════════════════════════════

    def impara(self, n_min_osservazioni: int = 50) -> Dict[str, Any]:
        """
        Ricalcola le soglie ottimali dai dati storici.

        Logica: analizza le osservazioni passate dove c'è outcome registrato.
        Per ogni "fascia" di d5 (es. ogni $5), calcola WR dei trade lasciati
        passare. Trova la soglia oltre la quale il WR scende sotto il 40%.
        Quella diventa la nuova soglia.

        Sicurezza: NON modifica le soglie se ha meno di n_min_osservazioni con
        outcome registrato (insufficient data).
        """
        result = {"updated": False, "reason": "L5_NOT_ARMED"}

        if CAPSULA_FASE_DEAD or not self.levels_armed["L5_IMPARA"]:
            return result

        try:
            rows = self._db_read(
                """SELECT direction, d5, d10, outcome, pnl_netto
                   FROM capsula_fase_osservazioni
                   WHERE outcome IS NOT NULL AND bloccante = 0
                   ORDER BY ts DESC LIMIT 1000"""
            )

            if len(rows) < n_min_osservazioni:
                return {"updated": False, "reason": f"INSUFFICIENT_DATA_{len(rows)}/{n_min_osservazioni}"}

            # Separo LONG/SHORT e calcolo WR per fascia di d5
            long_wins  = [r for r in rows if r[0] == "LONG"  and r[3] and "WIN" in r[3].upper()]
            long_loss  = [r for r in rows if r[0] == "LONG"  and r[3] and "LOSS" in r[3].upper()]
            short_wins = [r for r in rows if r[0] == "SHORT" and r[3] and "WIN" in r[3].upper()]
            short_loss = [r for r in rows if r[0] == "SHORT" and r[3] and "LOSS" in r[3].upper()]

            # Per LONG, le d5 NEGATIVE forti sono pericolose (scarico).
            # Trovo la soglia: media d5 dei LOSS LONG che hanno d5 < 0.
            d5_loss_long = sorted([r[1] for r in long_loss if r[1] is not None and r[1] < 0])
            if d5_loss_long and len(d5_loss_long) >= 10:
                # Prendo la mediana come soglia candidata
                mediana = d5_loss_long[len(d5_loss_long) // 2]
                # Soglia conservativa: 70% della mediana (più stringente)
                nuova_d5 = abs(mediana) * 0.7
                # Bounded: tra 10 e 50 USD (sanity)
                nuova_d5 = max(10.0, min(50.0, nuova_d5))
                vecchia_d5 = self.soglia_d5
                self.soglia_d5 = nuova_d5

                # Proporzionalmente aggiorno d10 e d2
                self.soglia_d10 = nuova_d5 * 1.6  # d10 = d5 × 1.6 (proporzione storica)
                self.soglia_d2  = nuova_d5 * 0.36 # d2  = d5 × 0.36

                self._publish_verdetto(note=f"L5_IMPARA: d5 {vecchia_d5:.1f}→{nuova_d5:.1f}")

                return {
                    "updated":     True,
                    "vecchia_d5":  vecchia_d5,
                    "nuova_d5":    nuova_d5,
                    "nuova_d10":   self.soglia_d10,
                    "nuova_d2":    self.soglia_d2,
                    "n_loss_long": len(d5_loss_long),
                }

            return {"updated": False, "reason": "NO_LOSS_PATTERN_FOUND", "n_long_loss": len(long_loss)}

        except Exception as e:
            self._error_count += 1
            log.debug(f"[FASE_IMPARA_ERR] {e}")
            return {"updated": False, "reason": f"ERROR:{e}"}

    # ═══════════════════════════════════════════════════════════════
    # L6 — AUTO-QUARANTENA (sicurezza ultima)
    # ═══════════════════════════════════════════════════════════════

    def _check_auto_quarantena(self) -> None:
        """
        Check periodico: se nelle ultime 24h la capsula ha proposto blocchi
        in modo prevalentemente sbagliato (≥60% di WIN evitati), entra in
        quarantena automatica.

        In quarantena: lascia passare tutto, continua a osservare per riprendersi.
        """
        if CAPSULA_FASE_DEAD or not self.levels_armed["L6_AUTO_QUAR"]:
            return

        now = time.time()
        # Check non più frequente di ogni ora
        if now - self._ultimo_check_quarantena_ts < 3600:
            return
        self._ultimo_check_quarantena_ts = now

        try:
            # Conta blocchi giusti vs sbagliati nelle ultime 24h
            rows = self._db_read(
                """SELECT outcome, pnl_netto FROM capsula_fase_osservazioni
                   WHERE proposta = 'BLOCCA'
                     AND outcome IS NOT NULL
                     AND ts > ?""",
                (now - 86400,)
            )

            if len(rows) < 10:
                return  # poche osservazioni, non decidere

            giusti = sum(1 for r in rows if r[0] and ("LOSS" in r[0].upper() or (r[1] and r[1] < 0)))
            sbagliati = len(rows) - giusti

            tasso_errore = sbagliati / len(rows) if rows else 0

            if tasso_errore >= 0.6:
                # ≥60% di blocchi sbagliati → quarantena
                if not self._quarantena_attiva:
                    self._quarantena_attiva = True
                    log.warning(f"[CAPSULA_FASE] 🚨 AUTO-QUARANTENA ATTIVATA: "
                               f"{sbagliati}/{len(rows)} blocchi sbagliati nelle ultime 24h ({tasso_errore*100:.0f}%)")
                    self._publish_verdetto(note=f"AUTO_QUARANTENA tasso_errore={tasso_errore*100:.0f}%")
            elif tasso_errore <= 0.3 and self._quarantena_attiva:
                # Recupero: <30% errore → riprendi
                self._quarantena_attiva = False
                log.info(f"[CAPSULA_FASE] ✅ Quarantena rimossa: tasso errore sceso a {tasso_errore*100:.0f}%")

        except Exception as e:
            self._error_count += 1
            log.debug(f"[FASE_QUARANTENA_ERR] {e}")

    # ═══════════════════════════════════════════════════════════════
    # VERDETTO PUBBLICO (chiamabile da app.py per endpoint diagnostico)
    # ═══════════════════════════════════════════════════════════════

    def get_verdetto(self) -> Dict[str, Any]:
        """
        Ritorna lo stato corrente della capsula in un dict leggibile.
        Usato per endpoint diagnostico su app.py.
        """
        precisione = 0.0
        totale_chiusi = self._stat_blocchi_giusti + self._stat_blocchi_sbagliati
        if totale_chiusi > 0:
            precisione = self._stat_blocchi_giusti / totale_chiusi * 100

        stato = "DEAD"
        if not CAPSULA_FASE_DEAD:
            if self._quarantena_attiva:
                stato = "QUARANTENA"
            elif self.is_blocking():
                stato = "BLOCCANTE"
            elif self.levels_armed["L3_PROPONE"]:
                stato = "OBSERVER"
            else:
                stato = "OCCHI_ONLY"

        return {
            "version":            self.VERSION,
            "stato":              stato,
            "levels_armed":       self.levels_armed,
            "eta_osservazioni":   self._stat_osservazioni,
            "blocchi_proposti":   self._stat_blocchi_proposti,
            "blocchi_giusti":     self._stat_blocchi_giusti,
            "blocchi_sbagliati":  self._stat_blocchi_sbagliati,
            "trade_passati":      self._stat_passati,
            "precisione_pct":     round(precisione, 1),
            "soglia_d10":         self.soglia_d10,
            "soglia_d5":          self.soglia_d5,
            "soglia_d2":          self.soglia_d2,
            "buffer_tick":        len(self._price_buffer),
            "error_count":        self._error_count,
        }

    def _publish_verdetto(self, note: str = "") -> None:
        """Scrive una riga nel log verdetti per timeline storica."""
        try:
            v = self.get_verdetto()
            self._db_write(
                """INSERT INTO capsula_fase_verdetti
                   (stato, eta_oss, blocchi_prop, blocchi_giusti, blocchi_sbag,
                    precisione, soglia_d10, soglia_d5, soglia_d2, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    v["stato"], v["eta_osservazioni"], v["blocchi_proposti"],
                    v["blocchi_giusti"], v["blocchi_sbagliati"],
                    v["precisione_pct"], v["soglia_d10"], v["soglia_d5"], v["soglia_d2"],
                    note,
                )
            )
        except Exception as e:
            self._error_count += 1
            log.debug(f"[FASE_PUBLISH_ERR] {e}")

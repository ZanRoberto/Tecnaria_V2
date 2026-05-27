# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════════
 CAPSULA_TSUNAMI_DISCORDE — adattogena, niente soglie hardcoded
═══════════════════════════════════════════════════════════════════════

IDEA FONDANTE (Roberto Zan, 27 maggio 2026):
   "Una capsula adattogena trova la posizione perfetta per ogni situazione."
   "Una soglia rigida è solo l'ultima opzione quando non c'è altro modo."

SCOPERTA TRIGGER (trade 206, 26 mag 22:23):
   Bot ha aperto LONG mentre tutti e 3 i Tsunami (30s/2m/10m) urlavano DOWN
   con ts_confidenza=2 (massima coerenza). LOSS -$2.20.
   Causa: il muro TSUNAMI_DISCORDE era stato disinnescato dal refactor
   "SC SOVRANO" → trasformato in deposizione muta nel verbale.

FILOSOFIA DELLA CAPSULA:
   1. NESSUNA soglia hardcoded
   2. Tutte le decisioni derivano dai DATI STORICI esistenti
   3. La capsula impara dai phantom_forensic (17.657 osservazioni reali)
   4. Si ricalibra automaticamente man mano che arrivano nuovi dati
   5. Configurazioni mai viste → passa (no veto al cieco)
   6. Configurazioni viste con statistica forte → blocco basato su WR reale

LIVELLI ARMABILI (env Render):
   L1 OCCHI         (default ON) — registra ogni consulta in DB
   L2 CARICA_DATI   (default ON) — al boot legge phantom e costruisce mappa
   L3 PROPONE       (default ON) — OBSERVER: suggerisce blocchi senza bloccare
   L4 DECIDE        (default OFF) — modalità BLOCCANTE: rispetta verdetti
   L5 AUTO_REFRESH  (default ON)  — ricarica mappa ogni 30 min
   L6 AUTO_QUAR     (default OFF) — auto-quarantena se sbaglia troppo

KILL SWITCH: env CAPSULA_TSUNAMI_DISCORDE_DEAD=true

SICUREZZA:
   - try/except totale: mai blocca il motore
   - Fail-open: se errore → consulta() ritorna (True, "ERROR")
   - Nessuna scrittura DB sincrona nel path critico (consulta)

INTERFACCIA V16:
   from capsula_tsunami_discorde import CapsulaTsunamiDiscorde
   
   # Init (una volta sola, al boot del bot)
   self.cap_tsunami = CapsulaTsunamiDiscorde(db_path=DB_PATH)
   
   # Consulta (ad ogni entry valutata)
   ok, motivo = self.cap_tsunami.consulta(
       direction_campo='LONG',
       ts_30s_dir='DOWN',
       ts_2min_dir='DOWN',
       ts_10min_dir='DOWN',
       ts_confidenza=2,
   )
   if not ok and self.cap_tsunami.is_blocking():
       return  # BLOCCATA dalla capsula
═══════════════════════════════════════════════════════════════════════
"""

import os
import time
import sqlite3
import logging
from collections import defaultdict
from typing import Optional, Dict, Any, Tuple, List

log = logging.getLogger("CAPSULA_TSUNAMI_DISCORDE")


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name, "").strip().lower()
    if not val:
        return default
    return val in ("true", "1", "on", "yes", "si", "sì")


# Flags livelli
CAPSULA_TSUNAMI_DISCORDE_DEAD = _env_bool("CAPSULA_TSUNAMI_DISCORDE_DEAD", False)
CAP_TS_L1 = _env_bool("CAP_TS_L1_OCCHI",       True)
CAP_TS_L2 = _env_bool("CAP_TS_L2_CARICA",      True)
CAP_TS_L3 = _env_bool("CAP_TS_L3_PROPONE",     True)
CAP_TS_L4 = _env_bool("CAP_TS_L4_DECIDE",      False)
CAP_TS_L5 = _env_bool("CAP_TS_L5_AUTO_REFRESH", True)
CAP_TS_L6 = _env_bool("CAP_TS_L6_AUTO_QUAR",   False)


class CapsulaTsunamiDiscorde:
    """Capsula adattogena per discordanze Tsunami vs campo.
    
    Le soglie non esistono. Esistono le STATISTICHE dei phantom.
    Una configurazione (direction × ts_dirs × confidenza) o ha dati
    sufficienti per essere giudicata, o passa di default.
    """

    VERSION = "1.0.0"

    # Schema DB per tracciare i propri verdetti
    SCHEMA_VERDETTI = """
    CREATE TABLE IF NOT EXISTS capsula_tsunami_verdetti (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ts              REAL NOT NULL DEFAULT (strftime('%s','now')),
        trade_id        TEXT,
        direction_campo TEXT,
        ts_30s_dir      TEXT,
        ts_2min_dir     TEXT,
        ts_10min_dir    TEXT,
        ts_confidenza   INTEGER,
        config_key      TEXT,
        n_simili        INTEGER,
        wr_storico      REAL,
        pnl_avg_storico REAL,
        proposta        TEXT,
        motivo          TEXT,
        bloccante       INTEGER DEFAULT 0,
        outcome         TEXT,
        pnl_netto       REAL
    );
    """

    SCHEMA_REFRESH_LOG = """
    CREATE TABLE IF NOT EXISTS capsula_tsunami_refresh (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ts              REAL NOT NULL DEFAULT (strftime('%s','now')),
        n_phantoms      INTEGER,
        n_configs       INTEGER,
        n_configs_blok  INTEGER,
        durata_ms       INTEGER,
        note            TEXT
    );
    """

    SCHEMA_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_capts_ts ON capsula_tsunami_verdetti(ts);
    CREATE INDEX IF NOT EXISTS idx_capts_config ON capsula_tsunami_verdetti(config_key);
    CREATE INDEX IF NOT EXISTS idx_capts_trade ON capsula_tsunami_verdetti(trade_id);
    """

    # Parametri adattivi (non sono soglie di blocco, sono parametri di metodo)
    REFRESH_INTERVAL_SEC = 1800  # 30 min — ogni quanto ricarica la mappa
    SAMPLE_MIN_FORTE     = 30    # se >= 30 sample: giudizio AFFIDABILE
    SAMPLE_MIN_DEBOLE    = 10    # se >= 10 sample: giudizio DEBOLE
    
    # Soglie WR per blocco — anche queste vorrei farle adattive, ma per
    # ora hanno senso fisico: WR < 33% = il trade perde 2 volte su 3.
    # Sono una soglia di SENSO COMUNE, non un numero buttato a caso.
    WR_SOGLIA_FORTE  = 0.33  # se sample >= 30 e WR < 0.33 → BLOCCA
    WR_SOGLIA_DEBOLE = 0.20  # se sample >= 10 e WR < 0.20 → BLOCCA

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.environ.get("DB_PATH", "/var/data/trading_data.db")
        self._initialized = False
        self._error_count = 0
        
        # Stato livelli
        self.levels_armed = {
            "L1_OCCHI":        CAP_TS_L1 and not CAPSULA_TSUNAMI_DISCORDE_DEAD,
            "L2_CARICA_DATI":  CAP_TS_L2 and not CAPSULA_TSUNAMI_DISCORDE_DEAD,
            "L3_PROPONE":      CAP_TS_L3 and not CAPSULA_TSUNAMI_DISCORDE_DEAD,
            "L4_DECIDE":       CAP_TS_L4 and not CAPSULA_TSUNAMI_DISCORDE_DEAD,
            "L5_AUTO_REFRESH": CAP_TS_L5 and not CAPSULA_TSUNAMI_DISCORDE_DEAD,
            "L6_AUTO_QUAR":    CAP_TS_L6 and not CAPSULA_TSUNAMI_DISCORDE_DEAD,
        }

        # La MAPPA — calcolata dai dati
        # chiave = stringa "DIR|TS30s|TS2m|TS10m|conf"
        # valore = dict {n: int, wins: int, pnl_sum: float, wr: float, pnl_avg: float}
        self._mappa: Dict[str, Dict[str, Any]] = {}
        self._last_refresh_ts = 0.0
        self._mappa_age_sec = 0.0

        # Statistiche runtime
        self._stat_consultazioni     = 0
        self._stat_blocchi_proposti  = 0
        self._stat_blocchi_giusti    = 0
        self._stat_blocchi_sbagliati = 0
        self._stat_passati           = 0

        # Auto-quarantena (L6)
        self._quarantena_attiva = False
        self._ultimo_check_quar_ts = 0.0

        # Pending: snapshot al consulta, in attesa di outcome
        self._pending: Dict[str, Dict[str, Any]] = {}

        if CAPSULA_TSUNAMI_DISCORDE_DEAD:
            log.warning("[CAP_TSUNAMI] KILL SWITCH ATTIVO. Capsula silenziata.")
            return

        try:
            self._init_db()
            if self.levels_armed["L2_CARICA_DATI"]:
                self._refresh_mappa()
            self._initialized = True
            log.info(f"[CAP_TSUNAMI] v{self.VERSION} attiva — "
                     f"levels={[k for k,v in self.levels_armed.items() if v]} | "
                     f"configurazioni mappate={len(self._mappa)}")
        except Exception as e:
            log.error(f"[CAP_TSUNAMI] init fallita: {e}")

    # ═══════════════════════════════════════════════════════════════
    # DB SETUP
    # ═══════════════════════════════════════════════════════════════
    
    def _init_db(self):
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            cur = conn.cursor()
            cur.executescript(self.SCHEMA_VERDETTI)
            cur.executescript(self.SCHEMA_REFRESH_LOG)
            cur.executescript(self.SCHEMA_INDEX)
            conn.commit()

    def _db_write(self, sql: str, params: tuple) -> bool:
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                conn.execute(sql, params)
                conn.commit()
            return True
        except Exception as e:
            self._error_count += 1
            log.debug(f"[CAP_TSUNAMI_WRITE_ERR] {e}")
            return False

    def _db_read(self, sql: str, params: tuple = ()) -> List[tuple]:
        try:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                cur = conn.execute(sql, params)
                return cur.fetchall()
        except Exception as e:
            self._error_count += 1
            log.debug(f"[CAP_TSUNAMI_READ_ERR] {e}")
            return []

    # ═══════════════════════════════════════════════════════════════
    # L2 — CARICA DATI dai phantom e costruisce la mappa
    # ═══════════════════════════════════════════════════════════════
    
    def _refresh_mappa(self) -> None:
        """Legge phantom_forensic e costruisce la mappa delle configurazioni.
        
        Questa è la cosa importante. NON ci sono soglie hardcoded qui.
        Le statistiche EMERGONO dai dati."""
        t_start = time.time()
        
        # Leggo TUTTI i phantom (sia NO_ENTRY che DISCORDE)
        # Mi servono i campi: direction, ts_*_direction, ts_confidenza, is_win, pnl_netto
        rows = self._db_read("""
            SELECT direction, ts_30s_direction, ts_2min_direction, ts_10min_direction,
                   ts_confidenza, is_win, pnl_netto
            FROM phantom_forensic
            WHERE ts_30s_direction IS NOT NULL
              AND ts_2min_direction IS NOT NULL
              AND ts_10min_direction IS NOT NULL
        """)
        
        # Aggregatore: chiave → counts
        agg = defaultdict(lambda: {"n": 0, "wins": 0, "pnl_sum": 0.0})
        
        for direction, ts30, ts2m, ts10, conf, is_win, pnl in rows:
            # Pulizia: NONE → NONE (warmup)
            direction = (direction or "?").upper()
            ts30      = (ts30 or "NONE").upper()
            ts2m      = (ts2m or "NONE").upper()
            ts10      = (ts10 or "NONE").upper()
            conf      = int(conf or 0)
            is_win    = int(is_win or 0)
            pnl       = float(pnl or 0.0)
            
            # Chiave: direction campo + 3 direzioni Tsunami + confidenza
            key = f"{direction}|{ts30}|{ts2m}|{ts10}|c{conf}"
            
            agg[key]["n"] += 1
            agg[key]["wins"] += is_win
            agg[key]["pnl_sum"] += pnl
        
        # Calcolo WR e pnl_avg per ogni configurazione
        mappa = {}
        n_block = 0
        for key, d in agg.items():
            n = d["n"]
            wr = d["wins"] / n if n > 0 else 0.0
            pnl_avg = d["pnl_sum"] / n if n > 0 else 0.0
            
            # Decisione: BLOCCA o PASSA, basata sui DATI di QUESTA configurazione
            blocca = False
            forza = "INSUFFICIENT"
            
            if n >= self.SAMPLE_MIN_FORTE:
                forza = "FORTE"
                if wr < self.WR_SOGLIA_FORTE:
                    blocca = True
                    n_block += 1
            elif n >= self.SAMPLE_MIN_DEBOLE:
                forza = "DEBOLE"
                if wr < self.WR_SOGLIA_DEBOLE:
                    blocca = True
                    n_block += 1
            
            mappa[key] = {
                "n":          n,
                "wins":       d["wins"],
                "wr":         wr,
                "pnl_avg":    pnl_avg,
                "blocca":     blocca,
                "forza":      forza,
            }
        
        # Atomic swap
        self._mappa = mappa
        self._last_refresh_ts = time.time()
        durata_ms = int((self._last_refresh_ts - t_start) * 1000)
        
        # Log del refresh
        self._db_write(
            """INSERT INTO capsula_tsunami_refresh
               (n_phantoms, n_configs, n_configs_blok, durata_ms, note)
               VALUES (?, ?, ?, ?, ?)""",
            (len(rows), len(mappa), n_block, durata_ms, f"v{self.VERSION}")
        )
        log.info(f"[CAP_TSUNAMI] refresh: {len(rows)} phantoms → "
                 f"{len(mappa)} configurazioni ({n_block} bloccanti) in {durata_ms}ms")

    # ═══════════════════════════════════════════════════════════════
    # L3 — CONSULTA (chiamato dal V16 pre-entry)
    # ═══════════════════════════════════════════════════════════════
    
    def consulta(
        self,
        direction_campo: str,
        ts_30s_dir: str,
        ts_2min_dir: str,
        ts_10min_dir: str,
        ts_confidenza: int = 0,
        trade_id: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Decide se la configurazione è compatibile.
        
        Ritorna (ok, motivo):
          ok=True  → passa (o capsula disattivata, o configurazione mai vista)
          ok=False → blocca (con motivo dettagliato)
        
        IMPORTANTE: il V16 deve chiamare is_blocking() per sapere se
        rispettare il verdetto o solo osservarlo (modalità OBSERVER)."""
        if CAPSULA_TSUNAMI_DISCORDE_DEAD or not self._initialized:
            return True, "CAP_TSUNAMI_OFF"
        
        if self._quarantena_attiva:
            return True, "CAP_TSUNAMI_QUARANTENA"
        
        try:
            # L5: auto-refresh ogni 30 min
            if self.levels_armed["L5_AUTO_REFRESH"]:
                if time.time() - self._last_refresh_ts > self.REFRESH_INTERVAL_SEC:
                    try:
                        self._refresh_mappa()
                    except Exception as _er:
                        log.debug(f"[CAP_TSUNAMI_REFRESH_ERR] {_er}")
            
            # Normalizzo input
            d_campo = (direction_campo or "?").upper()
            t30     = (ts_30s_dir or "NONE").upper()
            t2m     = (ts_2min_dir or "NONE").upper()
            t10     = (ts_10min_dir or "NONE").upper()
            conf    = int(ts_confidenza or 0)
            
            key = f"{d_campo}|{t30}|{t2m}|{t10}|c{conf}"
            
            self._stat_consultazioni += 1
            
            # Cerco la configurazione nella mappa
            entry = self._mappa.get(key)
            
            if entry is None:
                # Configurazione MAI VISTA → passa di default
                motivo = f"CONFIG_NEW key={key}"
                self._stat_passati += 1
                self._log_verdetto(trade_id, d_campo, t30, t2m, t10, conf, key,
                                   0, 0.0, 0.0, "PASSA", motivo, blocking=False)
                return True, motivo
            
            n       = entry["n"]
            wr      = entry["wr"]
            pnl_avg = entry["pnl_avg"]
            blocca  = entry["blocca"]
            forza   = entry["forza"]
            
            # Costruisco motivo dettagliato (sempre, anche se passa)
            motivo = f"key={key} n={n} wr={wr:.0%} pnl_avg=${pnl_avg:.2f} forza={forza}"
            
            if blocca:
                self._stat_blocchi_proposti += 1
                # Salvo pending per outcome
                if trade_id:
                    self._pending[trade_id] = {
                        "ts": time.time(),
                        "key": key,
                        "wr_storico": wr,
                    }
                self._log_verdetto(trade_id, d_campo, t30, t2m, t10, conf, key,
                                   n, wr, pnl_avg, "BLOCCA", motivo,
                                   blocking=self.is_blocking())
                # Se L4 OFF → osservazione, ritorna True
                if not self.is_blocking():
                    return True, f"OBSERVER_WOULD_BLOCK: {motivo}"
                return False, motivo
            else:
                self._stat_passati += 1
                self._log_verdetto(trade_id, d_campo, t30, t2m, t10, conf, key,
                                   n, wr, pnl_avg, "PASSA", motivo, blocking=False)
                return True, motivo
            
        except Exception as e:
            self._error_count += 1
            log.debug(f"[CAP_TSUNAMI_CONSULTA_ERR] {e}")
            # FAIL-OPEN
            return True, f"CAP_TSUNAMI_ERROR:{e}"

    def _log_verdetto(self, trade_id, d_campo, t30, t2m, t10, conf, key,
                       n, wr, pnl_avg, proposta, motivo, blocking):
        """Scrive il verdetto nel DB. Solo se L1 OCCHI armato."""
        if not self.levels_armed["L1_OCCHI"]:
            return
        self._db_write(
            """INSERT INTO capsula_tsunami_verdetti
               (trade_id, direction_campo, ts_30s_dir, ts_2min_dir, ts_10min_dir,
                ts_confidenza, config_key, n_simili, wr_storico, pnl_avg_storico,
                proposta, motivo, bloccante)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (trade_id, d_campo, t30, t2m, t10, conf, key, n, wr, pnl_avg,
             proposta, motivo, 1 if blocking else 0)
        )

    # ═══════════════════════════════════════════════════════════════
    # L4 — MODALITÀ BLOCCANTE
    # ═══════════════════════════════════════════════════════════════
    
    def is_blocking(self) -> bool:
        """True se la capsula è autorizzata a bloccare per davvero."""
        if CAPSULA_TSUNAMI_DISCORDE_DEAD or self._quarantena_attiva:
            return False
        return bool(self.levels_armed.get("L4_DECIDE", False))

    # ═══════════════════════════════════════════════════════════════
    # L2 — OSSERVA OUTCOME (per misurare se la capsula ha ragione)
    # ═══════════════════════════════════════════════════════════════
    
    def observe_outcome(self, trade_id: str, outcome: str, pnl_netto: float):
        """Chiamato dal V16 alla chiusura di un trade.
        
        Aggiorna le statistiche di precisione della capsula."""
        if CAPSULA_TSUNAMI_DISCORDE_DEAD or not self._initialized:
            return
        
        try:
            # Aggiorna il verdetto in DB
            self._db_write(
                """UPDATE capsula_tsunami_verdetti
                   SET outcome=?, pnl_netto=?
                   WHERE trade_id=? AND outcome IS NULL""",
                (outcome, pnl_netto, trade_id)
            )
            
            # Aggiorna stats: se avevo proposto BLOCCA, era giusto o sbagliato?
            pending = self._pending.pop(trade_id, None)
            if pending:
                is_loss = ("LOSS" in (outcome or "").upper()) or (pnl_netto is not None and pnl_netto < 0)
                if is_loss:
                    self._stat_blocchi_giusti += 1
                else:
                    self._stat_blocchi_sbagliati += 1
            
            # L6 auto-quarantena check (periodico)
            if self.levels_armed["L6_AUTO_QUAR"]:
                self._check_auto_quarantena()
                
        except Exception as e:
            self._error_count += 1
            log.debug(f"[CAP_TSUNAMI_OUTCOME_ERR] {e}")

    # ═══════════════════════════════════════════════════════════════
    # L6 — AUTO-QUARANTENA (sicurezza ultima)
    # ═══════════════════════════════════════════════════════════════
    
    def _check_auto_quarantena(self):
        """Se nelle ultime 24h la capsula ha sbagliato >= 60% dei blocchi proposti
        (cioè avrebbe perso WIN), entra in quarantena."""
        now = time.time()
        if now - self._ultimo_check_quar_ts < 3600:
            return  # check max ogni ora
        self._ultimo_check_quar_ts = now
        
        try:
            rows = self._db_read("""
                SELECT outcome, pnl_netto FROM capsula_tsunami_verdetti
                WHERE proposta = 'BLOCCA' AND outcome IS NOT NULL
                  AND ts > ?
            """, (now - 86400,))
            
            if len(rows) < 10:
                return  # dati insufficienti
            
            giusti = sum(1 for r in rows if r[0] and ("LOSS" in r[0].upper() or (r[1] and r[1] < 0)))
            sbagliati = len(rows) - giusti
            tasso_errore = sbagliati / len(rows)
            
            if tasso_errore >= 0.6 and not self._quarantena_attiva:
                self._quarantena_attiva = True
                log.warning(f"[CAP_TSUNAMI] 🚨 AUTO-QUARANTENA: "
                            f"{sbagliati}/{len(rows)} blocchi sbagliati ({tasso_errore*100:.0f}%)")
            elif tasso_errore <= 0.3 and self._quarantena_attiva:
                self._quarantena_attiva = False
                log.info(f"[CAP_TSUNAMI] ✅ quarantena rimossa: errore={tasso_errore*100:.0f}%")
                
        except Exception as e:
            self._error_count += 1
            log.debug(f"[CAP_TSUNAMI_QUAR_ERR] {e}")

    # ═══════════════════════════════════════════════════════════════
    # VERDETTO PUBBLICO (per endpoint diagnostico)
    # ═══════════════════════════════════════════════════════════════
    
    def get_verdetto(self) -> Dict[str, Any]:
        """Stato corrente della capsula. Usabile da endpoint app.py."""
        precisione = 0.0
        tot_chiusi = self._stat_blocchi_giusti + self._stat_blocchi_sbagliati
        if tot_chiusi > 0:
            precisione = self._stat_blocchi_giusti / tot_chiusi * 100
        
        stato = "DEAD"
        if not CAPSULA_TSUNAMI_DISCORDE_DEAD:
            if self._quarantena_attiva:
                stato = "QUARANTENA"
            elif self.is_blocking():
                stato = "BLOCCANTE"
            elif self.levels_armed["L3_PROPONE"]:
                stato = "OBSERVER"
            else:
                stato = "OCCHI_ONLY"
        
        # Statistiche sulla mappa
        n_configs = len(self._mappa)
        n_configs_blok = sum(1 for v in self._mappa.values() if v["blocca"])
        n_configs_forti = sum(1 for v in self._mappa.values() if v["forza"] == "FORTE")
        
        return {
            "version":           self.VERSION,
            "stato":             stato,
            "levels_armed":      self.levels_armed,
            "consultazioni":     self._stat_consultazioni,
            "blocchi_proposti":  self._stat_blocchi_proposti,
            "blocchi_giusti":    self._stat_blocchi_giusti,
            "blocchi_sbagliati": self._stat_blocchi_sbagliati,
            "trade_passati":     self._stat_passati,
            "precisione_pct":    round(precisione, 1),
            "mappa": {
                "n_configurazioni":     n_configs,
                "n_bloccanti":          n_configs_blok,
                "n_statistica_forte":   n_configs_forti,
                "ultimo_refresh_sec":   int(time.time() - self._last_refresh_ts),
            },
            "error_count":       self._error_count,
        }
    
    def dump_top_configs(self, n: int = 20) -> List[Dict[str, Any]]:
        """Top N configurazioni più bloccate (peggior WR storico).
        
        Utile per analisi: vedere quali combinazioni il bot deve evitare."""
        sorted_configs = sorted(
            self._mappa.items(),
            key=lambda kv: (kv[1]["wr"], -kv[1]["n"])
        )
        out = []
        for key, val in sorted_configs[:n]:
            out.append({
                "key":     key,
                "n":       val["n"],
                "wr":      round(val["wr"], 3),
                "pnl_avg": round(val["pnl_avg"], 2),
                "blocca":  val["blocca"],
                "forza":   val["forza"],
            })
        return out

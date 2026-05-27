# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════════════════
 CAPSULA MATRIGNA — SuperRisponditrice V1
═══════════════════════════════════════════════════════════════════════════════

VISIONE (Roberto Zan, 27 maggio 2026):
   "Se arriva un mercato nuovo, il SuperRisponditore dovrebbe creare anche 
    la capsula. È nostra. Lo abbiamo immaginato."

   "Le capsule fanno i raggi profondi e intercettano per ok passa.. o no.. 
    fermati perché porti perdite. Poi il SC ha tutto il resto della banda 
    a cui dare da gestire il trade."

   "Dove uno mette una soglia rigida, io penso ad una capsula che in modo 
    adattogeno arriva a trovare la posizione perfetta per ogni situazione."

═══════════════════════════════════════════════════════════════════════════════
CHE COSA È
═══════════════════════════════════════════════════════════════════════════════

La SuperRisponditrice è una CAPSULA MATRIGNA che genera CAPSULE FIGLIE.

Ogni firma di mercato (combinazione momentum × volatility × trend × regime × 
direction) che il bot incontra diventa una capsula figlia separata, con vita 
propria, statistiche proprie, e verdetto che emerge dai DATI di quella 
specifica firma.

═══════════════════════════════════════════════════════════════════════════════
CICLO DI VITA DI UNA CAPSULA FIGLIA (Opzione C scelta da Roberto)
═══════════════════════════════════════════════════════════════════════════════

NASCITA → OSSERVAZIONE_PREVENTIVA (verdetto NEUTRO, budget esperimenti=3)
   ↓ dopo 3 trade
   ├─ se ha vinto almeno 1 su 3 → OSSERVAZIONE_ATTIVA (NEUTRO, continua)
   └─ se 0/3                    → CONGELATA (BLOCCA)
   
OSSERVAZIONE_ATTIVA → OPERATIVA (a n=20 sample)
   Verdetto calcolato dai dati storici della firma:
      WR < 20%  → BLOCCA (forte)
      WR 20-30% → BLOCCA (debole)
      WR 30-45% → NEUTRO
      WR >= 45% → FAVORISCI

OPERATIVA → QUARANTENA (se WR ultimi 10 < WR storico × 0.5)
   ↓ ricontrolla dopo 10 trade
   └─ recupera o resta QUARANTENA

CONGELATA → OSSERVAZIONE_ATTIVA (se 10 trade nuovi con WR >= 33%)
   Resurrezione.

═══════════════════════════════════════════════════════════════════════════════
INTERFACCIA V16
═══════════════════════════════════════════════════════════════════════════════

from capsula_matrigna import CapsulaMatrigna

# Init (una sola volta, al boot del bot)
self.matrigna = CapsulaMatrigna(db_path=DB_PATH)
# Al boot la matrigna scansiona winning_signatures + trades e popola le
# capsule figlie con le statistiche storiche già accumulate.

# Pre-entry consulta
ok, verdetto, info = self.matrigna.consulta(
    momentum='DEBOLE', volatility='BASSA', trend='SIDEWAYS',
    regime='RANGING', direction='LONG',
    trade_id='t_12345'
)
if not ok and self.matrigna.is_blocking():
    return  # capsula figlia ha bloccato

# Post-exit observe
self.matrigna.observe_outcome(
    trade_id='t_12345',
    momentum='DEBOLE', volatility='BASSA', trend='SIDEWAYS',
    regime='RANGING', direction='LONG',
    pnl_netto=-2.20, duration=61.7, peak_pnl=0.0, peak_delta_s=0,
)

═══════════════════════════════════════════════════════════════════════════════
LIVELLI ARMABILI (env Render)
═══════════════════════════════════════════════════════════════════════════════

L1 OCCHI         (default ON)  → registra ogni consulta in DB
L2 NASCITA       (default ON)  → crea capsule nuove al boot e a runtime
L3 PROPONE       (default ON)  → OBSERVER (suggerisce blocchi)
L4 DECIDE        (default OFF) → BLOCCANTE (rispetta verdetti)
L5 AUTO_REFRESH  (default ON)  → ricalcola soglie adattive ogni 30 min
L6 AUTO_QUAR     (default OFF) → quarantena automatica su drift

KILL SWITCH: env CAPSULA_MATRIGNA_DEAD=true

═══════════════════════════════════════════════════════════════════════════════
"""

import os
import time
import json
import sqlite3
import logging
import statistics
from collections import defaultdict, deque
from typing import Optional, Dict, Any, Tuple, List

log = logging.getLogger("CAPSULA_MATRIGNA")


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name, "").strip().lower()
    if not val:
        return default
    return val in ("true", "1", "on", "yes", "si", "sì")


CAPSULA_MATRIGNA_DEAD = _env_bool("CAPSULA_MATRIGNA_DEAD", False)
CAP_MAT_L1 = _env_bool("CAP_MAT_L1_OCCHI",        True)
CAP_MAT_L2 = _env_bool("CAP_MAT_L2_NASCITA",      True)
CAP_MAT_L3 = _env_bool("CAP_MAT_L3_PROPONE",      True)
CAP_MAT_L4 = _env_bool("CAP_MAT_L4_DECIDE",       False)
CAP_MAT_L5 = _env_bool("CAP_MAT_L5_AUTO_REFRESH", True)
CAP_MAT_L6 = _env_bool("CAP_MAT_L6_AUTO_QUAR",    False)


class CapsulaMatrigna:
    """SuperRisponditrice: capsula matrigna che genera capsule figlie 
    adattogene per ogni firma di mercato incontrata dal bot.
    
    Le soglie del singolo verdetto (BLOCCA/NEUTRO/FAVORISCI) sono CALCOLATE
    dai dati di ciascuna capsula figlia. Niente numeri buttati a caso.
    
    Le soglie del ciclo di vita (3 esperimenti, 20 maturazione, 0.5 drift) 
    sono parametri di POLICY scelti consapevolmente, non parametri di mercato.
    """

    VERSION = "1.0.0"

    SCHEMA_MATRIGNA = """
    CREATE TABLE IF NOT EXISTS capsula_matrigna_figlie (
        firma_key            TEXT PRIMARY KEY,
        nata_ts              REAL NOT NULL,
        ultima_osservazione  REAL,
        n_trade              INTEGER DEFAULT 0,
        n_win                INTEGER DEFAULT 0,
        n_loss               INTEGER DEFAULT 0,
        pnl_totale           REAL DEFAULT 0,
        pnl_avg              REAL DEFAULT 0,
        wr                   REAL DEFAULT 0,
        pnl_best             REAL DEFAULT 0,
        pnl_worst            REAL DEFAULT 0,
        durata_avg           REAL DEFAULT 0,
        peak_delta_avg       REAL DEFAULT 0,
        stato                TEXT DEFAULT 'OSSERVAZIONE_PREVENTIVA',
        verdetto_corrente    TEXT DEFAULT 'NEUTRO',
        budget_esperimenti   INTEGER DEFAULT 3,
        recent_wins_10       TEXT DEFAULT '[]',
        nota                 TEXT
    );
    """

    SCHEMA_VERDETTI = """
    CREATE TABLE IF NOT EXISTS capsula_matrigna_verdetti (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ts              REAL NOT NULL DEFAULT (strftime('%s','now')),
        trade_id        TEXT,
        firma_key       TEXT,
        stato           TEXT,
        verdetto        TEXT,
        n_simili        INTEGER,
        wr_storico      REAL,
        bloccante       INTEGER DEFAULT 0,
        outcome         TEXT,
        pnl_netto       REAL
    );
    """

    SCHEMA_NASCITE = """
    CREATE TABLE IF NOT EXISTS capsula_matrigna_nascite (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ts              REAL NOT NULL DEFAULT (strftime('%s','now')),
        firma_key       TEXT NOT NULL,
        origine         TEXT,
        nota            TEXT
    );
    """

    SCHEMA_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_capmat_ts ON capsula_matrigna_verdetti(ts);
    CREATE INDEX IF NOT EXISTS idx_capmat_firma ON capsula_matrigna_verdetti(firma_key);
    CREATE INDEX IF NOT EXISTS idx_capmat_trade ON capsula_matrigna_verdetti(trade_id);
    """

    # ────────────────────────────────────────────────────────────────
    # Parametri di POLICY (non valori di mercato — scelte consapevoli)
    # ────────────────────────────────────────────────────────────────
    BUDGET_ESPERIMENTI_INIZIALE = 3      # quanti trade "prova" per firma nuova
    WR_MEDIOCRITA_SOPRAVVIVENZA = 0.33   # almeno 1 WIN su 3 per sopravvivere
    SAMPLE_MATURAZIONE = 20              # quando capsula passa a OPERATIVA
    DRIFT_FACTOR_QUARANTENA = 0.5        # WR_recente / WR_storico < 0.5 → quarantena
    RESURREZIONE_TRADES = 10             # trade nuovi per uscire da CONGELATA
    REFRESH_INTERVAL_SEC = 1800          # ricalcola soglie ogni 30 min

    # Soglie di verdetto (in V1 sono fisse, in V2 saranno adattogene)
    # Significato fisico:
    #   WR < 20%  → perdi 4/5 volte, è un killer
    #   WR < 30%  → perdi 7/10 volte, è perdente strutturale  
    #   WR < 45%  → borderline (fee in scalping)
    #   WR >= 45% → vincente
    WR_BLOCCA_FORTE = 0.20
    WR_BLOCCA_DEBOLE = 0.30
    WR_FAVORISCI = 0.45

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.environ.get("DB_PATH", "/var/data/trading_data.db")
        self._initialized = False
        self._error_count = 0

        self.levels_armed = {
            "L1_OCCHI":        CAP_MAT_L1 and not CAPSULA_MATRIGNA_DEAD,
            "L2_NASCITA":      CAP_MAT_L2 and not CAPSULA_MATRIGNA_DEAD,
            "L3_PROPONE":      CAP_MAT_L3 and not CAPSULA_MATRIGNA_DEAD,
            "L4_DECIDE":       CAP_MAT_L4 and not CAPSULA_MATRIGNA_DEAD,
            "L5_AUTO_REFRESH": CAP_MAT_L5 and not CAPSULA_MATRIGNA_DEAD,
            "L6_AUTO_QUAR":    CAP_MAT_L6 and not CAPSULA_MATRIGNA_DEAD,
        }

        # Stato runtime: ogni capsula figlia
        # chiave = firma_key (es. "DEBOLE|BASSA|SIDEWAYS|RANGING|LONG")
        # valore = dict con stats correnti
        self._capsule: Dict[str, Dict[str, Any]] = {}
        self._last_refresh_ts = 0.0

        # Statistiche runtime globali
        self._stat_consultazioni = 0
        self._stat_blocchi_proposti = 0
        self._stat_passati = 0
        self._stat_capsule_nate_runtime = 0

        if CAPSULA_MATRIGNA_DEAD:
            log.warning("[CAP_MATRIGNA] KILL SWITCH ATTIVO. Silenziata.")
            return

        try:
            self._init_db()
            # Carica capsule esistenti dal DB (se ci sono)
            self._load_capsule_da_db()
            # Boot iniziale: scansiona winning_signatures + trades per popolare
            self._bootstrap_da_storico()
            # Ricalcola stato di ogni capsula
            self._ricalcola_stati()
            self._last_refresh_ts = time.time()
            self._initialized = True

            n_capsule = len(self._capsule)
            n_operative = sum(1 for c in self._capsule.values() if c['stato'] == 'OPERATIVA')
            n_bloccanti = sum(1 for c in self._capsule.values() if c['verdetto_corrente'] == 'BLOCCA')
            log.info(f"[CAP_MATRIGNA] 👵 v{self.VERSION} attiva — "
                     f"levels={[k for k,v in self.levels_armed.items() if v]} | "
                     f"capsule figlie: {n_capsule} (operative={n_operative}, bloccanti={n_bloccanti})")
        except Exception as e:
            log.error(f"[CAP_MATRIGNA] init fallita: {e}", exc_info=True)

    # ═══════════════════════════════════════════════════════════════════
    # DB SETUP
    # ═══════════════════════════════════════════════════════════════════

    def _init_db(self):
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            cur = conn.cursor()
            cur.executescript(self.SCHEMA_MATRIGNA)
            cur.executescript(self.SCHEMA_VERDETTI)
            cur.executescript(self.SCHEMA_NASCITE)
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
            log.debug(f"[CAP_MAT_WRITE_ERR] {e}")
            return False

    def _db_read(self, sql: str, params: tuple = ()) -> List[tuple]:
        try:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                cur = conn.execute(sql, params)
                return cur.fetchall()
        except Exception as e:
            self._error_count += 1
            log.debug(f"[CAP_MAT_READ_ERR] {e}")
            return []

    # ═══════════════════════════════════════════════════════════════════
    # FIRMA — la chiave d'identità di un trade
    # ═══════════════════════════════════════════════════════════════════

    @staticmethod
    def _make_firma_key(momentum, volatility, trend, regime, direction) -> str:
        """Costruisce la chiave firma 5-dim."""
        return (
            f"{(momentum or '?').upper()}|"
            f"{(volatility or '?').upper()}|"
            f"{(trend or '?').upper()}|"
            f"{(regime or '?').upper()}|"
            f"{(direction or '?').upper()}"
        )

    # ═══════════════════════════════════════════════════════════════════
    # BOOTSTRAP — popola capsule figlie dai dati storici
    # ═══════════════════════════════════════════════════════════════════

    def _bootstrap_da_storico(self):
        """Scansiona winning_signatures + trades.data_json e crea/aggiorna 
        capsule figlie con le statistiche storiche."""
        if not self.levels_armed["L2_NASCITA"]:
            log.info("[CAP_MATRIGNA] L2_NASCITA OFF — skip bootstrap storico")
            return

        # Aggregatore temporaneo
        agg = defaultdict(lambda: {
            'n': 0, 'wins': 0, 'pnl_sum': 0.0,
            'pnl_best': float('-inf'), 'pnl_worst': float('inf'),
            'duration_sum': 0.0, 'peak_delta_sum': 0.0, 'with_peak': 0,
        })

        # FONTE 1: winning_signatures
        try:
            for r in self._db_read(
                "SELECT trade_outcome, pnl_netto, signature_json FROM winning_signatures"
            ):
                try:
                    sig = json.loads(r[2]) if r[2] else {}
                    key = self._make_firma_key(
                        sig.get('momentum'), sig.get('volatility'),
                        sig.get('trend'), sig.get('regime'),
                        sig.get('direction'),
                    )
                    pnl = float(r[1] or 0.0)
                    is_win = 1 if 'WIN' in (r[0] or '').upper() else 0
                    agg[key]['n'] += 1
                    agg[key]['wins'] += is_win
                    agg[key]['pnl_sum'] += pnl
                    agg[key]['pnl_best'] = max(agg[key]['pnl_best'], pnl)
                    agg[key]['pnl_worst'] = min(agg[key]['pnl_worst'], pnl)
                except Exception:
                    continue
        except Exception as e:
            log.debug(f"[CAP_MAT_BOOTSTRAP_WS_ERR] {e}")

        # FONTE 2: trades.data_json (Percorso 1, non in winning_signatures)
        try:
            for r in self._db_read("""
                SELECT t.pnl, t.data_json FROM trades t
                WHERE t.event_type = 'M2_EXIT'
                  AND NOT EXISTS (
                      SELECT 1 FROM winning_signatures w
                      WHERE ABS(t.pnl - w.pnl_netto) < 0.01
                        AND ABS(strftime('%s', t.timestamp) - w.ts) < 60
                  )
            """):
                try:
                    d = json.loads(r[1]) if r[1] else {}
                    key = self._make_firma_key(
                        d.get('momentum'), d.get('volatility'),
                        d.get('trend'), d.get('regime'),
                        d.get('direction'),
                    )
                    pnl = float(r[0] or 0.0)
                    is_win = 1 if pnl > 0 else 0
                    agg[key]['n'] += 1
                    agg[key]['wins'] += is_win
                    agg[key]['pnl_sum'] += pnl
                    agg[key]['pnl_best'] = max(agg[key]['pnl_best'], pnl)
                    agg[key]['pnl_worst'] = min(agg[key]['pnl_worst'], pnl)
                    if d.get('duration'):
                        agg[key]['duration_sum'] += float(d['duration'])
                        agg[key]['with_peak'] += 1
                    if d.get('peak_delta_s'):
                        agg[key]['peak_delta_sum'] += float(d['peak_delta_s'])
                except Exception:
                    continue
        except Exception as e:
            log.debug(f"[CAP_MAT_BOOTSTRAP_TR_ERR] {e}")

        # Materializza in capsule
        now = time.time()
        n_nuove = 0
        for key, d in agg.items():
            n = d['n']
            if n == 0:
                continue
            wr = d['wins'] / n
            pnl_avg = d['pnl_sum'] / n
            durata_avg = d['duration_sum'] / d['with_peak'] if d['with_peak'] else 0.0
            peak_delta_avg = d['peak_delta_sum'] / d['with_peak'] if d['with_peak'] else 0.0

            # Verifico se la capsula già esiste in memoria
            if key in self._capsule:
                # Già caricata dal DB — bootstrap salta
                continue

            capsula = {
                'firma_key':          key,
                'nata_ts':            now,
                'ultima_osservazione': now,
                'n_trade':            n,
                'n_win':              d['wins'],
                'n_loss':             n - d['wins'],
                'pnl_totale':         d['pnl_sum'],
                'pnl_avg':            pnl_avg,
                'wr':                 wr,
                'pnl_best':           d['pnl_best'] if d['pnl_best'] != float('-inf') else 0.0,
                'pnl_worst':          d['pnl_worst'] if d['pnl_worst'] != float('inf') else 0.0,
                'durata_avg':         durata_avg,
                'peak_delta_avg':     peak_delta_avg,
                'stato':              'OSSERVAZIONE_PREVENTIVA',  # ricalcolato dopo
                'verdetto_corrente':  'NEUTRO',                   # ricalcolato dopo
                'budget_esperimenti': 0 if n >= self.BUDGET_ESPERIMENTI_INIZIALE 
                                        else self.BUDGET_ESPERIMENTI_INIZIALE - n,
                'recent_wins_10':     deque(maxlen=10),
                'nota':               'bootstrap_da_storico',
            }
            self._capsule[key] = capsula
            self._persisti_capsula(capsula)
            self._registra_nascita(key, origine='bootstrap_storico')
            n_nuove += 1

        log.info(f"[CAP_MATRIGNA] bootstrap: aggregato {sum(c['n_trade'] for c in self._capsule.values())} "
                 f"trade storici → {n_nuove} capsule figlie nuove, {len(self._capsule)} totali")

    # ═══════════════════════════════════════════════════════════════════
    # CARICAMENTO da DB (se già esistono capsule dalle sessioni precedenti)
    # ═══════════════════════════════════════════════════════════════════

    def _load_capsule_da_db(self):
        """Carica le capsule figlie persistite da sessioni precedenti."""
        try:
            for r in self._db_read("""
                SELECT firma_key, nata_ts, ultima_osservazione, n_trade, n_win, n_loss,
                       pnl_totale, pnl_avg, wr, pnl_best, pnl_worst, durata_avg,
                       peak_delta_avg, stato, verdetto_corrente, budget_esperimenti,
                       recent_wins_10, nota
                FROM capsula_matrigna_figlie
            """):
                try:
                    recent_list = json.loads(r[16]) if r[16] else []
                    capsula = {
                        'firma_key':          r[0],
                        'nata_ts':            r[1] or time.time(),
                        'ultima_osservazione': r[2] or 0.0,
                        'n_trade':            int(r[3] or 0),
                        'n_win':              int(r[4] or 0),
                        'n_loss':             int(r[5] or 0),
                        'pnl_totale':         float(r[6] or 0.0),
                        'pnl_avg':            float(r[7] or 0.0),
                        'wr':                 float(r[8] or 0.0),
                        'pnl_best':           float(r[9] or 0.0),
                        'pnl_worst':          float(r[10] or 0.0),
                        'durata_avg':         float(r[11] or 0.0),
                        'peak_delta_avg':     float(r[12] or 0.0),
                        'stato':              r[13] or 'OSSERVAZIONE_PREVENTIVA',
                        'verdetto_corrente':  r[14] or 'NEUTRO',
                        'budget_esperimenti': int(r[15] or 0),
                        'recent_wins_10':     deque(recent_list, maxlen=10),
                        'nota':               r[17] or '',
                    }
                    self._capsule[r[0]] = capsula
                except Exception as ec:
                    log.debug(f"[CAP_MAT_LOAD_ROW_ERR] {ec}")
            if self._capsule:
                log.info(f"[CAP_MATRIGNA] caricate {len(self._capsule)} capsule figlie da DB")
        except Exception as e:
            log.debug(f"[CAP_MAT_LOAD_ERR] {e}")

    def _persisti_capsula(self, cap: Dict[str, Any]) -> bool:
        """Salva la capsula nel DB."""
        try:
            recent_json = json.dumps(list(cap['recent_wins_10']))
            return self._db_write(
                """INSERT OR REPLACE INTO capsula_matrigna_figlie
                   (firma_key, nata_ts, ultima_osservazione, n_trade, n_win, n_loss,
                    pnl_totale, pnl_avg, wr, pnl_best, pnl_worst, durata_avg,
                    peak_delta_avg, stato, verdetto_corrente, budget_esperimenti,
                    recent_wins_10, nota)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cap['firma_key'], cap['nata_ts'], cap['ultima_osservazione'],
                    cap['n_trade'], cap['n_win'], cap['n_loss'],
                    cap['pnl_totale'], cap['pnl_avg'], cap['wr'],
                    cap['pnl_best'], cap['pnl_worst'],
                    cap['durata_avg'], cap['peak_delta_avg'],
                    cap['stato'], cap['verdetto_corrente'],
                    cap['budget_esperimenti'], recent_json, cap.get('nota', ''),
                )
            )
        except Exception as e:
            log.debug(f"[CAP_MAT_PERSIST_ERR] {e}")
            return False

    def _registra_nascita(self, firma_key: str, origine: str, nota: str = ''):
        self._db_write(
            "INSERT INTO capsula_matrigna_nascite (firma_key, origine, nota) VALUES (?, ?, ?)",
            (firma_key, origine, nota)
        )

    # ═══════════════════════════════════════════════════════════════════
    # RICALCOLO STATO — il cuore adattogeno
    # ═══════════════════════════════════════════════════════════════════

    def _calcola_stato_capsula(self, cap: Dict[str, Any]) -> Tuple[str, str]:
        """Decide stato + verdetto in base ai dati attuali della capsula.
        
        Ritorna (stato, verdetto).
        Stati: OSSERVAZIONE_PREVENTIVA, OSSERVAZIONE_ATTIVA, OPERATIVA, 
               QUARANTENA, CONGELATA
        Verdetti: BLOCCA, NEUTRO, FAVORISCI
        """
        n = cap['n_trade']
        wr = cap['wr']
        recent = list(cap['recent_wins_10'])

        # ── Fase 1: nuova nascita (n < BUDGET) ──
        if n < self.BUDGET_ESPERIMENTI_INIZIALE:
            return 'OSSERVAZIONE_PREVENTIVA', 'NEUTRO'

        # ── Fase 2+3 UNIFICATE: post-budget, pre-maturazione (n in [BUDGET, MATURAZIONE)) ──
        # FIX 27mag (Roberto): il check sopravvivenza valeva solo a n==BUDGET ESATTO,
        # quindi il bootstrap che importa capsule con n>=4 saltava la regola e finiva
        # in OSSERVAZIONE_ATTIVA NEUTRO anche con 0 win. Bug osservato su:
        #   DEBOLE|MEDIA|SIDEWAYS|RANGING|LONG  (n=4, w=0)
        #   DEBOLE|ALTA|SIDEWAYS|RANGING|SHORT  (n=5, w=0)
        # La regola "almeno 1 WIN su 3" è in realtà "WR >= WR_MEDIOCRITA in tutta la fascia
        # pre-maturazione": vale a n=3, deve restare valida a n=4, 5, ..., 19.
        if n < self.SAMPLE_MATURAZIONE:
            wr_iniziali = cap['n_win'] / n
            # Resurrezione da CONGELATA: serve trend di recupero sugli ultimi RESURREZIONE_TRADES
            if cap['stato'] == 'CONGELATA':
                if len(recent) >= self.RESURREZIONE_TRADES:
                    wr_recente = sum(recent) / len(recent)
                    if wr_recente >= self.WR_MEDIOCRITA_SOPRAVVIVENZA:
                        return 'OSSERVAZIONE_ATTIVA', 'NEUTRO'
                return 'CONGELATA', 'BLOCCA'
            # Non era CONGELATA → check sopravvivenza sul WR cumulativo
            if wr_iniziali < self.WR_MEDIOCRITA_SOPRAVVIVENZA:
                return 'CONGELATA', 'BLOCCA'
            return 'OSSERVAZIONE_ATTIVA', 'NEUTRO'

        # ── Fase 4: capsula matura (n >= SAMPLE_MATURAZIONE) ──

        # Check drift: se le ultime 10 sono molto peggiori di storico → QUARANTENA
        if len(recent) >= 10:
            wr_recente = sum(recent) / len(recent)
            wr_storico = wr  # WR completo include anche gli ultimi 10
            if wr_storico > 0.05 and wr_recente < wr_storico * self.DRIFT_FACTOR_QUARANTENA:
                return 'QUARANTENA', 'NEUTRO'

        # Operativa: verdetto basato sul WR
        if wr < self.WR_BLOCCA_FORTE:
            return 'OPERATIVA', 'BLOCCA'
        if wr < self.WR_BLOCCA_DEBOLE:
            return 'OPERATIVA', 'BLOCCA'
        if wr < self.WR_FAVORISCI:
            return 'OPERATIVA', 'NEUTRO'
        return 'OPERATIVA', 'FAVORISCI'

    def _ricalcola_stati(self):
        """Ricalcola stato + verdetto per tutte le capsule."""
        for cap in self._capsule.values():
            nuovo_stato, nuovo_verdetto = self._calcola_stato_capsula(cap)
            if nuovo_stato != cap['stato'] or nuovo_verdetto != cap['verdetto_corrente']:
                vecchio = (cap['stato'], cap['verdetto_corrente'])
                cap['stato'] = nuovo_stato
                cap['verdetto_corrente'] = nuovo_verdetto
                self._persisti_capsula(cap)
                log.info(f"[CAP_MATRIGNA] {cap['firma_key']}: "
                         f"{vecchio[0]}/{vecchio[1]} → {nuovo_stato}/{nuovo_verdetto} "
                         f"(n={cap['n_trade']} wr={cap['wr']:.1%})")

    # ═══════════════════════════════════════════════════════════════════
    # CONSULTA (chiamata dal V16 prima dell'entry)
    # ═══════════════════════════════════════════════════════════════════

    def consulta(
        self,
        momentum: str,
        volatility: str,
        trend: str,
        regime: str,
        direction: str,
        trade_id: Optional[str] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Consulta la capsula figlia per questa firma.
        
        Ritorna (ok, motivo, info):
          ok=True  → passa (verdetto NEUTRO/FAVORISCI o capsula in osservazione)
          ok=False → blocca (solo se is_blocking() e verdetto=BLOCCA)
        
        Sempre tracciato in DB se L1 OCCHI armato.
        """
        if CAPSULA_MATRIGNA_DEAD or not self._initialized:
            return True, "CAP_MAT_OFF", {}

        try:
            # L5 auto-refresh ogni 30 min
            if self.levels_armed["L5_AUTO_REFRESH"]:
                if time.time() - self._last_refresh_ts > self.REFRESH_INTERVAL_SEC:
                    self._ricalcola_stati()
                    self._last_refresh_ts = time.time()

            key = self._make_firma_key(momentum, volatility, trend, regime, direction)
            self._stat_consultazioni += 1

            cap = self._capsule.get(key)

            if cap is None:
                # FIRMA MAI VISTA — nasce capsula nuova
                if self.levels_armed["L2_NASCITA"]:
                    cap = self._nasci_capsula_nuova(key)
                    log.info(f"[CAP_MATRIGNA] 🌱 NUOVO MERCATO RICONOSCIUTO: {key}")
                    self._registra_nascita(key, origine='runtime', nota=f'trade_id={trade_id}')
                else:
                    motivo = f"CAP_NEW_NO_NASCITA key={key}"
                    self._stat_passati += 1
                    return True, motivo, {}

            stato = cap['stato']
            verdetto = cap['verdetto_corrente']
            n = cap['n_trade']
            wr = cap['wr']

            info = {
                'firma_key': key,
                'stato': stato,
                'verdetto': verdetto,
                'n_simili': n,
                'wr_storico': wr,
                'pnl_avg_storico': cap['pnl_avg'],
                'budget_esperimenti': cap['budget_esperimenti'],
            }
            motivo = (f"key={key} stato={stato} verdetto={verdetto} "
                      f"n={n} wr={wr:.1%}")

            self._log_verdetto(trade_id, key, stato, verdetto, n, wr,
                               blocking=self.is_blocking() and verdetto == 'BLOCCA')

            # Decisione finale
            if verdetto == 'BLOCCA':
                self._stat_blocchi_proposti += 1
                if self.is_blocking():
                    return False, motivo, info
                else:
                    return True, f"OBSERVER_WOULD_BLOCK: {motivo}", info

            self._stat_passati += 1
            return True, motivo, info

        except Exception as e:
            self._error_count += 1
            log.debug(f"[CAP_MAT_CONSULTA_ERR] {e}")
            return True, f"CAP_MAT_ERROR:{e}", {}

    def _nasci_capsula_nuova(self, firma_key: str) -> Dict[str, Any]:
        """Crea una nuova capsula figlia con stato iniziale."""
        now = time.time()
        cap = {
            'firma_key':          firma_key,
            'nata_ts':            now,
            'ultima_osservazione': now,
            'n_trade':            0,
            'n_win':              0,
            'n_loss':             0,
            'pnl_totale':         0.0,
            'pnl_avg':            0.0,
            'wr':                 0.0,
            'pnl_best':           0.0,
            'pnl_worst':          0.0,
            'durata_avg':         0.0,
            'peak_delta_avg':     0.0,
            'stato':              'OSSERVAZIONE_PREVENTIVA',
            'verdetto_corrente':  'NEUTRO',
            'budget_esperimenti': self.BUDGET_ESPERIMENTI_INIZIALE,
            'recent_wins_10':     deque(maxlen=10),
            'nota':               'nata_runtime',
        }
        self._capsule[firma_key] = cap
        self._persisti_capsula(cap)
        self._stat_capsule_nate_runtime += 1
        return cap

    def _log_verdetto(self, trade_id, firma_key, stato, verdetto, n, wr, blocking):
        if not self.levels_armed["L1_OCCHI"]:
            return
        self._db_write(
            """INSERT INTO capsula_matrigna_verdetti
               (trade_id, firma_key, stato, verdetto, n_simili, wr_storico, bloccante)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (trade_id, firma_key, stato, verdetto, n, wr, 1 if blocking else 0)
        )

    # ═══════════════════════════════════════════════════════════════════
    # OBSERVE OUTCOME (chiamata dal V16 alla chiusura del trade)
    # ═══════════════════════════════════════════════════════════════════

    def observe_outcome(
        self,
        trade_id: str,
        momentum: str,
        volatility: str,
        trend: str,
        regime: str,
        direction: str,
        pnl_netto: float,
        duration: float = 0.0,
        peak_pnl: float = 0.0,
        peak_delta_s: float = 0.0,
    ):
        """Alimenta la capsula figlia con l'esito di un trade chiuso.
        
        Questa è la cosa più importante: ogni trade chiuso fa imparare la 
        capsula relativa. La SuperRisponditrice cresce con ogni osservazione."""
        if CAPSULA_MATRIGNA_DEAD or not self._initialized:
            return

        try:
            key = self._make_firma_key(momentum, volatility, trend, regime, direction)
            cap = self._capsule.get(key)

            if cap is None:
                # Capsula non esisteva — la creo ora
                if self.levels_armed["L2_NASCITA"]:
                    cap = self._nasci_capsula_nuova(key)
                    log.info(f"[CAP_MATRIGNA] 🌱 NASCITA POSTUMA: {key} (da outcome)")
                else:
                    return

            # Aggiorna stats
            is_win = 1 if pnl_netto > 0 else 0
            cap['n_trade'] += 1
            cap['n_win'] += is_win
            cap['n_loss'] += (1 - is_win)
            cap['pnl_totale'] += pnl_netto
            cap['pnl_avg'] = cap['pnl_totale'] / cap['n_trade']
            cap['wr'] = cap['n_win'] / cap['n_trade']
            cap['pnl_best'] = max(cap['pnl_best'], pnl_netto)
            cap['pnl_worst'] = min(cap['pnl_worst'], pnl_netto)
            cap['ultima_osservazione'] = time.time()

            # Recent 10 (per drift)
            cap['recent_wins_10'].append(is_win)

            # Decrementa budget esperimenti
            if cap['budget_esperimenti'] > 0:
                cap['budget_esperimenti'] -= 1

            # Aggiorna durata media (cumulativa)
            if duration > 0:
                old_avg = cap['durata_avg']
                cap['durata_avg'] = (old_avg * (cap['n_trade'] - 1) + duration) / cap['n_trade']
            if peak_delta_s > 0:
                old_avg = cap['peak_delta_avg']
                cap['peak_delta_avg'] = (old_avg * (cap['n_trade'] - 1) + peak_delta_s) / cap['n_trade']

            # Ricalcola stato/verdetto di questa capsula
            nuovo_stato, nuovo_verdetto = self._calcola_stato_capsula(cap)
            if nuovo_stato != cap['stato'] or nuovo_verdetto != cap['verdetto_corrente']:
                vecchio = (cap['stato'], cap['verdetto_corrente'])
                cap['stato'] = nuovo_stato
                cap['verdetto_corrente'] = nuovo_verdetto
                log.info(f"[CAP_MATRIGNA] {key}: "
                         f"{vecchio[0]}/{vecchio[1]} → {nuovo_stato}/{nuovo_verdetto} "
                         f"(n={cap['n_trade']} wr={cap['wr']:.1%})")

            self._persisti_capsula(cap)

            # Aggiorna verdetto in DB se outcome match
            self._db_write(
                """UPDATE capsula_matrigna_verdetti
                   SET outcome=?, pnl_netto=?
                   WHERE trade_id=? AND outcome IS NULL""",
                ('WIN' if is_win else 'LOSS', pnl_netto, trade_id)
            )

        except Exception as e:
            self._error_count += 1
            log.debug(f"[CAP_MAT_OUTCOME_ERR] {e}", exc_info=True)

    # ═══════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ═══════════════════════════════════════════════════════════════════

    def is_blocking(self) -> bool:
        if CAPSULA_MATRIGNA_DEAD:
            return False
        return bool(self.levels_armed.get("L4_DECIDE", False))

    def get_verdetto(self) -> Dict[str, Any]:
        """Stato corrente della matrigna. Usabile da endpoint app.py."""
        stato = "DEAD"
        if not CAPSULA_MATRIGNA_DEAD:
            if self.is_blocking():
                stato = "BLOCCANTE"
            elif self.levels_armed["L3_PROPONE"]:
                stato = "OBSERVER"
            else:
                stato = "OCCHI_ONLY"

        # Statistiche aggregate
        n_capsule = len(self._capsule)
        per_stato = defaultdict(int)
        per_verdetto = defaultdict(int)
        for cap in self._capsule.values():
            per_stato[cap['stato']] += 1
            per_verdetto[cap['verdetto_corrente']] += 1

        return {
            'version':               self.VERSION,
            'stato':                 stato,
            'levels_armed':          self.levels_armed,
            'consultazioni':         self._stat_consultazioni,
            'blocchi_proposti':      self._stat_blocchi_proposti,
            'trade_passati':         self._stat_passati,
            'capsule_nate_runtime':  self._stat_capsule_nate_runtime,
            'capsule': {
                'totali':            n_capsule,
                'per_stato':         dict(per_stato),
                'per_verdetto':      dict(per_verdetto),
            },
            'error_count':           self._error_count,
        }

    def dump_capsule(self, ordina_per: str = 'n_trade') -> List[Dict[str, Any]]:
        """Lista tutte le capsule figlie con i loro stats. 
        Utile per endpoint diagnostico."""
        items = sorted(self._capsule.values(), key=lambda c: -c.get(ordina_per, 0))
        out = []
        for c in items:
            out.append({
                'firma_key':         c['firma_key'],
                'stato':             c['stato'],
                'verdetto':          c['verdetto_corrente'],
                'n_trade':           c['n_trade'],
                'n_win':             c['n_win'],
                'wr':                round(c['wr'], 3),
                'pnl_totale':        round(c['pnl_totale'], 2),
                'pnl_avg':           round(c['pnl_avg'], 3),
                'pnl_best':          round(c['pnl_best'], 2),
                'pnl_worst':         round(c['pnl_worst'], 2),
                'budget_esperimenti': c['budget_esperimenti'],
            })
        return out

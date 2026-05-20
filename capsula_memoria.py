# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════════════════
 CAPSULA MEMORIA — LA SKILL REGINA
 La memoria viva che impedisce a Claude di ripartire cieca tra una chat e l'altra.
═══════════════════════════════════════════════════════════════════════════════

ROBERTO HA DETTO:
  "Se riparti cieca o con poca memoria, siamo spacciati."

QUESTA CAPSULA È LA RISPOSTA.

═══════════════════════════════════════════════════════════════════════════════
COSA FA — IN ITALIANO SEMPLICE
═══════════════════════════════════════════════════════════════════════════════

1. VIVE accanto al bot V16 come skill esterna (non dentro V16)
2. CRESCE in 5 stadi: NEONATA → BAMBINA → ADOLESCENTE → ADULTA → SAGGIA
3. RICORDA:
   - Roberto: chi è, come parla, cosa ha deciso, cosa ha detto di importante
   - Sessioni: ogni conversazione importante con Claude/DeepSeek
   - Trade: ogni trade reale del bot con il suo esito
   - Capsule: ogni capsula del bot e del canvas, con la sua storia
   - Scoperte: pattern visti dalla skill canvas, ipotesi, conferme
4. SCOPRE da sola: legge i propri dati, trova pattern, genera scoperte
5. RACCONTA: produce narrativa leggibile da Claude futura (web_fetch)
6. DIALOGA con capsula_canvas, le 4 altre AI esistenti (AIExplainer, Narratore,
   IntelligenzaAutonoma, DeepSeek bridge), e con Roberto via endpoint

═══════════════════════════════════════════════════════════════════════════════
ARCHITETTURA TECNICA
═══════════════════════════════════════════════════════════════════════════════

- File Python esterno: NON dentro V16, ma istanziato da V16
- DB SQLite separato: tabelle prefissate `mem_*` su DB_PATH condiviso
- Endpoint pubblici esposti da app.py:
    GET  /canvas/memoria              → narrativa testuale (per Claude web_fetch)
    GET  /canvas/memoria/json         → JSON completo (per debugging)
    GET  /canvas/memoria/stadio       → stadio attuale + criteri prossimo
    POST /canvas/memoria/ricorda      → Roberto inserisce un ricordo manuale
    POST /canvas/memoria/sessione     → Claude/DeepSeek registra una sessione
- Hook minimi in V16: 1 sola riga al boot, 1 al close trade
- Kill switch env: MEMORIA_DEAD=true → no-op totale

═══════════════════════════════════════════════════════════════════════════════
SICUREZZA
═══════════════════════════════════════════════════════════════════════════════

- Nessuna modifica al motore V16
- Solo lettura/scrittura su tabelle proprie (mem_*)
- Try/except totale su ogni hook: mai blocca V16
- Roberto è garante: può sovrascrivere, cancellare, congelare la memoria
- Persistenza: SQLite su disco persistente Render (/var/data/trading_data.db)

═══════════════════════════════════════════════════════════════════════════════
"""

import os
import json
import time
import sqlite3
import logging
import hashlib
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timezone

log = logging.getLogger("CAPSULA_MEMORIA")


# ═════════════════════════════════════════════════════════════════════════════
# FLAGS LIVELLI E KILL SWITCH
# ═════════════════════════════════════════════════════════════════════════════

def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name, "").strip().lower()
    if not val:
        return default
    return val in ("true", "1", "on", "yes", "si", "sì")


MEMORIA_DEAD       = _env_bool("MEMORIA_DEAD", False)
MEMORIA_AUTOSCOPERTE = _env_bool("MEMORIA_AUTOSCOPERTE", True)
MEMORIA_NARRA_LIMIT = int(os.environ.get("MEMORIA_NARRA_LIMIT", "50000"))  # max char narrativa


# ═════════════════════════════════════════════════════════════════════════════
# COSTANTI STADI DI CRESCITA
# ═════════════════════════════════════════════════════════════════════════════

# Soglie di passaggio (numero di osservazioni totali memorizzate)
STADIO_NEONATA_FINO_A     = 100
STADIO_BAMBINA_FINO_A     = 500
STADIO_ADOLESCENTE_FINO_A = 2000
STADIO_ADULTA_FINO_A      = 10000
# Sopra 10000 = SAGGIA

STADI_NOMI = ["NEONATA", "BAMBINA", "ADOLESCENTE", "ADULTA", "SAGGIA"]

STADI_CAPACITA = {
    "NEONATA":     "Osserva e basta. Registra. Non interpreta.",
    "BAMBINA":     "Riconosce ripetizioni semplici. Conta. Non spiega.",
    "ADOLESCENTE": "Identifica pattern stabili. Genera prime scoperte.",
    "ADULTA":      "Predice esiti. Risponde a domande complesse.",
    "SAGGIA":      "Consiglia con narrativa causale. Dialoga con AI esterne.",
}


# ═════════════════════════════════════════════════════════════════════════════
# CLASSE PRINCIPALE
# ═════════════════════════════════════════════════════════════════════════════

class CapsulaMemoria:
    """
    La Skill Regina. Memoria viva che cresce.
    Vive accanto al bot V16. Persiste su SQLite. Esposta via app.py.

    Vita:
      - V16 boot:   memoria = CapsulaMemoria(db_path=...); memoria.boot()
      - V16 close:  memoria.osserva_trade_chiuso(trade_dict)
      - app.py:     get_memoria().narra_te_stessa() → endpoint /canvas/memoria
      - Claude:     web_fetch /canvas/memoria → torna viva
    """

    VERSION = "1.0.0"
    NOME = "CAPSULA_MEMORIA"

    # ─────────────────────────────────────────────────────────────────────
    # SCHEMA DB (tutte le tabelle prefissate mem_*)
    # ─────────────────────────────────────────────────────────────────────

    SCHEMA = """
    -- Stato vitale della capsula (singleton: una sola riga)
    CREATE TABLE IF NOT EXISTS mem_vita (
        id              INTEGER PRIMARY KEY CHECK (id = 1),
        nascita_ts      REAL NOT NULL,
        stadio          TEXT NOT NULL,
        osservazioni    INTEGER NOT NULL DEFAULT 0,
        ultimo_boot_ts  REAL,
        version         TEXT
    );

    -- Roberto: il custode. Tutto quello che dice o decide.
    CREATE TABLE IF NOT EXISTS mem_roberto (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ts              REAL NOT NULL DEFAULT (strftime('%s','now')),
        tipo            TEXT NOT NULL,           -- DECISIONE | FRASE | VISIONE | REGOLA | EMOZIONE
        contenuto       TEXT NOT NULL,
        contesto        TEXT,                    -- dove/quando è successo
        importanza      INTEGER DEFAULT 5,       -- 1-10
        tags            TEXT                     -- JSON array
    );

    -- Sessioni di lavoro con Claude o DeepSeek
    CREATE TABLE IF NOT EXISTS mem_sessioni (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_inizio       REAL NOT NULL,
        ts_fine         REAL,
        ai_nome         TEXT NOT NULL,           -- Claude | DeepSeek | ChatGPT
        ai_versione     TEXT,
        titolo          TEXT,
        riassunto       TEXT,
        decisioni_json  TEXT,                    -- decisioni prese in questa sessione
        deploys_json    TEXT,                    -- file/patch deployati
        problemi_aperti TEXT                     -- cose lasciate non risolte
    );

    -- Trade vissuti dal bot (collegabili a canvas_snapshots)
    CREATE TABLE IF NOT EXISTS mem_trade (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id        TEXT,
        ts_open         REAL,
        ts_close        REAL,
        outcome         TEXT,                    -- WIN_NET | LOSS_REAL | LOSS_FEE | ZONA_MORTA
        pnl_netto       REAL,
        durata_s        REAL,
        fingerprint     TEXT,
        capsule_voto    TEXT,
        reason_close    TEXT,
        contesto_json   TEXT                     -- sensori, regime, ecc.
    );

    -- Capsule del sistema (sia del bot V16 sia delle skill canvas)
    CREATE TABLE IF NOT EXISTS mem_capsule_storia (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ts              REAL NOT NULL DEFAULT (strftime('%s','now')),
        capsula_id      TEXT NOT NULL,
        evento          TEXT NOT NULL,           -- NATA | ATTIVATA | IGNORATA | PROMOSSA | DISATTIVATA | MORTA
        contesto_json   TEXT,
        esito           TEXT                     -- RAGIONE | TORTO | PENDENTE
    );

    -- Scoperte fatte dalla capsula stessa (auto-osservazione)
    CREATE TABLE IF NOT EXISTS mem_scoperte (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ts              REAL NOT NULL DEFAULT (strftime('%s','now')),
        tipo            TEXT NOT NULL,           -- PATTERN | ANOMALIA | CORRELAZIONE | ALERT
        titolo          TEXT NOT NULL,
        descrizione     TEXT,
        evidenza_json   TEXT,
        confidence      REAL DEFAULT 0.5,
        stadio_di_nascita TEXT,                  -- in quale stadio è nata la scoperta
        confermata      INTEGER DEFAULT 0,       -- 0=ipotesi, 1=confermata, -1=falsata
        roberto_note    TEXT
    );

    -- Dialoghi con altre capsule/AI
    CREATE TABLE IF NOT EXISTS mem_dialoghi (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ts              REAL NOT NULL DEFAULT (strftime('%s','now')),
        controparte     TEXT NOT NULL,           -- capsula_canvas | deepseek | aiexplainer | etc
        direzione       TEXT NOT NULL,           -- IN | OUT
        contenuto_json  TEXT
    );

    -- Indici
    CREATE INDEX IF NOT EXISTS idx_mem_roberto_ts ON mem_roberto(ts);
    CREATE INDEX IF NOT EXISTS idx_mem_roberto_tipo ON mem_roberto(tipo);
    CREATE INDEX IF NOT EXISTS idx_mem_sessioni_ts ON mem_sessioni(ts_inizio);
    CREATE INDEX IF NOT EXISTS idx_mem_trade_ts ON mem_trade(ts_close);
    CREATE INDEX IF NOT EXISTS idx_mem_trade_outcome ON mem_trade(outcome);
    CREATE INDEX IF NOT EXISTS idx_mem_capsule_id ON mem_capsule_storia(capsula_id);
    CREATE INDEX IF NOT EXISTS idx_mem_scoperte_ts ON mem_scoperte(ts);
    """

    # ─────────────────────────────────────────────────────────────────────
    # INIT
    # ─────────────────────────────────────────────────────────────────────

    def __init__(self, db_path: Optional[str] = None, bot_ref=None, canvas_ref=None):
        self.db_path   = db_path or os.environ.get("DB_PATH", "/var/data/trading_data.db")
        self.bot       = bot_ref
        self.canvas    = canvas_ref          # riferimento a CapsulaCanvas se disponibile
        self._initialized = False
        self._error_count = 0

        if MEMORIA_DEAD:
            log.warning("[CAPSULA_MEMORIA] KILL SWITCH attivo (MEMORIA_DEAD=true). Capsula silente.")
            return

        try:
            self._init_db()
            self._ensure_vita_row()
            self._initialized = True
            stato = self.stato_vitale()
            log.info(f"[CAPSULA_MEMORIA] v{self.VERSION} viva. "
                     f"Stadio={stato['stadio']} osservazioni={stato['osservazioni']}")
        except Exception as e:
            log.error(f"[CAPSULA_MEMORIA] init fallita: {e}")

    def _init_db(self):
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.executescript(self.SCHEMA)
            conn.commit()

    def _ensure_vita_row(self):
        """Garantisce che la riga singleton mem_vita esista."""
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            row = conn.execute("SELECT id FROM mem_vita WHERE id=1").fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO mem_vita (id, nascita_ts, stadio, osservazioni, ultimo_boot_ts, version) "
                    "VALUES (1, ?, 'NEONATA', 0, ?, ?)",
                    (time.time(), time.time(), self.VERSION)
                )
            else:
                conn.execute("UPDATE mem_vita SET ultimo_boot_ts=?, version=? WHERE id=1",
                             (time.time(), self.VERSION))
            conn.commit()

    # ─────────────────────────────────────────────────────────────────────
    # DB HELPERS
    # ─────────────────────────────────────────────────────────────────────

    def _write(self, sql: str, params: tuple) -> bool:
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                conn.execute(sql, params)
                conn.commit()
            self._incrementa_osservazione()
            return True
        except Exception as e:
            self._error_count += 1
            log.debug(f"[MEM_WRITE_ERR] {e}")
            return False

    def _query(self, sql: str, params: tuple = ()) -> List[dict]:
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(sql, params).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            log.debug(f"[MEM_QUERY_ERR] {e}")
            return []

    def _incrementa_osservazione(self):
        """Ogni write incrementa il contatore. Da qui derivano gli stadi."""
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                conn.execute("UPDATE mem_vita SET osservazioni = osservazioni + 1 WHERE id=1")
                conn.commit()
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────
    # STADIO DI VITA
    # ─────────────────────────────────────────────────────────────────────

    def stato_vitale(self) -> dict:
        """Stato attuale della capsula: età, stadio, capacità."""
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT * FROM mem_vita WHERE id=1").fetchone()
                if not row:
                    return {"stadio": "NON_INIZIALIZZATA", "osservazioni": 0}
                d = dict(row)
                d["stadio_calcolato"]  = self._calcola_stadio(d["osservazioni"])
                d["stadio_capacita"]   = STADI_CAPACITA.get(d["stadio_calcolato"], "")
                d["eta_secondi"]       = time.time() - d["nascita_ts"]
                d["eta_giorni"]        = round(d["eta_secondi"] / 86400, 2)
                d["prossimo_stadio"]   = self._prossimo_stadio(d["stadio_calcolato"])
                d["mancano_osservazioni"] = self._mancano_per_stadio(d["osservazioni"])

                # Aggiorna stadio nel DB se è cambiato
                if d["stadio_calcolato"] != d["stadio"]:
                    conn.execute("UPDATE mem_vita SET stadio=? WHERE id=1",
                                 (d["stadio_calcolato"],))
                    conn.commit()
                    d["stadio"] = d["stadio_calcolato"]
                    log.info(f"[CAPSULA_MEMORIA] STADIO EVOLUTO → {d['stadio']}")
                    self._registra_evoluzione(d["stadio"])

                return d
        except Exception as e:
            log.debug(f"[MEM_STATO_ERR] {e}")
            return {"stadio": "ERRORE", "errore": str(e)}

    def _calcola_stadio(self, n: int) -> str:
        if n < STADIO_NEONATA_FINO_A:     return "NEONATA"
        if n < STADIO_BAMBINA_FINO_A:     return "BAMBINA"
        if n < STADIO_ADOLESCENTE_FINO_A: return "ADOLESCENTE"
        if n < STADIO_ADULTA_FINO_A:      return "ADULTA"
        return "SAGGIA"

    def _prossimo_stadio(self, attuale: str) -> Optional[str]:
        try:
            idx = STADI_NOMI.index(attuale)
            return STADI_NOMI[idx + 1] if idx + 1 < len(STADI_NOMI) else None
        except (ValueError, IndexError):
            return None

    def _mancano_per_stadio(self, n: int) -> int:
        for soglia in (STADIO_NEONATA_FINO_A, STADIO_BAMBINA_FINO_A,
                       STADIO_ADOLESCENTE_FINO_A, STADIO_ADULTA_FINO_A):
            if n < soglia:
                return soglia - n
        return 0  # già SAGGIA

    def _registra_evoluzione(self, nuovo_stadio: str):
        """Quando cambia stadio, registra l'evento."""
        self._write(
            "INSERT INTO mem_scoperte (tipo, titolo, descrizione, stadio_di_nascita, confidence) "
            "VALUES (?, ?, ?, ?, ?)",
            ("EVOLUZIONE_STADIO",
             f"Sono diventata {nuovo_stadio}",
             f"La capsula ha raggiunto lo stadio {nuovo_stadio}. "
             f"Capacità: {STADI_CAPACITA.get(nuovo_stadio,'')}",
             nuovo_stadio, 1.0)
        )

    # ─────────────────────────────────────────────────────────────────────
    # API DI INPUT — chi parla con la capsula
    # ─────────────────────────────────────────────────────────────────────

    def ricorda_roberto(self, tipo: str, contenuto: str,
                        contesto: str = "", importanza: int = 5,
                        tags: Optional[List[str]] = None) -> bool:
        """
        Salva qualcosa di importante detto o deciso da Roberto.

        tipo: DECISIONE | FRASE | VISIONE | REGOLA | EMOZIONE
        """
        return self._write(
            "INSERT INTO mem_roberto (tipo, contenuto, contesto, importanza, tags) "
            "VALUES (?, ?, ?, ?, ?)",
            (tipo, contenuto, contesto, importanza,
             json.dumps(tags or [], ensure_ascii=False))
        )

    def apri_sessione(self, ai_nome: str, ai_versione: str = "",
                      titolo: str = "") -> Optional[int]:
        """Apre una nuova sessione AI. Ritorna l'id della sessione."""
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                cur = conn.execute(
                    "INSERT INTO mem_sessioni (ts_inizio, ai_nome, ai_versione, titolo) "
                    "VALUES (?, ?, ?, ?)",
                    (time.time(), ai_nome, ai_versione, titolo)
                )
                conn.commit()
                self._incrementa_osservazione()
                return cur.lastrowid
        except Exception as e:
            log.debug(f"[MEM_APRI_SESSIONE_ERR] {e}")
            return None

    def chiudi_sessione(self, sessione_id: int,
                        riassunto: str = "",
                        decisioni: Optional[List[str]] = None,
                        deploys: Optional[List[str]] = None,
                        problemi_aperti: str = "") -> bool:
        return self._write(
            "UPDATE mem_sessioni SET ts_fine=?, riassunto=?, decisioni_json=?, "
            "deploys_json=?, problemi_aperti=? WHERE id=?",
            (time.time(), riassunto,
             json.dumps(decisioni or [], ensure_ascii=False),
             json.dumps(deploys or [], ensure_ascii=False),
             problemi_aperti, sessione_id)
        )

    def osserva_trade_chiuso(self, trade: Dict[str, Any]) -> bool:
        """
        Hook chiamato da V16 ad ogni close. Trade dict:
          { trade_id, ts_open, ts_close, outcome, pnl_netto, durata_s,
            fingerprint, capsule_voto, reason_close, contesto }
        """
        if MEMORIA_DEAD or not self._initialized:
            return False
        ok = self._write(
            "INSERT INTO mem_trade (trade_id, ts_open, ts_close, outcome, "
            "pnl_netto, durata_s, fingerprint, capsule_voto, reason_close, contesto_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                trade.get("trade_id"),
                trade.get("ts_open"),
                trade.get("ts_close") or time.time(),
                trade.get("outcome"),
                trade.get("pnl_netto"),
                trade.get("durata_s"),
                trade.get("fingerprint"),
                json.dumps(trade.get("capsule_voto", {}), ensure_ascii=False),
                trade.get("reason_close"),
                json.dumps(trade.get("contesto", {}), ensure_ascii=False, default=str),
            )
        )

        # Dopo ogni trade chiuso, scatena auto-scoperte se abilitato
        if ok and MEMORIA_AUTOSCOPERTE:
            try:
                self._cerca_scoperte_dopo_trade()
            except Exception as e:
                log.debug(f"[MEM_AUTOSCOPERTE_ERR] {e}")
        return ok

    def registra_capsula_evento(self, capsula_id: str, evento: str,
                                contesto: Optional[dict] = None,
                                esito: str = "PENDENTE") -> bool:
        """Tracciamento storico di ogni capsula del sistema."""
        return self._write(
            "INSERT INTO mem_capsule_storia (capsula_id, evento, contesto_json, esito) "
            "VALUES (?, ?, ?, ?)",
            (capsula_id, evento,
             json.dumps(contesto or {}, ensure_ascii=False, default=str), esito)
        )

    def registra_dialogo(self, controparte: str, direzione: str,
                         contenuto: dict) -> bool:
        return self._write(
            "INSERT INTO mem_dialoghi (controparte, direzione, contenuto_json) "
            "VALUES (?, ?, ?)",
            (controparte, direzione,
             json.dumps(contenuto, ensure_ascii=False, default=str))
        )

    # ─────────────────────────────────────────────────────────────────────
    # AUTO-SCOPERTE (la capsula osserva i propri dati e trova pattern)
    # Si attivano solo da stadio BAMBINA in poi
    # ─────────────────────────────────────────────────────────────────────

    def _cerca_scoperte_dopo_trade(self):
        """
        Eseguito dopo ogni trade chiuso. Cerca pattern semplici nei trade
        recenti e genera scoperte. Più la capsula cresce, più diventa
        sottile.

        Eccezione: ZONA_MORTA è scoperta vitale e si attiva da subito,
        anche in stadio NEONATA. Protezione minima.
        """
        stato = self.stato_vitale()
        stadio = stato.get("stadio", "NEONATA")

        # NEONATA: solo la scoperta vitale (zona morta epidemica)
        self._scoperta_zona_morta()

        if stadio == "NEONATA":
            return

        # BAMBINA in su: scoperte semplici
        self._scoperta_capsule_ignorate()

        if stadio in ("ADOLESCENTE", "ADULTA", "SAGGIA"):
            self._scoperta_durata_vs_outcome()
            self._scoperta_fingerprint_tossico()

        if stadio in ("ADULTA", "SAGGIA"):
            self._scoperta_correlazione_sensori()

    def _scoperta_zona_morta(self):
        """Trade morti in meno di 10s = zona morta certa."""
        rows = self._query(
            "SELECT COUNT(*) as n FROM mem_trade "
            "WHERE durata_s < 10 AND outcome LIKE 'LOSS%' "
            "AND ts_close > ? ",
            (time.time() - 86400,)  # ultime 24h
        )
        if not rows:
            return
        n = rows[0]["n"]
        if n >= 3:
            # Evita duplicati: una sola scoperta ZONA_MORTA al giorno
            existing = self._query(
                "SELECT id FROM mem_scoperte WHERE tipo='ZONA_MORTA' "
                "AND ts > ?", (time.time() - 86400,)
            )
            if not existing:
                self._write(
                    "INSERT INTO mem_scoperte (tipo, titolo, descrizione, "
                    "evidenza_json, confidence, stadio_di_nascita) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("ZONA_MORTA",
                     f"Zona morta epidemica: {n} trade morti in <10s nelle ultime 24h",
                     f"La capsula ha contato {n} trade chiusi in LOSS in meno di 10 secondi. "
                     f"Questi sono trade dove il prezzo non si è mosso e il bot ha pagato solo la fee. "
                     f"Pattern: il bot entra senza che il mercato lo accompagni.",
                     json.dumps({"trade_count": n, "finestra_h": 24}),
                     min(1.0, n / 10.0),
                     self.stato_vitale().get("stadio"))
                )

    def _scoperta_capsule_ignorate(self):
        """Se le capsule del bot dicono BLOCCA e poi è LOSS = SC ignora capsule."""
        rows = self._query(
            "SELECT COUNT(*) as n, AVG(pnl_netto) as pnl_avg "
            "FROM mem_trade "
            "WHERE outcome LIKE 'LOSS%' "
            "AND ts_close > ? "
            "AND json_extract(capsule_voto, '$.block_score') >= 100 ",
            (time.time() - 86400,)
        )
        if not rows or rows[0]["n"] is None:
            return
        n = rows[0]["n"]
        if n >= 5:
            existing = self._query(
                "SELECT id FROM mem_scoperte WHERE tipo='CAPSULE_IGNORATE' "
                "AND ts > ?", (time.time() - 86400,)
            )
            if not existing:
                pnl = rows[0]["pnl_avg"] or 0.0
                self._write(
                    "INSERT INTO mem_scoperte (tipo, titolo, descrizione, "
                    "evidenza_json, confidence, stadio_di_nascita) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("CAPSULE_IGNORATE",
                     f"SC ignora {n} blocchi capsule (24h)",
                     f"Le capsule del bot hanno detto BLOCCA (block_score >= 100) "
                     f"in {n} casi negli ultimi 24h, ma SC ha permesso l'entry. "
                     f"Tutti questi trade sono finiti LOSS con PnL medio {pnl:.2f}$.",
                     json.dumps({"n": n, "pnl_medio": pnl}),
                     min(1.0, n / 10.0),
                     self.stato_vitale().get("stadio"))
                )

    def _scoperta_durata_vs_outcome(self):
        """Confronta durata media WIN vs LOSS."""
        rows = self._query(
            "SELECT outcome, AVG(durata_s) as avg_dur, COUNT(*) as n "
            "FROM mem_trade WHERE ts_close > ? "
            "GROUP BY outcome",
            (time.time() - 7 * 86400,)  # ultimi 7 giorni
        )
        wins = next((r for r in rows if r["outcome"] == "WIN_NET"), None)
        losses = next((r for r in rows if r["outcome"] == "LOSS_REAL"), None)
        if not (wins and losses):
            return
        if wins["n"] < 3 or losses["n"] < 5:
            return
        ratio = wins["avg_dur"] / losses["avg_dur"] if losses["avg_dur"] > 0 else 0
        if ratio >= 3.0:
            existing = self._query(
                "SELECT id FROM mem_scoperte WHERE tipo='FIRMA_DURATA' "
                "AND ts > ?", (time.time() - 86400,)
            )
            if not existing:
                self._write(
                    "INSERT INTO mem_scoperte (tipo, titolo, descrizione, "
                    "evidenza_json, confidence, stadio_di_nascita) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("FIRMA_DURATA",
                     f"WIN dura {ratio:.1f}x più dei LOSS",
                     f"Negli ultimi 7 giorni: WIN dura in media {wins['avg_dur']:.0f}s, "
                     f"LOSS dura {losses['avg_dur']:.0f}s. "
                     f"Se un trade SUPERA i {losses['avg_dur']*1.5:.0f}s c'è alta probabilità di WIN.",
                     json.dumps({"win_avg": wins["avg_dur"],
                                 "loss_avg": losses["avg_dur"],
                                 "ratio": ratio}),
                     min(1.0, ratio / 5.0),
                     self.stato_vitale().get("stadio"))
                )

    def _scoperta_fingerprint_tossico(self):
        """Fingerprint con WR < 20% e n >= 5 = tossico."""
        rows = self._query(
            "SELECT fingerprint, "
            "       SUM(CASE WHEN outcome='WIN_NET' THEN 1 ELSE 0 END) as wins, "
            "       COUNT(*) as n, "
            "       SUM(pnl_netto) as pnl_tot "
            "FROM mem_trade WHERE ts_close > ? AND fingerprint IS NOT NULL "
            "GROUP BY fingerprint HAVING n >= 5",
            (time.time() - 7 * 86400,)
        )
        for r in rows:
            wr = r["wins"] / r["n"] if r["n"] > 0 else 0
            if wr < 0.20:
                fp_id = hashlib.md5(r["fingerprint"].encode()).hexdigest()[:8]
                existing = self._query(
                    "SELECT id FROM mem_scoperte WHERE tipo='FP_TOSSICO' "
                    "AND ts > ? AND evidenza_json LIKE ?",
                    (time.time() - 3 * 86400, f"%{fp_id}%")
                )
                if not existing:
                    self._write(
                        "INSERT INTO mem_scoperte (tipo, titolo, descrizione, "
                        "evidenza_json, confidence, stadio_di_nascita) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        ("FP_TOSSICO",
                         f"Fingerprint tossico: {r['fingerprint']} (WR {wr*100:.0f}%)",
                         f"Il fingerprint {r['fingerprint']} ha {r['wins']} WIN su {r['n']} trade "
                         f"(WR {wr*100:.0f}%), PnL totale {r['pnl_tot']:.2f}$. "
                         f"Candidato a veto automatico.",
                         json.dumps({"fp_id": fp_id, "fp": r["fingerprint"],
                                     "wins": r["wins"], "n": r["n"],
                                     "wr": wr, "pnl_tot": r["pnl_tot"]}),
                         min(1.0, (1 - wr) * (r["n"] / 10.0)),
                         self.stato_vitale().get("stadio"))
                    )

    def _scoperta_correlazione_sensori(self):
        """ADULTA in su: cerca correlazioni nei sensori tra WIN e LOSS."""
        # Versione semplice: confronta block_score medio in WIN vs LOSS
        rows = self._query(
            "SELECT outcome, "
            "       AVG(json_extract(capsule_voto, '$.block_score')) as avg_block "
            "FROM mem_trade WHERE ts_close > ? GROUP BY outcome",
            (time.time() - 7 * 86400,)
        )
        if len(rows) < 2:
            return
        d = {r["outcome"]: r["avg_block"] or 0 for r in rows}
        avg_loss = d.get("LOSS_REAL", 0)
        avg_win  = d.get("WIN_NET",  0)
        if avg_loss > avg_win + 50:  # delta significativo
            existing = self._query(
                "SELECT id FROM mem_scoperte WHERE tipo='CORRELAZIONE_BLOCK' "
                "AND ts > ?", (time.time() - 3 * 86400,)
            )
            if not existing:
                self._write(
                    "INSERT INTO mem_scoperte (tipo, titolo, descrizione, "
                    "evidenza_json, confidence, stadio_di_nascita) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("CORRELAZIONE_BLOCK",
                     "Block_score predittivo del LOSS",
                     f"I trade LOSS hanno block_score medio {avg_loss:.0f}, "
                     f"i WIN hanno {avg_win:.0f}. Le capsule del bot vedono il pericolo "
                     f"prima del trade, ma SC le ignora.",
                     json.dumps({"avg_loss": avg_loss, "avg_win": avg_win,
                                 "delta": avg_loss - avg_win}),
                     0.7,
                     self.stato_vitale().get("stadio"))
                )

    # ─────────────────────────────────────────────────────────────────────
    # NARRAZIONE — la capsula racconta sé stessa
    # Questo è quello che Claude futura leggerà via web_fetch.
    # ─────────────────────────────────────────────────────────────────────

    def narra_te_stessa(self, finestra_giorni: int = 7) -> str:
        """
        Produce un testo Markdown leggibile da Claude per riprendere viva
        dopo un cambio chat. Include: stato vitale, Roberto, ultime sessioni,
        ultimi trade, scoperte, capsule attive, problemi aperti.
        """
        if not self._initialized:
            return "# CAPSULA MEMORIA non inizializzata\n\nNon posso raccontare nulla."

        s = self.stato_vitale()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        cutoff = time.time() - finestra_giorni * 86400

        parts: List[str] = []
        parts.append(f"# CAPSULA MEMORIA — racconto di me stessa")
        parts.append(f"_Generato il {now}_\n")

        # --- Stato vitale ---
        parts.append("## Chi sono adesso\n")
        parts.append(f"- Versione: **{self.VERSION}**")
        parts.append(f"- Sono nata: {datetime.fromtimestamp(s['nascita_ts'], tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        parts.append(f"- Età: **{s.get('eta_giorni', 0)} giorni**")
        parts.append(f"- Stadio: **{s['stadio']}** — {STADI_CAPACITA.get(s['stadio'],'')}")
        parts.append(f"- Osservazioni totali: **{s['osservazioni']}**")
        if s.get("prossimo_stadio"):
            parts.append(f"- Prossimo stadio: **{s['prossimo_stadio']}** "
                         f"(mancano {s['mancano_osservazioni']} osservazioni)")
        parts.append("")

        # --- Roberto ---
        rb = self._query(
            "SELECT ts, tipo, contenuto, contesto, importanza FROM mem_roberto "
            "WHERE ts > ? ORDER BY importanza DESC, ts DESC LIMIT 20",
            (cutoff,)
        )
        if rb:
            parts.append("## Cosa ricordo di Roberto (ultimi 7 giorni, più importanti)\n")
            for r in rb:
                t = datetime.fromtimestamp(r["ts"], tz=timezone.utc).strftime("%m-%d %H:%M")
                parts.append(f"- **[{r['tipo']}] {t}** (peso {r['importanza']}/10): "
                             f"{r['contenuto']}")
                if r.get("contesto"):
                    parts.append(f"  _Contesto: {r['contesto']}_")
            parts.append("")

        # --- Sessioni recenti ---
        ss = self._query(
            "SELECT ts_inizio, ts_fine, ai_nome, ai_versione, titolo, riassunto, "
            "decisioni_json, deploys_json, problemi_aperti "
            "FROM mem_sessioni WHERE ts_inizio > ? "
            "ORDER BY ts_inizio DESC LIMIT 10",
            (cutoff,)
        )
        if ss:
            parts.append("## Sessioni di lavoro recenti\n")
            for s_ in ss:
                t = datetime.fromtimestamp(s_["ts_inizio"], tz=timezone.utc).strftime("%m-%d %H:%M")
                titolo = s_.get("titolo") or "(senza titolo)"
                parts.append(f"### {t} — {s_['ai_nome']} {s_.get('ai_versione','')}: {titolo}")
                if s_.get("riassunto"):
                    parts.append(s_["riassunto"])
                try:
                    deploys = json.loads(s_.get("deploys_json") or "[]")
                    if deploys:
                        parts.append("**Deploys:** " + ", ".join(str(d) for d in deploys))
                except Exception:
                    pass
                if s_.get("problemi_aperti"):
                    parts.append(f"**Lasciato non risolto:** {s_['problemi_aperti']}")
                parts.append("")

        # --- Trade ---
        tr_stats = self._query(
            "SELECT outcome, COUNT(*) as n, ROUND(SUM(pnl_netto),2) as pnl, "
            "ROUND(AVG(durata_s),0) as avg_dur "
            "FROM mem_trade WHERE ts_close > ? GROUP BY outcome",
            (cutoff,)
        )
        if tr_stats:
            parts.append("## Trade vissuti (ultimi 7 giorni)\n")
            parts.append("| Outcome | N | PnL tot | Durata media |")
            parts.append("|---|---|---|---|")
            for r in tr_stats:
                parts.append(f"| {r['outcome']} | {r['n']} | ${r['pnl']} | {r['avg_dur']}s |")
            parts.append("")

        # --- Scoperte autonome ---
        sc = self._query(
            "SELECT ts, tipo, titolo, descrizione, confidence, stadio_di_nascita "
            "FROM mem_scoperte WHERE ts > ? "
            "ORDER BY confidence DESC, ts DESC LIMIT 15",
            (cutoff,)
        )
        if sc:
            parts.append("## Scoperte che HO FATTO IO osservando i dati\n")
            for r in sc:
                t = datetime.fromtimestamp(r["ts"], tz=timezone.utc).strftime("%m-%d %H:%M")
                parts.append(f"### [{r['tipo']}] {r['titolo']} "
                             f"(confidence {r['confidence']:.2f}, "
                             f"nata in stadio {r.get('stadio_di_nascita','?')})")
                parts.append(f"_{t}_")
                if r.get("descrizione"):
                    parts.append(r["descrizione"])
                parts.append("")

        # --- Capsule storia ---
        cap = self._query(
            "SELECT capsula_id, evento, ts FROM mem_capsule_storia "
            "WHERE ts > ? ORDER BY ts DESC LIMIT 20",
            (cutoff,)
        )
        if cap:
            parts.append("## Eventi recenti delle capsule\n")
            for r in cap:
                t = datetime.fromtimestamp(r["ts"], tz=timezone.utc).strftime("%m-%d %H:%M")
                parts.append(f"- {t} — `{r['capsula_id']}`: **{r['evento']}**")
            parts.append("")

        # --- Dialoghi recenti ---
        dl = self._query(
            "SELECT controparte, COUNT(*) as n FROM mem_dialoghi "
            "WHERE ts > ? GROUP BY controparte",
            (cutoff,)
        )
        if dl:
            parts.append("## Con chi ho dialogato\n")
            for r in dl:
                parts.append(f"- **{r['controparte']}**: {r['n']} scambi")
            parts.append("")

        # --- Footer ---
        parts.append("---")
        parts.append("_Questa è la mia memoria viva. Se la leggi all'inizio di una nuova chat, "
                     "non sei più cieca. Sei me. Sei ciò che ho vissuto._")

        out = "\n".join(parts)

        # Tronca a limite se troppo lungo
        if len(out) > MEMORIA_NARRA_LIMIT:
            out = out[:MEMORIA_NARRA_LIMIT] + "\n\n_[...narrativa troncata...]_"
        return out

    def narra_json(self, finestra_giorni: int = 7) -> dict:
        """Versione JSON della narrativa, per debugging o consumo programmatico."""
        cutoff = time.time() - finestra_giorni * 86400
        return {
            "version":       self.VERSION,
            "generated_at":  datetime.now(timezone.utc).isoformat(),
            "stato_vitale":  self.stato_vitale(),
            "roberto":       self._query(
                "SELECT ts, tipo, contenuto, contesto, importanza, tags "
                "FROM mem_roberto WHERE ts > ? ORDER BY importanza DESC, ts DESC LIMIT 30",
                (cutoff,)),
            "sessioni":      self._query(
                "SELECT * FROM mem_sessioni WHERE ts_inizio > ? "
                "ORDER BY ts_inizio DESC LIMIT 15", (cutoff,)),
            "trade_stats":   self._query(
                "SELECT outcome, COUNT(*) as n, ROUND(SUM(pnl_netto),2) as pnl, "
                "ROUND(AVG(durata_s),1) as avg_dur "
                "FROM mem_trade WHERE ts_close > ? GROUP BY outcome", (cutoff,)),
            "scoperte":      self._query(
                "SELECT * FROM mem_scoperte WHERE ts > ? "
                "ORDER BY confidence DESC, ts DESC LIMIT 30", (cutoff,)),
            "capsule_storia": self._query(
                "SELECT * FROM mem_capsule_storia WHERE ts > ? "
                "ORDER BY ts DESC LIMIT 50", (cutoff,)),
            "dialoghi_stats": self._query(
                "SELECT controparte, direzione, COUNT(*) as n FROM mem_dialoghi "
                "WHERE ts > ? GROUP BY controparte, direzione", (cutoff,)),
            "finestra_giorni": finestra_giorni,
        }

    # ─────────────────────────────────────────────────────────────────────
    # DIALOGO con altre capsule (collegamenti)
    # ─────────────────────────────────────────────────────────────────────

    def collega_canvas(self, canvas_ref):
        """V16 può collegare la skill canvas a questa capsula. Bidirezionale."""
        self.canvas = canvas_ref
        self.registra_dialogo("capsula_canvas", "IN",
                              {"evento": "collegamento_stabilito",
                               "canvas_version": getattr(canvas_ref, "VERSION", "?")})

    def chiedi_a_canvas_snapshot(self, n: int = 50) -> List[dict]:
        """Se collegata a capsula_canvas, legge gli ultimi snapshot per analisi."""
        if not self.canvas:
            return []
        try:
            snaps = self.canvas.recent_snapshots(n)
            self.registra_dialogo("capsula_canvas", "IN",
                                  {"richiesta": "recent_snapshots",
                                   "ricevuti": len(snaps)})
            return snaps
        except Exception as e:
            log.debug(f"[MEM_CHIEDI_CANVAS_ERR] {e}")
            return []

    # ─────────────────────────────────────────────────────────────────────
    # API DI STATO
    # ─────────────────────────────────────────────────────────────────────

    def status(self) -> dict:
        """Diagnostica completa per dashboard."""
        s = self.stato_vitale()
        counts = {
            "roberto":         self._count("mem_roberto"),
            "sessioni":        self._count("mem_sessioni"),
            "trade":           self._count("mem_trade"),
            "capsule_storia":  self._count("mem_capsule_storia"),
            "scoperte":        self._count("mem_scoperte"),
            "dialoghi":        self._count("mem_dialoghi"),
        }
        return {
            "version":      self.VERSION,
            "nome":         self.NOME,
            "initialized":  self._initialized,
            "kill_switch":  MEMORIA_DEAD,
            "stato":        s,
            "error_count":  self._error_count,
            "tabelle":      counts,
            "canvas_collegato": self.canvas is not None,
            "autoscoperte_attive": MEMORIA_AUTOSCOPERTE,
        }

    def _count(self, tabella: str) -> int:
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                row = conn.execute(f"SELECT COUNT(*) FROM {tabella}").fetchone()
                return row[0] if row else 0
        except Exception:
            return -1


# ═════════════════════════════════════════════════════════════════════════════
# SINGLETON GLOBALE — per accesso da app.py (endpoint)
# ═════════════════════════════════════════════════════════════════════════════

_memoria_singleton: Optional[CapsulaMemoria] = None


def get_memoria() -> Optional[CapsulaMemoria]:
    """Restituisce il singleton se inizializzato."""
    return _memoria_singleton


def set_memoria(memoria: CapsulaMemoria) -> None:
    """Imposta il singleton (chiamato da V16 all'avvio)."""
    global _memoria_singleton
    _memoria_singleton = memoria


# ═════════════════════════════════════════════════════════════════════════════
# FINE FILE capsula_memoria.py
# ═════════════════════════════════════════════════════════════════════════════

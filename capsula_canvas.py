# -*- coding: utf-8 -*-
"""
capsula_canvas.py — L'OCCHIO del bot OVERTOP.

RICOSTRUITO 2giu2026 (Roberto + Claude).
Il CapsulaCanvas originale (la capsula che Roberto aveva concepito per mettere
l'occhio dentro al bot) era andato PERSO: il file capsula_canvas.py era stato
sovrascritto per errore con una copia del bot, e la classe CapsulaCanvas con
observe_entry/observe_exit non esisteva più in nessun file del progetto.
Risultato: ImportError silenzioso → self.canvas=None → canvas_snapshots muto
dal 31mag2026 (208k righe poi STOP).

Questa è la ricostruzione PULITA e MINIMALE. Fa una cosa sola: registra in
canvas_snapshots lo stato del campo PRIMA che il trade nasca (observe_entry) e
l'esito quando si chiude (observe_exit). Nient'altro. L'occhio nudo.

Firme allineate ESATTAMENTE a come il bot le chiama:
  - __init__(self, db_path)                              [riga bot 7102]
  - status(self) -> str                                  [riga bot 7103]
  - observe_entry(self, verbale, trade_id)               [righe bot 10379, 11128]
  - observe_exit(self, trade_id, outcome, pnl_netto,
                 durata_s, reason)                        [riga bot 13416]

Tabella canvas_snapshots (già esistente):
  id, ts, evento, trade_id, fingerprint, sensori_json, capsule_voto,
  sc_decision, outcome, pnl_netto, durata_s, reason, note
"""

import sqlite3
import json
import time


class CapsulaCanvas:
    """L'occhio osservativo. Registra ogni valutazione d'ingresso (il 'prima
    del seme') e l'esito a chiusura. Non decide, non blocca: solo osserva."""

    def __init__(self, db_path):
        self.db_path = db_path
        self._writes = 0
        self._errors = 0
        self._ensure_table()

    # ──────────────────────────────────────────────────────────────────
    def _conn(self):
        # timeout generoso: il bot scrive spesso, evito 'database is locked'
        return sqlite3.connect(self.db_path, timeout=10.0)

    def _ensure_table(self):
        """Crea la tabella se non esiste (idempotente). Non tocca i dati
        esistenti — se la tabella c'è già con le 208k righe, le lascia."""
        try:
            conn = self._conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS canvas_snapshots (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts           REAL    NOT NULL DEFAULT (strftime('%s','now')),
                    evento       TEXT    NOT NULL,
                    trade_id     TEXT,
                    fingerprint  TEXT,
                    sensori_json TEXT,
                    capsule_voto TEXT,
                    sc_decision  TEXT,
                    outcome      TEXT,
                    pnl_netto    REAL,
                    durata_s     REAL,
                    reason       TEXT,
                    note         TEXT
                )
            """)
            conn.commit()
            conn.close()
        except Exception:
            # Mai bloccare il motore per colpa dell'occhio.
            self._errors += 1

    # ──────────────────────────────────────────────────────────────────
    def status(self):
        """Stato sintetico per il log di avvio del bot."""
        return f"attivo (scritture={self._writes}, errori={self._errors})"

    # ──────────────────────────────────────────────────────────────────
    def observe_entry(self, verbale, trade_id=None):
        """Registra il fotogramma PRE-NASCITA: lo stato del campo nell'istante
        della valutazione d'ingresso. È il 'prima del seme'.
        `verbale` è il dict che il bot costruisce con i sensori del campo."""
        try:
            v = verbale if isinstance(verbale, dict) else {}

            # firma = (momentum × volatilità × trend × regime)  — se presenti
            fingerprint = "|".join(str(v.get(k, "?")) for k in
                                   ("momentum", "volatility", "trend", "regime"))

            # sensori del campo (il 'prima del seme'): tutto ciò che il bot
            # ha messo nel verbale alla nascita. Salvo l'intero stato utile.
            sensori = {
                "score":           v.get("score"),
                "soglia":          v.get("soglia"),
                "fp_wr":           v.get("fp_wr"),
                "drift":           v.get("drift"),
                "oi_carica":       v.get("oi_carica"),
                "oi_stato":        v.get("oi_stato"),
                "oi_carica_short": v.get("oi_carica_short"),
                "breath_fase":     v.get("breath_fase"),
                "breath_energia":  v.get("breath_energia"),
                "regime":          v.get("regime"),
                "momentum":        v.get("momentum"),
                "volatility":      v.get("volatility"),
                "trend":           v.get("trend"),
                "direction":       v.get("direction"),
                # PRIMA DEL SEME — vita dell'energia (2giu, intuizione Roberto):
                # range_pos = dove sta il prezzo nel range (alto=sul picco?)
                # drift_slope = l'energia accelera (>0 viva) o decade (<0 morta)?
                # seed_score = forza totale dell'impulso alla nascita
                "range_pos":       v.get("range_pos"),
                "drift_slope":     v.get("drift_slope"),
                "seed_score":      v.get("seed_score"),
            }

            capsule_voto = v.get("capsule_voto")
            if capsule_voto is not None and not isinstance(capsule_voto, str):
                capsule_voto = json.dumps(capsule_voto, default=str)

            conn = self._conn()
            conn.execute(
                """INSERT INTO canvas_snapshots
                   (ts, evento, trade_id, fingerprint, sensori_json,
                    capsule_voto, sc_decision)
                   VALUES (?, 'ENTRY_VALUTAZIONE', ?, ?, ?, ?, ?)""",
                (time.time(), trade_id, fingerprint,
                 json.dumps(sensori, default=str),
                 capsule_voto,
                 str(v.get("sc_decision")) if v.get("sc_decision") is not None else None)
            )
            conn.commit()
            conn.close()
            self._writes += 1
        except Exception:
            self._errors += 1

    # ──────────────────────────────────────────────────────────────────
    def observe_exit(self, trade_id=None, outcome=None, pnl_netto=None,
                     durata_s=None, reason=None, nascita=None):
        """Registra l'esito alla chiusura del trade. Riga EXIT separata,
        collegata all'entry tramite trade_id.
        `nascita` (3giu): i sensori del campo all'apertura del trade VERO,
        passati dallo shadow. Salvati in sensori_json così OGNI riga EXIT
        contiene nascita+esito insieme — analisi maschio/femmina senza
        aggancio temporale fragile (lo shadow esiste solo per i trade reali)."""
        try:
            sensori_nascita = None
            if isinstance(nascita, dict):
                sensori_nascita = json.dumps(nascita, default=str)
            conn = self._conn()
            conn.execute(
                """INSERT INTO canvas_snapshots
                   (ts, evento, trade_id, outcome, pnl_netto, durata_s, reason,
                    sensori_json)
                   VALUES (?, 'EXIT', ?, ?, ?, ?, ?, ?)""",
                (time.time(), trade_id, outcome,
                 float(pnl_netto) if pnl_netto is not None else None,
                 float(durata_s) if durata_s is not None else None,
                 str(reason) if reason is not None else None,
                 sensori_nascita)
            )
            conn.commit()
            conn.close()
            self._writes += 1
        except Exception:
            self._errors += 1

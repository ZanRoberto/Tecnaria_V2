# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════════
 CAPSULA REGIME-EDGE — Gioca dove i win pagano i loss, stai fuori dalla merda
═══════════════════════════════════════════════════════════════════════

AUTORE INTELLETTUALE: Roberto Zan (31 maggio 2026)
SCOPERTA FONDANTE (dai dati reali oracolo_snapshot, 31mag):
  Il bot NON perde perché sbaglia direzione. Perde perché GIOCA NEL CAMPO
  SBAGLIATO. I numeri reali del bot stesso:
    - SIDEWAYS (laterale): WR 1.5%-30%, PnL sempre NEGATIVO, 450+ trade REALI.
      → È LA MERDA. Qui il -697 è stato bruciato.
    - UP (trend in salita) + momentum MEDIO/FORTE: WR 55%-78%, PnL POSITIVO.
      → È IL CAMPO BUONO. Qui i win sono grossi e pagano i loss.

PRINCIPIO (Roberto): la capsula è una TENAGLIA, non una leva sola.
  - GANASCIA FRENO: sta FUORI dal SIDEWAYS (merda dimostrata su dati reali).
  - GANASCIA SPINTA: FAVORISCE l'UP/trend (campo buono).
  NON azzera i loss (impossibile: anche a WR 78% perdi 22 volte su 100).
  Rende i loss SOSTENIBILI: pochi loss piccoli pagati da win grossi.

ASIMMETRIA ONESTA DEI DATI (importante):
  - La prova "SIDEWAYS perde" è REALE (real=162,168,70,60... centinaia di trade).
  - La prova "UP vince" è SIMULATA (real=0: mai eseguito davvero, solo phantom).
  → Quindi la capsula nasce PIÙ FORTE SUL FRENO che sulla spinta. Blocca il
    SIDEWAYS con decisione; favorisce l'UP ma lascia che siano i primi trade
    REALI a confermarlo, prima di spingere forte.

LIVELLI (sul modello delle altre capsule di Roberto):
  L1 OCCHI    — registra ogni consulto (regime, direzione, verdetto). ON.
  L2 MEMORIA  — collega regime-entry → esito reale del trade. ON.
  L3 PROPONE  — OBSERVER: scrive il verdetto ma NON blocca/favorisce davvero.
                ON di default. Sicurezza: nasce senza toccare il bot.
  L4 DECIDE   — il bot RISPETTA il verdetto (blocca SIDEWAYS, favorisce UP).
                OFF di default. Armare via env solo quando L3 ha dimostrato
                di aver ragione sui trade reali.

KILL SWITCH: env CAPSULA_REGIME_EDGE_DEAD=true → la capsula è come inesistente.
ARMA L4:     env CAPSULA_REGIME_EDGE_L4=true   → passa da osservatrice a decisore.

SICUREZZA:
  - Fail-open totale: se crasha, ritorna sempre "lascia passare" (None).
  - In L3 non tocca NESSUNA decisione: solo osserva e registra.
  - Nessun numero di mercato inventato: le soglie sono i WR REALI del bot.

NOTE DEPLOY:
  - File accanto a OVERTOP_BASSANO_V16_PRODUCTION.py
  - Import in V16:  from capsula_regime_edge import CapsulaRegimeEdge
  - 3 hook minimi: init, consulta (all'entry), observe_outcome (alla chiusura)
  - Tabelle DB create automaticamente al primo avvio
═══════════════════════════════════════════════════════════════════════
"""

import os
import json
import time
import sqlite3
import logging
from typing import Optional, Dict, Any, Tuple

log = logging.getLogger("CAPSULA_REGIME_EDGE")


class CapsulaRegimeEdge:

    # ─────────────────────────────────────────────────────────────────
    # MAPPA EDGE — derivata RIGA PER RIGA da oracolo_snapshot (31mag2026).
    # Chiave = (momentum, trend). Valore = (wr_reale, pnl_reale, n_real).
    # NON sono numeri inventati: sono i risultati che il bot HA prodotto.
    # ─────────────────────────────────────────────────────────────────
    EDGE_MAP = {
        # --- CAMPO BUONO: trend UP (win grossi che pagano i loss) ---
        ("FORTE", "UP"):     {"wr": 0.78, "pnl": 0.49, "real": 0, "verdetto": "FAVORISCI"},
        ("MEDIO", "UP"):     {"wr": 0.65, "pnl": 0.16, "real": 0, "verdetto": "FAVORISCI"},
        # --- ZONA NEUTRA: trend DOWN / casi misti (lascia decidere il resto) ---
        ("DEBOLE", "UP"):    {"wr": 0.55, "pnl": -0.27, "real": 0, "verdetto": "NEUTRO"},
        ("FORTE", "DOWN"):   {"wr": 0.60, "pnl": -0.45, "real": 0, "verdetto": "NEUTRO"},
        ("MEDIO", "DOWN"):   {"wr": 0.45, "pnl": -1.45, "real": 0, "verdetto": "NEUTRO"},
        # --- LA MERDA: SIDEWAYS (perdite reali dimostrate, centinaia di trade) ---
        ("DEBOLE", "SIDEWAYS"): {"wr": 0.10, "pnl": -1.92, "real": 347, "verdetto": "BLOCCA"},
        ("MEDIO", "SIDEWAYS"):  {"wr": 0.23, "pnl": -1.48, "real": 130, "verdetto": "BLOCCA"},
        ("FORTE", "SIDEWAYS"):  {"wr": 0.45, "pnl": -1.00, "real": 13,  "verdetto": "NEUTRO"},
        # FORTE|SIDEWAYS resta NEUTRO: WR più alto e pochi dati → non lo condanno.
    }

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.dead = os.environ.get("CAPSULA_REGIME_EDGE_DEAD", "false").lower() == "true"
        self.l4_armata = os.environ.get("CAPSULA_REGIME_EDGE_L4", "false").lower() == "true"
        self._consulti = 0
        self._blocchi_proposti = 0
        self._init_db()
        log.info(f"[REGIME_EDGE] init — dead={self.dead} L4_decide={self.l4_armata}")

    def _init_db(self):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS regime_edge_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts          REAL DEFAULT (strftime('%s','now')),
                    regime      TEXT,
                    momentum    TEXT,
                    trend       TEXT,
                    direction   TEXT,
                    verdetto    TEXT,
                    wr_atteso   REAL,
                    pnl_atteso  REAL,
                    l4_attiva   INTEGER
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS regime_edge_esiti (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts          REAL DEFAULT (strftime('%s','now')),
                    momentum    TEXT,
                    trend       TEXT,
                    verdetto    TEXT,
                    pnl_reale   REAL,
                    esito       TEXT
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            log.debug(f"[REGIME_EDGE] init_db err {e}")

    # ─────────────────────────────────────────────────────────────────
    # HOOK 1 — consulta(): chiamato all'ENTRY del bot.
    #   Ritorna:
    #     None                → lascia decidere il resto del sistema (NEUTRO)
    #     ("BLOCCA", motivo)  → questo contesto è la merda (solo se L4 armata)
    #     ("FAVORISCI", mot.) → questo contesto è buono (solo se L4 armata)
    #   In L3 (default) ritorna SEMPRE None ma REGISTRA il verdetto: osserva.
    # ─────────────────────────────────────────────────────────────────
    def consulta(self, regime: str, momentum: str, trend: str,
                 direction: str) -> Optional[Tuple[str, str]]:
        if self.dead:
            return None
        try:
            self._consulti += 1
            info = self.EDGE_MAP.get((momentum, trend))
            if not info:
                return None  # contesto non mappato → non mi pronuncio
            verdetto = info["verdetto"]

            # registra SEMPRE (L1+L2 osservazione)
            self._registra(regime, momentum, trend, direction, verdetto, info)

            if verdetto == "BLOCCA":
                self._blocchi_proposti += 1
                motivo = (f"REGIME_EDGE_MERDA {momentum}|{trend}: "
                          f"WR reale {info['wr']:.0%} PnL {info['pnl']:+.2f} "
                          f"su {info['real']} trade reali")
                # In L3 osserva soltanto. In L4 blocca davvero.
                return ("BLOCCA", motivo) if self.l4_armata else None

            if verdetto == "FAVORISCI":
                motivo = (f"REGIME_EDGE_BUONO {momentum}|{trend}: "
                          f"WR atteso {info['wr']:.0%} (simulato, da confermare)")
                return ("FAVORISCI", motivo) if self.l4_armata else None

            return None
        except Exception as e:
            log.debug(f"[REGIME_EDGE] consulta err {e}")
            return None  # fail-open

    # ─────────────────────────────────────────────────────────────────
    # HOOK 2 — observe_outcome(): chiamato alla CHIUSURA del trade.
    #   Serve a verificare se i verdetti della capsula erano giusti:
    #   un FAVORISCI che poi vince = capsula aveva ragione.
    #   Questo è ciò che permetterà di armare L4 con dati reali.
    # ─────────────────────────────────────────────────────────────────
    def observe_outcome(self, momentum: str, trend: str, pnl_reale: float):
        if self.dead:
            return
        try:
            info = self.EDGE_MAP.get((momentum, trend))
            verdetto = info["verdetto"] if info else "NEUTRO"
            esito = "WIN" if pnl_reale > 0 else "LOSS"
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT INTO regime_edge_esiti (momentum, trend, verdetto, pnl_reale, esito)
                VALUES (?, ?, ?, ?, ?)
            """, (momentum, trend, verdetto, round(pnl_reale, 4), esito))
            conn.commit()
            conn.close()
        except Exception as e:
            log.debug(f"[REGIME_EDGE] observe_outcome err {e}")

    def _registra(self, regime, momentum, trend, direction, verdetto, info):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT INTO regime_edge_log
                    (regime, momentum, trend, direction, verdetto, wr_atteso, pnl_atteso, l4_attiva)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (regime, momentum, trend, direction, verdetto,
                  info["wr"], info["pnl"], 1 if self.l4_armata else 0))
            conn.commit()
            conn.close()
        except Exception as e:
            log.debug(f"[REGIME_EDGE] registra err {e}")

    def stato(self) -> Dict[str, Any]:
        return {
            "dead": self.dead,
            "l4_decide": self.l4_armata,
            "consulti": self._consulti,
            "blocchi_proposti": self._blocchi_proposti,
        }

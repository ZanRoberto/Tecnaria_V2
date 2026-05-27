# -*- coding: utf-8 -*-
"""
ONE-SHOT RICALCOLA STATI MATRIGNA — da lanciare in SSH Render
DOPO il deploy del fix in capsula_matrigna.py.

Cosa fa:
  1. Apre il DB
  2. Per ogni capsula figlia, applica la NUOVA logica _calcola_stato_capsula
  3. UPDATE su capsula_matrigna_figlie se stato/verdetto cambiano
  4. Log dei cambiamenti

NON tocca il bot in esecuzione. Modifica solo la tabella capsula_matrigna_figlie.
Al prossimo riavvio (o ogni 30 min se L5 attivo) il bot legge i nuovi stati.

USO:
  cd ~/project/src
  python3 one_shot_ricalcola_matrigna.py

SICUREZZA:
  - Backup automatico della tabella prima di scrivere
  - Modalità DRY-RUN attivabile con env DRY_RUN=true
"""
import os
import sys
import json
import time
import sqlite3
from collections import deque

DB_PATH = os.environ.get("DB_PATH", "/var/data/trading_data.db")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() in ("true", "1", "yes", "on")

# Import della classe — il KILL SWITCH evita che si attivino timer/init
os.environ["CAPSULA_MATRIGNA_DEAD"] = "true"
from capsula_matrigna import CapsulaMatrigna

cm = CapsulaMatrigna(db_path=DB_PATH)
# Bypassiamo il kill switch SOLO per il metodo di calcolo, che è puro
# (non scrive niente, riceve dict e ritorna tupla)

conn = sqlite3.connect(DB_PATH, timeout=10)
conn.row_factory = sqlite3.Row

# Backup pre-modifica
ts = int(time.time())
backup_table = f"capsula_matrigna_figlie_backup_{ts}"
conn.execute(f"CREATE TABLE IF NOT EXISTS {backup_table} AS SELECT * FROM capsula_matrigna_figlie")
conn.commit()
print(f"[BACKUP] tabella di backup creata: {backup_table}")

# Leggo tutte le capsule
rows = list(conn.execute("""
    SELECT firma_key, n_trade, n_win, n_loss, wr, stato, verdetto_corrente,
           budget_esperimenti, recent_wins_10
    FROM capsula_matrigna_figlie
"""))

print(f"\n{'='*110}")
print(f"RICALCOLO STATI — {len(rows)} capsule trovate{'  [DRY RUN]' if DRY_RUN else ''}")
print(f"{'='*110}")

cambiamenti = 0
for r in rows:
    firma = r["firma_key"]
    try:
        recent_list = json.loads(r["recent_wins_10"]) if r["recent_wins_10"] else []
    except Exception:
        recent_list = []
    cap = {
        'firma_key':         firma,
        'n_trade':           int(r["n_trade"] or 0),
        'n_win':             int(r["n_win"] or 0),
        'n_loss':            int(r["n_loss"] or 0),
        'wr':                float(r["wr"] or 0.0),
        'stato':             r["stato"] or 'OSSERVAZIONE_PREVENTIVA',
        'verdetto_corrente': r["verdetto_corrente"] or 'NEUTRO',
        'recent_wins_10':    deque(recent_list, maxlen=10),
    }
    stato_old = cap['stato']
    verd_old  = cap['verdetto_corrente']
    stato_new, verd_new = cm._calcola_stato_capsula(cap)

    if stato_new != stato_old or verd_new != verd_old:
        cambiamenti += 1
        marker = "[DRY] " if DRY_RUN else ""
        print(f"  {marker}⬅️  {firma}")
        print(f"        n={cap['n_trade']} w={cap['n_win']} wr={cap['wr']*100:.1f}%")
        print(f"        {stato_old}/{verd_old}  →  {stato_new}/{verd_new}")
        if not DRY_RUN:
            conn.execute(
                "UPDATE capsula_matrigna_figlie SET stato=?, verdetto_corrente=? WHERE firma_key=?",
                (stato_new, verd_new, firma)
            )

if not DRY_RUN:
    conn.commit()
    print(f"\n[COMMIT] {cambiamenti} capsule aggiornate.")
else:
    print(f"\n[DRY RUN] {cambiamenti} capsule SAREBBERO aggiornate (niente scritto).")

conn.close()
print(f"\n{'='*110}")
print(f"FATTO. Per rollback: DROP TABLE capsula_matrigna_figlie; ")
print(f"                    ALTER TABLE {backup_table} RENAME TO capsula_matrigna_figlie;")
print(f"{'='*110}")

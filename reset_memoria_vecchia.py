"""RESET MEMORIA OVERTOP V16 - v2"""
import sqlite3, os, sys, shutil

DB_PATH = os.environ.get("DB_PATH", "/var/data/trading_data.db")
print("="*70)
print("RESET MEMORIA OVERTOP V16 - v2 (TABELLE CORRETTE)")
print("="*70)
print(f"DB target: {DB_PATH}")
print()

if not os.path.exists(DB_PATH):
    print(f"DB non trovato: {DB_PATH}")
    sys.exit(1)

backup_v2 = DB_PATH + ".backup_pre_reset_v2"
shutil.copy2(DB_PATH, backup_v2)
print(f"OK Backup v2: {backup_v2}")
print()

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

print("STATO INIZIALE:")
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tabelle = [r[0] for r in cur.fetchall()]
for t in sorted(tabelle):
    try:
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        print(f"   - {t}: {cur.fetchone()[0]} righe")
    except Exception as e:
        print(f"   - {t}: ERR {e}")
print()

if 'capsule' in tabelle:
    cur.execute("PRAGMA table_info(capsule)")
    cols = [r[1] for r in cur.fetchall()]
    print(f"Schema 'capsule': {cols}")
    cur.execute("SELECT COUNT(*) FROM capsule")
    prima = cur.fetchone()[0]
    cur.execute("SELECT * FROM capsule LIMIT 3")
    for s in cur.fetchall():
        print(f"   Sample: {str(s)[:150]}")
    if 'livello' in cols:
        cur.execute("DELETE FROM capsule WHERE livello != 'STATIC'")
    elif 'id' in cols:
        cur.execute("DELETE FROM capsule WHERE id NOT LIKE 'STATIC_%'")
    else:
        print("ATT: schema sconosciuto")
    eliminati = cur.rowcount
    cur.execute("SELECT COUNT(*) FROM capsule")
    print(f"OK capsule: {prima} -> {cur.fetchone()[0]} ({eliminati} eliminate)")
    print()

if 'capsule_log' in tabelle:
    cur.execute("SELECT COUNT(*) FROM capsule_log")
    n = cur.fetchone()[0]
    if n > 0:
        cur.execute("DELETE FROM capsule_log")
        print(f"OK capsule_log: {n} eliminate")

if 'veritas_stats' in tabelle:
    cur.execute("SELECT COUNT(*) FROM veritas_stats")
    n = cur.fetchone()[0]
    if n > 0:
        cur.execute("DELETE FROM veritas_stats")
        print(f"OK veritas_stats: {n} eliminate")

if 'veritas_closed' in tabelle:
    cur.execute("SELECT COUNT(*) FROM veritas_closed")
    n = cur.fetchone()[0]
    if n > 0:
        cur.execute("DELETE FROM veritas_closed")
        print(f"OK veritas_closed: {n} eliminate")

if 'bot_state' in tabelle:
    cur.execute("SELECT key FROM bot_state")
    chiavi = [r[0] for r in cur.fetchall()]
    print(f"bot_state chiavi: {chiavi}")
    da_pulire = ['phantom_stats','veritas_stats','veritas_closed',
                 'm2_recent_trades','oracolo_fingerprints',
                 'matrimonio_stats','signal_tracker_stats',
                 'sc_pesi','phantom_sup_log','m2_log']
    for k in da_pulire:
        if k in chiavi:
            cur.execute("DELETE FROM bot_state WHERE key = ?", (k,))
            print(f"   OK bot_state: '{k}' eliminata")

conn.commit()

print()
print("STATO FINALE:")
for t in sorted(tabelle):
    try:
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        print(f"   - {t}: {cur.fetchone()[0]} righe")
    except:
        pass

conn.close()

print()
data_dir = os.path.dirname(DB_PATH)
for fname in ['capsule_attive.json','phantom_state.json','veritas_state.json']:
    full = os.path.join(data_dir, fname)
    if os.path.exists(full):
        shutil.copy2(full, full + ".backup_pre_reset_v2")
        with open(full,'w') as f:
            f.write('{}')
        print(f"OK {fname}: svuotato")

print()
print("="*70)
print("RESET v2 COMPLETATO")
print("="*70)
print()
print("PROSSIMI PASSI:")
print("  1. Render Dashboard -> Manual Deploy -> Clear build cache & deploy")
print("  2. Bot riparte pulito")
print()
print(f"BACKUP v2: {backup_v2}")
print(f"BACKUP v1: {DB_PATH}.backup_pre_reset")
print()
print("Per tornare indietro:")
print(f"  cp {backup_v2} {DB_PATH}")

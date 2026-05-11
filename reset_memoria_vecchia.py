"""
═══════════════════════════════════════════════════════════════════════════
🧹 RESET MEMORIA VECCHIA — OVERTOP V16
═══════════════════════════════════════════════════════════════════════════

OBIETTIVO:
  Cancellare le statistiche e le capsule LEARNED accumulate quando il bot
  era ROTTO (doppia-fee, regime instabile, phantom sup impazzito, timeout 3min,
  SHORT castrato). Mantenere il codice (intelligenza) e le capsule STATIC
  (memoria di lungo termine sul mercato BTC).

COSA CANCELLA:
  ✗ Tutte le capsule LEARNED (memoria del bot rotto)
  ✗ Tutte le capsule AUTO generate dall'Oracle (basate su dati vecchi)
  ✗ Tutte le SUPERCAPSULE (idem)
  ✗ Phantom stats (10.075 osservazioni inflazionate da bug doppia-fee)
  ✗ Veritas stats (500 segnali sotto vecchia logica)
  ✗ Oracolo Dinamico fingerprint (22 contesti con WR sbagliati)
  ✗ Bot state cumulativo (PnL, WR, n_trades)

COSA NON CANCELLA:
  ✓ Tutto il codice (nessuna modifica al .py)
  ✓ Le 10 capsule STATIC (memoria sul mercato BTC reale)
  ✓ Le 3 capsule STATIC SHORT nuove (12mag)
  ✓ La struttura del DB

USO:
  Carica questo file su Render via shell:
    python reset_memoria_vecchia.py
  
  Poi RIAVVIA il bot da Render dashboard (Manual Deploy / Restart).
  Bot riparte con codice nuovo + zero pregiudizi statistici.
═══════════════════════════════════════════════════════════════════════════
"""
import sqlite3
import os
import sys

DB_PATH = os.environ.get("DB_PATH", "/var/data/trading_data.db")

print("="*70)
print("🧹 RESET MEMORIA OVERTOP V16")
print("="*70)
print(f"DB target: {DB_PATH}")
print()

if not os.path.exists(DB_PATH):
    print(f"❌ DB non trovato: {DB_PATH}")
    print("   Controlla DB_PATH env variable")
    sys.exit(1)

# Backup prima
backup_path = DB_PATH + ".backup_pre_reset"
if not os.path.exists(backup_path):
    import shutil
    shutil.copy2(DB_PATH, backup_path)
    print(f"✅ Backup creato: {backup_path}")
else:
    print(f"⚠️  Backup gia esistente: {backup_path} (NON sovrascritto)")
print()

# Apertura DB
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# Lista tabelle presenti
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tabelle = [r[0] for r in cur.fetchall()]
print(f"📋 Tabelle trovate: {len(tabelle)}")
for t in tabelle:
    cur.execute(f"SELECT COUNT(*) FROM {t}")
    n = cur.fetchone()[0]
    print(f"   • {t}: {n} righe")
print()

# ═══════════════════════════════════════════════════════════
# RESET 1: capsule_permanenti — mantieni solo STATIC
# ═══════════════════════════════════════════════════════════
if 'capsule_permanenti' in tabelle:
    cur.execute("SELECT COUNT(*) FROM capsule_permanenti")
    prima = cur.fetchone()[0]
    # Mantieni solo le STATIC (livello scolpito sul mercato reale)
    cur.execute("""
        DELETE FROM capsule_permanenti 
        WHERE id NOT LIKE 'STATIC_%'
    """)
    eliminati = cur.rowcount
    cur.execute("SELECT COUNT(*) FROM capsule_permanenti")
    dopo = cur.fetchone()[0]
    print(f"✅ capsule_permanenti: {prima} → {dopo} (eliminati {eliminati} LEARNED/AUTO)")

# ═══════════════════════════════════════════════════════════
# RESET 2: bot_state — pulisci stato cumulativo
# ═══════════════════════════════════════════════════════════
if 'bot_state' in tabelle:
    keys_da_resettare = [
        'phantom_stats',
        'veritas_stats',
        'veritas_closed',
        'm2_recent_trades',
        'oracolo_fingerprints',
        'matrimonio_stats',
        'signal_tracker_stats',
        'sc_pesi',  # pesi adattativi imparati su dati vecchi
        # NON resettiamo: capital (preserva 10000), total_trades (info storica)
    ]
    for k in keys_da_resettare:
        cur.execute("DELETE FROM bot_state WHERE key = ?", (k,))
        if cur.rowcount > 0:
            print(f"✅ bot_state: eliminata chiave '{k}'")

# ═══════════════════════════════════════════════════════════
# RESET 3: telemetry — pulisci log eventi vecchi (opzionale)
# ═══════════════════════════════════════════════════════════
if 'telemetry' in tabelle:
    cur.execute("SELECT COUNT(*) FROM telemetry")
    n = cur.fetchone()[0]
    if n > 0:
        cur.execute("DELETE FROM telemetry")
        print(f"✅ telemetry: {n} righe eliminate (log storici puliti)")

conn.commit()
conn.close()

# ═══════════════════════════════════════════════════════════
# RESET 4: file JSON
# ═══════════════════════════════════════════════════════════
data_dir = os.path.dirname(DB_PATH)
files_da_resettare = [
    'capsule_attive.json',     # capsule runtime (LEARNED/AUTO)
    'phantom_state.json',      # se esiste
    'veritas_state.json',      # se esiste
]
for fname in files_da_resettare:
    full = os.path.join(data_dir, fname)
    if os.path.exists(full):
        # Backup e svuota
        backup = full + ".backup_pre_reset"
        if not os.path.exists(backup):
            import shutil
            shutil.copy2(full, backup)
        with open(full, 'w') as f:
            f.write('{}')
        print(f"✅ {fname}: svuotato (backup → {os.path.basename(backup)})")

print()
print("="*70)
print("🦊 RESET COMPLETATO")
print("="*70)
print()
print("PROSSIMI PASSI:")
print("  1. Vai su Render Dashboard")
print("  2. Clicca 'Manual Deploy' → 'Clear build cache & deploy'")
print("     (oppure 'Restart' se preferisci)")
print("  3. Il bot riparte SENZA memoria statistica vecchia")
print("  4. Mantiene le STATIC capsule (10 totali) come muraglia")
print("  5. Comincia ad accumulare NUOVI dati con la logica fixata")
print()
print(f"BACKUP DISPONIBILE: {backup_path}")
print("(se qualcosa va storto, lo ripristini con:")
print(f"  cp {backup_path} {DB_PATH})")
print()

"""
RESET CHIRURGICO CAPSULE — OVERTOP BASSANO
Cosa fa:
  1. Svuota capsule_permanenti dal DB (RA_ e SuperCapsule accumulate)
  2. Mantiene STATIC tossiche (dati reali BTC — non si toccano)
  3. Svuota capsule_attive.json dalle RA_ e SC_ accumulate
  4. Mantiene solo le STATIC_ hardcoded nel json
  5. Resetta consecutive_losses nel narratore per sbloccare il loop difensivo
  6. NON tocca: oracolo brain, signal tracker, matrimoni, capitale

Eseguire su Render via console o come script one-shot.
"""
import sqlite3
import json
import os

DB_PATH = os.environ.get("DB_PATH", "/home/app/data/trading_data.db")
CAPSULE_FILE = "capsule_attive.json"

print("="*60)
print("RESET CHIRURGICO CAPSULE — OVERTOP BASSANO")
print("="*60)

# ── 1. DB: capsule_permanenti ─────────────────────────────────────────────
print("\n[1] Leggo capsule_permanenti dal DB...")
try:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT id, azione, forza FROM capsule_permanenti").fetchall()
    print(f"    Trovate {len(rows)} capsule permanenti:")
    
    da_tenere = []
    da_eliminare = []
    
    for r in rows:
        cap_id, azione, forza = r
        # Tieni solo BLOCCA_DEBOLE_ALTA_SIDEWAYS — unica con dati reali confermati
        if cap_id == 'RA_BLOCCA_DEBOLE_ALTA_SIDEWAYS':
            da_tenere.append(cap_id)
            print(f"    ✅ TENGO: {cap_id} ({azione} forza={forza})")
        else:
            da_eliminare.append(cap_id)
            print(f"    🗑️  ELIMINO: {cap_id} ({azione})")
    
    # Elimina tutto tranne quella tenuta
    for cap_id in da_eliminare:
        conn.execute("DELETE FROM capsule_permanenti WHERE id=?", (cap_id,))
    
    conn.commit()
    conn.close()
    print(f"\n    Eliminate {len(da_eliminare)} capsule permanenti")
    print(f"    Mantenute {len(da_tenere)} capsule permanenti")
    
except Exception as e:
    print(f"    ❌ Errore DB: {e}")

# ── 2. capsule_attive.json ────────────────────────────────────────────────
print(f"\n[2] Leggo {CAPSULE_FILE}...")
try:
    if os.path.exists(CAPSULE_FILE):
        with open(CAPSULE_FILE) as f:
            caps = json.load(f)
        
        print(f"    Trovate {len(caps)} capsule nel file")
        
        tenute = []
        eliminate_n = 0
        
        for c in caps:
            cid = c.get('capsule_id') or c.get('id', '')
            livello = c.get('livello', '')
            tipo = c.get('tipo', '')
            
            # Tieni solo le STATIC_ — sono i veti basati su dati BTC reali
            if cid.startswith('STATIC_'):
                tenute.append(c)
                print(f"    ✅ TENGO: {cid}")
            else:
                eliminate_n += 1
        
        with open(CAPSULE_FILE, 'w') as f:
            json.dump(tenute, f, indent=2)
        
        print(f"\n    Eliminate {eliminate_n} capsule dal file")
        print(f"    Mantenute {len(tenute)} capsule STATIC nel file")
    else:
        print(f"    File non trovato — creo file vuoto con solo STATIC")
        with open(CAPSULE_FILE, 'w') as f:
            json.dump([], f)

except Exception as e:
    print(f"    ❌ Errore file: {e}")

# ── 3. Reset consecutive_losses nel bot_state ─────────────────────────────
print("\n[3] Reset stato narratore nel DB...")
try:
    conn = sqlite3.connect(DB_PATH)
    
    # Leggi stato corrente
    rows = dict(conn.execute("SELECT key, value FROM bot_state").fetchall())
    print(f"    Chiavi trovate in bot_state: {list(rows.keys())}")
    
    # Reset consecutive_losses se esiste
    if 'consecutive_losses' in rows:
        old = rows['consecutive_losses']
        conn.execute("UPDATE bot_state SET value='0' WHERE key='consecutive_losses'")
        print(f"    consecutive_losses: {old} → 0")
    
    # Reset loss_streak
    if 'loss_streak' in rows:
        old = rows['loss_streak']
        conn.execute("UPDATE bot_state SET value='0' WHERE key='loss_streak'")
        print(f"    loss_streak: {old} → 0")
    
    conn.commit()
    conn.close()
    print("    ✅ Bot state aggiornato")

except Exception as e:
    print(f"    ❌ Errore bot_state: {e}")

# ── 4. Verifica finale ────────────────────────────────────────────────────
print("\n[4] Verifica finale...")
try:
    conn = sqlite3.connect(DB_PATH)
    n_perm = conn.execute("SELECT COUNT(*) FROM capsule_permanenti").fetchone()[0]
    conn.close()
    print(f"    capsule_permanenti nel DB: {n_perm}")
    
    if os.path.exists(CAPSULE_FILE):
        with open(CAPSULE_FILE) as f:
            caps = json.load(f)
        print(f"    capsule nel file JSON: {len(caps)}")
        for c in caps:
            print(f"      - {c.get('capsule_id') or c.get('id')}")

except Exception as e:
    print(f"    ❌ Errore verifica: {e}")

print("\n" + "="*60)
print("RESET COMPLETATO")
print("="*60)
print("""
Cosa succede al prossimo restart del bot:
  - 0 capsule RA_ / SC_ difensive caricate
  - Solo STATIC_ tossiche attive (dati reali BTC)
  - RA_BLOCCA_DEBOLE_ALTA_SIDEWAYS mantenuta (8 loss reali confermati)
  - Il bot ricomincia con stato neutro
  - Il Ragionatore rigenera capsule solo da nuovi dati reali

IMPORTANTE: fare restart del bot su Render dopo questo script.
""")

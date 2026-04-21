"""
OVERTOP ORACLE AUTO — Background Worker
Gira ogni ora, analizza il bot, inietta capsule SAFE automaticamente.
Due modalità: AUTO (autonomo) / MANUAL (aspetta domanda umana)
"""
import os, time, json, sqlite3, logging, threading, urllib.request
from datetime import datetime

log = logging.getLogger("oracle_auto")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [ORACLE_AUTO] %(message)s")

# ── CONFIG ────────────────────────────────────────────────────────────
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DB_PATH          = os.environ.get("DB_PATH", "/home/app/data/trading_data.db")
ORACLE_DB_PATH   = os.environ.get("ORACLE_DB_PATH", "/home/app/data/oracle_auto.db")
BOT_BASE_URL     = os.environ.get("BOT_BASE_URL", "http://localhost:5000")
AUTO_INTERVAL    = int(os.environ.get("ORACLE_AUTO_INTERVAL", "3600"))  # secondi tra run

# ── DB ORACLE ─────────────────────────────────────────────────────────
def init_oracle_db():
    conn = sqlite3.connect(ORACLE_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oracle_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            mode TEXT,
            domanda TEXT,
            capsule_emesse INTEGER,
            capsule_iniettate INTEGER,
            capsule_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oracle_capsule_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            nome TEXT,
            sicurezza TEXT,
            urgenza TEXT,
            azione TEXT,
            esito TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oracle_mode (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.execute("INSERT OR IGNORE INTO oracle_mode VALUES ('mode', 'MANUAL')")
    conn.commit()
    conn.close()

def get_mode():
    try:
        conn = sqlite3.connect(ORACLE_DB_PATH)
        row = conn.execute("SELECT value FROM oracle_mode WHERE key='mode'").fetchone()
        conn.close()
        return row[0] if row else 'MANUAL'
    except:
        return 'MANUAL'

def set_mode(mode):
    conn = sqlite3.connect(ORACLE_DB_PATH)
    conn.execute("INSERT OR REPLACE INTO oracle_mode VALUES ('mode', ?)", (mode,))
    conn.commit()
    conn.close()

def log_run(mode, domanda, capsule_emesse, capsule_iniettate, capsule_json):
    try:
        conn = sqlite3.connect(ORACLE_DB_PATH)
        conn.execute(
            "INSERT INTO oracle_runs (ts,mode,domanda,capsule_emesse,capsule_iniettate,capsule_json) VALUES (?,?,?,?,?,?)",
            (datetime.utcnow().isoformat(), mode, domanda[:500], capsule_emesse, capsule_iniettate, json.dumps(capsule_json, ensure_ascii=False))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"log_run error: {e}")

def log_capsule(nome, sicurezza, urgenza, azione, esito):
    try:
        conn = sqlite3.connect(ORACLE_DB_PATH)
        conn.execute(
            "INSERT INTO oracle_capsule_log (ts,nome,sicurezza,urgenza,azione,esito) VALUES (?,?,?,?,?,?)",
            (datetime.utcnow().isoformat(), nome, sicurezza, urgenza, azione[:200], esito)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"log_capsule error: {e}")

# ── LEGGI STATUS BOT ──────────────────────────────────────────────────
def get_bot_status():
    try:
        req = urllib.request.Request(f"{BOT_BASE_URL}/oracle/status")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log.warning(f"get_bot_status error: {e}")
        return {}

# ── GENERA DOMANDA AUTONOMA ───────────────────────────────────────────
def genera_domanda_autonoma(status):
    """
    Analizza lo status e genera la domanda più utile per il sistema.
    Logica: prioritizza le anomalie più critiche.
    """
    problemi = []

    # Nessun trade
    if status.get('total_trades', 0) == 0:
        problemi.append("Il bot ha 0 trade eseguiti — analizza tutti i blocchi attivi e genera capsule correttive")

    # Loss streak alta
    streak = status.get('loss_streak', 0)
    if streak >= 3:
        problemi.append(f"Loss streak di {streak} trade consecutivi — analizza causa e genera capsule difensiva")

    # PnL negativo
    pnl = status.get('pnl', 0)
    if pnl < -20:
        problemi.append(f"PnL negativo ${pnl:.2f} — analizza i trade persi e genera capsule correttive")

    # Regime RANGING con soglia alta
    regime = status.get('regime', '')
    soglia = status.get('soglia_base', 55)
    if regime == 'RANGING' and isinstance(soglia, (int, float)) and soglia > 58:
        problemi.append(f"Regime RANGING con soglia {soglia} alta — valuta se abbassare per permettere entry selettive")

    # OI FUOCO ma pochi trade
    oi_stato = status.get('oi_stato', '')
    oi_carica = status.get('oi_carica', 0)
    trades = status.get('total_trades', 0)
    if oi_stato == 'FUOCO' and oi_carica > 0.7 and trades < 5:
        problemi.append(f"OI FUOCO con carica {oi_carica:.2f} ma soli {trades} trade — il sistema sta perdendo opportunità")

    # Bot non running
    if not status.get('running', False):
        problemi.append("Il bot non risulta in esecuzione — verifica lo stato e genera capsule di diagnosi")

    # Default: health check generale
    if not problemi:
        problemi.append("Analisi di routine: verifica stato generale del sistema, performance e opportunità di ottimizzazione")

    # Prendi il problema più critico
    return problemi[0]

# ── CHIAMA DEEPSEEK ───────────────────────────────────────────────────
def chiama_deepseek(system_prompt, user_msg, max_tokens=3000):
    if not DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY non configurata")
    payload = json.dumps({
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg}
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3
    }).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {DEEPSEEK_API_KEY}"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
    return result["choices"][0]["message"]["content"]

# ── ESTRAI DOMANDE ────────────────────────────────────────────────────
def estrai_domande(text):
    lines = text.split('\n')
    domande = []
    ins = False
    for l in lines:
        if 'DOMANDE AL SUPERRISPONDITORE' in l:
            ins = True
            continue
        if ins:
            import re
            m = re.match(r'^\d+\.\s+(.+)', l)
            if m:
                domande.append(m.group(1).strip())
    return domande

# ── PARSE CAPSULE ─────────────────────────────────────────────────────
def parse_capsule(text):
    s = text.find('[')
    e = text.rfind(']')
    if s == -1 or e == -1:
        return []
    try:
        return json.loads(text[s:e+1])
    except:
        try:
            clean = text[s:e+1].replace('\n', ' ').replace('\r', '')
            return json.loads(clean)
        except:
            return []

# ── INIETTA CAPSULE NEL BOT ───────────────────────────────────────────
def inietta_capsule_bot(cap):
    """
    Inietta una capsule nel bot tramite bot_state DB.
    Usa il canale bridge commands già esistente.
    """
    try:
        nome = cap.get('nome', 'UNKNOWN')
        soluzione = cap.get('soluzione', '')
        urgenza = cap.get('urgenza', 'MEDIA')

        # Scrivi nel DB bot_state come oracle_capsule
        conn = sqlite3.connect(DB_PATH)
        oracle_caps = []
        try:
            row = conn.execute("SELECT value FROM bot_state WHERE key='oracle_auto_capsule'").fetchone()
            if row:
                oracle_caps = json.loads(row[0])
        except:
            pass

        oracle_caps.append({
            'ts': datetime.utcnow().isoformat(),
            'nome': nome,
            'urgenza': urgenza,
            'soluzione': soluzione[:300],
            'effetto_atteso': cap.get('effetto_atteso', '')[:200],
            'codice': cap.get('codice', ''),
        })
        # Tieni solo ultime 20
        oracle_caps = oracle_caps[-20:]

        conn.execute(
            "INSERT OR REPLACE INTO bot_state VALUES ('oracle_auto_capsule', ?)",
            (json.dumps(oracle_caps, ensure_ascii=False),)
        )
        conn.commit()
        conn.close()

        log.info(f"✅ Capsule iniettata: {nome} [{urgenza}]")
        return True
    except Exception as e:
        log.error(f"inietta_capsule_bot error: {e}")
        return False

# ── RUN ORACLE ────────────────────────────────────────────────────────
def run_oracle(domanda_manuale=None):
    """
    Esegue un ciclo Oracle completo.
    domanda_manuale: se None → modalità AUTO (genera domanda da status)
    """
    log.info(f"=== ORACLE RUN {'AUTO' if not domanda_manuale else 'MANUAL'} ===")

    # 1. Leggi status bot
    status = get_bot_status()
    mode = 'MANUAL' if domanda_manuale else 'AUTO'

    # 2. Costruisci contesto live
    live_ctx = (
        f"\n\n=== STATO LIVE BOT ===\n"
        f"Regime: {status.get('regime','N/A')} ({status.get('regime_conf',0)}%)\n"
        f"Assetto: {status.get('state','N/A')}\n"
        f"OI: {status.get('oi_stato','N/A')} carica={status.get('oi_carica',0):.3f}\n"
        f"SOGLIA_BASE: {status.get('soglia_base','N/A')}\n"
        f"RSI: {status.get('rsi','N/A')} MACD: {status.get('macd_hist','N/A')}\n"
        f"Trade: {status.get('total_trades',0)} | W:{status.get('wins',0)} L:{status.get('losses',0)}\n"
        f"PnL: ${status.get('pnl',0):.2f} | Capitale: ${status.get('capital',10000):.2f}\n"
        f"Loss streak: {status.get('loss_streak',0)}\n"
        f"Running: {status.get('running',False)}\n"
        f"=== FINE STATO LIVE ===\n"
    )

    # 3. Genera o usa domanda
    if domanda_manuale:
        domanda = domanda_manuale
    else:
        domanda = genera_domanda_autonoma(status)
        log.info(f"Domanda autonoma: {domanda}")

    # 4. Livello 1 — Risponditore
    try:
        from app import _ORACLE_PROMPT_L1, _ORACLE_PROMPT_L2
    except:
        log.error("Impossibile importare prompt da app.py")
        return

    try:
        r1 = chiama_deepseek(_ORACLE_PROMPT_L1, domanda + live_ctx)
        log.info(f"L1 risposta: {len(r1)} chars")
    except Exception as e:
        log.error(f"L1 error: {e}")
        return

    domande = estrai_domande(r1)
    if not domande:
        log.warning("Nessuna domanda estratta dal L1")
        return

    log.info(f"Domande per L2: {len(domande)}")

    # 5. Livello 2 — Superrisponditore
    l2_input = (
        f"DOMANDE DAL RISPONDITORE:\n" +
        "\n".join([f"{i+1}. {d}" for i,d in enumerate(domande)]) +
        f"\n\nCONTESTO: {domanda}" + live_ctx
    )

    try:
        r2 = chiama_deepseek(_ORACLE_PROMPT_L2, l2_input)
        log.info(f"L2 risposta: {len(r2)} chars")
    except Exception as e:
        log.error(f"L2 error: {e}")
        return

    # 6. Parse capsule
    capsule = parse_capsule(r2)
    log.info(f"Capsule emesse: {len(capsule)}")

    # 7. Filtra e inietta solo SAFE
    iniettate = []
    for cap in capsule:
        sicurezza = cap.get('sicurezza', 'RISCHIO').upper()
        nome = cap.get('nome', 'UNKNOWN')
        urgenza = cap.get('urgenza', 'MEDIA')

        if sicurezza == 'SAFE':
            ok = inietta_capsule_bot(cap)
            esito = 'INIETTATA' if ok else 'ERRORE'
            iniettate.append(cap)
        else:
            esito = f'SKIP_{sicurezza}'
            log.info(f"Skip capsule {nome}: {sicurezza}")

        log_capsule(nome, sicurezza, urgenza, cap.get('soluzione','')[:100], esito)

    # 8. Log run
    log_run(mode, domanda, len(capsule), len(iniettate), capsule)
    log.info(f"=== RUN COMPLETATO: {len(capsule)} emesse, {len(iniettate)} iniettate ===")
    return {'emesse': len(capsule), 'iniettate': len(iniettate), 'capsule': capsule}

# ── LOOP AUTONOMO ─────────────────────────────────────────────────────
def auto_loop():
    """Loop che gira in background — attivo solo in modalità AUTO."""
    log.info(f"Oracle Auto Loop avviato — intervallo: {AUTO_INTERVAL}s")
    while True:
        try:
            mode = get_mode()
            if mode == 'AUTO':
                log.info("Modalità AUTO — avvio run autonomo")
                run_oracle()
            else:
                log.info("Modalità MANUAL — skip run autonomo")
        except Exception as e:
            log.error(f"auto_loop error: {e}")
        time.sleep(AUTO_INTERVAL)

# ── ENTRY POINT ───────────────────────────────────────────────────────
def start_background():
    """Avvia il loop in background thread."""
    init_oracle_db()
    t = threading.Thread(target=auto_loop, daemon=True)
    t.start()
    log.info("Oracle Auto background thread avviato")
    return t

if __name__ == '__main__':
    init_oracle_db()
    auto_loop()

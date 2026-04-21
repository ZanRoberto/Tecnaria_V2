"""
OVERTOP ORACLE AUTO — Event-Driven Worker
Si sveglia ogni 30s, controlla oracle_trigger nel heartbeat.
Se trova un'anomalia → analizza → inietta capsule SAFE automaticamente.
Due modalità: AUTO (autonomo) / MANUAL (aspetta domanda umana)
"""
import os, time, json, sqlite3, logging, threading, urllib.request
from datetime import datetime

log = logging.getLogger("oracle_auto")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [ORACLE_AUTO] %(message)s")

# ── CONFIG ────────────────────────────────────────────────────────────
DEEPSEEK_API_KEY  = os.environ.get("DEEPSEEK_API_KEY", "")
DB_PATH           = os.environ.get("DB_PATH", "/home/app/data/trading_data.db")
ORACLE_DB_PATH    = os.environ.get("ORACLE_DB_PATH", "/home/app/data/oracle_auto.db")
BOT_BASE_URL      = os.environ.get("BOT_BASE_URL", "http://localhost:5000")
CHECK_INTERVAL    = 30    # secondi tra check trigger
COOLDOWN_AFTER    = 300   # 5 minuti cooldown dopo un run per non spammare DeepSeek

# ── DB ORACLE ─────────────────────────────────────────────────────────
def init_oracle_db():
    conn = sqlite3.connect(ORACLE_DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS oracle_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, mode TEXT,
        trigger_nome TEXT, domanda TEXT,
        capsule_emesse INTEGER, capsule_iniettate INTEGER, capsule_json TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS oracle_capsule_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, nome TEXT,
        sicurezza TEXT, urgenza TEXT, soluzione TEXT, esito TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS oracle_mode (key TEXT PRIMARY KEY, value TEXT)""")
    conn.execute("INSERT OR IGNORE INTO oracle_mode VALUES ('mode', 'MANUAL')")
    conn.execute("INSERT OR IGNORE INTO oracle_mode VALUES ('last_run_ts', '0')")
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

def get_last_run_ts():
    try:
        conn = sqlite3.connect(ORACLE_DB_PATH)
        row = conn.execute("SELECT value FROM oracle_mode WHERE key='last_run_ts'").fetchone()
        conn.close()
        return float(row[0]) if row else 0
    except:
        return 0

def set_last_run_ts(ts):
    try:
        conn = sqlite3.connect(ORACLE_DB_PATH)
        conn.execute("INSERT OR REPLACE INTO oracle_mode VALUES ('last_run_ts', ?)", (str(ts),))
        conn.commit()
        conn.close()
    except:
        pass

def log_run(trigger_nome, domanda, capsule_emesse, capsule_iniettate, capsule_json):
    try:
        conn = sqlite3.connect(ORACLE_DB_PATH)
        conn.execute(
            "INSERT INTO oracle_runs (ts,mode,trigger_nome,domanda,capsule_emesse,capsule_iniettate,capsule_json) VALUES (?,?,?,?,?,?,?)",
            (datetime.utcnow().isoformat(), get_mode(), trigger_nome, domanda[:500],
             capsule_emesse, capsule_iniettate, json.dumps(capsule_json, ensure_ascii=False))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"log_run error: {e}")

def log_capsule(nome, sicurezza, urgenza, soluzione, esito):
    try:
        conn = sqlite3.connect(ORACLE_DB_PATH)
        conn.execute(
            "INSERT INTO oracle_capsule_log (ts,nome,sicurezza,urgenza,soluzione,esito) VALUES (?,?,?,?,?,?)",
            (datetime.utcnow().isoformat(), nome, sicurezza, urgenza, soluzione[:200], esito)
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

def get_oracle_trigger():
    """Legge oracle_trigger dal DB bot_state."""
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT value FROM bot_state WHERE key='oracle_trigger'").fetchone()
        conn.close()
        return row[0] if row and row[0] else ''
    except:
        return ''

def clear_oracle_trigger():
    """Cancella il trigger dopo averlo processato."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('oracle_trigger', '')")
        conn.commit()
        conn.close()
    except:
        pass

# ── TRIGGER → DOMANDA ─────────────────────────────────────────────────
def trigger_to_domanda(trigger, status):
    """Converte il codice trigger nella domanda più precisa per L1."""
    domande = {
        "LOSS_STREAK": lambda: f"Il bot ha una loss streak di {status.get('loss_streak',0)} trade consecutivi. Analizza la causa strutturale e genera capsule difensive per proteggere il capitale.",
        "OI_FUOCO_ZERO_TRADE": lambda: f"OI_LONG è in stato FUOCO con carica {status.get('oi_carica',0):.2f} ma il bot non sta entrando. Analizza cosa blocca l'entry e genera capsule per sbloccare il sistema.",
        "PHANTOM_IRRIGIDISCE": lambda: "Il Phantom Supervisor sta irrigidendo la soglia in modo progressivo. Analizza il loop di irrigidimento e genera capsule per stabilizzare la soglia.",
        "PNL_DRAWDOWN": lambda: f"Il PnL della sessione è negativo: ${status.get('pnl',0):.2f}. Analizza i trade persi e genera capsule per correggere il comportamento.",
        "EXPLOSIVE_ZERO_TRADE": lambda: "Il regime è EXPLOSIVE ma il bot non sta entrando. È una finestra di opportunità che si sta perdendo. Analizza i blocchi attivi e genera capsule per permettere entry selettive.",
        "DIFENSIVO_BLOCCATO": lambda: f"Il bot è in stato DIFENSIVO da troppo tempo senza trade. Analizza se il blocco difensivo è giustificato o se va allentato.",
    }
    # Trova la chiave che matcha
    for key, fn in domande.items():
        if trigger.startswith(key):
            return fn()
    # Default
    return f"Anomalia rilevata: {trigger}. Analizza la situazione e genera capsule correttive appropriate."

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
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {DEEPSEEK_API_KEY}"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
    return result["choices"][0]["message"]["content"]

# ── ESTRAI DOMANDE ────────────────────────────────────────────────────
def estrai_domande(text):
    import re
    lines = text.split('\n')
    domande, ins = [], False
    for l in lines:
        if 'DOMANDE AL SUPERRISPONDITORE' in l:
            ins = True; continue
        if ins:
            m = re.match(r'^\d+\.\s+(.+)', l)
            if m: domande.append(m.group(1).strip())
    return domande

# ── PARSE CAPSULE ─────────────────────────────────────────────────────
def parse_capsule(text):
    s, e = text.find('['), text.rfind(']')
    if s == -1 or e == -1: return []
    try:
        return json.loads(text[s:e+1])
    except:
        try:
            return json.loads(text[s:e+1].replace('\n',' ').replace('\r',''))
        except:
            return []

# ── INIETTA CAPSULE NEL BOT ───────────────────────────────────────────
def inietta_capsule_bot(cap):
    try:
        conn = sqlite3.connect(DB_PATH)
        oracle_caps = []
        try:
            row = conn.execute("SELECT value FROM bot_state WHERE key='oracle_auto_capsule'").fetchone()
            if row: oracle_caps = json.loads(row[0])
        except:
            pass
        oracle_caps.append({
            'ts':             datetime.utcnow().isoformat(),
            'nome':           cap.get('nome', 'UNKNOWN'),
            'urgenza':        cap.get('urgenza', 'MEDIA'),
            'soluzione':      cap.get('soluzione', '')[:300],
            'effetto_atteso': cap.get('effetto_atteso', '')[:200],
            'codice':         cap.get('codice', ''),
        })
        oracle_caps = oracle_caps[-20:]
        conn.execute(
            "INSERT OR REPLACE INTO bot_state VALUES ('oracle_auto_capsule', ?)",
            (json.dumps(oracle_caps, ensure_ascii=False),)
        )
        conn.commit()
        conn.close()
        log.info(f"✅ Iniettata: {cap.get('nome')} [{cap.get('urgenza')}]")
        return True
    except Exception as e:
        log.error(f"inietta error: {e}")
        return False

# ── RUN ORACLE ────────────────────────────────────────────────────────
def run_oracle(trigger_nome='', domanda_manuale=None):
    log.info(f"=== ORACLE RUN trigger={trigger_nome or 'MANUALE'} ===")

    status = get_bot_status()

    # Contesto live
    live_ctx = (
        f"\n\n=== STATO LIVE BOT ===\n"
        f"Regime: {status.get('regime','N/A')} ({status.get('regime_conf',0)}%)\n"
        f"Assetto: {status.get('state','N/A')}\n"
        f"OI: {status.get('oi_stato','N/A')} carica={status.get('oi_carica',0):.3f}\n"
        f"SOGLIA_BASE: {status.get('soglia_base','N/A')}\n"
        f"RSI: {status.get('rsi','N/A')} MACD: {status.get('macd_hist','N/A')}\n"
        f"Trade: {status.get('total_trades',0)} | W:{status.get('wins',0)} L:{status.get('losses',0)}\n"
        f"PnL: ${status.get('pnl',0):.2f} | Loss streak: {status.get('loss_streak',0)}\n"
        f"Anomalia rilevata: {trigger_nome}\n"
        f"=== FINE STATO LIVE ===\n"
    )

    domanda = domanda_manuale or trigger_to_domanda(trigger_nome, status)
    log.info(f"Domanda: {domanda[:100]}...")

    try:
        from app import _ORACLE_PROMPT_L1, _ORACLE_PROMPT_L2
    except Exception as e:
        log.error(f"Import prompt error: {e}")
        return None

    # L1
    try:
        r1 = chiama_deepseek(_ORACLE_PROMPT_L1, domanda + live_ctx)
    except Exception as e:
        log.error(f"L1 error: {e}")
        return None

    domande = estrai_domande(r1)
    if not domande:
        log.warning("Nessuna domanda dal L1")
        return None

    # L2
    l2_input = (
        "DOMANDE DAL RISPONDITORE:\n" +
        "\n".join([f"{i+1}. {d}" for i,d in enumerate(domande)]) +
        f"\n\nCONTESTO: {domanda}\nANOMALIA: {trigger_nome}" + live_ctx
    )
    try:
        r2 = chiama_deepseek(_ORACLE_PROMPT_L2, l2_input)
    except Exception as e:
        log.error(f"L2 error: {e}")
        return None

    capsule = parse_capsule(r2)
    log.info(f"Capsule emesse: {len(capsule)}")

    iniettate = []
    for cap in capsule:
        sicurezza = cap.get('sicurezza', 'RISCHIO').upper()
        nome = cap.get('nome', 'UNKNOWN')
        if sicurezza == 'SAFE':
            ok = inietta_capsule_bot(cap)
            esito = 'INIETTATA' if ok else 'ERRORE'
            if ok: iniettate.append(cap)
        else:
            esito = f'SKIP_{sicurezza}'
        log_capsule(nome, sicurezza, cap.get('urgenza','?'), cap.get('soluzione','')[:100], esito)

    log_run(trigger_nome, domanda, len(capsule), len(iniettate), capsule)
    set_last_run_ts(time.time())
    log.info(f"=== COMPLETATO: {len(capsule)} emesse, {len(iniettate)} iniettate ===")
    return {'emesse': len(capsule), 'iniettate': len(iniettate), 'capsule': capsule}

# ── EVENT-DRIVEN LOOP ─────────────────────────────────────────────────
def event_loop():
    """
    Controlla ogni 30s se c'è un oracle_trigger nel heartbeat.
    Si sveglia SOLO quando il bot segnala un'anomalia.
    Cooldown di 5 minuti dopo ogni run per non spammare DeepSeek.
    """
    log.info(f"Oracle Event Loop avviato — check ogni {CHECK_INTERVAL}s, cooldown {COOLDOWN_AFTER}s")
    while True:
        try:
            mode = get_mode()
            if mode == 'AUTO':
                # Controlla cooldown
                last_run = get_last_run_ts()
                in_cooldown = (time.time() - last_run) < COOLDOWN_AFTER

                if in_cooldown:
                    remaining = int(COOLDOWN_AFTER - (time.time() - last_run))
                    log.debug(f"Cooldown attivo — {remaining}s rimanenti")
                else:
                    # Leggi trigger
                    trigger = get_oracle_trigger()
                    if trigger:
                        log.info(f"🔔 TRIGGER RILEVATO: {trigger}")
                        clear_oracle_trigger()
                        run_oracle(trigger_nome=trigger)
                    else:
                        log.debug("Nessun trigger — sistema OK")
            else:
                log.debug("Modalità MANUAL — skip")

        except Exception as e:
            log.error(f"event_loop error: {e}")

        time.sleep(CHECK_INTERVAL)

# ── ENTRY POINT ───────────────────────────────────────────────────────
def start_background():
    init_oracle_db()
    t = threading.Thread(target=event_loop, daemon=True)
    t.start()
    log.info("Oracle Auto event-driven thread avviato")
    return t

if __name__ == '__main__':
    init_oracle_db()
    event_loop()

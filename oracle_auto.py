"""
ORACLE AUTO — Background Worker Event-Driven
============================================
Legge oracle_trigger dal heartbeat ogni 30s.
Se trigger presente → invoca pipeline Dual-AI DeepSeek.
Processa SuperCapsule JSON incluso campo CODICE → CapsuleExecutor.
Modalità AUTO: event-driven (non polling orario).
"""

import json
import os
import sqlite3
import threading
import time
import logging
import re
import urllib.request as _ur

log = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────────────
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
ORACLE_DB_PATH   = os.path.join(os.path.dirname(
    os.environ.get("DB_PATH", "/home/app/data/trading_data.db")
), "oracle_auto.db")
COOLDOWN_SECONDS = 300   # 5 min tra run auto sullo stesso trigger

_heartbeat_ref   = None
_bot_ref         = None
_last_trigger    = ""
_last_run_ts     = 0.0
_running         = False
_mode            = "AUTO"   # AUTO | MANUAL

# ── Prompt sistema ──────────────────────────────────────────────────────────

SYSTEM_RISPONDITORE = (
    "Sei il RISPONDITORE L1 di OVERTOP — il primo analista della pipeline Dual-AI.\n"
    "Ricevi lo stato live del bot e un trigger di anomalia.\n"
    "Il tuo compito: analizza la situazione e formula 3-5 domande PRECISE e TECNICHE\n"
    "per il SUPERRISPONDITORE L2, che risponderà con SuperCapsule JSON operative.\n"
    "Le domande devono essere specifiche, non generiche. Usa i dati reali che vedi.\n"
    "Formato risposta: lista numerata di domande, nessun altro testo."
)

SYSTEM_SUPERRISPONDITORE = (
    "Sei il SUPERRISPONDITORE L2 di OVERTOP — il giudice supremo. Parli SOLO in SuperCapsule JSON.\n\n"
    "Per ogni domanda ricevuta emetti UNA SuperCapsule con questa struttura ESATTA:\n"
    "{\n"
    "  \"nome\": \"NOME_BREVE\",\n"
    "  \"urgenza\": \"CRITICA|ALTA|MEDIA\",\n"
    "  \"sicurezza\": \"SAFE|VALUTA|RISCHIO\",\n"
    "  \"problema\": \"...\",\n"
    "  \"causa\": \"...\",\n"
    "  \"perché\": \"...\",\n"
    "  \"soluzione\": \"...\",\n"
    "  \"codice\": \"OPZIONALE: funzione Python con firma specifica\",\n"
    "  \"target_method\": \"OPZIONALE: nome metodo bot da sostituire\",\n"
    "  \"effetto_atteso\": \"...\"\n"
    "}\n\n"
    "REGOLE SEMAFORO sicurezza (OBBLIGATORIO):\n"
    "  SAFE   = solo lettura (print, getattr, .get()) — nessuna assegnazione\n"
    "  VALUTA = modifica parametri reversibili: SOGLIA_BASE, soglie, size, veti, boost_seed — USA QUESTO di default\n"
    "  RISCHIO = SOLO per: abilitare SHORT live, modificare _direction, ordini reali\n"
    "  REGOLA CRITICA: in dubbio tra VALUTA e RISCHIO → scegli sempre VALUTA.\n"
    "  Capsule di soglia, size, veto, seed, OI, contesto = SEMPRE VALUTA.\n\n"
    "REGOLE CAMPO CODICE:\n"
    "  - Se generi codice Python, usa firma: def nome_funzione(self, **kwargs):\n"
    "  - Il codice NON può toccare: TRADE_SIZE_USD, LEVERAGE, FEE_PCT, STOP_LIVE\n"
    "  - Il codice NON può chiamare: exec, eval, os, sys, subprocess, _place_order\n"
    "  - Usa solo: math, collections, getattr, self.campo, self.oracolo, self._oi_carica\n"
    "  - target_method deve essere uno di: _calcola_soglia_ranging, _calcola_soglia_explosive,\n"
    "    _calcola_soglia_trending, _get_ia_soglia_boost, _get_dynamic_soglia_max,\n"
    "    _calcola_size_boost, _check_extra_veto, _seed_modifier, _exit_energy_modifier\n\n"
    "Rispondi SOLO con array JSON valido [ {...}, {...} ]. NESSUN testo fuori. Ordina per urgenza."
)

# ── DB Init ──────────────────────────────────────────────────────────────────

def _init_db():
    conn = sqlite3.connect(ORACLE_DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS oracle_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, mode TEXT, trigger TEXT,
        domanda TEXT, capsule_emesse INTEGER,
        capsule_iniettate INTEGER, capsule_json TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS oracle_capsule_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, capsule_id TEXT, nome TEXT,
        sicurezza TEXT, urgenza TEXT, azione TEXT, esito TEXT)""")
    conn.commit()
    conn.close()

# ── DeepSeek API ─────────────────────────────────────────────────────────────

def _deepseek_call(system: str, user: str, max_tokens: int = 2000) -> str:
    if not DEEPSEEK_API_KEY:
        return ""
    payload = json.dumps({
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user}
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3
    }).encode()
    req = _ur.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {DEEPSEEK_API_KEY}"}
    )
    with _ur.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
    return result["choices"][0]["message"]["content"]

# ── Contesto Status ──────────────────────────────────────────────────────────

def _build_status_context() -> str:
    if _heartbeat_ref is None:
        return "[STATO: non disponibile]"
    try:
        hb = dict(_heartbeat_ref)
        st = hb.get("signal_tracker", {})
        st_top = st.get("top", [])[:3] if isinstance(st, dict) else []
        ph = hb.get("phantom", {})
        ia = hb.get("ia_stats", {})

        return (
            f"\nSTATO LIVE BOT:\n"
            f"  regime={hb.get('regime','?')} conf={hb.get('regime_conf',0):.0%}\n"
            f"  assetto={hb.get('comparto','?')} gomme={hb.get('gomme','?')}\n"
            f"  oi_stato={hb.get('oi_stato','?')} carica={hb.get('oi_carica',0):.3f}\n"
            f"  soglia_base={hb.get('m2_soglia_base','?')} soglia_min={hb.get('m2_soglia_min','?')}\n"
            f"  last_score={hb.get('m2_last_score',0):.1f} last_soglia={hb.get('m2_last_soglia',0):.1f}\n"
            f"  rsi={hb.get('m2_campo_stats',{}).get('rsi',0):.1f} "
            f"macd={hb.get('m2_campo_stats',{}).get('macd_hist',0):.4f}\n"
            f"  m2_trades={hb.get('m2_trades',0)} wr={hb.get('m2_wr',0):.1%} "
            f"pnl=${hb.get('m2_pnl',0):.2f}\n"
            f"  loss_streak={hb.get('m2_loss_streak',0)} state={hb.get('m2_state','?')}\n"
            f"  capsule_attive={ia.get('attive',0)} static={ia.get('static',0)}\n"
            f"  phantom_bilancio=${ph.get('bilancio',0):.0f}\n"
            f"SIGNAL_TRACKER_TOP:\n"
            + "\n".join(
                f"  {s.get('context','?')}: hit={s.get('hit_60s',0):.0%} n={s.get('n',0)} pnl={s.get('pnl_sim_avg',0):+.2f}"
                for s in st_top
            )
        )
    except Exception as e:
        return f"[STATO: errore {e}]"

# ── Genera domanda dal trigger ────────────────────────────────────────────────

def _domanda_da_trigger(trigger: str, status: dict) -> str:
    """Genera la domanda specifica per il Risponditore L1 in base al trigger."""
    oi_carica = status.get('oi_carica', 0)
    regime    = status.get('regime', '?')

    if trigger.startswith("WIN_PATTERN_"):
        parts = trigger.replace("WIN_PATTERN_", "")
        fp_match = re.match(r"([A-Z]+[|][A-Z]+[|][A-Z]+)_pnl([^_]+)_reason(.+)", parts)
        if fp_match:
            fp, pnl, reason = fp_match.group(1), fp_match.group(2), fp_match.group(3)
            mom, vol, trend = fp.split("|") if "|" in fp else ("?","?","?")
            return (
                f"Il bot ha chiuso un trade VINCENTE di +${pnl} USDC. "
                f"Contesto entry: momentum={mom}, volatilità={vol}, trend={trend}, "
                f"regime={regime}, OI={status.get('oi_stato','?')} carica={oi_carica:.2f}, "
                f"motivo chiusura: {reason}. "
                f"OBIETTIVO: analizza PERCHÉ ha vinto, identifica il pattern vincente, "
                f"genera SuperCapsule che amplifica questo fingerprint {fp} in futuro. "
                f"Considera anche se generare CODICE Python per ottimizzare la soglia in questo contesto."
            )

    if trigger.startswith("LOSS_PATTERN_"):
        parts = trigger.replace("LOSS_PATTERN_", "")
        fp_match = re.match(r"([A-Z]+[|][A-Z]+[|][A-Z]+)_pnl([^_]+)_reason(.+)", parts)
        if fp_match:
            fp, pnl, reason = fp_match.group(1), fp_match.group(2), fp_match.group(3)
            mom, vol, trend = fp.split("|") if "|" in fp else ("?","?","?")
            return (
                f"Il bot ha chiuso un trade in PERDITA di ${pnl} USDC. "
                f"Contesto entry: momentum={mom}, volatilità={vol}, trend={trend}, "
                f"regime={regime}, OI carica={oi_carica:.2f}, chiusura: {reason}. "
                f"OBIETTIVO: analizza PERCHÉ è entrato qui, identifica il pattern di perdita, "
                f"genera SuperCapsule che blocchi o riduca size per fingerprint {fp}. "
                f"Se il pattern è sistematico, genera anche CODICE Python per _check_extra_veto."
            )

    domande = {
        "LOSS_STREAK": lambda: (
            f"Il bot ha {status.get('loss_streak',0)} loss consecutivi. "
            f"Analizza la causa strutturale. Genera capsule difensive. "
            f"Considera CODICE per _calcola_soglia_ranging se il regime è RANGING."
        ),
        "OI_FUOCO_ZERO_TRADE": lambda: (
            f"OI FUOCO carica={oi_carica:.2f} ma 0 trade. Regime={regime}. "
            f"Analizza cosa blocca l'entry. Genera capsule per sbloccare. "
            f"Considera CODICE per _get_ia_soglia_boost che abbassa la soglia con OI FUOCO alto."
        ),
        "PHANTOM_IRRIGIDISCE": lambda: (
            f"Phantom Supervisor sta irrigidendo la soglia. Analizza il loop. "
            f"Genera capsule per stabilizzare. Considera CODICE per _get_dynamic_soglia_max "
            f"che limita il tetto della soglia in base al regime."
        ),
        "PNL_DRAWDOWN": lambda: (
            f"PnL sessione negativo: ${status.get('pnl',0):.2f}. "
            f"Analizza i trade persi. Genera capsule correttive. "
            f"Considera CODICE per _exit_energy_modifier che anticipa l'uscita in contesti negativi."
        ),
        "EXPLOSIVE_ZERO_TRADE": lambda: (
            f"Regime EXPLOSIVE ma 0 trade. Analizza i blocchi attivi. "
            f"Genera capsule per entry selettive. "
            f"Considera CODICE per _calcola_soglia_explosive con soglia più bassa in EXPLOSIVE."
        ),
        "DIFENSIVO_BLOCCATO": lambda: (
            f"Bot in DIFENSIVO da troppo tempo. Analizza se il blocco è giustificato. "
            f"Genera capsule per allentare se opportuno."
        ),
    }

    for key, fn in domande.items():
        if trigger.startswith(key):
            return fn()

    return (
        f"Anomalia rilevata: {trigger}. Regime={regime}, OI={status.get('oi_stato','?')} "
        f"carica={oi_carica:.2f}, trades={status.get('total_trades',0)}. "
        f"Analizza e genera SuperCapsule correttive. "
        f"Se opportuno, includi CODICE Python per migliorare il comportamento del bot."
    )

# ── Processa SuperCapsule ────────────────────────────────────────────────────

def _processa_supercapsule(capsule_list: list, trigger: str) -> tuple:
    """
    Processa le SuperCapsule emesse dal Superrisponditore.
    Gestisce il campo CODICE → CapsuleExecutor.
    Returns: (iniettate: int, log_entries: list)
    """
    iniettate = 0
    log_entries = []

    for cap in capsule_list:
        nome     = cap.get("nome", "UNNAMED")
        sicurezza = cap.get("sicurezza", "RISCHIO").upper()
        urgenza  = cap.get("urgenza", "MEDIA").upper()
        codice   = cap.get("codice", "")
        target   = cap.get("target_method", "")
        soluzione = cap.get("soluzione", "")
        effetto  = cap.get("effetto_atteso", "")

        # Semaforo: RISCHIO → declassa a VALUTA se non è davvero RISCHIO
        if sicurezza == "RISCHIO":
            # RISCHIO vero = SHORT live, _direction, ordini reali
            if not any(k in soluzione.lower() for k in ["short live", "_direction", "ordine reale", "binance"]):
                sicurezza = "VALUTA"
                log.info(f"[ORACLE] ⬇️  {nome}: RISCHIO→VALUTA (non critico)")

        entry = {
            "nome": nome, "sicurezza": sicurezza, "urgenza": urgenza,
            "esito": "SKIP"
        }

        # Gestione CODICE → CapsuleExecutor
        if codice and target and _bot_ref and hasattr(_bot_ref, 'capsule_executor') and _bot_ref.capsule_executor:
            ok, cap_id, err = _bot_ref.capsule_executor.add_capsule(
                nome=nome,
                sorgente=codice,
                target_method=target,
                contesto={"trigger": trigger},
                firma_autore="SUPERRISPONDITORE"
            )
            if ok:
                log.info(f"[ORACLE] 🧬 CODICE aggiunto a CapsuleExecutor: {nome} → {target} (PHANTOM)")
                entry["esito"] = "CODICE_PHANTOM"
                iniettate += 1
            else:
                log.warning(f"[ORACLE] ⚠️  CODICE rifiutato {nome}: {err}")
                entry["esito"] = f"CODICE_RIFIUTATO: {err}"

        # Gestione capsule non-codice
        elif sicurezza in ("SAFE", "VALUTA"):
            # Inietta come capsule_ragionatore nel heartbeat
            if _heartbeat_ref is not None:
                try:
                    _heartbeat_ref["capsule_ragionatore"] = _heartbeat_ref.get("capsule_ragionatore", [])
                    _heartbeat_ref["capsule_ragionatore"].append({
                        "id":      f"RA_{nome}_{int(time.time())}",
                        "azione":  _estrai_azione(cap),
                        "params":  _estrai_params(cap),
                        "motivo":  soluzione[:200],
                        "forza":   0.65 if urgenza == "CRITICA" else 0.55,
                        "vita":    600,
                        "ts":      __import__('datetime').datetime.utcnow().isoformat(),
                        "fonte":   "ORACLE_AUTO",
                    })
                    entry["esito"] = "INIETTATA"
                    iniettate += 1
                    log.info(f"[ORACLE] 💊 {nome} ({sicurezza}) iniettata nel bot")
                except Exception as e:
                    entry["esito"] = f"INJECT_ERR: {e}"
        else:
            entry["esito"] = f"BLOCCATA_{sicurezza}"
            log.info(f"[ORACLE] 🔴 {nome} bloccata (semaforo {sicurezza})")

        log_entries.append(entry)

    return iniettate, log_entries

def _estrai_azione(cap: dict) -> str:
    soluzione = cap.get("soluzione", "").lower()
    if "abbassa soglia" in soluzione or "soglia" in soluzione:
        return "ABBASSA_SOGLIA"
    if "blocca" in soluzione:
        return "BLOCCA_CONTESTO"
    if "boost" in soluzione or "ampli" in soluzione:
        return "BOOST_SIZE"
    return "ABBASSA_SOGLIA"

def _estrai_params(cap: dict) -> dict:
    azione = _estrai_azione(cap)
    if azione == "ABBASSA_SOGLIA":
        return {"delta": -5}
    if azione == "BLOCCA_CONTESTO":
        return {}
    if azione == "BOOST_SIZE":
        return {"mult": 1.2}
    return {}

# ── Run Oracle ───────────────────────────────────────────────────────────────

def run_oracle(trigger: str = "") -> dict:
    """Esegue il ciclo completo Risponditore → Superrisponditore → Iniezione."""
    global _last_trigger, _last_run_ts

    if not trigger and _heartbeat_ref:
        trigger = _heartbeat_ref.get("oracle_trigger", "")

    if not trigger:
        return {"status": "no_trigger"}

    # Cooldown
    if trigger == _last_trigger and time.time() - _last_run_ts < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - (time.time() - _last_run_ts))
        return {"status": "cooldown", "remaining": remaining}

    _last_trigger = trigger
    _last_run_ts  = time.time()

    status = {}
    if _heartbeat_ref:
        status = {
            "regime": _heartbeat_ref.get("regime", "?"),
            "oi_stato": _heartbeat_ref.get("oi_stato", "?"),
            "oi_carica": _heartbeat_ref.get("oi_carica", 0),
            "total_trades": _heartbeat_ref.get("m2_trades", 0),
            "pnl": _heartbeat_ref.get("m2_pnl", 0),
            "loss_streak": _heartbeat_ref.get("m2_loss_streak", 0),
        }

    ctx = _build_status_context()
    domanda = _domanda_da_trigger(trigger, status)

    log.info(f"[ORACLE] 🔍 Run: trigger={trigger}")

    # ── L1: Risponditore ────────────────────────────────────────────────────
    try:
        risposta_l1 = _deepseek_call(
            SYSTEM_RISPONDITORE,
            f"Trigger: {trigger}\n{ctx}\nDomanda originale: {domanda}",
            max_tokens=1000
        )
        log.info(f"[ORACLE] L1 risposta: {risposta_l1[:200]}...")
    except Exception as e:
        log.error(f"[ORACLE] L1 error: {e}")
        risposta_l1 = domanda  # fallback: usa la domanda diretta

    # ── L2: Superrisponditore ────────────────────────────────────────────────
    try:
        risposta_l2 = _deepseek_call(
            SYSTEM_SUPERRISPONDITORE,
            f"Stato bot:\n{ctx}\n\nDomande da analizzare:\n{risposta_l1}",
            max_tokens=3000
        )
        log.info(f"[ORACLE] L2 risposta: {risposta_l2[:200]}...")
    except Exception as e:
        log.error(f"[ORACLE] L2 error: {e}")
        return {"status": "l2_error", "error": str(e)}

    # ── Parse JSON ───────────────────────────────────────────────────────────
    capsule_list = []
    try:
        clean = risposta_l2.replace("```json", "").replace("```", "").strip()
        capsule_list = json.loads(clean)
        if not isinstance(capsule_list, list):
            capsule_list = [capsule_list]
    except Exception as e:
        log.error(f"[ORACLE] Parse JSON L2 fallito: {e}")
        # Tenta estrazione parziale
        try:
            match = re.search(r'\[.*\]', risposta_l2, re.DOTALL)
            if match:
                capsule_list = json.loads(match.group())
        except Exception:
            pass

    # ── Processa e inietta ───────────────────────────────────────────────────
    iniettate, log_entries = _processa_supercapsule(capsule_list, trigger)

    # ── Pulisci trigger ───────────────────────────────────────────────────────
    if _heartbeat_ref and trigger:
        try:
            _heartbeat_ref["oracle_trigger"] = ""
        except Exception:
            pass

    # ── Salva nel DB ─────────────────────────────────────────────────────────
    try:
        conn = sqlite3.connect(ORACLE_DB_PATH)
        conn.execute(
            "INSERT INTO oracle_runs (ts, mode, trigger, domanda, capsule_emesse, capsule_iniettate, capsule_json) VALUES (?,?,?,?,?,?,?)",
            (
                __import__('datetime').datetime.utcnow().isoformat(),
                _mode, trigger, risposta_l1[:500],
                len(capsule_list), iniettate,
                json.dumps(capsule_list, ensure_ascii=False)[:2000]
            )
        )
        for entry in log_entries:
            conn.execute(
                "INSERT INTO oracle_capsule_log (ts, nome, sicurezza, urgenza, azione, esito) VALUES (?,?,?,?,?,?)",
                (
                    __import__('datetime').datetime.utcnow().isoformat(),
                    entry["nome"], entry["sicurezza"], entry["urgenza"],
                    entry.get("azione", ""), entry["esito"]
                )
            )
        conn.commit()
        conn.close()
    except Exception as e:
        log.debug(f"[ORACLE] DB save: {e}")

    log.info(f"[ORACLE] ✅ Run completato: {len(capsule_list)} capsule emesse, {iniettate} iniettate")
    return {
        "status":    "ok",
        "trigger":   trigger,
        "emesse":    len(capsule_list),
        "iniettate": iniettate,
        "log":       log_entries,
    }

# ── Background Thread ─────────────────────────────────────────────────────────

def _loop():
    global _running
    _running = True
    log.info("[ORACLE_AUTO] 🤖 Background worker avviato — event-driven ogni 30s")
    while _running:
        try:
            if _mode == "AUTO" and _heartbeat_ref:
                trigger = _heartbeat_ref.get("oracle_trigger", "")
                if trigger:
                    run_oracle(trigger)
        except Exception as e:
            log.debug(f"[ORACLE_AUTO] loop error: {e}")
        time.sleep(30)

def start_background(heartbeat_data=None, bot_instance=None):
    global _heartbeat_ref, _bot_ref
    _heartbeat_ref = heartbeat_data
    _bot_ref       = bot_instance
    _init_db()
    t = threading.Thread(target=_loop, daemon=True, name="oracle_auto")
    t.start()
    return t

def set_mode(mode: str):
    global _mode
    _mode = mode.upper()

def stop():
    global _running
    _running = False

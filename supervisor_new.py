"""
AI SUPERVISOR V2 — MULTI-ASSET CROSS-INTELLIGENCE
===================================================
Una chiamata DeepSeek ogni 5 minuti.
Legge BTC + SOL + GOLD insieme.
Decide dove il mercato offre opportunità reale.
"""

import os, json, time, threading, logging, urllib.request
from datetime import datetime

log = logging.getLogger(__name__)

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
CALL_INTERVAL    = 300  # 5 minuti — non ogni 7 secondi

# ── Storage globale ──────────────────────────────────────────────────────────
_supervisor_log   = []
_supervisor_lock  = threading.Lock()
_last_call        = 0
_last_result      = {}
_asset_snapshots  = {}   # {asset: heartbeat_dict}
_asset_lock       = threading.Lock()

# URL degli altri bot — fetch server-side (bypassa CORS)
PEER_URLS = {
    "BTCUSDC": os.environ.get("URL_BTC", "https://tecnaria-v2.onrender.com"),
    "SOLUSDC": os.environ.get("URL_SOL", "https://tecnaria-v4.onrender.com"),
    "XAUUSDT": os.environ.get("URL_GOLD", "https://tecnaria-v5.onrender.com"),
}

def _fetch_peers():
    """Fetch heartbeat dagli altri bot ogni 10 secondi — server side."""
    import urllib.request
    while True:
        for asset, url in PEER_URLS.items():
            try:
                req = urllib.request.urlopen(url+"/heartbeat", timeout=5)
                data = json.loads(req.read())
                with _asset_lock:
                    _asset_snapshots[asset] = data
            except:
                pass
        time.sleep(10)

threading.Thread(target=_fetch_peers, daemon=True, name="sv_peer_fetch").start()

def register_asset(asset: str, heartbeat_data: dict, heartbeat_lock):
    """Ogni bot registra il proprio heartbeat al supervisor."""
    def updater():
        while True:
            try:
                with heartbeat_lock:
                    snap = dict(heartbeat_data)
                with _asset_lock:
                    _asset_snapshots[asset] = snap
            except: pass
            time.sleep(5)
    t = threading.Thread(target=updater, daemon=True, name=f"sv_feed_{asset}")
    t.start()

# ── DeepSeek call ────────────────────────────────────────────────────────────

def _build_prompt(snaps: dict) -> str:
    """Costruisce prompt cross-asset per DeepSeek."""
    
    lines = []
    for asset, hb in snaps.items():
        if not hb: continue
        regime    = hb.get("regime", "?")
        score     = hb.get("m2_last_score", 0)
        soglia    = hb.get("m2_last_soglia", 60)
        oi_stato  = hb.get("oi_stato", "?")
        oi_carica = hb.get("oi_carica", 0)
        trades    = hb.get("m2_trades", 0)
        wr        = round(hb.get("m2_wins", 0) / max(1, trades) * 100, 1) if trades > 0 else 0
        pnl       = hb.get("m2_pnl", 0)
        phantom   = hb.get("phantom", {})
        bil       = phantom.get("bilancio", 0)
        protetti  = phantom.get("total_saved", 0)
        mancati   = phantom.get("total_missed", 0)
        warmup    = hb.get("m2_score_components", {}).get("warmup_rsi", 0)
        dist_buy  = score - soglia  # positivo = sopra soglia
        
        lines.append(f"""
{asset}:
  Regime={regime} | Score={score:.1f}/Soglia={soglia:.1f} | Distanza_BUY={dist_buy:+.1f}
  OracoloInterno={oi_stato} carica={oi_carica:.2f}
  Trades={trades} WR={wr}% PnL=${pnl:+.2f}
  Phantom: protetti=${protetti:.0f} mancati=${mancati:.0f} bilancio=${bil:.0f}
  Warmup RSI/MACD={warmup}/35""")
    
    return f"""Sei il supervisore strategico cross-asset di OVERTOP BASSANO.
Hai 3 bot attivi: BTC, SOL, GOLD (XAUUSDT). Ogni bot è un sniper autonomo.

STATO ATTUALE DEI 3 MERCATI:
{''.join(lines)}

IL TUO COMPITO:
1. Identifica DOVE c'è l'opportunità reale adesso (quale asset)
2. Identifica DOVE il rischio è alto (quale asset evitare)
3. Dai UN SOLO consiglio strategico operativo

REGOLE:
- Se phantom bilancio positivo alto → i filtri funzionano, non toccare
- Se distanza_buy negativa ma oi_stato=FUOCO → opportunità imminente
- Se tutti e 3 in RANGING → scrivi "MERCATO FERMO — attendi rottura"
- Non inventare dati. Usa solo quelli forniti.
- MAX 4 righe di analisi. Sii diretto come un trader, non un professore.

Rispondi SOLO con JSON:
{{
  "asset_migliore": "BTC|SOL|GOLD|NESSUNO",
  "asset_peggiore": "BTC|SOL|GOLD|NESSUNO",
  "stato_mercato": "OPPORTUNITA|ATTESA|PERICOLOSO|FERMO",
  "analisi": "max 200 caratteri — cosa sta succedendo ADESSO",
  "azione": "cosa fare concretamente in 1 frase",
  "alert_level": "green|yellow|red",
  "prossimo_trigger": "cosa aspettare per entrare sul mercato migliore"
}}"""

def _call_deepseek(snaps: dict) -> dict:
    if not DEEPSEEK_API_KEY:
        return {"errore": "No API key", "stato_mercato": "SCONOSCIUTO"}
    
    prompt = _build_prompt(snaps)
    payload = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 400
    }).encode()
    
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"]
            content = content.strip().replace("```json","").replace("```","").strip()
            result = json.loads(content)
            result["ts"] = datetime.utcnow().strftime("%H:%M:%S")
            result["tokens"] = data.get("usage", {}).get("total_tokens", 0)
            return result
    except Exception as e:
        return {
            "errore": str(e),
            "stato_mercato": "ERRORE",
            "alert_level": "red",
            "analisi": f"Errore connessione: {str(e)[:80]}",
            "ts": datetime.utcnow().strftime("%H:%M:%S")
        }

def _supervisor_loop():
    global _last_call, _last_result
    time.sleep(20)  # boot delay
    log.info("[SUPERVISOR_V2] 🧠 Multi-asset supervisor avviato — ogni 5 minuti")
    
    while True:
        try:
            time.sleep(CALL_INTERVAL)
            with _asset_lock:
                snaps = dict(_asset_snapshots)
            
            if not snaps:
                continue
            
            result = _call_deepseek(snaps)
            result["n_assets"] = len(snaps)
            result["assets"] = list(snaps.keys())
            
            _last_call = time.time()
            _last_result = result
            
            with _supervisor_lock:
                _supervisor_log.append(result)
                if len(_supervisor_log) > 50:
                    _supervisor_log.pop(0)
            
            log.info(f"[SUPERVISOR_V2] {result.get('stato_mercato','?')} | "
                    f"Migliore={result.get('asset_migliore','?')} | "
                    f"Token={result.get('tokens',0)}")
        except Exception as e:
            log.error(f"[SUPERVISOR_V2] {e}")

threading.Thread(target=_supervisor_loop, daemon=True, name="supervisor_v2").start()

def get_last_result() -> dict:
    return _last_result

def get_log() -> list:
    with _supervisor_lock:
        return list(reversed(_supervisor_log[-20:]))

def get_next_call_in() -> int:
    """Secondi al prossimo ciclo."""
    return max(0, int(_last_call + CALL_INTERVAL - time.time()))

def get_asset_snapshots() -> dict:
    with _asset_lock:
        return dict(_asset_snapshots)

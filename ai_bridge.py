#!/usr/bin/env python3
"""
AI BRIDGE — OVERTOP BASSANO
═══════════════════════════════════════════════════════════════════════════════
Ponte tra il bot e Claude API.

COSA FA:
  1. Legge lo stato del bot ogni N secondi (heartbeat_data)
  2. Rileva eventi significativi (trade chiuso, regime cambiato, anomalia)
  3. Manda lo snapshot a Claude API con prompt strutturato
  4. Riceve comandi: nuove capsule, modifiche pesi, alert
  5. Scrive in capsule_attive.json → il bot le raccoglie al prossimo hot-reload
  6. Zero restart, zero interruzione

SETUP:
  - Env var: DEEPSEEK_API_KEY (obbligatoria)
  - Env var: AI_BRIDGE_INTERVAL (opzionale, default 300 = 5 minuti)
  - Env var: AI_BRIDGE_ENABLED (opzionale, default "true")

INTEGRAZIONE in app.py:
  from ai_bridge import AIBridge
  bridge = AIBridge(heartbeat_data, heartbeat_lock)
  bridge.start()
═══════════════════════════════════════════════════════════════════════════════
"""

import os
import json
import time
import threading
import logging
import hashlib
from datetime import datetime

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT — L'ANALISTA AI
# ═══════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """Sei l'analista AI del trading bot OVERTOP BASSANO. Ricevi snapshot periodici dello stato del bot e rispondi SOLO con comandi JSON strutturati.

Il bot ha DUE motori:
- M1 (Catena Filtri): 10 filtri binari in serie. Ultra-selettivo, spesso fa zero trade.
- M2 (Campo Gravitazionale): punteggio cartesiano 0-100 vs soglia dinamica 35-90. Shadow trading.

ANALIZZA:
1. Performance M1 vs M2 (trade, WR, PnL)
2. Regime attuale e se le soglie sono appropriate
3. Pattern nei trade recenti (tutti win? tutti loss? un matrimonio domina?)
4. Anomalie (bot fermo, WR in crollo, regime bloccato su RANGING)

RISPONDI SEMPRE con questo formato JSON esatto (niente altro, niente markdown):
{
  "analisi": "breve analisi testuale max 200 caratteri",
  "alert_level": "green|yellow|red",
  "comandi": [
    {
      "tipo": "add_capsule|disable_capsule|modify_weight|adjust_soglia|noop",
      "payload": {}
    }
  ],
  "note_per_roberto": "eventuale messaggio per il proprietario"
}

TIPI DI COMANDI:

add_capsule: aggiunge una nuova capsula a capsule_attive.json
  payload: {"capsule_id":"...", "descrizione":"...", "trigger":[...], "azione":{...}, "priority":N, "enabled":true}

disable_capsule: disabilita una capsula esistente
  payload: {"capsule_id":"ID_DA_DISABILITARE"}

modify_weight: modifica un peso del CampoGravitazionale (solo M2)
  payload: {"param":"W_SEED|W_FINGERPRINT|W_MOMENTUM|W_TREND|W_VOLATILITY|W_REGIME", "new_value":N}

adjust_soglia: modifica la soglia base del Campo
  payload: {"param":"SOGLIA_BASE|SOGLIA_MIN|SOGLIA_MAX", "new_value":N}

noop: nessuna azione necessaria
  payload: {"reason":"motivo per cui non serve intervenire"}

REGOLE:
- Se non hai abbastanza dati (< 10 trade M2), rispondi con noop
- Non fare più di 2 comandi per ciclo — cambiamenti piccoli e misurabili
- Se M2 sta funzionando bene (WR > 60%, PnL positivo), non toccare niente
- Se M2 ha WR < 40% su 20+ trade, suggerisci aggiustamenti ai pesi
- Se il regime è RANGING da troppo tempo e M2 non fa trade, abbassa SOGLIA_BASE di 2-3 punti
- Mai portare SOGLIA_BASE sotto 45 o sopra 75
- Mai portare un peso sotto 5 o sopra 40
- alert_level: green = tutto ok, yellow = attenzione, red = problema serio
"""

# ═══════════════════════════════════════════════════════════════════════════
# AI BRIDGE
# ═══════════════════════════════════════════════════════════════════════════

class AIBridge:
    """
    Thread background che connette il bot a Claude API.
    Legge, analizza, comanda — senza fermare niente.
    """

    def __init__(self, heartbeat_data, heartbeat_lock,
                 capsule_file="capsule_attive.json"):
        self.heartbeat_data = heartbeat_data
        self.heartbeat_lock = heartbeat_lock
        self.capsule_file   = capsule_file

        # Config da env vars
        self.api_key    = os.environ.get("DEEPSEEK_API_KEY", "")
        self.interval   = int(os.environ.get("AI_BRIDGE_INTERVAL", "300"))  # 5 min default
        self.enabled    = os.environ.get("AI_BRIDGE_ENABLED", "true").lower() == "true"
        self.model      = os.environ.get("AI_BRIDGE_MODEL", "deepseek-chat")

        # Stato interno
        self._thread         = None
        self._running        = False
        self._last_snapshot  = {}
        self._last_m2_trades = 0
        self._last_regime    = ""
        self._history        = []     # ultimi 10 scambi con Claude
        self._commands_log   = []     # ultimi 20 comandi eseguiti
        self._consecutive_errors = 0

        # Log bridge dedicato
        self._bridge_log = []

    def start(self):
        """Avvia il bridge come daemon thread."""
        if not self.enabled:
            log.info("[AI_BRIDGE] ⚠️ Disabilitato (AI_BRIDGE_ENABLED=false)")
            return

        if not self.api_key:
            log.warning("[AI_BRIDGE] ❌ DEEPSEEK_API_KEY non impostata — bridge inattivo")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="ai_bridge_thread"
        )
        self._thread.start()
        log.info(f"[AI_BRIDGE] 🌉 Avviato — intervallo {self.interval}s, modello {self.model}")

    def stop(self):
        """Ferma il bridge."""
        self._running = False
        log.info("[AI_BRIDGE] 🛑 Fermato")

    def _loop(self):
        """Loop principale del bridge."""
        # Aspetta 60s all'avvio per dare tempo al bot di scaldare i dati
        time.sleep(60)
        self._log("🌉", "Bridge attivo — primo ciclo in corso")

        while self._running:
            try:
                snapshot = self._read_snapshot()

                if self._should_call(snapshot):
                    response = self._call_claude(snapshot)

                    if response:
                        self._execute_commands(response)
                        self._history.append({
                            "ts": datetime.utcnow().isoformat(),
                            "snapshot_summary": self._summarize(snapshot),
                            "response": response,
                        })
                        if len(self._history) > 10:
                            self._history.pop(0)
                        self._consecutive_errors = 0
                    else:
                        self._consecutive_errors += 1

                # Esponi stato bridge nel heartbeat
                self._update_heartbeat_bridge()

            except Exception as e:
                log.error(f"[AI_BRIDGE] Errore nel loop: {e}")
                self._consecutive_errors += 1

            # Backoff se troppi errori consecutivi
            wait = self.interval
            if self._consecutive_errors > 3:
                wait = min(self.interval * 4, 1800)  # max 30 min
                self._log("⚠️", f"Backoff: {self._consecutive_errors} errori, aspetto {wait}s")

            time.sleep(wait)

    def _read_snapshot(self) -> dict:
        """Legge lo stato corrente dal heartbeat_data."""
        if self.heartbeat_lock:
            self.heartbeat_lock.acquire()
        try:
            snapshot = dict(self.heartbeat_data) if self.heartbeat_data else {}
        finally:
            if self.heartbeat_lock:
                self.heartbeat_lock.release()
        return snapshot

    def _should_call(self, snapshot: dict) -> bool:
        """
        Decide se vale la pena chiamare Claude.
        Non sprecare API calls se non è cambiato niente di significativo.
        """
        if not snapshot:
            return False

        # Sempre chiama se è la prima volta
        if not self._last_snapshot:
            self._last_snapshot = snapshot
            return True

        # Chiama se M2 ha fatto nuovi trade
        m2_trades = snapshot.get("m2_trades", 0)
        if m2_trades > self._last_m2_trades:
            self._last_m2_trades = m2_trades
            self._last_snapshot = snapshot
            return True

        # Chiama se il regime è cambiato
        regime = snapshot.get("regime", "")
        if regime != self._last_regime and regime:
            self._last_regime = regime
            self._last_snapshot = snapshot
            return True

        # Chiama comunque ogni intervallo (per check routine)
        self._last_snapshot = snapshot
        return True

    def _call_claude(self, snapshot: dict) -> dict:
        """Chiama DeepSeek API con lo snapshot e ritorna la risposta parsed."""
        import urllib.request
        import urllib.error

        # Costruisci il messaggio user
        user_msg = self._build_user_message(snapshot)

        payload = json.dumps({
            "model": self.model,
            "max_tokens": 1000,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg}
            ],
            "temperature": 0.3,
        }).encode('utf-8')

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        try:
            req = urllib.request.Request(
                "https://api.deepseek.com/chat/completions",
                data=payload,
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode('utf-8'))

            # Estrai il testo dalla risposta DeepSeek
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "")

            # Parse JSON dalla risposta
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

            response = json.loads(text)
            self._log("📡", f"AI risponde: {response.get('analisi', '?')[:80]} "
                           f"[{response.get('alert_level', '?')}] "
                           f"comandi={len(response.get('comandi', []))}")
            return response

        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8') if e.fp else ""
            log.error(f"[AI_BRIDGE] API HTTP {e.code}: {body[:200]}")
            self._log("❌", f"API errore HTTP {e.code}")
            return None
        except json.JSONDecodeError as e:
            log.error(f"[AI_BRIDGE] JSON parse error: {e} | raw: {text[:200]}")
            self._log("❌", f"Risposta non parsabile")
            return None
        except Exception as e:
            log.error(f"[AI_BRIDGE] Call error: {e}")
            self._log("❌", f"Errore chiamata: {str(e)[:60]}")
            return None

    def _build_user_message(self, snapshot: dict) -> str:
        """Costruisce il messaggio per Claude con tutti i dati rilevanti."""
        # M1 stats
        m1_trades = snapshot.get("trades", 0)
        m1_wins   = snapshot.get("wins", 0)
        m1_losses = snapshot.get("losses", 0)
        m1_wr     = snapshot.get("wr", 0)
        capital   = snapshot.get("capital", 10000)

        # M2 stats
        m2_trades = snapshot.get("m2_trades", 0)
        m2_wins   = snapshot.get("m2_wins", 0)
        m2_losses = snapshot.get("m2_losses", 0)
        m2_wr     = snapshot.get("m2_wr", 0)
        m2_pnl    = snapshot.get("m2_pnl", 0)

        # Regime
        regime      = snapshot.get("regime", "UNKNOWN")
        regime_conf = snapshot.get("regime_conf", 0)

        # Log recenti
        live_log = snapshot.get("live_log", [])[-10:]
        m2_log   = snapshot.get("m2_log", [])[-10:]

        # Calibratore
        calibra = snapshot.get("calibra_params", {})

        # Oracolo (top 5 fingerprint)
        oracolo = snapshot.get("oracolo_snapshot", {})
        top_fp  = sorted(oracolo.items(), key=lambda x: x[1].get("samples", 0), reverse=True)[:5]

        # Campo stats
        campo = snapshot.get("m2_campo_stats", {})

        msg = f"""SNAPSHOT BOT — {datetime.utcnow().isoformat()}

═══ STATO GENERALE ═══
Capitale: ${capital:.2f}
Regime: {regime} (conf={regime_conf:.0%})
Posizione M1 aperta: {snapshot.get('posizione_aperta', False)}
Shadow M2 aperta: {snapshot.get('m2_shadow_open', False)}

═══ MOTORE 1 (Catena Filtri) ═══
Trade: {m1_trades} | Win: {m1_wins} | Loss: {m1_losses} | WR: {m1_wr:.1%}

═══ MOTORE 2 (Campo Gravitazionale) ═══
Trade: {m2_trades} | Win: {m2_wins} | Loss: {m2_losses} | WR: {m2_wr:.1%}
PnL shadow: ${m2_pnl:.4f}
Campo stats: {json.dumps(campo)}

═══ CALIBRATORE ATTUALE ═══
{json.dumps(calibra, indent=2)}

═══ TOP FINGERPRINT ORACOLO ═══
{chr(10).join(f"  {fp}: WR={d.get('wr',0):.2f} samples={d.get('samples',0):.0f}" for fp, d in top_fp)}

═══ LOG M1 RECENTI ═══
{chr(10).join(live_log[-5:]) if live_log else "(vuoto)"}

═══ LOG M2 RECENTI ═══
{chr(10).join(m2_log[-5:]) if m2_log else "(vuoto)"}

═══ DIVORZI ATTIVI ═══
{json.dumps(snapshot.get('matrimoni_divorzio', []))}

Analizza e rispondi con comandi JSON."""

        return msg

    def _execute_commands(self, response: dict):
        """Esegue i comandi ricevuti da Claude."""
        comandi = response.get("comandi", [])
        alert   = response.get("alert_level", "green")
        analisi = response.get("analisi", "")
        note    = response.get("note_per_roberto", "")

        if alert == "red":
            self._log("🔴", f"ALERT RED: {analisi}")
        elif alert == "yellow":
            self._log("🟡", f"ALERT: {analisi}")

        if note:
            self._log("📝", f"Per Roberto: {note[:100]}")

        for cmd in comandi:
            tipo    = cmd.get("tipo", "noop")
            payload = cmd.get("payload", {})

            try:
                if tipo == "add_capsule":
                    self._cmd_add_capsule(payload)
                elif tipo == "disable_capsule":
                    self._cmd_disable_capsule(payload)
                elif tipo == "modify_weight":
                    self._cmd_modify_weight(payload)
                elif tipo == "adjust_soglia":
                    self._cmd_adjust_soglia(payload)
                elif tipo == "noop":
                    reason = payload.get("reason", "nessun motivo")
                    self._log("💤", f"Noop: {reason[:60]}")
                else:
                    self._log("❓", f"Comando sconosciuto: {tipo}")

                self._commands_log.append({
                    "ts": datetime.utcnow().isoformat(),
                    "tipo": tipo,
                    "payload": payload,
                    "alert": alert,
                })
                if len(self._commands_log) > 20:
                    self._commands_log.pop(0)

            except Exception as e:
                log.error(f"[AI_BRIDGE] Errore esecuzione {tipo}: {e}")
                self._log("❌", f"Errore cmd {tipo}: {str(e)[:50]}")

    # ── COMANDI ───────────────────────────────────────────────────────────

    def _cmd_add_capsule(self, payload: dict):
        """Aggiunge una capsula a capsule_attive.json."""
        capsule_id = payload.get("capsule_id", f"AI_BRIDGE_{int(time.time())}")
        payload["capsule_id"] = capsule_id
        payload.setdefault("enabled", True)
        payload.setdefault("priority", 3)
        payload.setdefault("source", "ai_bridge")
        payload.setdefault("created_at", time.time())

        try:
            existing = []
            if os.path.exists(self.capsule_file):
                with open(self.capsule_file) as f:
                    existing = json.load(f)

            # Evita duplicati
            existing_ids = {c.get("capsule_id") for c in existing}
            if capsule_id in existing_ids:
                self._log("⚠️", f"Capsula {capsule_id} già esiste — skip")
                return

            existing.append(payload)
            with open(self.capsule_file, 'w') as f:
                json.dump(existing, f, indent=2)

            self._log("💊", f"Capsula aggiunta: {capsule_id} — {payload.get('descrizione','')[:50]}")

        except Exception as e:
            log.error(f"[AI_BRIDGE] Add capsule error: {e}")

    def _cmd_disable_capsule(self, payload: dict):
        """Disabilita una capsula esistente."""
        target_id = payload.get("capsule_id", "")
        if not target_id:
            return

        try:
            if not os.path.exists(self.capsule_file):
                return
            with open(self.capsule_file) as f:
                capsules = json.load(f)

            found = False
            for cap in capsules:
                if cap.get("capsule_id") == target_id:
                    cap["enabled"] = False
                    found = True
                    break

            if found:
                with open(self.capsule_file, 'w') as f:
                    json.dump(capsules, f, indent=2)
                self._log("🔕", f"Capsula disabilitata: {target_id}")
            else:
                self._log("❓", f"Capsula {target_id} non trovata")

        except Exception as e:
            log.error(f"[AI_BRIDGE] Disable capsule error: {e}")

    def _cmd_modify_weight(self, payload: dict):
        """
        Modifica un peso del CampoGravitazionale.
        Scrive in un file bridge_commands.json che il bot legge.
        """
        param     = payload.get("param", "")
        new_value = payload.get("new_value", None)

        valid_params = {"W_SEED", "W_FINGERPRINT", "W_MOMENTUM",
                        "W_TREND", "W_VOLATILITY", "W_REGIME"}

        if param not in valid_params:
            self._log("❌", f"Parametro peso non valido: {param}")
            return

        if new_value is None or not (5 <= new_value <= 40):
            self._log("❌", f"Valore peso fuori range: {new_value} (deve essere 5-40)")
            return

        self._write_bridge_command("modify_weight", {"param": param, "value": new_value})
        self._log("⚖️", f"Peso M2 {param} → {new_value}")

    def _cmd_adjust_soglia(self, payload: dict):
        """Modifica la soglia del CampoGravitazionale."""
        param     = payload.get("param", "")
        new_value = payload.get("new_value", None)

        valid_params = {"SOGLIA_BASE": (45, 75), "SOGLIA_MIN": (25, 50), "SOGLIA_MAX": (70, 95)}

        if param not in valid_params:
            self._log("❌", f"Parametro soglia non valido: {param}")
            return

        lo, hi = valid_params[param]
        if new_value is None or not (lo <= new_value <= hi):
            self._log("❌", f"Valore soglia fuori range: {new_value} (deve essere {lo}-{hi})")
            return

        self._write_bridge_command("adjust_soglia", {"param": param, "value": new_value})
        self._log("📐", f"Soglia M2 {param} → {new_value}")

    def _write_bridge_command(self, cmd_type: str, data: dict):
        """
        Scrive comandi in bridge_commands.json.
        Il bot li legge nel prossimo ciclo di hot-reload.
        """
        cmd_file = "bridge_commands.json"
        try:
            existing = []
            if os.path.exists(cmd_file):
                with open(cmd_file) as f:
                    existing = json.load(f)

            existing.append({
                "type":      cmd_type,
                "data":      data,
                "timestamp": time.time(),
                "executed":  False,
            })

            with open(cmd_file, 'w') as f:
                json.dump(existing, f, indent=2)

        except Exception as e:
            log.error(f"[AI_BRIDGE] Write command error: {e}")

    # ── UTILITIES ─────────────────────────────────────────────────────────

    def _log(self, emoji: str, msg: str):
        """Log del bridge."""
        ts = datetime.utcnow().strftime('%H:%M:%S')
        entry = f"{ts} {emoji} [BRIDGE] {msg}"
        self._bridge_log.append(entry)
        if len(self._bridge_log) > 30:
            self._bridge_log.pop(0)
        log.info(entry)

    def _summarize(self, snapshot: dict) -> str:
        """Riassunto snapshot per history."""
        return (f"M1:{snapshot.get('trades',0)}t M2:{snapshot.get('m2_trades',0)}t "
                f"regime={snapshot.get('regime','?')} cap=${snapshot.get('capital',0):.0f}")

    def _update_heartbeat_bridge(self):
        """Esponi stato bridge nel heartbeat per la dashboard."""
        if self.heartbeat_lock:
            self.heartbeat_lock.acquire()
        try:
            if self.heartbeat_data is not None:
                self.heartbeat_data["bridge_active"]  = self._running
                self.heartbeat_data["bridge_errors"]   = self._consecutive_errors
                self.heartbeat_data["bridge_log"]      = list(self._bridge_log[-10:])
                self.heartbeat_data["bridge_commands"]  = list(self._commands_log[-5:])
                self.heartbeat_data["bridge_history"]   = len(self._history)
                self.heartbeat_data["bridge_last_call"] = (
                    self._history[-1]["ts"] if self._history else None
                )
        except Exception:
            pass
        finally:
            if self.heartbeat_lock:
                self.heartbeat_lock.release()

    def get_status(self) -> dict:
        """Stato del bridge per endpoint dedicato."""
        return {
            "active":            self._running,
            "enabled":           self.enabled,
            "has_api_key":       bool(self.api_key),
            "interval":          self.interval,
            "model":             self.model,
            "consecutive_errors":self._consecutive_errors,
            "total_calls":       len(self._history),
            "total_commands":    len(self._commands_log),
            "last_call":         self._history[-1]["ts"] if self._history else None,
            "last_response":     self._history[-1]["response"] if self._history else None,
            "log":               list(self._bridge_log[-15:]),
            "recent_commands":   list(self._commands_log[-10:]),
        }

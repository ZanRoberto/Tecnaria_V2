#!/usr/bin/env python3
"""
BOT LAUNCHER — OVERTOP BASSANO V14 PRODUCTION
═══════════════════════════════════════════════════════════════════════════════
Avvia il bot come processo worker su Render.

PROCFILE su Render:
  web:    python app.py
  worker: python bot_launcher.py

NOTA: PAPER_TRADE = True nel file production → nessun ordine reale.
Imposta PAPER_TRADE = False solo dopo paper test soddisfacente.
═══════════════════════════════════════════════════════════════════════════════
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("[LAUNCHER] 🚀 OVERTOP BASSANO V14 LAUNCHER STARTING...")
print("[LAUNCHER] 📁 Working directory:", os.getcwd())
print("[LAUNCHER] 📦 Python:", sys.version.split()[0])

try:
    from OVERTOP_BASSANO_V14_PRODUCTION import OvertopBassanoV14Production

    print("[LAUNCHER] ✅ Import OK: OvertopBassanoV14Production")
    print("[LAUNCHER] 🔧 Creazione istanza bot...")

    bot = OvertopBassanoV14Production()

    print("[LAUNCHER] ▶️  bot.run() — bot LIVE")
    bot.run()

except ImportError as e:
    print(f"[LAUNCHER] ❌ IMPORT ERROR: {e}")
    print("[LAUNCHER] ⚠️  Verifica che OVERTOP_BASSANO_V14_PRODUCTION.py sia nella stessa directory")
    sys.exit(1)
except Exception as e:
    print(f"[LAUNCHER] ❌ ERRORE CRITICO: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

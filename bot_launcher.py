#!/usr/bin/env python3
"""
BOT LAUNCHER - OVERTOP BASSANO V14 CON MEMORIA
═══════════════════════════════════════════════════════════════════════════════
Questo file avvia il bot V14 con memoria matrimoni/divorzi/separazioni.

Usa questo file come processo worker su Render (insieme a app.py).

PROCFILE su Render deve essere:
web: python app.py
worker: python bot_launcher.py

═══════════════════════════════════════════════════════════════════════════════
"""

import sys
import os

# Assicura che i path siano corretti
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("[LAUNCHER] 🚀 OVERTOP BASSANO V14 LAUNCHER STARTING...")
print("[LAUNCHER] 📁 Working directory:", os.getcwd())
print("[LAUNCHER] 📦 Python path:", sys.path[:3])

try:
    # Importa il bot con MEMORIA
    from OVERTOP_BASSANO_V14 import OvertopBassanoV14Memoria
    print("[LAUNCHER] ✅ Imported OvertopBassanoV14Memoria")
    
    # Crea istanza
    print("[LAUNCHER] 🔧 Creating bot instance...")
    bot = OvertopBassanoV14Memoria()
    
    # Avvia il bot
    print("[LAUNCHER] ▶️ Starting bot.run()...")
    print("[LAUNCHER] 🟢 Bot is now LIVE")
    print("[LAUNCHER] 💓 Sending heartbeat to Mission Control every 30s")
    print("[LAUNCHER] 📊 WR should rise from 0.4% towards 50%+")
    print("[LAUNCHER] 🧠 Memory system active: matrimoni/separazioni/divorzi")
    
    bot.run()
    
except ImportError as e:
    print(f"[LAUNCHER] ❌ IMPORT ERROR: {e}")
    print("[LAUNCHER] ⚠️ Make sure OVERTOP_BASSANO_V14_1_.py exists in same directory")
    sys.exit(1)
except Exception as e:
    print(f"[LAUNCHER] ❌ CRITICAL ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)


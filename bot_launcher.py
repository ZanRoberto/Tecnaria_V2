#!/usr/bin/env python3
"""
BOT LAUNCHER - OVERTOP BASSANO V14 CON MEMORIA (DEBUG VERSION)
═══════════════════════════════════════════════════════════════════════════════
Versione con logging completo per debuggare errori del worker process.
═══════════════════════════════════════════════════════════════════════════════
"""
import sys
import os
import logging
import traceback

# Setup logging per catturare TUTTO
logging.basicConfig(
    level=logging.DEBUG,
    format='[LAUNCHER] %(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)

# Assicura che i path siano corretti
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger.info("🚀 OVERTOP BASSANO V14 LAUNCHER STARTING...")
logger.info(f"📁 Working directory: {os.getcwd()}")
logger.info(f"📦 Python path: {sys.path[:3]}")
logger.info(f"📦 Files in current directory: {os.listdir('.')}")

try:
    # Step 1: Importa il bot con MEMORIA
    logger.info("⏳ Attempting to import OVERTOP_BASSANO_V14...")
    try:
        from OVERTOP_BASSANO_V14 import OvertopBassanoV14Memoria
        logger.info("✅ Successfully imported OvertopBassanoV14Memoria")
    except ImportError as ie:
        logger.error(f"❌ IMPORT ERROR: {ie}")
        logger.error(f"⚠️ Make sure OVERTOP_BASSANO_V14.py exists in: {os.getcwd()}")
        logger.error(f"⚠️ Current files: {os.listdir('.')}")
        raise
    except Exception as e:
        logger.error(f"❌ UNEXPECTED ERROR during import: {e}")
        logger.error(traceback.format_exc())
        raise
    
    # Step 2: Crea istanza
    logger.info("⏳ Creating bot instance...")
    try:
        bot = OvertopBassanoV14Memoria()
        logger.info("✅ Bot instance created successfully")
    except Exception as e:
        logger.error(f"❌ ERROR creating bot instance: {e}")
        logger.error(traceback.format_exc())
        raise
    
    # Step 3: Verifica che il bot abbia il metodo run()
    logger.info("⏳ Checking bot methods...")
    if not hasattr(bot, 'run'):
        logger.error("❌ Bot does not have a 'run()' method!")
        logger.error(f"⚠️ Bot methods: {dir(bot)}")
        raise AttributeError("Bot missing run() method")
    logger.info("✅ Bot has run() method")
    
    # Step 4: Avvia il bot
    logger.info("▶️ Starting bot.run()...")
    logger.info("🟢 Bot is now LIVE")
    logger.info("💓 Sending heartbeat to Mission Control every 30s")
    logger.info("📊 WR should rise from 0.4% towards 50%+")
    logger.info("🧠 Memory system active: matrimoni/separazioni/divorzi")
    
    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("⚠️ Bot interrupted by keyboard")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ ERROR during bot.run(): {e}")
        logger.error(traceback.format_exc())
        raise
        
except ImportError as e:
    logger.error(f"[LAUNCHER] ❌ IMPORT ERROR: {e}")
    logger.error(f"[LAUNCHER] ⚠️ Full traceback: {traceback.format_exc()}")
    sys.exit(1)
except AttributeError as e:
    logger.error(f"[LAUNCHER] ❌ ATTRIBUTE ERROR: {e}")
    logger.error(f"[LAUNCHER] ⚠️ Full traceback: {traceback.format_exc()}")
    sys.exit(1)
except Exception as e:
    logger.error(f"[LAUNCHER] ❌ CRITICAL ERROR: {e}")
    logger.error(f"[LAUNCHER] ⚠️ Full traceback: {traceback.format_exc()}")
    sys.exit(1)

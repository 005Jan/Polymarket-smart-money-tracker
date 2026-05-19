"""
config.py — Parámetros centrales del bot.
Las wallets NO van aquí — se cargan dinámicamente desde data/smart_wallets.json.
"""
import os
from pathlib import Path

# ── Rutas de datos (volumen Docker: ./data:/app/data) ─────────────────────────
DATA_DIR     = Path(os.getenv("DATA_DIR", "./data"))
WALLETS_FILE = DATA_DIR / "smart_wallets.json"
CSV_FILE     = DATA_DIR / "simulacion_trading.csv"
LOG_FILE     = DATA_DIR / "bot.log"

# ── Hot-reload de wallets ─────────────────────────────────────────────────────
WALLETS_RELOAD_MINUTES   = 60     # releer smart_wallets.json cada hora

# ── Parámetros del radar ──────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS    = 60     # ciclo principal del bot
REQUEST_DELAY_SECONDS    = 2.5    # pausa entre peticiones HTTP
ACTIVITY_LIMIT           = 100    # últimas N transacciones por wallet

# ── Lógica de consenso ────────────────────────────────────────────────────────
CONSENSUS_WINDOW_MINUTES = 90     # ventana temporal para detectar consenso
MIN_WALLETS_FOR_SIGNAL   = 2      # mínimo de wallets que deben coincidir
# Wallets "premium" amb track record extraordinari (>$1M PnL i >95% WR):
# una sola d'aquestes ja és senyal suficient (sense necessitat de consens)
PREMIUM_WALLET_MIN_PNL       = 1_000_000.0
PREMIUM_WALLET_MIN_WIN_RATE  = 0.95

# ── Filtro EV (anti-exit-liquidity) ──────────────────────────────────────────
# Si el precio actual supera en más de X% el precio medio del sniper → ABORTAR
EV_MAX_SLIPPAGE_PCT      = 0.15   # 15% máximo (ampliat per captar més mercats actius)

# ── Sistema de cuarentena ─────────────────────────────────────────────────────
QUARANTINE_STRIKES       = 3      # pérdidas consecutivas antes de cuarentena
QUARANTINE_DAYS          = 7      # días de cuarentena

# ── Paper trading ─────────────────────────────────────────────────────────────
VIRTUAL_BANKROLL         = 1000.0
KELLY_FRACTION           = 0.25   # 1/4 Kelly — conservador
MAX_POSITION_PCT         = 0.10   # máximo 10% banca por posición
MIN_POSITION_USD         = 5.0

# ── Gestión de posiciones ─────────────────────────────────────────────────────
TAKE_PROFIT_PCT          = 0.20
STOP_LOSS_PCT            = 0.40
MAX_HOLD_HOURS           = 36   # reduït de 72→36 (els EXPIRY perden molt)

# ── Filtres de diversificació i resolució (millores 2026-05-12) ──────────────
# Màxim de posicions amb la mateixa "pregunta" base (anti-concentració)
# Ex: limita a 3 posicions en variants de "Russia-Ukraine Ceasefire by ..."
MAX_POSITIONS_PER_QUESTION = 3
# Només entrar en mercats que es resolen en menys de X dies
MAX_DAYS_TO_RESOLUTION     = 90   # ampliat de 30→90 (les smart wallets aposten a 60-90d)
# Liquiditat mínima del mercat (USD) per evitar mercats il·líquids
MIN_MARKET_LIQUIDITY_USD   = 5000.0

# ── sniper_catcher.py (modo daemon) ──────────────────────────────────────────
SNIPER_RUN_INTERVAL_HOURS = 24    # buscar nuevas wallets 1 vez/día

# Filtros de calidad para sniper_catcher
F1_MIN_AVG_TRADE_SIZE    = 50.0
F2_MIN_TRADES            = 20
F2_MAX_TRADES            = 500
F3_MIN_WIN_RATE          = 0.65
F4_MIN_PNL               = 2000.0
TRADE_WINDOW_DAYS        = 90

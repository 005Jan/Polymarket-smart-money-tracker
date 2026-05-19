"""
Bot de Copytrading Polymarket — Fase 1 (PAPER TRADING)
=======================================================
SEGURIDAD: Este bot NO interactúa con claves privadas ni blockchains.
Solo monitoriza, detecta y registra en un CSV local.

Flujo por ciclo:
  1. WalletRegistry recarga smart_wallets.json si toca (cada hora)
  2. Radar: escanea actividad reciente de wallets activas
  3. Lógica: detecta consenso (2+ wallets en mismo mercado/outcome)
  4. Filtro EV: aborta si el precio ya se movió demasiado (anti-exit-liquidity)
  5. Simulator: registra apertura si pasa todos los filtros
  6. Monitor: comprueba take-profit/stop-loss; notifica al RiskManager
"""
import json
import sys
import signal
import time
import logging
from datetime import datetime, timezone

from config import (
    POLL_INTERVAL_SECONDS, LOG_FILE, REQUEST_DELAY_SECONDS,
    MAX_POSITIONS_PER_QUESTION, MAX_DAYS_TO_RESOLUTION, MIN_MARKET_LIQUIDITY_USD,
)
from radar        import WalletRegistry, scan_all_wallets, fetch_recent_sells
from logic        import RiskManager, detect_consensus
from clob_client  import (
    get_midpoint, get_spread, get_market_info,
    get_token_id_for_outcome, estimate_slippage,
)
from simulator    import PaperSimulator

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ── Estado global ─────────────────────────────────────────────────────────────
registry = WalletRegistry()
risk     = RiskManager()
sim      = PaperSimulator()
running  = True


def _handle_stop(sig, frame):
    global running
    log.info("Señal de parada recibida. Cerrando el bot...")
    running = False


signal.signal(signal.SIGINT,  _handle_stop)
signal.signal(signal.SIGTERM, _handle_stop)


# ── Procesamiento de señales ──────────────────────────────────────────────────

def process_new_signal(sig_data: dict):
    """
    Para una señal de consenso detectada:
      1. Obtiene info del mercado (pregunta, token IDs)
      2. Filtro EV: compara midpoint actual vs precio sniper
      3. Calcula tamaño Kelly y slippage
      4. Registra apertura en el CSV
    """
    market_id = sig_data["market_id"]
    outcome   = sig_data["outcome"]

    if sim.already_traded(market_id, outcome):
        return

    log.info(
        f"Nueva señal: {sig_data['n_wallets']} wallets en "
        f"{market_id[:12]}… → {outcome}"
    )

    # Información del mercado desde Gamma API
    info      = get_market_info(market_id)
    question  = info.get("question") or f"Mercado {market_id[:16]}"
    liquidity = float(info.get("liquidity") or 0)

    # ── Filtre de liquiditat mínima ──────────────────────────────────────────
    if liquidity < MIN_MARKET_LIQUIDITY_USD:
        log.info(
            f"  ✗ LIQ_LOW: liquiditat ${liquidity:.0f} < "
            f"${MIN_MARKET_LIQUIDITY_USD:.0f} — ignorant"
        )
        return

    # ── Filtre per diversificació (max posicions per pregunta) ───────────────
    n_open = sim.count_positions_by_question(question)
    if n_open >= MAX_POSITIONS_PER_QUESTION:
        log.info(
            f"  ✗ DIVERSIF: ja {n_open} posicions amb la mateixa pregunta "
            f"'{question[:40]}...' — saltant"
        )
        return

    # ── Filtre per data de resolució (només mercats que es resolen aviat) ────
    from datetime import datetime, timezone, timedelta
    end_date_raw = info.get("endDate") or info.get("end_date_iso") or info.get("endDateIso")
    if end_date_raw:
        try:
            end_dt = datetime.fromisoformat(str(end_date_raw).replace("Z", "+00:00"))
            days_to_end = (end_dt - datetime.now(timezone.utc)).days
            if days_to_end > MAX_DAYS_TO_RESOLUTION:
                log.info(
                    f"  ✗ TOO_FAR: mercat es resol en {days_to_end}d "
                    f"> límit {MAX_DAYS_TO_RESOLUTION}d — saltant"
                )
                return
            if days_to_end < 0:
                log.info(f"  ✗ EXPIRED: mercat ja vençut — saltant")
                return
        except Exception:
            pass  # si no podem parsejar, continuem (no bloquegem per error)

    # Token ID del outcome concreto (YES o NO)
    token_id = get_token_id_for_outcome(info, outcome)

    # Precio actual del CLOB
    midpoint = get_midpoint(token_id) if token_id else None

    if not midpoint:
        # Fallback: precio desde Gamma API
        prices_raw = info.get("outcomePrices")
        if prices_raw:
            try:
                prices   = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                idx      = 0 if outcome == "YES" else 1
                midpoint = float(prices[idx]) if len(prices) > idx else None
            except Exception:
                pass

    if not midpoint or midpoint <= 0.02 or midpoint >= 0.98:
        log.warning(
            f"Precio inválido ({midpoint}) para {market_id[:12]}… — ignorando señal"
        )
        return

    # ── Filtro EV (anti-exit-liquidity) ──────────────────────────────────────
    passes, reason = risk.passes_ev_filter(
        sig_data.get("avg_entry_price"), midpoint
    )
    log.info(f"  EV filter: {reason}")
    if not passes:
        return

    ev_drift = (
        (midpoint - sig_data["avg_entry_price"]) / sig_data["avg_entry_price"]
        if sig_data.get("avg_entry_price") and sig_data["avg_entry_price"] > 0
        else 0.0
    )

    # Sizing Kelly y slippage
    spread   = get_spread(token_id) if token_id else 0.0
    size_usd = risk.kelly_size(midpoint, sig_data["n_wallets"])
    slippage = estimate_slippage(spread, size_usd, liquidity)

    time.sleep(REQUEST_DELAY_SECONDS)

    sim.open_position(
        signal       = sig_data,
        midpoint     = midpoint,
        slippage     = slippage,
        size_usd     = size_usd,
        question     = question,
        token_id     = token_id,
        ev_drift_pct = ev_drift,
    )


def check_smart_wallet_exits():
    """
    Comprova si les smart wallets que ens van generar el senyal de compra
    han sortit del mercat. Si sí, tanquem la posició (seguim les seves vendes).

    Aquesta és la lògica clau per a mercats lents: en comptes d'esperar TP/SL,
    sortim quan els experts surten.
    """
    if not sim.open_positions:
        return

    # Recopilar totes les wallets úniques de les posicions obertes
    wallets_to_check = set()
    for pos in sim.open_positions:
        wallets_to_check.update(pos.wallets)

    if not wallets_to_check:
        return

    log.info(f"  Comprovant sortides de {len(wallets_to_check)} wallets úniques...")
    exits = fetch_recent_sells(list(wallets_to_check), hours=12)

    if not exits:
        log.info("  Cap smart wallet ha venut recentment.")
        return

    closed_count = 0
    for pos in list(sim.open_positions):
        key = (pos.market_id, pos.outcome)
        if key not in exits:
            continue
        # Quines de les nostres wallets signal·ladores han venut?
        sellers = [w for w in pos.wallets if w in exits[key]]
        if not sellers:
            continue

        # Almenys una smart wallet originadora ha sortit → tanquem
        current = get_midpoint(pos.token_id) if pos.token_id else None
        if current is None:
            continue

        log.info(
            f"  ⚡ WALLET_EXIT [{pos.position_id}]: {len(sellers)} wallet(s) "
            f"originadores han venut — tancant posició"
        )
        pnl = sim.close_manually(pos, current, "WALLET_EXIT")
        for wallet in pos.wallets:
            risk.record_close(wallet, pnl)
        closed_count += 1
        time.sleep(1.0)

    if closed_count:
        log.info(f"  Total posicions tancades per WALLET_EXIT: {closed_count}")


def monitor_open_positions():
    """
    Comprueba take-profit, stop-loss y expiración.
    Tras cada cierre notifica al RiskManager con el PnL por wallet.
    """
    if not sim.open_positions:
        return

    log.info(f"Monitorizando {len(sim.open_positions)} posición/es abiertas...")

    for pos in list(sim.open_positions):
        if not pos.token_id:
            continue

        current = get_midpoint(pos.token_id)
        if current is None:
            log.debug(f"Sin precio para posición {pos.position_id}")
            continue

        gain_pct = (current - pos.entry_price) / pos.entry_price * 100
        log.info(
            f"  [{pos.position_id}] {pos.outcome} @ {current:.4f} "
            f"(entrada {pos.entry_price:.4f}) | {gain_pct:+.1f}%"
        )

        closed, pnl = sim.check_exits(pos, current)
        if closed:
            # Notificar a RiskManager por cada wallet que generó la señal
            for wallet in pos.wallets:
                risk.record_close(wallet, pnl)

        time.sleep(1.5)


# ── Loop principal ────────────────────────────────────────────────────────────

def run():
    log.info("=" * 62)
    log.info("  BOT COPYTRADING POLYMARKET — FASE 1 (PAPER TRADING)")
    log.info("  MODO: Solo lectura. Sin transacciones blockchain.")
    log.info("=" * 62)
    log.info(registry.summary())

    if not registry.get_wallets():
        log.error(
            "No hay wallets en smart_wallets.json. "
            "Ejecuta sniper_catcher.py primero."
        )
        sys.exit(1)

    cycle = 0
    while running:
        cycle += 1
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        log.info(f"\n{'─'*50}")
        log.info(f"Ciclo #{cycle} — {now_str}")
        log.info(registry.summary())

        # Cuarentenas activas
        quarantined = risk.quarantine_status()
        if quarantined:
            log.info(
                f"Cuarentenas activas: "
                + ", ".join(
                    f"{q['wallet'][:10]}… ({q['days_left']}d)"
                    for q in quarantined
                )
            )

        # ── 1. Radar ────────────────────────────────────────────────────────
        trades = scan_all_wallets(registry)
        log.info(f"Radar: {len(trades)} trades recientes")

        # ── 2. Consenso ─────────────────────────────────────────────────────
        # Construïm un mapa wallet → metadata per detectar premium wallets
        wallet_meta_map = {}
        for w in registry.get_wallets():
            wallet_meta_map[w.lower()] = registry.get_metadata(w)

        signals = detect_consensus(
            trades,
            risk_manager=risk,
            wallet_metadata=wallet_meta_map,
        )
        n_premium = sum(1 for s in signals if s.get("is_premium"))
        log.info(
            f"Consenso: {len(signals)} señal/es detectadas "
            f"({n_premium} premium-individual)"
        )

        # ── 3. Procesar señales nuevas ───────────────────────────────────────
        for sig in signals:
            try:
                process_new_signal(sig)
            except Exception as e:
                log.error(f"Error procesando señal {sig['market_id']}: {e}")

        # ── 4a. Seguiment de vendes de smart wallets (exit copying) ──────────
        try:
            check_smart_wallet_exits()
        except Exception as e:
            log.error(f"Error comprovant sortides de smart wallets: {e}")

        # ── 4b. Monitorizar posiciones abiertas (TP/SL/expiry) ───────────────
        try:
            monitor_open_positions()
        except Exception as e:
            log.error(f"Error monitorizando posiciones: {e}")

        # ── Estado ──────────────────────────────────────────────────────────
        log.info(sim.status_summary())
        log.info(f"Próximo ciclo en {POLL_INTERVAL_SECONDS}s...")
        time.sleep(POLL_INTERVAL_SECONDS)

    log.info("Bot detenido correctamente.")


if __name__ == "__main__":
    run()

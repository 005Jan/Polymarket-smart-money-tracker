"""
logic.py — Lógica cuantitativa y gestión de riesgo.

Contiene:
  - RiskManager : cuarentena por strikes, filtro EV, Kelly fraccional
  - detect_consensus : detecta señales de copia desde trades de wallets
"""
from datetime import datetime, timezone, timedelta
from config import (
    CONSENSUS_WINDOW_MINUTES, MIN_WALLETS_FOR_SIGNAL,
    VIRTUAL_BANKROLL, KELLY_FRACTION, MAX_POSITION_PCT, MIN_POSITION_USD,
    QUARANTINE_STRIKES, QUARANTINE_DAYS, EV_MAX_SLIPPAGE_PCT,
)


# ── RiskManager ───────────────────────────────────────────────────────────────

class RiskManager:
    """
    Gestiona el riesgo a nivel de wallet:
      - Sistema de cuarentena: 3 pérdidas consecutivas → 7 días de blacklist
      - Filtro EV: abortar si el precio se alejó demasiado del precio del sniper
      - Kelly fraccional: sizing conservador del capital
    """

    def __init__(self):
        # Pérdidas consecutivas por wallet (se resetea al ganar)
        self._consecutive_losses: dict[str, int] = {}
        # Wallets en cuarentena: address → datetime de expiración
        self._quarantine: dict[str, datetime] = {}

    # ── Cuarentena ────────────────────────────────────────────────────────────

    def record_close(self, wallet: str, pnl: float) -> None:
        """
        Registra el cierre de una posición para una wallet.
        Llamar desde main.py después de cada cierre.
        """
        if pnl < 0:
            n = self._consecutive_losses.get(wallet, 0) + 1
            self._consecutive_losses[wallet] = n
            if n >= QUARANTINE_STRIKES:
                until = datetime.now(timezone.utc) + timedelta(days=QUARANTINE_DAYS)
                self._quarantine[wallet] = until
                self._consecutive_losses[wallet] = 0
                print(
                    f"[risk] ⚠ CUARENTENA: {wallet[:10]}… "
                    f"({QUARANTINE_STRIKES} pérdidas consecutivas) "
                    f"hasta {until.strftime('%Y-%m-%d')}"
                )
        else:
            # Victoria resetea el contador de racha negativa
            self._consecutive_losses[wallet] = 0

    def is_quarantined(self, wallet: str) -> bool:
        """Devuelve True si la wallet está temporalmente bloqueada."""
        until = self._quarantine.get(wallet)
        if until is None:
            return False
        if datetime.now(timezone.utc) < until:
            return True
        # Cuarentena expirada — limpiar
        del self._quarantine[wallet]
        print(f"[risk] Cuarentena expirada para {wallet[:10]}…")
        return False

    def filter_wallets(self, wallets: list[str]) -> list[str]:
        """Filtra wallets en cuarentena de una lista de candidatos."""
        active   = [w for w in wallets if not self.is_quarantined(w)]
        blocked  = len(wallets) - len(active)
        if blocked:
            print(f"[risk] {blocked} wallet(s) en cuarentena — excluidas del consenso")
        return active

    def quarantine_status(self) -> list[dict]:
        """Estado actual de cuarentenas (para logging)."""
        now = datetime.now(timezone.utc)
        return [
            {"wallet": w, "until": str(u), "days_left": max(0, (u - now).days)}
            for w, u in self._quarantine.items()
            if u > now
        ]

    # ── Filtro EV ─────────────────────────────────────────────────────────────

    def passes_ev_filter(
        self,
        sniper_avg_price: float | None,
        current_midpoint: float,
    ) -> tuple[bool, str]:
        """
        Compara el precio actual con el precio al que compró el sniper.
        Si el mercado ya se movió más de EV_MAX_SLIPPAGE_PCT, abortamos.

        Protege contra ser "exit liquidity": evitamos comprar cuando el sniper
        ya movió el precio y quiere vender.

        Returns: (passes: bool, reason: str)
        """
        if not sniper_avg_price or sniper_avg_price <= 0:
            return True, "sin precio sniper — permitido"

        drift = (current_midpoint - sniper_avg_price) / sniper_avg_price

        if drift > EV_MAX_SLIPPAGE_PCT:
            return False, (
                f"EV ABORTADO: precio actual {current_midpoint:.4f} vs "
                f"sniper {sniper_avg_price:.4f} "
                f"(+{drift*100:.1f}% > límite {EV_MAX_SLIPPAGE_PCT*100:.0f}%)"
            )
        # NOTA: Hem eliminat el rebuig per drift negatiu fort.
        # Si el preu ha caigut respecte al que va pagar el sniper, estem
        # ENTRANT MÉS BARAT que ell — això és un avantatge, no un risc.
        # Només el drift positiu (estar pagant més que ell) és perillós.

        if drift < -0.60:
            # Drift molt negatiu (>60%): el mercat probablement ja sap alguna
            # cosa que el sniper no sabia. Aquí sí val la pena evitar-ho.
            return False, (
                f"EV ABORTADO: caiguda extrema {drift*100:.1f}% (>60%)"
            )

        return True, f"EV OK: drift {drift*100:+.1f}% (entrada {'descompte' if drift < 0 else 'premium'})"

    # ── Kelly fraccional ──────────────────────────────────────────────────────

    def kelly_size(
        self,
        price: float,
        n_wallets: int,
        bankroll: float = VIRTUAL_BANKROLL,
    ) -> float:
        """
        Kelly fraccional conservador (KELLY_FRACTION = 0.25 por defecto).

        p_implied  = precio de mercado (probabilidad del mercado)
        p_sniper   = p_implied + edge estimado por consenso de wallets
        b          = odds netos (ganancia por unidad si gana)
        f*         = (p*b - q) / b   → fracción Kelly pura
        size       = f* × KELLY_FRACTION × bankroll   → fracción conservadora

        Con KELLY_FRACTION = 0.25 el drawdown máximo esperado es ~4× menor
        que con Kelly puro, a costa de ~25% menos de retorno esperado.
        """
        if price <= 0.02 or price >= 0.98:
            return 0.0

        # Cada wallet adicional confirma un ~3% de edge extra
        edge       = 0.03 * (n_wallets - 1)
        p_sniper   = min(price + edge, 0.90)
        b          = (1 / price) - 1
        q          = 1 - p_sniper
        f_star     = (p_sniper * b - q) / b

        if f_star <= 0:
            return 0.0

        size = f_star * KELLY_FRACTION * bankroll
        cap  = bankroll * MAX_POSITION_PCT
        return round(max(MIN_POSITION_USD, min(size, cap)), 2)


# ── Detección de consenso ─────────────────────────────────────────────────────

def _parse_timestamp(raw) -> datetime | None:
    if raw is None:
        return None
    try:
        if isinstance(raw, (int, float)):
            ts = raw / 1000 if raw > 1e10 else raw
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None


def _is_buy(trade: dict) -> bool:
    """
    Campo oficial Data API: 'side' (BUY / SELL).
    'type' indica el tipo de actividad (TRADE, REDEEM...), no la dirección.
    """
    side = str(trade.get("side") or "").upper()
    if side == "BUY":
        return True
    if side == "SELL":
        return False
    # Fallback por compatibilidad
    return "BUY" in str(trade.get("type") or "").upper()


def detect_consensus(
    trades: list[dict],
    risk_manager: RiskManager | None = None,
) -> list[dict]:
    """
    Agrupa BUYs recientes por (conditionId, outcome).
    Filtra wallets en cuarentena si se pasa un RiskManager.

    Señal generada cuando MIN_WALLETS_FOR_SIGNAL o más wallets distintas
    compraron el mismo outcome en la ventana temporal configurada.

    Campos oficiales Data API usados:
      conditionId → ID del mercado
      outcome     → "Yes" / "No" (nombre del resultado)
      side        → "BUY" / "SELL"
      timestamp   → unix timestamp (int64)
      usdcSize    → valor USD de la operación
      price       → precio de ejecución
    """
    cutoff      = datetime.now(timezone.utc) - timedelta(minutes=CONSENSUS_WINDOW_MINUTES)
    recent_buys = []

    for t in trades:
        if not _is_buy(t):
            continue
        ts = _parse_timestamp(t.get("timestamp"))
        if ts and ts >= cutoff:
            recent_buys.append({**t, "_ts": ts})

    # Agrupar por (conditionId, outcome normalizado)
    groups: dict[tuple, list] = {}
    for t in recent_buys:
        market_id = (t.get("conditionId") or t.get("market") or "").strip()
        outcome   = str(t.get("outcome") or "").strip().upper()
        if outcome in ("YES", "SI", "SÍ", "TRUE"):
            outcome = "YES"
        elif outcome in ("NO", "FALSE"):
            outcome = "NO"
        else:
            continue

        if not market_id:
            continue

        wallet = t.get("wallet", "")
        # Aplicar filtro de cuarentena a nivel de wallet individual
        if risk_manager and risk_manager.is_quarantined(wallet):
            continue

        key = (market_id, outcome)
        if key not in groups:
            groups[key] = []
        # Cada wallet cuenta una sola vez por (mercado, outcome)
        if not any(x.get("wallet") == wallet for x in groups[key]):
            groups[key].append(t)

    signals = []
    for (market_id, outcome), group in groups.items():
        if len(group) < MIN_WALLETS_FOR_SIGNAL:
            continue

        prices = [float(t["price"]) for t in group if t.get("price") and float(t["price"]) > 0]
        wallets = [t.get("wallet", "") for t in group]

        signals.append({
            "market_id":       market_id,
            "outcome":         outcome,
            "wallets":         wallets,
            "n_wallets":       len(wallets),
            "latest_trade":    max(t["_ts"] for t in group),
            "avg_entry_price": round(sum(prices) / len(prices), 4) if prices else None,
        })

    signals.sort(key=lambda s: s["n_wallets"], reverse=True)
    return signals

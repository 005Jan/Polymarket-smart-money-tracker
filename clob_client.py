"""
Cliente CLOB de Polymarket.
Obtiene precios midpoint, spread del order book y estima slippage.
"""
import httpx
import json

CLOB_API  = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

HEADERS = {"User-Agent": "PolyResearch-Bot/1.0 (paper-trading only)"}


def get_midpoint(token_id: str) -> float | None:
    """
    Precio midpoint actual del order book.
    Campo oficial: mid_price (string). Endpoint: GET /midpoint?token_id=...
    """
    try:
        r = httpx.get(
            f"{CLOB_API}/midpoint",
            params={"token_id": token_id},
            headers=HEADERS,
            timeout=8.0,
        )
        if r.status_code == 200:
            data = r.json()
            # Campo oficial: mid_price. Fallback a "mid" por si cambia.
            val = data.get("mid_price") or data.get("mid")
            return float(val) if val else None
    except Exception as e:
        print(f"[clob] midpoint error para {token_id[:12]}…: {e}")
    return None


def get_spread(token_id: str) -> float:
    """Spread bid-ask actual. Componente principal del slippage."""
    try:
        r = httpx.get(
            f"{CLOB_API}/spread",
            params={"token_id": token_id},
            headers=HEADERS,
            timeout=8.0,
        )
        if r.status_code == 200:
            return float(r.json().get("spread", 0))
    except Exception:
        pass
    return 0.0


def get_book_depth(token_id: str) -> dict:
    """Order book completo — bids y asks con sus tamaños."""
    try:
        r = httpx.get(
            f"{CLOB_API}/book",
            params={"token_id": token_id},
            headers=HEADERS,
            timeout=8.0,
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


def get_market_info(condition_id: str) -> dict:
    """Información del mercado desde Gamma API (pregunta, precios, liquidez)."""
    try:
        r = httpx.get(
            f"{GAMMA_API}/markets",
            params={"conditionId": condition_id},
            headers=HEADERS,
            timeout=10.0,
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                return data[0]
            if isinstance(data, dict):
                return data
    except Exception as e:
        print(f"[clob] market_info error: {e}")
    return {}


def get_token_id_for_outcome(market_info: dict, outcome: str) -> str:
    """
    Extrae el token_id correcto según el outcome (YES o NO).
    Prueba múltiples formatos que usa la API de Polymarket.
    """
    # Intento 1: array de tokens con campo outcome
    tokens = market_info.get("tokens") or []
    for t in tokens:
        if not isinstance(t, dict):
            continue
        t_outcome = str(t.get("outcome", "")).upper()
        if outcome == "YES" and t_outcome in ("YES", "SÍ", "SI", "TRUE"):
            return t.get("token_id") or t.get("tokenId") or ""
        if outcome == "NO" and t_outcome in ("NO", "FALSE"):
            return t.get("token_id") or t.get("tokenId") or ""

    # Intento 2: clobTokenIds (índice 0 = YES, índice 1 = NO)
    clob_ids = market_info.get("clobTokenIds") or []
    if isinstance(clob_ids, str):
        try:
            clob_ids = json.loads(clob_ids)
        except Exception:
            clob_ids = []
    idx = 0 if outcome == "YES" else 1
    if isinstance(clob_ids, list) and len(clob_ids) > idx:
        return clob_ids[idx]

    return ""


def estimate_slippage(spread: float, size_usd: float, liquidity: float) -> float:
    """
    Estima slippage total como fracción del precio.

    Componentes:
      - Half-spread: coste de cruzar el spread bid-ask (inevitable en market orders)
      - Market impact: efecto de nuestro tamaño sobre el precio (mayor orden = más impacto)

    Fórmula simplificada usada en market microstructure:
      slippage = spread/2 + (size / liquidity) * 0.5
    """
    half_spread   = spread / 2
    market_impact = (size_usd / max(liquidity, 1)) * 0.5
    return round(min(half_spread + market_impact, 0.05), 4)  # cap 5%

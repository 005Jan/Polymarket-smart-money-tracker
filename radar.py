"""
radar.py — Extraccion de datos y gestion dinamica de wallets.

WalletRegistry: carga smart_wallets.json y lo recarga automaticamente cada hora.
scan_all_wallets: consulta la actividad reciente de cada wallet activa.
"""
import json
import time
import httpx
from datetime import datetime, timezone
from pathlib import Path

from config import (
    WALLETS_FILE, WALLETS_RELOAD_MINUTES,
    REQUEST_DELAY_SECONDS, ACTIVITY_LIMIT,
)

DATA_API = "https://data-api.polymarket.com"
HEADERS  = {"User-Agent": "PolyResearch-Bot/1.0 (paper-trading only)"}


# ── WalletRegistry ────────────────────────────────────────────────────────────

class WalletRegistry:
    """
    Mantiene la lista de wallets objetivo actualizada dinamicamente.
    Lee smart_wallets.json y lo recarga cada WALLETS_RELOAD_MINUTES minutos.
    El bot NO necesita reiniciarse cuando sniper_catcher actualiza el JSON.
    """

    def __init__(self):
        self._wallets:   list[str]       = []
        self._metadata:  list[dict]      = []
        self._last_load: datetime | None = None
        self._load()

    def get_wallets(self) -> list[str]:
        """Devuelve wallets activas, recargando si toca."""
        if self._should_reload():
            self._load()
        return list(self._wallets)

    def get_metadata(self, address: str) -> dict:
        """Devuelve los metadatos del sniper (win_rate, pnl...) para una wallet."""
        for m in self._metadata:
            if m.get("address", "").lower() == address.lower():
                return m
        return {}

    def _should_reload(self) -> bool:
        if self._last_load is None:
            return True
        elapsed = (datetime.now(timezone.utc) - self._last_load).total_seconds()
        return elapsed >= WALLETS_RELOAD_MINUTES * 60

    def _load(self) -> None:
        path = Path(WALLETS_FILE)
        if not path.exists():
            if not self._wallets:
                print(f"[wallets] {WALLETS_FILE} no encontrado. Ejecuta sniper_catcher.py primero.")
            return
        try:
            data    = json.loads(path.read_text(encoding="utf-8"))
            snipers = data.get("snipers", [])
            wallets = [s["address"] for s in snipers if s.get("address", "").startswith("0x")]
            if not wallets:
                print(f"[wallets] {WALLETS_FILE} sin snipers validos.")
                return
            prev             = len(self._wallets)
            self._wallets    = wallets
            self._metadata   = snipers
            self._last_load  = datetime.now(timezone.utc)
            print(f"[wallets] {len(wallets)} wallets cargadas (antes: {prev})")
        except Exception as e:
            print(f"[wallets] Error leyendo {WALLETS_FILE}: {e}")

    def summary(self) -> str:
        if not self._wallets:
            return "Sin wallets cargadas."
        ts = self._last_load.strftime("%H:%M:%S") if self._last_load else "?"
        return f"{len(self._wallets)} wallets activas (reload: {ts} UTC)"


# ── Fetch de actividad ────────────────────────────────────────────────────────

def fetch_wallet_activity(address: str, client: httpx.Client | None = None) -> list[dict]:
    """
    Endpoint oficial: GET /activity?user={address}&side=BUY&limit={n}
    Campos: conditionId, outcome, side, price, usdcSize, timestamp
    """
    params = {
        "user":          address,
        "side":          "BUY",
        "limit":         ACTIVITY_LIMIT,
        "sortBy":        "TIMESTAMP",
        "sortDirection": "DESC",
    }

    def _do(c: httpx.Client) -> list[dict]:
        try:
            r = c.get(f"{DATA_API}/activity", params=params, headers=HEADERS, timeout=10.0)
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, list) else data.get("data", [])
            if r.status_code == 429:
                print("[radar] Rate limit 429 — esperando 30s...")
                time.sleep(30)
        except httpx.TimeoutException:
            print(f"[radar] Timeout {address[:10]}...")
        except Exception as e:
            print(f"[radar] Error {address[:10]}...: {e}")
        return []

    if client:
        return _do(client)
    with httpx.Client(timeout=10.0) as c:
        return _do(c)


def fetch_recent_sells(wallets: list[str], hours: int = 6) -> dict[tuple, list[str]]:
    """
    Per a una llista de wallets, retorna les vendes recents (últimes N hores).
    Útil per detectar quan els smart wallets surten d'un mercat en el qual nosaltres
    estem dins, i així replicar la sortida.

    Retorna: dict {(market_id, outcome_normalitzat): [wallet, ...]}
      - market_id = conditionId
      - outcome_normalitzat = "YES" o "NO"
    """
    from datetime import datetime, timezone, timedelta
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp()
    exits: dict[tuple, list[str]] = {}

    with httpx.Client(timeout=10.0) as client:
        for wallet in wallets:
            params = {
                "user":          wallet,
                "side":          "SELL",
                "limit":         50,
                "sortBy":        "TIMESTAMP",
                "sortDirection": "DESC",
            }
            try:
                r = client.get(f"{DATA_API}/activity", params=params, headers=HEADERS, timeout=10.0)
                if r.status_code != 200:
                    continue
                data   = r.json()
                trades = data if isinstance(data, list) else data.get("data", [])
                for t in trades:
                    ts = t.get("timestamp", 0)
                    if isinstance(ts, (int, float)) and ts > 1e10:
                        ts = ts / 1000
                    if ts < cutoff_ts:
                        break  # ordenat per timestamp DESC
                    market_id = (t.get("conditionId") or t.get("market") or "").strip()
                    outcome_raw = str(t.get("outcome") or "").strip().upper()
                    if outcome_raw in ("YES", "SI", "SÍ", "TRUE"):
                        outcome = "YES"
                    elif outcome_raw in ("NO", "FALSE"):
                        outcome = "NO"
                    else:
                        continue
                    if not market_id:
                        continue
                    key = (market_id, outcome)
                    if key not in exits:
                        exits[key] = []
                    if wallet not in exits[key]:
                        exits[key].append(wallet)
            except Exception as e:
                print(f"[radar/sells] Error {wallet[:10]}...: {e}")
            time.sleep(REQUEST_DELAY_SECONDS)

    return exits


def scan_all_wallets(registry: WalletRegistry) -> list[dict]:
    """
    Escanea todas las wallets activas con rate limiting.
    Anade campo 'wallet' a cada trade para identificar su origen.
    """
    wallets = registry.get_wallets()
    if not wallets:
        return []

    all_trades: list[dict] = []
    with httpx.Client(timeout=10.0) as client:
        for i, wallet in enumerate(wallets, 1):
            short = wallet[:6] + "..." + wallet[-4:]
            print(f"[radar] ({i}/{len(wallets)}) {short}", end=" ", flush=True)
            trades = fetch_wallet_activity(wallet, client)
            for t in trades:
                t["wallet"] = wallet
            all_trades.extend(trades)
            print(f"-> {len(trades)} trades")
            if i < len(wallets):
                time.sleep(REQUEST_DELAY_SECONDS)

    return all_trades

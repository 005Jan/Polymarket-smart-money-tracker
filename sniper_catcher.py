"""
sniper_catcher.py
=================
Extrae y filtra "Sniper Wallets" de Polymarket aplicando 4 filtros de calidad.

Filtros aplicados:
  F1 - Anti-Lotto:        Average Trade Size > $50 (usdcSize)
  F2 - Anti-Market Maker: 20 <= trades (90 días) <= 500
  F3 - Sniper Rate:       Win Rate > 65%  (cashPnl > 0 en mercados cerrados)
  F4 - Rentabilidad Real: PnL neto > $2,000 USD

Endpoints oficiales (verificados con docs.polymarket.com):
  Data API  → https://data-api.polymarket.com/v1/leaderboard
  Data API  → https://data-api.polymarket.com/activity
  Data API  → https://data-api.polymarket.com/positions
  CLOB API  → https://clob.polymarket.com/midpoint   (campo: mid_price)

Output: smart_wallets.json
"""

import httpx
import json
import time
import sys
from datetime import datetime, timezone, timedelta

from config import WALLETS_FILE, SNIPER_RUN_INTERVAL_HOURS

# ── Configuración ─────────────────────────────────────────────────────────────

OUTPUT_FILE = WALLETS_FILE

DATA_API = "https://data-api.polymarket.com"

HEADERS = {
    "User-Agent": "PolyResearch/1.0 (academic data analysis)",
    "Accept":     "application/json",
}

# Filtros de calidad
F1_MIN_AVG_TRADE_SIZE = 50.0    # USD  — Anti-Lotto
F2_MIN_TRADES         = 20      # operaciones en 90 días
F2_MAX_TRADES         = 500     # operaciones en 90 días — Anti-Market Maker
F3_MIN_WIN_RATE       = 0.65    # 65% mercados ganados
F4_MIN_PNL            = 2000.0  # USD beneficio neto

TRADE_WINDOW_DAYS     = 90      # ventana temporal para F1 y F2

# Rate limiting
SLEEP_BETWEEN_WALLETS = 2.0     # segundos entre wallets
SLEEP_BETWEEN_PAGES   = 1.0     # segundos entre páginas del leaderboard

# Leaderboard: máx 50 por petición según docs oficiales
LEADERBOARD_LIMIT     = 50
LEADERBOARD_PAGES     = 20      # 20 páginas × 50 = 1.000 wallets escaneadas

ACTIVITY_LIMIT        = 500     # máx trades a pedir por wallet

# ── HTTP helper ───────────────────────────────────────────────────────────────

def get(client: httpx.Client, url: str, params: dict = None, retries: int = 3) -> list | dict | None:
    """GET con reintentos y manejo de rate limit 429."""
    for attempt in range(retries):
        try:
            r = client.get(url, params=params, headers=HEADERS, timeout=15.0)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = 30 * (attempt + 1)
                print(f"  [!] Rate limit (429) — esperando {wait}s...")
                time.sleep(wait)
                continue
            if r.status_code == 404:
                return None
            print(f"  [!] HTTP {r.status_code} en {url}")
            return None
        except httpx.TimeoutException:
            print(f"  [!] Timeout (intento {attempt + 1}/{retries})")
            time.sleep(5)
        except Exception as e:
            print(f"  [!] Error: {e}")
            return None
    return None


def short(addr: str) -> str:
    return addr[:6] + "…" + addr[-4:] if len(addr) > 10 else addr


def parse_ts(raw) -> datetime | None:
    if raw is None:
        return None
    try:
        if isinstance(raw, (int, float)):
            ts = raw / 1000 if raw > 1e10 else raw
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


# ── Fase 1: Leaderboard ───────────────────────────────────────────────────────

def fetch_leaderboard_candidates(client: httpx.Client) -> list[dict]:
    """
    Endpoint oficial: GET /v1/leaderboard
    Params documentados: timePeriod=ALL, orderBy=PNL, limit≤50, offset
    Campos de respuesta: proxyWallet, pnl, vol, rank, userName
    """
    candidates = []
    print(f"\n{'='*60}")
    print(f"FASE 1 — Leaderboard ({LEADERBOARD_PAGES} páginas × {LEADERBOARD_LIMIT})")
    print(f"{'='*60}")

    for page in range(LEADERBOARD_PAGES):
        offset = page * LEADERBOARD_LIMIT
        print(f"  Página {page + 1}/{LEADERBOARD_PAGES} (offset={offset})...", end=" ")

        data = get(client, f"{DATA_API}/v1/leaderboard", params={
            "timePeriod": "ALL",
            "orderBy":    "PNL",
            "limit":      LEADERBOARD_LIMIT,
            "offset":     offset,
        })

        if not data:
            print("sin datos — fin.")
            break

        entries = data if isinstance(data, list) else data.get("data", [])
        if not entries:
            print("vacío — fin.")
            break

        new = 0
        for entry in entries:
            # Campo oficial de dirección: proxyWallet
            addr = (entry.get("proxyWallet") or entry.get("address") or "").strip()
            if not addr.startswith("0x") or len(addr) != 42:
                continue

            # Campo oficial de PnL: pnl
            pnl = float(entry.get("pnl") or 0)
            if pnl < F4_MIN_PNL:
                continue   # pre-filtre F4 rápido

            # Campo oficial de volumen: vol (NO "volume")
            candidates.append({
                "address":  addr,
                "username": entry.get("userName") or entry.get("pseudonym") or "",
                "pnl_lb":   pnl,
                "vol_lb":   float(entry.get("vol") or 0),
                "rank":     int(entry.get("rank") or 0),
            })
            new += 1

        print(f"{len(entries)} entradas | {new} candidatos nuevos (total: {len(candidates)})")

        if len(entries) < LEADERBOARD_LIMIT:
            break   # última página

        time.sleep(SLEEP_BETWEEN_PAGES)

    print(f"\n  Candidatos pre-filtrados (PnL>${F4_MIN_PNL:,.0f}): {len(candidates)}")
    return candidates


# ── Fase 2: Actividad reciente ────────────────────────────────────────────────

def fetch_trade_stats(client: httpx.Client, address: str) -> dict | None:
    """
    Endpoint oficial: GET /activity
    Params: user, side=BUY, limit≤500, start/end (unix timestamp)
    Campos clave: side (BUY/SELL), usdcSize (USD), size (tokens), price, timestamp
    """
    cutoff    = datetime.now(timezone.utc) - timedelta(days=TRADE_WINDOW_DAYS)
    cutoff_ts = int(cutoff.timestamp())

    data = get(client, f"{DATA_API}/activity", params={
        "user":  address,
        "side":  "BUY",          # solo compras (campo oficial: side)
        "limit": ACTIVITY_LIMIT,
        "start": cutoff_ts,      # unix timestamp — últimos 90 días
        "sortBy": "TIMESTAMP",
        "sortDirection": "DESC",
    })

    if not data:
        return None

    trades = data if isinstance(data, list) else data.get("data", [])
    if not trades:
        return {"trades_in_window": 0, "avg_trade_size_usd": 0, "total_volume_usd": 0}

    sizes = []
    for t in trades:
        # usdcSize es el campo oficial en USD (size = cantidad de tokens, no USD)
        usd = float(t.get("usdcSize") or 0)
        if usd <= 0:
            # fallback: estimar desde tokens × precio
            price  = float(t.get("price") or 0)
            tokens = float(t.get("size") or 0)
            usd    = tokens * price if price > 0 else 0
        if usd > 0:
            sizes.append(usd)

    if not sizes:
        return {"trades_in_window": 0, "avg_trade_size_usd": 0, "total_volume_usd": 0}

    return {
        "trades_in_window":   len(sizes),
        "avg_trade_size_usd": round(sum(sizes) / len(sizes), 2),
        "total_volume_usd":   round(sum(sizes), 2),
    }


# ── Fase 3: Win Rate desde posiciones ────────────────────────────────────────

def fetch_win_rate(client: httpx.Client, address: str) -> dict | None:
    """
    Endpoint oficial: GET /positions
    Params: user, sizeThreshold, limit≤500, sortBy=CASHPNL

    No existe un campo 'resolved' — se usa endDate para saber si cerró
    y cashPnl > 0 para saber si ganó.

    Campos oficiales: cashPnl, percentPnl, realizedPnl, endDate,
                      redeemable, curPrice, avgPrice, currentValue, initialValue
    """
    now = datetime.now(timezone.utc)
    data = get(client, f"{DATA_API}/positions", params={
        "user":           address,
        "sizeThreshold":  "0.01",
        "limit":          500,
        "sortBy":         "CASHPNL",
        "sortDirection":  "DESC",
    })

    if not data:
        return None

    positions = data if isinstance(data, list) else data.get("data", [])
    if not positions:
        return {"win_rate": 0.0, "markets_won": 0, "markets_total": 0, "cash_pnl_total": 0.0}

    wins      = 0
    total     = 0
    pnl_total = 0.0

    for pos in positions:
        # Determinar si el mercado ya cerró usando endDate
        end_raw = pos.get("endDate") or pos.get("end_date")
        if end_raw:
            end_dt = parse_ts(end_raw)
            market_closed = end_dt is not None and end_dt < now
        else:
            # Si no hay endDate, usar redeemable como proxy de "resuelto"
            market_closed = bool(pos.get("redeemable"))

        if not market_closed:
            continue   # posición abierta — no cuenta para win rate

        # cashPnl es el campo oficial de P&L en cash
        cash_pnl = float(
            pos.get("cashPnl") or
            pos.get("realizedPnl") or
            pos.get("cash_pnl") or 0
        )
        # fallback: currentValue - initialValue
        if cash_pnl == 0:
            curr = float(pos.get("currentValue") or 0)
            init = float(pos.get("initialValue") or 0)
            cash_pnl = curr - init

        total     += 1
        pnl_total += cash_pnl
        if cash_pnl > 0:
            wins += 1

    if total == 0:
        return {"win_rate": 0.0, "markets_won": 0, "markets_total": 0, "cash_pnl_total": 0.0}

    return {
        "win_rate":       round(wins / total, 4),
        "markets_won":    wins,
        "markets_total":  total,
        "cash_pnl_total": round(pnl_total, 2),
    }


# ── Filtros ───────────────────────────────────────────────────────────────────

def apply_filters(w: dict) -> tuple[bool, list[str]]:
    fails = []

    avg = w.get("avg_trade_size_usd", 0)
    if avg < F1_MIN_AVG_TRADE_SIZE:
        fails.append(f"F1-LOTTO avg=${avg:.0f} < ${F1_MIN_AVG_TRADE_SIZE}")

    n = w.get("trades_in_window", 0)
    if n < F2_MIN_TRADES:
        fails.append(f"F2-LOW trades={n} < {F2_MIN_TRADES}")
    elif n > F2_MAX_TRADES:
        fails.append(f"F2-MM trades={n} > {F2_MAX_TRADES}")

    wr = w.get("win_rate", 0)
    if wr < F3_MIN_WIN_RATE:
        fails.append(f"F3-WR {wr*100:.1f}% < {F3_MIN_WIN_RATE*100:.0f}%")

    pnl = w.get("pnl_lb", 0)
    if pnl < F4_MIN_PNL:
        fails.append(f"F4-PNL ${pnl:.0f} < ${F4_MIN_PNL:,.0f}")

    return (len(fails) == 0, fails)


def _score(w: dict) -> float:
    wr         = w.get("win_rate", 0)
    pnl        = w.get("pnl_lb", 0)
    volume     = max(w.get("total_volume_usd", 1), 1)
    efficiency = pnl / volume

    return round(
        (wr * 40) +
        (min(pnl / 10_000, 5) * 10) +
        (min(efficiency * 100, 5) * 6),
        4,
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*60)
    print("  SNIPER CATCHER — Polymarket Smart Money Filter")
    print(f"  F1 AvgTrade>${F1_MIN_AVG_TRADE_SIZE} | F2 {F2_MIN_TRADES}-{F2_MAX_TRADES} trades")
    print(f"  F3 WR>{F3_MIN_WIN_RATE*100:.0f}% | F4 PnL>${F4_MIN_PNL:,.0f}")
    print("="*60)

    filter_stats = {"f1": 0, "f2": 0, "f3": 0, "f4": 0}
    results      = []

    with httpx.Client(timeout=15.0) as client:

        # FASE 1
        candidates = fetch_leaderboard_candidates(client)
        if not candidates:
            print("\n[ERROR] Leaderboard vacío. Verifica la API.")
            sys.exit(1)

        # FASE 2 + 3
        print(f"\n{'='*60}")
        print(f"FASE 2+3 — Analizando {len(candidates)} candidatos")
        print(f"{'='*60}")

        for i, cand in enumerate(candidates, 1):
            addr = cand["address"]
            print(f"\n[{i:>3}/{len(candidates)}] {short(addr)} "
                  f"(rank #{cand['rank']}) | PnL=${cand['pnl_lb']:,.0f}")

            print("  → actividad 90d...", end=" ")
            trade_stats = fetch_trade_stats(client, addr)
            time.sleep(SLEEP_BETWEEN_WALLETS)

            if not trade_stats:
                print("sin datos")
                continue
            print(f"trades={trade_stats['trades_in_window']} avg=${trade_stats['avg_trade_size_usd']:.0f}")

            print("  → posiciones (win rate)...", end=" ")
            wr_stats = fetch_win_rate(client, addr)
            time.sleep(SLEEP_BETWEEN_WALLETS)

            if not wr_stats:
                print("sin datos")
                continue
            print(f"WR={wr_stats['win_rate']*100:.1f}% ({wr_stats['markets_won']}/{wr_stats['markets_total']})")

            wallet = {**cand, **trade_stats, **wr_stats}
            passed, fails = apply_filters(wallet)

            if passed:
                sc = _score(wallet)
                print(f"  ✓ SNIPER | score={sc:.2f}")
                results.append({
                    "address":            addr,
                    "username":           cand.get("username", ""),
                    "polymarket_url":     f"https://polymarket.com/profile/{addr}",
                    "rank_leaderboard":   cand["rank"],
                    "pnl_usd":            cand["pnl_lb"],
                    "win_rate":           wr_stats["win_rate"],
                    "win_rate_pct":       f"{wr_stats['win_rate']*100:.1f}%",
                    "markets_won":        wr_stats["markets_won"],
                    "markets_total":      wr_stats["markets_total"],
                    "cash_pnl_closed":    wr_stats["cash_pnl_total"],
                    "trades_90d":         trade_stats["trades_in_window"],
                    "avg_trade_size_usd": trade_stats["avg_trade_size_usd"],
                    "total_volume_usd":   trade_stats["total_volume_usd"],
                    "score":              sc,
                })
            else:
                for f in fails:
                    key = f[1:3].lower()
                    if key in filter_stats:
                        filter_stats[key] += 1
                print(f"  ✗ {' | '.join(fails)}")

    results.sort(key=lambda w: w["score"], reverse=True)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filters_applied": {
            "F1_min_avg_trade_usd": F1_MIN_AVG_TRADE_SIZE,
            "F2_min_trades_90d":    F2_MIN_TRADES,
            "F2_max_trades_90d":    F2_MAX_TRADES,
            "F3_min_win_rate":      F3_MIN_WIN_RATE,
            "F4_min_pnl_usd":       F4_MIN_PNL,
        },
        "summary": {
            "candidates_scanned":    len(candidates),
            "snipers_found":         len(results),
            "discarded_f1_lotto":    filter_stats["f1"],
            "discarded_f2_mm":       filter_stats["f2"],
            "discarded_f3_win_rate": filter_stats["f3"],
            "discarded_f4_pnl":      filter_stats["f4"],
        },
        "snipers": results,
    }

    OUTPUT_FILE.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\n{'='*60}")
    print(f"  RESULTADO")
    print(f"  Candidatos escaneados : {len(candidates)}")
    print(f"  Snipers encontrados   : {len(results)}")
    print(f"  Descartados F1-Lotto  : {filter_stats['f1']}")
    print(f"  Descartados F2-MM     : {filter_stats['f2']}")
    print(f"  Descartados F3-WR     : {filter_stats['f3']}")
    print(f"  Descartados F4-PnL    : {filter_stats['f4']}")
    print(f"\n  Guardado en: {OUTPUT_FILE.resolve()}")
    print(f"{'='*60}\n")

    if results:
        print("TOP 5 SNIPERS:")
        for w in results[:5]:
            print(f"  {w['address']}  WR={w['win_rate_pct']}  "
                  f"PnL=${w['pnl_usd']:,.0f}  score={w['score']:.2f}")
        print()


if __name__ == "__main__":
    daemon = "--daemon" in sys.argv

    if daemon:
        interval_s = SNIPER_RUN_INTERVAL_HOURS * 3600
        print(
            f"[sniper] Modo daemon — ejecutando cada {SNIPER_RUN_INTERVAL_HOURS}h. "
            "Ctrl-C para parar.",
            flush=True,
        )
        while True:
            try:
                main()
            except Exception as e:
                print(f"[sniper] Error en ejecución: {e}", flush=True)
            next_run = datetime.now(timezone.utc).timestamp() + interval_s
            print(
                f"[sniper] Próxima ejecución: "
                f"{datetime.fromtimestamp(next_run, tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
                flush=True,
            )
            time.sleep(interval_s)
    else:
        main()

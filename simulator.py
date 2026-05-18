"""
simulator.py — Paper Trading Log.
Totes les ordres s'escriuen en un CSV local. Mai interactua amb la blockchain.
check_exits retorna (tancat, pnl) perque main.py pugui notificar el RiskManager.
"""
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from config import CSV_FILE, TAKE_PROFIT_PCT, STOP_LOSS_PCT, MAX_HOLD_HOURS

FIELDNAMES = [
    "Tipo",
    "Timestamp_Deteccion",
    "ID_Mercado",
    "Pregunta",
    "Opcion_Comprada",
    "Carteras_Detectadas",
    "Precio_Wallets_Avg",
    "EV_Drift_Pct",               # drift precio actual vs precio sniper
    "Precio_Mercado_Simulado",
    "Slippage_Estimado_Pct",
    "Precio_Final_Simulado",
    "Tamano_Posicion_USD",
    "Acciones_Simuladas",
    "Timestamp_Cierre",
    "Precio_Cierre",
    "PnL_USD",
    "PnL_Pct",
    "Motivo_Cierre",
]


@dataclass
class Position:
    position_id:       str
    market_id:         str
    question:          str
    outcome:           str
    entry_price:       float
    raw_price:         float
    slippage:          float
    size_usd:          float
    shares:            float
    n_wallets:         int
    wallets:           list[str]        # lista de wallets que generaron la senal
    wallets_avg_price: float | None
    ev_drift_pct:      float            # drift EV registrado en apertura
    token_id:          str
    opened_at:         datetime


class PaperSimulator:
    def __init__(self):
        self._ensure_data_dir()
        self._init_csv()
        self.open_positions: list[Position] = []
        self._seen_markets:  set[tuple]     = set()

    def already_traded(self, market_id: str, outcome: str) -> bool:
        return (market_id, outcome) in self._seen_markets

    def count_positions_by_question(self, question: str) -> int:
        """
        Compta quantes posicions obertes hi ha amb una pregunta semblant.
        Compara els primers 40 caràcters per agrupar variants del mateix mercat
        (ex: "Russia-Ukraine Ceasefire by May 17" vs "by May 24").
        """
        prefix = question[:40].lower().strip()
        if not prefix:
            return 0
        return sum(
            1 for p in self.open_positions
            if p.question[:40].lower().strip() == prefix
        )

    def _ensure_data_dir(self):
        Path(CSV_FILE).parent.mkdir(parents=True, exist_ok=True)

    def _init_csv(self):
        if not Path(CSV_FILE).exists():
            with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()
            print(f"[sim] CSV creado: {CSV_FILE}")

    def open_position(
        self,
        signal:       dict,
        midpoint:     float,
        slippage:     float,
        size_usd:     float,
        question:     str,
        token_id:     str   = "",
        ev_drift_pct: float = 0.0,
    ) -> "Position | None":
        """Registra apertura. Devuelve Position o None si ya existe."""
        key = (signal["market_id"], signal["outcome"])
        if key in self._seen_markets:
            return None

        final_price = round(midpoint * (1 + slippage), 4)
        shares      = round(size_usd / final_price, 4)
        now         = datetime.now(timezone.utc)
        pid         = f"{signal['market_id'][:8]}_{now.strftime('%H%M%S')}"

        pos = Position(
            position_id=pid,
            market_id=signal["market_id"],
            question=question,
            outcome=signal["outcome"],
            entry_price=final_price,
            raw_price=midpoint,
            slippage=slippage,
            size_usd=size_usd,
            shares=shares,
            n_wallets=signal["n_wallets"],
            wallets=signal.get("wallets", []),
            wallets_avg_price=signal.get("avg_entry_price"),
            ev_drift_pct=ev_drift_pct,
            token_id=token_id,
            opened_at=now,
        )
        self.open_positions.append(pos)
        self._seen_markets.add(key)

        wallets_str = ", ".join(w[:8] + "..." for w in pos.wallets)
        self._write_row({
            "Tipo":                    "APERTURA",
            "Timestamp_Deteccion":     now.isoformat(),
            "ID_Mercado":              signal["market_id"],
            "Pregunta":                question[:120],
            "Opcion_Comprada":         signal["outcome"],
            "Carteras_Detectadas":     f"{signal['n_wallets']} ({wallets_str})",
            "Precio_Wallets_Avg":      signal.get("avg_entry_price", ""),
            "EV_Drift_Pct":            f"{ev_drift_pct*100:+.1f}%",
            "Precio_Mercado_Simulado": midpoint,
            "Slippage_Estimado_Pct":   f"{slippage * 100:.2f}%",
            "Precio_Final_Simulado":   final_price,
            "Tamano_Posicion_USD":     size_usd,
            "Acciones_Simuladas":      shares,
            "Timestamp_Cierre":        "",
            "Precio_Cierre":           "",
            "PnL_USD":                 "",
            "PnL_Pct":                 "",
            "Motivo_Cierre":           "",
        })

        print(
            f"[sim] APERTURA #{pid} | {signal['outcome']} "
            f"'{question[:45]}...' @ {final_price:.4f} | "
            f"${size_usd:.2f} | {signal['n_wallets']} wallets | "
            f"drift {ev_drift_pct*100:+.1f}%",
            flush=True,
        )
        return pos

    def check_exits(self, pos: Position, current_price: float) -> tuple[bool, float]:
        """
        Comprueba condiciones de salida.
        Devuelve (cerrado: bool, pnl: float) para que main.py
        pueda notificar al RiskManager con el resultado.
        """
        gain       = (current_price - pos.entry_price) / pos.entry_price
        hours_open = (datetime.now(timezone.utc) - pos.opened_at).total_seconds() / 3600

        if gain >= TAKE_PROFIT_PCT:
            return True, self._close(pos, current_price, "TAKE_PROFIT")
        if gain <= -STOP_LOSS_PCT:
            return True, self._close(pos, current_price, "STOP_LOSS")
        if hours_open >= MAX_HOLD_HOURS:
            return True, self._close(pos, current_price, "EXPIRY")

        return False, 0.0

    def close_manually(self, pos: Position, exit_price: float, reason: str) -> float:
        """
        Tancament forçat des de fora (ex: quan una smart wallet ha sortit del mercat).
        Wrapper públic per a _close().
        """
        return self._close(pos, exit_price, reason)

    def _close(self, pos: Position, exit_price: float, reason: str) -> float:
        """Registra cierre en CSV y devuelve el PnL."""
        proceeds = pos.shares * exit_price
        pnl      = proceeds - pos.size_usd
        pnl_pct  = pnl / pos.size_usd * 100
        now      = datetime.now(timezone.utc)

        if pos in self.open_positions:
            self.open_positions.remove(pos)

        self._write_row({
            "Tipo":                    f"CIERRE_{reason}",
            "Timestamp_Deteccion":     pos.opened_at.isoformat(),
            "ID_Mercado":              pos.market_id,
            "Pregunta":                pos.question[:120],
            "Opcion_Comprada":         pos.outcome,
            "Carteras_Detectadas":     pos.n_wallets,
            "Precio_Wallets_Avg":      pos.wallets_avg_price or "",
            "EV_Drift_Pct":            f"{pos.ev_drift_pct*100:+.1f}%",
            "Precio_Mercado_Simulado": pos.raw_price,
            "Slippage_Estimado_Pct":   f"{pos.slippage * 100:.2f}%",
            "Precio_Final_Simulado":   pos.entry_price,
            "Tamano_Posicion_USD":     pos.size_usd,
            "Acciones_Simuladas":      pos.shares,
            "Timestamp_Cierre":        now.isoformat(),
            "Precio_Cierre":           exit_price,
            "PnL_USD":                 round(pnl, 2),
            "PnL_Pct":                 f"{pnl_pct:+.1f}%",
            "Motivo_Cierre":           reason,
        })

        sign = "+" if pnl >= 0 else ""
        print(
            f"[sim] CIERRE {reason} #{pos.position_id} | "
            f"{sign}${pnl:.2f} ({sign}{pnl_pct:.1f}%) | "
            f"entrada {pos.entry_price:.4f} salida {exit_price:.4f}",
            flush=True,
        )
        return pnl

    def _write_row(self, row: dict):
        with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=FIELDNAMES).writerow(row)

    def status_summary(self) -> str:
        if not self.open_positions:
            return "Sin posiciones abiertas."
        lines = [f"  Posiciones abiertas: {len(self.open_positions)}"]
        for p in self.open_positions:
            h = (datetime.now(timezone.utc) - p.opened_at).total_seconds() / 3600
            lines.append(
                f"    [{p.position_id}] {p.outcome} '{p.question[:38]}...' "
                f"@ {p.entry_price:.4f} | ${p.size_usd:.2f} | {h:.1f}h"
            )
        return "\n".join(lines)

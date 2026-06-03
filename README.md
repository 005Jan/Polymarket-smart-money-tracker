# Polymarket Smart Money Tracker

> ⚠️ **PROJECT ARCHIVED (2026-05-28)** — After 10 days of paper trading, the strategy showed no edge (9.5% win rate, -2.47% ROI). See [FINDINGS.md](FINDINGS.md) for the full analysis.

A quantitative **copy-trading bot** for Polymarket that identifies profitable "smart wallets" from the leaderboard and replicates their trades in **paper trading mode** (simulation only, no real funds).

## Architecture

Docker-based system with two services:

| Service | Function |
|---|---|
| `sniper-catcher` | Every 24h, scans the Polymarket leaderboard and identifies wallets with high PnL and win-rate |
| `copytrader` | Every 60s, monitors wallet activity and replicates trades when consensus is detected |

## Quick Start

```bash
# 1. Generate initial smart wallets list (~20 min)
sudo docker compose up --build sniper-catcher

# 2. Start both services in background
sudo docker compose up -d
```

## Project Structure

```
copytrader/
├── main.py                 # Core copytrader loop
├── sniper_catcher.py       # Smart wallet discovery
├── radar.py                # Wallet activity scanner
├── logic.py                # Consensus detection & risk management
├── simulator.py            # Paper trading log
├── clob_client.py          # Polymarket API client (CLOB + Gamma)
├── config.py               # Central configuration
├── Dockerfile
├── docker-compose.yml
└── data/                   # Simulation CSVs, logs, wallets (gitignored)
```

## Configuration

All parameters in `config.py`:

| Parameter | Value | Description |
|---|---|---|
| `MIN_WALLETS_FOR_SIGNAL` | 2 | Minimum wallet consensus to trigger a trade |
| `EV_MAX_SLIPPAGE_PCT` | 15% | Maximum acceptable premium vs sniper entry price |
| `MAX_DAYS_TO_RESOLUTION` | 90 | Only enter markets resolving within X days |
| `MAX_POSITIONS_PER_QUESTION` | 3 | Anti-concentration per question |
| `MIN_MARKET_LIQUIDITY_USD` | 5000 | Skip illiquid markets |
| `TAKE_PROFIT_PCT` | 20% | Close on gain |
| `STOP_LOSS_PCT` | 40% | Close on loss |
| `MAX_HOLD_HOURS` | 72 | Close on expiry |
| `KELLY_FRACTION` | 0.25 | Conservative sizing (1/4 Kelly) |

## Risk Management

- **EV filter (anti exit-liquidity):** rejects entries where price has already moved too far from the sniper's entry
- **Wallet quarantine:** a wallet with 3 consecutive losses is excluded for 7 days
- **Exit tracking:** closes positions when smart wallets sell (`WALLET_EXIT` signal)
- **Diversification:** max 3 positions per market question

## Outputs

| File | Content |
|---|---|
| `data/simulacion_trading.csv` | Full trade log (entries and exits) |
| `data/bot.log` | Detailed bot log |
| `data/smart_wallets.json` | Current tracked wallets |

## Security

This bot **does not interact with private keys or blockchain**. It only reads public Polymarket data and logs simulated operations.

## Results

After 10 days of live paper trading:
- **Win rate:** 9.5%
- **ROI:** -2.47%
- **Conclusion:** Copy-trading on prediction markets does not provide a reliable edge when wallets are already widely tracked. See [FINDINGS.md](FINDINGS.md) for detailed analysis.

## License

MIT

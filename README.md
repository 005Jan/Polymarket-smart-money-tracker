# CopyTrader Polymarket

> ⚠️ **PROJECTE ARXIVAT (2026-05-28)** — Després de 10 dies de paper trading,
> l'estratègia no va mostrar edge (9.5% win rate, -2.47% ROI). Veure
> [FINDINGS.md](FINDINGS.md) per a l'anàlisi complet.


Bot quantitatiu de **copy-trading** per a Polymarket que segueix "smart wallets" (carteres rentables) per replicar les seves operacions en mode **paper trading** (simulació, sense diners reals).

## 🏗️ Arquitectura

Sistema basat en Docker amb dos serveis:

| Servei | Funció |
|---|---|
| `sniper-catcher` | Cada 24h, escaneja el leaderboard de Polymarket i identifica wallets amb alt PnL i win-rate per copiar |
| `copytrader` | Cada 60s, monitoritza l'activitat de les wallets i replica compres/vendes quan hi ha consens |

## 🚀 Inici ràpid

```bash
# 1. Generar la llista inicial de smart wallets (triga ~20 min)
sudo docker compose up --build sniper-catcher

# 2. Iniciar tots dos serveis en segon pla
sudo docker compose up -d
```

## 📂 Estructura del projecte

```
copytrader/
├── main.py                 # Loop principal del bot copytrader
├── sniper_catcher.py       # Cerca de smart wallets
├── radar.py                # Escaneig d'activitat de wallets
├── logic.py                # Detecció de consens i risc
├── simulator.py            # Paper trading log
├── clob_client.py          # Client API Polymarket (CLOB + Gamma)
├── config.py               # Paràmetres centrals
├── Dockerfile
├── docker-compose.yml
└── data/                   # CSV de simulació, logs, wallets (no commitejat)
```

## ⚙️ Configuració principal

Tots els paràmetres a `config.py`:

| Paràmetre | Valor | Descripció |
|---|---|---|
| `MIN_WALLETS_FOR_SIGNAL` | 2 | Mínim de wallets que han de coincidir per generar senyal |
| `EV_MAX_SLIPPAGE_PCT` | 15% | Màxim de premium acceptable vs preu del sniper |
| `MAX_DAYS_TO_RESOLUTION` | 90 | Només entrar en mercats que es resolen en menys de X dies |
| `MAX_POSITIONS_PER_QUESTION` | 3 | Anti-concentració per pregunta |
| `MIN_MARKET_LIQUIDITY_USD` | 5000 | Saltar mercats il·líquids |
| `TAKE_PROFIT_PCT` | 20% | Tancament per guany |
| `STOP_LOSS_PCT` | 40% | Tancament per pèrdua |
| `MAX_HOLD_HOURS` | 72 | Tancament per expiració |
| `KELLY_FRACTION` | 0.25 | Sizing conservador (1/4 Kelly) |

## 🛡️ Sistemes de risc

- **Filtre EV (anti exit-liquidity):** rebutja entrades on el preu ja ha pujat massa respecte al que va pagar el sniper
- **Quarantena de wallets:** una wallet amb 3 pèrdues consecutives queda exclosa 7 dies
- **Seguiment de vendes:** tanquem quan les smart wallets venen (`WALLET_EXIT`)
- **Diversificació:** màxim 3 posicions per pregunta del mercat

## 📊 Outputs

- `data/simulacion_trading.csv` — Log de totes les operacions (apertures i tancaments)
- `data/bot.log` — Log detallat del bot
- `data/smart_wallets.json` — Llista actual de wallets a seguir

## ⚠️ Seguretat

Aquest bot **NO interactua amb claus privades ni blockchain**. Només llegeix dades públiques de Polymarket i registra les operacions simulades en un CSV local.

## 📈 Filtres aplicats per detectar smart wallets

| Filtre | Valor | Descripció |
|---|---|---|
| F1 | Avg trade > $50 | Evitar wallets "lotto" amb tiquets minúsculs |
| F2 | 20-500 trades en 90d | Evitar market makers i wallets inactives |
| F3 | Win rate > 65% | Només wallets consistents |
| F4 | PnL > $2,000 | Volum mínim de guany |

---

*Projecte personal de paper trading. Cap diner real implicat.*

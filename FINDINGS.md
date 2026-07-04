# Findings — Fase 1 (Paper Trading)

**Període d'experiment:** 2026-05-15 → 2026-05-25 (10.6 dies)
**Estat:** ⛔ **Aturat el 2026-05-28** després d'optimitzar i no trobar edge.

## 📊 Resultats finals

| Mètrica | Valor |
|---|---|
| Apertures totals | 37 |
| Tancaments | 21 |
| **Win rate** | **9.5%** (2/21) |
| **PnL** | **-$8.38** sobre $339 invertit |
| **ROI** | **-2.47%** |
| Mercats únics | 1 (Rihanna Album) |

### Per motiu de tancament

| Motiu | Ops | PnL | WR |
|---|---|---|---|
| WALLET_EXIT | 15 | -$2.16 | 13% |
| EXPIRY (36h) | 6 | -$6.22 | 0% |

## 🧠 Conclusions

### 1. L'estratègia copy-trading naïf no té edge en aquest context
Després de 21 ops, el win rate (9.5%) és **per sota d'una estratègia random** (50%).
La tendència empitjorava amb el temps, no millorava.

### 2. Hipòtesi sobre el "100% win rate" de les smart wallets
Les top wallets de Polymarket leaderboard mostren WR del 100% **perquè el càlcul
es fa només sobre posicions resoltes**. Aguanten les seves perdedores en mercats
GTA-VI que no es resolen en mesos/anys. Nosaltres sí tanquem (per EXPIRY), per
això perdem mentre ells "guanyen" sobre paper.

### 3. Concentració massiva del senyal
Tots els 37 senyals que van passar els filtres van ser del mateix mercat
("New Rihanna Album before GTA VI?"). Les pròpies wallets es concentren allà.
No vam aconseguir diversificació real.

### 4. WALLET_EXIT funciona, EXPIRY és el problema
El sistema de "sortir quan elles surten" estava pràcticament breakeven.
Els grans pèrdues vénen de posicions que es podreixen 36h sense que ningú
les tanqui (-$6.22 en 6 ops vs -$2.16 en 15 ops).

## 🔧 Optimitzacions provades (no van canviar el resultat)

1. EV slippage 7% → 15%
2. Eliminar bloqueig de drift negatiu (-20% → -60%)
3. Filtre TOO_FAR 30d → 90d
4. Senyals premium individuals per top wallets ($1M PnL + 95% WR)
5. MAX_HOLD_HOURS 72h → 36h
6. MIN_MARKET_LIQUIDITY $5000 → $1000
7. Diversificació per pregunta (3 max)
8. Sistema WALLET_EXIT (tancar quan smart wallets venen)

**Conclusió metodològica:** Cap canvi de paràmetre va fer pivotar la mètrica
de fons. Continuar tocant paràmetres hauria estat overfitting.

## ✅ Què s'ha guanyat

✅ Validat el procés de paper trading **abans d'arriscar diners reals** ($0 perduts)
✅ Codi modular i ben estructurat (reutilitzable per altres estratègies)
✅ Pipeline complet sniper → radar → consens → simulator
✅ Aprenentatge: el copy-trading directe no és tan fàcil com sona

## 🚀 Possibles propers passos (si algun dia es retoma)

1. **Analitzar dades a posteriori** — quins TIPUS de mercats guanyen les wallets quan es resolen
2. **Estratègia diferent** — mean reversion, anàlisi LLM, market making
3. **Diferent font de senyal** — orderbook activity, transaccions onchain en temps real
4. **Estudiar el slippage realista** — els nostres filtres potser desviaven el resultat

---

*Arxivat: 2026-05-28*

# Instalación en Raspberry Pi

## Estructura de archivos

```
/home/pi/copytrader/
├── config.py
├── radar.py
├── logic.py
├── clob_client.py
├── simulator.py
├── main.py
├── requirements.txt
├── venv/
└── simulacion_trading.csv   ← se crea automáticamente
```

---

## Paso 1 — Copiar archivos a la Raspberry Pi

Desde tu ordenador (con la Pi en la misma red o via VPN):

```bash
# Opción A: SCP
scp -r CopyTrader/ pi@IP_DE_TU_PI:/home/pi/copytrader

# Opción B: Copiar a USB y mover desde la Pi
```

---

## Paso 2 — Instalar Python y dependencias en la Pi

```bash
ssh pi@IP_DE_TU_PI

# Actualizar sistema
sudo apt update && sudo apt upgrade -y

# Instalar Python 3.11+ (Raspberry Pi OS Bookworm ya lo incluye)
sudo apt install python3 python3-pip python3-venv -y

# Crear entorno virtual
cd /home/pi/copytrader
python3 -m venv venv

# Instalar dependencias
venv/bin/pip install -r requirements.txt
```

---

## Paso 3 — Configurar wallets objetivo

Edita `config.py` y añade las direcciones de las wallets:

```bash
nano config.py
```

Para encontrar wallets de Smart Money en Polymarket:
- Ve a https://polymarket.com/leaderboard
- Copia las direcciones 0x de los mejores traders

```python
TARGET_WALLETS = [
    "0xDIRECCION_WALLET_1",
    "0xDIRECCION_WALLET_2",
    # ...
]
```

---

## Paso 4a — Ejecutar con tmux (más simple, para probar)

```bash
# Instalar tmux si no está
sudo apt install tmux -y

# Crear sesión
tmux new-session -s copytrader

# Dentro de tmux, ejecutar el bot
cd /home/pi/copytrader
venv/bin/python main.py

# Para desconectarte sin parar el bot: Ctrl+B, luego D
# Para volver a la sesión: tmux attach -t copytrader
# Para ver el log: tail -f bot.log
```

---

## Paso 4b — Ejecutar como servicio systemd (para producción 24/7)

```bash
# Copiar el archivo de servicio
sudo cp copytrader.service /etc/systemd/system/

# Recargar systemd
sudo systemctl daemon-reload

# Habilitar para arranque automático
sudo systemctl enable copytrader

# Iniciar el servicio
sudo systemctl start copytrader

# Comprobar estado
sudo systemctl status copytrader

# Ver logs en tiempo real
sudo journalctl -u copytrader -f
```

Para parar el bot:
```bash
sudo systemctl stop copytrader
```

---

## Paso 5 — Ver resultados

El bot crea `simulacion_trading.csv` automáticamente.

Ver en tiempo real:
```bash
# Ver últimas líneas del CSV
tail -20 simulacion_trading.csv

# Ver el log del bot
tail -f bot.log

# Abrir CSV formateado (si tienes csvkit instalado)
pip install csvkit
csvlook simulacion_trading.csv | less -S
```

---

## Verificar que el bot funciona

En los primeros 2-3 ciclos deberías ver en los logs:

```
[radar] (1/5) Escaneando 0xAbCd…
[radar]   → 23 trades encontrados
[logic] Consenso: 1 señal detectada
[sim] APERTURA #abc123_143022 | YES 'Will X happen?' @ 0.3812 | $18.50 | 2 wallets
```

Si ves "No hay wallets válidas", edita config.py con direcciones reales.

---

## Consejos

- La VPN debe estar activa antes de iniciar el bot (Polymarket puede bloquear IPs sin VPN)
- El bot respeta los rate limits con pausas de 2.5s entre peticiones
- Deja correr al menos 1 semana antes de evaluar resultados
- Revisa el CSV periódicamente para ver si las señales tienen edge real

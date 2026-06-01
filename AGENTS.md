# AGENTS.md

## Project Overview

This is a Raspberry Pi IoT sensor monitoring & LoRa remote LED control project.

It contains:

- `backend/`：FastAPI backend (Python 3.11)
  - Reads YL-40 PCF8591 sensor data via I2C (temperature & light)
  - Sends LoRa commands to remote Raspberry Pi B for GPIO18 LED control
  - API: `GET /sensor`, `GET /temp`, `GET /light`, `GET /led`, `POST /led`
  - `lora.py`: LoRaNode class (serial comm, CRC, ACK retry logic)
- `frontend/`：Static HTML/CSS/JS IoT Dashboard
  - Dark tech theme with glassmorphism cards
  - Displays real-time sensor readings & LED switch
  - Responsive layout for both mobile and desktop
- `nginx/`：Nginx reverse proxy config
  - Serves frontend static files
  - Proxies `/api/` to backend
- `docker-compose.yml`：Docker Compose deployment config
- `receiver_b.py`：Standalone receiver script for Raspberry Pi B (not in Docker)

Target device:

- Raspberry Pi A (runs Docker backend + nginx + LoRa transmitter)
- Raspberry Pi B (runs `receiver_b.py` + LoRa receiver + GPIO18 LED)
- Linux / Debian
- Docker Compose
- Requires I2C (`/dev/i2c-1`), serial (`/dev/ttyS0`), and GPIO access

## Important Rules for AI Agent

Before editing code:

1. Do not scan or modify `venv/`.
2. Do not scan or modify `__pycache__/`.
3. Do not modify `cloudflared-linux-arm64.deb`.
4. Read `docker-compose.yml` first.
5. Then read files in `backend/`, `frontend/`, and `nginx/`.
6. Make minimal changes.
7. Do not rewrite the whole project unless explicitly requested.

## Hardware Dependencies

### Raspberry Pi A (Docker host)
- `/dev/i2c-1`: YL-40 PCF8591 sensor
- `/dev/ttyS0`: LoRa serial port
- `privileged: true` required for GPIO/Serial access inside container

### LoRa Module Pins
- M0 = GPIO22 (set LOW for Normal Mode)
- M1 = GPIO27 (set LOW for Normal Mode)

### Raspberry Pi B (receiver)
- Runs `receiver_b.py` directly (NOT inside Docker)
- Controls GPIO18 LED locally
- Uses same LoRa module setup (M0/M1 LOW, /dev/ttyS0)

## Ignore These Files

The following are generated or external files:

```text
venv/
__pycache__/
*.pyc
cloudflared-linux-arm64.deb
```

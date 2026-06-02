# AGENTS.md

## Project Overview

This is a Raspberry Pi IoT sensor monitoring & LoRa remote LED/fan control project.

It contains:

- `backend/`：FastAPI backend (Python 3.11)
  - Reads YL-40 PCF8591 sensor data via I2C (temperature & light)
  - Sends LoRa commands to remote Raspberry Pi B for GPIO18 LED control and GPIO17 fan relay control
  - Tracks LoRa receiver availability with a background PING/PONG heartbeat
  - API: `GET /sensor`, `GET /temp`, `GET /light`, `GET /led`, `POST /led`, `GET /fan`, `POST /fan`, `GET /lora/status`
  - `lora.py`: LoRaNode class (serial comm, CRC, ACK retry logic, LED/FAN commands, heartbeat ping)
- `frontend/`：Static HTML/CSS/JS IoT Dashboard
  - Dark tech theme with glassmorphism cards
  - Displays real-time sensor readings, LED switch, fan switch, LoRa status, and operation logs
  - Responsive layout for both mobile and desktop
- `nginx/`：Nginx reverse proxy config
  - Serves frontend static files
  - Proxies `/api/` to backend
- `docker-compose.yml`：Docker Compose deployment config
- `receiver_b.py`：Standalone receiver script for Raspberry Pi B (not in Docker)

Target device:

- Raspberry Pi A (runs Docker backend + nginx + LoRa transmitter)
- Raspberry Pi B (runs `receiver_b.py` + LoRa receiver + GPIO18 LED + GPIO17 fan relay)
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
- Controls GPIO17 fan relay locally
- Uses same LoRa module setup (M0/M1 LOW, /dev/ttyS0)

## Current Improvement Plan

### Sensor Cache for Higher Concurrent Reads

Goal: improve concurrent browser/user queries for sensor readings without making every HTTP request touch the I2C bus.

Recommended approach:

1. Add an in-process `sensor_cache` in the FastAPI backend.
2. Start one background sensor polling thread on application startup.
3. Poll PCF8591 at a fixed interval, for example 1 second.
4. Store the latest temperature, light percentage, raw ADC values, timestamp, and error state in the cache.
5. Protect cache reads/writes with a `threading.Lock`.
6. Make `GET /sensor`, `GET /temp`, and `GET /light` return the latest cached value instead of reading I2C directly.
7. Include metadata such as `cached: true`, `updated_at`, and optionally `age_ms` so the frontend can show stale data if needed.
8. Keep LoRa control endpoints (`/led`, `/fan`, `/lora/status`) separate from the sensor cache.

Expected benefit:

- Many users can refresh `/sensor` at the same time while only one backend thread reads I2C.
- Reduces I2C bus pressure and avoids repeated PCF8591 channel switching per request.
- Makes API latency more stable because requests return memory data instead of waiting for hardware I/O.

Important constraints:

- Keep the first implementation in-process; do not introduce Redis or a database unless multi-container backend replicas are added later.
- If sensor polling fails, keep serving the last known value with an error/stale marker instead of crashing the API.
- Make minimal changes and preserve the existing API response fields for frontend compatibility.

## Ignore These Files

The following are generated or external files:

```text
venv/
__pycache__/
*.pyc
cloudflared-linux-arm64.deb
```

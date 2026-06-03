# AGENTS.md

## Project Overview

This is a Raspberry Pi IoT sensor monitoring & LoRa remote LED/fan control project.

It contains:

- `backend/`：FastAPI backend (Python 3.11)
  - Reads YL-40 PCF8591 sensor data via I2C (temperature & light)
  - Sends LoRa commands to remote Raspberry Pi B for GPIO18 LED control and GPIO17 fan relay control
  - Maintains in-process caches for sensor data and remote LED/FAN state
  - Tracks LoRa receiver availability with a background PING/PONG heartbeat
  - Protects every LoRa serial read/write with one shared serial lock
  - Requires login token auth for hardware write APIs
  - API: `GET /sensor`, `GET /temp`, `GET /light`, `GET /led`, `POST /led`, `GET /fan`, `POST /fan`, `GET /lora/status`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`
  - `lora.py`: LoRaNode class (serial comm, CRC, ACK retry logic, LED/FAN commands, heartbeat ping, device state query)
- `frontend/`：Static HTML/CSS/JS IoT Dashboard
  - Dark tech theme with glassmorphism cards
  - Displays real-time sensor readings, LED switch, fan switch, LoRa status, and operation logs
  - Shows a login modal only when hardware control permission is needed
  - Stores the control token in `localStorage`
  - Shows auth Toast feedback on login/logout
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
8. If the user says "其他不动", modify only the explicitly requested file(s).

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
- Maintains local LED/FAN state for `QUERY,STATE`
- Normal heartbeat packets are mostly silent; only the first successful PING/PONG handshake should be logged
- Uses same LoRa module setup (M0/M1 LOW, /dev/ttyS0)

## Current Backend Behavior

### Caches and Hardware I/O

- `GET /sensor`, `GET /temp`, and `GET /light` must read sensor cache only.
- `GET /led`, `GET /fan`, and `GET /lora/status` must read cached state/status only.
- User-triggered GET requests must not directly access GPIO, I2C, `ser.write()`, or `ser.readline()`.
- A background thread may read PCF8591 sensor data every 1 second and update `sensor_cache`.
- A background thread may query Raspberry Pi B every 1 second with LoRa `QUERY,STATE` and update LED/FAN state cache.
- `POST /led` and `POST /fan` are the hardware write APIs. They require auth, use the shared serial lock, send LoRa commands, wait for ACK, then update cache.
- If polling fails, keep serving the last known cached value with error/stale metadata instead of crashing the API.

### LoRa Protocol

- Heartbeat:
  - A -> B: `PING,<crc>`
  - B -> A: `PONG,<crc>`
- Hardware control:
  - A -> B: `CMD,LED,ON|OFF,<crc>`
  - A -> B: `CMD,FAN,ON|OFF,<crc>`
  - B -> A: `ACK,LED,ON|OFF,<crc>` or `ACK,FAN,ON|OFF,<crc>`
- Device state query:
  - A -> B: `QUERY,STATE,<crc>`
  - B -> A: `STATE,LED,ON|OFF,FAN,ON|OFF,<crc>`

### Auth

- SQLite database path on Raspberry Pi: `/home/pi/yl40iot.db`
- Docker Compose mounts `/home/pi/yl40iot.db:/home/pi/yl40iot.db`.
- Existing tables:
  - `users(id, username, password_hash, role, is_active, created_at)`
  - `auth_tokens(id, user_id, token_hash, client_ip, user_agent, expires_at, revoked, created_at)`
- Frontend stores access token in `localStorage` under `yl40iot_access_token`.
- Frontend sends hardware write requests with `Authorization: Bearer <token>`.
- Logout revokes/clears token only; it must not change LED/FAN hardware state.

## Planned Refactor

When refactoring backend, do not split it too much. Keep the shape simple:

- Keep `main.py` as the FastAPI app, route layer, CORS, Pydantic models, and SQLite auth.
- Move sensor/I2C/cache/thread code into `backend/sensor.py`.
- Move LoRa device orchestration into `backend/device.py`.
- Heartbeat belongs in `device.py`.
- Keep low-level serial protocol code in `backend/lora.py`.
- Preserve API paths and Docker one-command deployment behavior.
- Frontend refactor is separate and should not be mixed into this backend refactor unless explicitly requested.

## Ignore These Files

The following are generated or external files:

```text
venv/
__pycache__/
*.pyc
cloudflared-linux-arm64.deb
```

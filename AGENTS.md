# AGENTS.md

## Project Overview

This is a Raspberry Pi IoT sensor monitoring & LoRa remote LED/fan control project.

It contains:

- `backend/`：FastAPI backend (Python 3.11)
  - In MQTT mode reads SHT35/YL40 values from MQTT caches, not local hardware
  - Sends LoRa commands to remote Raspberry Pi B for GPIO18 LED control and GPIO24 fan relay control
  - Maintains in-process caches for sensor data and remote LED/FAN state
  - Tracks LoRa receiver availability with a background PING/PONG heartbeat
  - Protects every LoRa serial read/write with one shared serial lock
  - Requires login token auth for hardware write APIs
  - API: `GET /sensor`, `GET /temp`, `GET /light`, `GET /led`, `POST /led`, `GET /fan`, `POST /fan`, `GET /lora/status`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`
  - `lora.py`: LoRaNode class (serial comm, CRC16 + HMAC/sequence secure messages, ACK retry logic, LED/FAN commands, heartbeat ping, device state query)
- `frontend/`：Static HTML/CSS/JS IoT Dashboard
  - Dark tech theme with glassmorphism cards
  - Displays real-time sensor readings, LED switch, fan switch, LoRa status, and operation logs
  - Shows a login modal only when hardware control permission is needed
  - Uses `HttpOnly + Secure + SameSite=Lax` cookie auth for control permission
  - Shows auth Toast feedback on login/logout
  - Responsive layout for both mobile and desktop
- `nginx/`：Nginx reverse proxy config
  - Serves frontend static files
  - Proxies `/api/` to backend
- `docker-compose.yml`：Docker Compose deployment config
- `receiver_b.py`：Standalone receiver script for Raspberry Pi B (not in Docker)
- `backend/pi_b_node.py`：MQTT migration mode Pi B node; never connects to MQTT
- `lora_gateway/lora_mqtt.py`：MQTT/LoRa bridge and the only Pi A LoRa serial owner in MQTT mode

Target device:

- Raspberry Pi A (runs Docker backend + nginx + LoRa transmitter)
- Raspberry Pi B (runs `receiver_b.py` + LoRa receiver + GPIO18 LED + GPIO24 fan relay)
- Raspberry Pi C (runs `pi_c_rs485_node.py`, reads SHT35 over RS485 and YL40 PCF8591 over I2C, publishes MQTT)
- Linux / Debian
- Docker Compose
- Requires I2C (`/dev/i2c-1`), serial (`/dev/ttyS0`), and GPIO access

## MQTT Migration Mode

- Select exactly one transport with `DEVICE_TRANSPORT=legacy_lora|mqtt`.
- `legacy_lora` remains the default rollback mode.
- In `mqtt` mode FastAPI must not open GPIO, I2C, RS485, or LoRa serial devices.
- In `mqtt` mode `lora-gateway` is the only Pi A process allowed to open `/dev/ttyS0`.
- Raspberry Pi B never connects to MQTT; `pi_b_node.py` receives commands and answers heartbeat/state only through LoRa.
- Raspberry Pi C publishes SHT35 and YL40 telemetry to MQTT.
- MQTT topics use the `yl40iot/v1/` prefix.
- MQTT control commands must never be retained.
- Do not automatically fall back from MQTT control to direct LoRa control.

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
9. For small frontend visual elements such as the fan icon, prefer reference-driven edits over freeform redesign.
10. If a fan icon redesign is requested, keep it simple and elegant: gray circular base, compact centered symbol, and avoid over-detailed pseudo-hardware decoration unless the user explicitly asks for it.

## Hardware Dependencies

### Raspberry Pi A (Docker host)
- `/dev/ttyS0`: LoRa serial port
- `/dev/gpiomem`: GPIO access for LoRa M0/M1
- Backend should run without `privileged: true`; Docker grants only device mounts plus gpio/i2c/dialout groups.

### Raspberry Pi C
- `/dev/ttyS0` or configured serial port: SHT35 RS485 adapter
- `/dev/i2c-1`: YL-40 PCF8591 sensor

### LoRa Module Pins
- M0 = GPIO22 (set LOW for Normal Mode)
- M1 = GPIO27 (set LOW for Normal Mode)

### Raspberry Pi B (receiver)
- Runs `receiver_b.py` directly (NOT inside Docker)
- Controls GPIO18 LED locally
- Controls GPIO24 fan relay locally
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
- In MQTT mode, SHT35/YL40 data comes from Pi C MQTT telemetry and Pi B must not send YL40 telemetry over LoRa.
- `POST /led` and `POST /fan` are the hardware write APIs. They require auth, use the shared serial lock, send LoRa commands, wait for ACK, then update cache.
- If polling fails, keep serving the last known cached value with error/stale metadata instead of crashing the API.

### LoRa Protocol

- Every LoRa message uses secure text framing:
  - `<payload>,<seq>,<hmac>,<crc16>`
  - `seq`: monotonic sequence number for replay protection
  - `hmac`: first 16 hex chars of HMAC-SHA256 over `<payload>,<seq>`
  - `crc16`: CRC16-CCITT over `<payload>,<seq>,<hmac>`
- A and B must share the same `LORA_HMAC_SECRET`.
- Heartbeat:
  - A -> B payload: `PING`
  - B -> A payload: `PONG`
- Hardware control:
  - A -> B payload: `CMD,LED,ON|OFF`
  - A -> B payload: `CMD,FAN,ON|OFF`
  - B -> A payload: `ACK,LED,ON|OFF` or `ACK,FAN,ON|OFF`
- Device state query:
  - A -> B payload: `QUERY,STATE`
  - B -> A payload: `STATE,LED,ON|OFF,FAN,ON|OFF`

### Auth

- SQLite database path on Raspberry Pi: `/home/pi/yl40iot.db`
- Docker Compose mounts `/home/pi/yl40iot.db:/home/pi/yl40iot.db`.
- Existing tables:
  - `users(id, username, password_hash, role, is_active, created_at)`
  - `auth_tokens(id, user_id, token_hash, client_ip, user_agent, expires_at, revoked, created_at)`
- Frontend does not store the control token in `localStorage`.
- Login sets `control_token` as an `HttpOnly + Secure + SameSite=Lax` cookie on `/api`.
- Browser automatically carries the cookie to authenticated write APIs.
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

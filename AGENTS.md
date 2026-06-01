# AGENTS.md

## Project Overview

This is a Raspberry Pi IoT sensor monitoring & GPIO control project.

It contains:

- `backend/`：FastAPI backend (Python 3.11)
  - Reads YL-40 PCF8591 sensor data via I2C (temperature & light)
  - Controls GPIO18 LED via `gpiozero`
  - API: `GET /sensor`, `GET /temp`, `GET /light`, `GET /led`, `POST /led`
- `frontend/`：Static HTML/CSS/JS IoT Dashboard
  - Dark tech theme with glassmorphism cards
  - Displays real-time sensor readings & LED switch
  - Responsive layout for both mobile and desktop
- `nginx/`：Nginx reverse proxy config
  - Serves frontend static files
  - Proxies `/api/` to backend
- `docker-compose.yml`：Docker Compose deployment config

Target device:

- Raspberry Pi (Linux / Debian)
- Docker Compose
- Requires I2C (`/dev/i2c-1`) and GPIO access

## Important Rules for AI Agent

Before editing code:

1. Do not scan or modify `venv/`.
2. Do not scan or modify `__pycache__/`.
3. Do not modify `cloudflared-linux-arm64.deb`.
4. Read `docker-compose.yml` first.
5. Then read files in `backend/`, `frontend/`, and `nginx/`.
6. Make minimal changes.
7. Do not rewrite the whole project unless explicitly requested.

## Ignore These Files

The following are generated or external files:

```text
venv/
__pycache__/
*.pyc
cloudflared-linux-arm64.deb
```

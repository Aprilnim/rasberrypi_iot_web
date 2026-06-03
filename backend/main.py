# ============================================================
# FastAPI 后端 —— YL-40 IoT 监控服务器
# 运行环境：树莓派 A 的 Docker 容器内
#
# 功能模块：
#   1. I2C 传感器读取（YL-40 PCF8591）→ 温度、光照
#   2. LoRa 串口通信 → 控制树莓派 B 的 LED + 风扇，并定时心跳检测
#   3. HTTP API → 供前端页面调用
#
# 数据流：
#   前端(浏览器) ──HTTP──► 树莓派A后端 ──LoRa──► 树莓派B(receiver_b.py)
#                                                ├── GPIO18 LED
#                                                └── GPIO17 风扇继电器
# ============================================================

# ------------------------------------------------------------
# 导入依赖模块
# FastAPI:       Web 框架，用来写 HTTP API
# CORSMiddleware: 允许前端跨域请求（浏览器安全策略）
# BaseModel:     Pydantic 数据校验，用来校验 POST 请求参数
# smbus2:        Linux I2C 通信库，读传感器用
# math:          数学函数（log 等），温度换算用
# threading:     Python 多线程，用来启动后台心跳线程
# time:          时间相关函数
# ------------------------------------------------------------
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import hashlib
import hmac
import smbus2 as smbus
import math
import os
import secrets
import sqlite3
import threading
import time

# ------------------------------------------------------------
# 创建 FastAPI 应用实例
# add_middleware(CORSMiddleware, ...):
#   允许任意来源的前端页面调用本后端接口
#   开发调试阶段开"*"，生产环境建议改成具体域名
# ------------------------------------------------------------
app = FastAPI(
    docs_url=None,
    redoc_url=None,
    openapi_url=None
)
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------
# I2C 总线初始化
# 树莓派默认 I2C 接口是总线 1，设备文件 /dev/i2c-1
# 这个 bus 对象后面 get_adc() 函数会反复使用它读取传感器
# ------------------------------------------------------------
bus = smbus.SMBus(1)

# PCF8591 ADC 芯片的 I2C 地址（硬件固定， datasheet 上写 0x48）
ADDR = 0x48

# 服务启动时间戳，用于计算后端运行时长
APP_START_TIME = time.time()

# SQLite 鉴权数据库。树莓派部署时使用 /home/pi/yl40iot.db。
AUTH_DB_PATH = os.environ.get("YL40IOT_DB_PATH", "/home/pi/yl40iot.db")
AUTH_TOKEN_TTL_SECONDS = 24 * 60 * 60
AUTH_COOKIE_NAME = "control_token"
AUTH_COOKIE_PATH = "/api"

# ============================================================
# LoRa 模块初始化
# ============================================================
# 从同目录的 lora.py 导入 LoRaNode 类
# LoRaNode 负责：
#   - 初始化 GPIO22(M0)、GPIO27(M1) 为低电平（Normal Mode）
#   - 打开串口 /dev/ttyS0，波特率 9600
#   - 提供 send_command() 发送 LED 控制命令并等待 ACK
#   - 提供 send_fan_command() 发送风扇控制命令并等待 ACK
#   - 提供 ping_once() 发送 PING 并等待 PONG（心跳用）
#   - 内部用 threading.Lock 保证串口读写互斥
#
# 初始化失败时（比如不在树莓派上跑、串口不存在），
# lora = None，后续所有 LoRa 操作都会优雅降级返回错误
# ------------------------------------------------------------
from lora import LoRaNode

try:
    lora = LoRaNode()
    print(f"[LoRa] Available = {lora.available}")
except Exception as e:
    print(f"[LoRa] Failed to initialize: {e}")
    lora = None


# ------------------------------------------------------------
# Pydantic 请求体模型
# LedState: 定义 POST /led 接口的请求体格式
# FanState: 定义 POST /fan 接口的请求体格式
# 前端发送 JSON: {"on": true} 或 {"on": false}
# FastAPI 自动用 Pydantic 校验这个字段是否存在、是否为 bool
# ------------------------------------------------------------
class LedState(BaseModel):
    on: bool


class FanState(BaseModel):
    on: bool


class LoginRequest(BaseModel):
    username: str
    password: str


# ============================================================
# 控制权限鉴权
# ============================================================
# GET 接口只读缓存，不要求登录；POST /led 和 POST /fan 会写硬件，必须登录。
# token 明文只返回给前端一次，数据库里只保存 token_hash。
# ------------------------------------------------------------
def get_auth_db():
    conn = sqlite3.connect(AUTH_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_password(password: str, stored_hash: str) -> bool:
    """校验密码。

    推荐格式：pbkdf2_sha256$iterations$salt$hex_digest
    兼容格式：64 位 sha256 十六进制；以及临时明文值。
    """
    if not stored_hash:
        return False

    if stored_hash.startswith("pbkdf2_sha256$"):
        try:
            _, iterations, salt, expected = stored_hash.split("$", 3)
            digest = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt.encode("utf-8"),
                int(iterations),
            ).hex()
            return hmac.compare_digest(digest, expected)
        except Exception:
            return False

    if len(stored_hash) == 64:
        digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(digest, stored_hash)

    return hmac.compare_digest(password, stored_hash)


def create_password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = 260000
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"


def get_client_ip(request: Request) -> str:
    cf_ip = request.headers.get("CF-Connecting-IP")
    forwarded_for = request.headers.get("X-Forwarded-For")
    if cf_ip:
        return cf_ip
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else ""


def require_control_auth(control_token: str | None = Cookie(default=None, alias=AUTH_COOKIE_NAME)):
    if not control_token:
        raise HTTPException(status_code=401, detail="请先登录后再控制硬件")

    token = control_token.strip()
    if not token:
        raise HTTPException(status_code=401, detail="登录凭证无效")

    now = int(time.time())
    token_hash = hash_token(token)

    with get_auth_db() as conn:
        row = conn.execute(
            """
            SELECT
                auth_tokens.id AS token_id,
                auth_tokens.expires_at,
                auth_tokens.revoked,
                users.id AS user_id,
                users.username,
                users.role,
                users.is_active
            FROM auth_tokens
            JOIN users ON users.id = auth_tokens.user_id
            WHERE auth_tokens.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()

    if row is None or row["revoked"]:
        raise HTTPException(status_code=401, detail="登录凭证无效或已退出")
    if row["expires_at"] <= now:
        raise HTTPException(status_code=401, detail="登录已过期")
    if not row["is_active"]:
        raise HTTPException(status_code=403, detail="用户已禁用")

    return {
        "id": row["user_id"],
        "username": row["username"],
        "role": row["role"],
        "token_hash": token_hash,
    }


# ============================================================
# LoRa 心跳状态变量（全局）
# ============================================================
# lora_online:      树莓派 B 当前是否在线（根据心跳判断）
# lora_fail_count:  连续心跳失败的次数
# last_pong_time:   上一次收到 PONG 的时间戳（Unix 时间，秒）
# heartbeat_lock:   线程锁，保护上面三个变量
#
# 为什么需要锁？
# 因为 heartbeat_loop 后台线程每 3 秒读写这些变量，
# 同时 /lora/status 接口也会被前端并发调用读写这些变量。
# 没有锁的话，可能出现"读到一半被改"的竞态条件。
# ------------------------------------------------------------
lora_online = False
lora_fail_count = 0
last_pong_time = None
heartbeat_lock = threading.Lock()


# ------------------------------------------------------------
# heartbeat_loop()
# 后台心跳线程的主循环，函数本身不会返回（while True）
#
# 工作流程：
#   1. 休眠 3 秒
#   2. 检查 LoRa 模块是否可用（lora is not None and available）
#      如果不可用，把 lora_online 标记为 False，继续下一轮
#   3. 调用 lora.ping_once() 通过 LoRa 发送 PING 并等待 PONG
#   4. 用 heartbeat_lock 锁保护，更新状态变量：
#      - 收到 PONG → lora_online=True, fail_count=0, 记录时间戳
#      - 没收到   → fail_count += 1，连续 3 次失败则 lora_online=False
#
# 注意：
#   - 失败不会停线程，每 3 秒继续尝试
#   - 这样树莓派 B 重新上电后可以自动恢复在线，不需要重启后端
# ------------------------------------------------------------
def heartbeat_loop():
    global lora_online, lora_fail_count, last_pong_time
    while True:
        time.sleep(3)                    # 每 3 秒执行一次心跳
        if lora is None or not lora.available:
            with heartbeat_lock:
                lora_online = False
            continue

        try:
            ok = lora.ping_once()        # 发送 PING，等待 PONG，返回 True/False
        except Exception as e:
            print(f"[Heartbeat] Exception: {e}")
            ok = False

        with heartbeat_lock:             # 获取锁，保护共享变量
            if ok:
                # 收到 PONG，设备在线，清零失败计数
                lora_online = True
                lora_fail_count = 0
                last_pong_time = time.time()
            else:
                # 没收到 PONG，失败次数加 1
                lora_fail_count += 1
                if lora_fail_count >= 3:
                    # 连续 3 次失败，判定设备离线
                    lora_online = False


# ------------------------------------------------------------
# 启动心跳后台线程
# 条件：LoRa 模块初始化成功才启动，否则没有意义
# daemon=True: 设为守护线程，主进程退出时自动结束，避免僵尸线程
# ------------------------------------------------------------
if lora is not None and lora.available:
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    print("[LoRa] Heartbeat thread started")


# ============================================================
# LED/FAN 真实状态缓存
# ============================================================
# 目的：
#   用户 GET /led、GET /fan 时只读取后端缓存，不直接访问串口。
#   后台线程每 1 秒通过 LoRa 查询一次树莓派 B 的真实状态，并更新缓存。
#   查询过程也在 lora.py 内部使用同一把串口锁，避免和心跳/控制命令串包。
# ------------------------------------------------------------
DEVICE_STATE_CACHE_INTERVAL = 1.0


def device_state_cache_loop():
    while True:
        if lora is not None and lora.available:
            try:
                lora.query_device_state()
            except Exception as e:
                print(f"[DeviceState] Exception: {e}")
        time.sleep(DEVICE_STATE_CACHE_INTERVAL)


if lora is not None and lora.available:
    threading.Thread(target=device_state_cache_loop, daemon=True).start()
    print("[DeviceState] Cache thread started")


# ============================================================
# 传感器数据读取函数
# ============================================================

# ------------------------------------------------------------
# get_adc(ch)
# 读取 PCF8591 ADC 芯片指定通道的原始数值
# ch: 通道编号，树莓派 B 端用 0x40(光照)、0x42(温度)
#
# PCF8591 的特性：
#   选择通道后，第一次 read_byte 读出来的是旧数据（无效），
#   第二次 read_byte 才是当前通道的真实 ADC 值。
#   所以代码里写两次 read_byte，第二次的返回值才是有效的。
#
# 返回值：0~255 的整数，或 None（I2C 通信失败时）
# ------------------------------------------------------------
def get_adc(ch):
    try:
        # 选择ADC通道（向芯片写入通道号）
        bus.write_byte(ADDR, ch)

        # 第一次读取无效（PCF8591特性：第一次返回的是上一次通道的数据）
        bus.read_byte(ADDR)

        # 第二次读取才是真实数据
        return bus.read_byte(ADDR)

    except:
        # I2C 通信失败（设备没接好、总线繁忙等）
        return None


# ------------------------------------------------------------
# get_temp(v)
# 把热敏电阻的 ADC 原始值换算成摄氏度
#
# 原理：
#   1. PCF8591 输出 0~255 的数字量，对应 0~Vcc 的电压
#   2. 通过分压公式 r = (255/v) - 1 算出热敏电阻阻值比例
#   3. 用 Beta 公式（热敏电阻经验公式）换算成绝对温度
#   4. 减去 273.15 转成摄氏度
#
# v: ADC 原始值 (0~255)
# 返回值：温度（℃），保留两位小数；异常时返回 0
# ------------------------------------------------------------
def get_temp(v):

    # 读取失败
    if v is None:
        return 0

    # 异常值过滤：ADC 为 0 或 255 时计算会出错，直接返回 0
    if v <= 5 or v >= 250:
        return 0

    try:
        # 热敏电阻阻值换算（比例值，不是真实欧姆）
        r = (255.0 / v) - 1.0

        # Beta公式计算温度（开尔文）
        # 3950 是热敏电阻的 B 值参数
        # 298.15 是 25℃ 对应的开尔文温度
        t = 1.0 / (
            math.log(r) / 3950.0 +
            1.0 / 298.15
        ) - 273.15

        return round(t, 2)

    except:
        # 数学计算异常（比如 v=0 导致除零，已被过滤但保险起见）
        return 0


# ============================================================
# 传感器缓存
# ============================================================
# 目的：
#   多个浏览器同时刷新 /sensor 时，不让每个 HTTP 请求都直接读 I2C。
#   后台线程每 1 秒读一次 PCF8591，把最新结果放进内存缓存；
#   API 请求只读缓存，响应更快，也减轻 I2C 总线压力。
# ------------------------------------------------------------
# 后台采样间隔。前端默认每 1 秒请求一次 /sensor，
# 所以这里也设为 1 秒，保证数据刷新频率和页面显示频率一致。
SENSOR_CACHE_INTERVAL = 1.0

# sensor_cache 会被后台采样线程写入，也会被多个 HTTP 请求读取。
# 用锁保证读写时拿到的是一份完整数据，避免并发读到半更新状态。
sensor_cache_lock = threading.Lock()

# 传感器缓存的唯一数据源。
# temperature/light_percent 是前端直接展示的换算值；
# temp_raw/light_raw 保留 ADC 原始值，方便调试硬件；
# updated_at/age_ms 用来判断缓存新不新；
# error 用来记录最近一次采样是否失败。
sensor_cache = {
    "temperature": 0,
    "light_percent": 0,
    "temp_raw": None,
    "light_raw": None,
    "updated_at": None,
    "error": "Sensor cache warming up",
}


def read_sensor_values():
    # 只有这个函数直接读取 I2C。HTTP 接口不要再直接调用 get_adc()，
    # 否则多人并发访问时仍然会把压力打到 I2C 总线上。
    light_raw = get_adc(0x40)
    temp_raw = get_adc(0x42)

    # 任意一个通道读取失败，都把错误写入缓存；
    # API 仍然返回结构化数据，避免前端因为异常响应中断刷新。
    error = None
    if light_raw is None or temp_raw is None:
        error = "I2C sensor read failed"

    # 光照换算沿用原来的逻辑：raw 越小代表越亮。
    # 如果本次 light_raw 读取失败，先按 0 计算，error 字段会告诉调用方这次采样异常。
    safe_light_raw = 0 if light_raw is None else light_raw
    return {
        "temperature": get_temp(temp_raw),
        "light_percent": round(((255.0 - safe_light_raw) / 255.0) * 100.0, 1),
        "temp_raw": temp_raw,
        "light_raw": light_raw,
        "updated_at": time.time(),
        "error": error,
    }


def update_sensor_cache_once():
    # 单次采样并原子更新缓存。
    # 先在锁外读 I2C，避免慢速硬件 I/O 长时间占住锁；
    # 读完后只在很短的 update 阶段加锁。
    values = read_sensor_values()
    with sensor_cache_lock:
        sensor_cache.update(values)


def sensor_cache_loop():
    # 后台常驻采样线程。它是整个 sensor_cache 的写入者，
    # 多个 API 请求只是读取缓存，因此并发量上来时也不会增加 I2C 读取次数。
    while True:
        try:
            update_sensor_cache_once()
        except Exception as e:
            # 防御性保护：采样异常不能让后台线程退出。
            # 保留旧缓存值，只更新 error，前端/调用方可以继续拿到上一份数据。
            with sensor_cache_lock:
                sensor_cache["error"] = str(e)
        time.sleep(SENSOR_CACHE_INTERVAL)


def get_sensor_cache_snapshot():
    # 给 HTTP 接口使用的缓存快照。
    # 返回 dict 副本，避免接口组装响应时后台线程同时改原始缓存。
    with sensor_cache_lock:
        data = dict(sensor_cache)

    # age_ms 表示缓存距当前时间多少毫秒。
    # 后续如果需要判断“数据过旧”，可以直接用这个字段。
    updated_at = data.get("updated_at")
    age_ms = None
    if updated_at is not None:
        age_ms = int((time.time() - updated_at) * 1000)

    data["cached"] = True
    data["age_ms"] = age_ms
    return data


try:
    # 服务启动时先同步采样一次，尽量避免第一次 /sensor 请求拿到空缓存。
    update_sensor_cache_once()
except Exception as e:
    with sensor_cache_lock:
        sensor_cache["error"] = str(e)

# 启动后台传感器采样线程。
# daemon=True 表示主进程退出时线程自动结束，不阻塞容器关闭。
threading.Thread(target=sensor_cache_loop, daemon=True).start()
print("[Sensor] Cache thread started")


# ============================================================
# HTTP API 接口定义
# nginx 配置把 /api/ 开头的请求代理到后端根路径
# 所以 FastAPI 里注册 /sensor，前端请求 /api/sensor
# ============================================================

# ------------------------------------------------------------
# GET /
# 首页健康检查接口，返回服务运行状态
# ------------------------------------------------------------
@app.get("/")
def root():

    return {
        "status": "running",
        "device": "YL-40"
    }


# ------------------------------------------------------------
# POST /auth/login
# 登录后签发控制硬件用的 HttpOnly Cookie token。
# ------------------------------------------------------------
@app.post("/auth/login")
def login(data: LoginRequest, request: Request, response: Response):
    username = data.username.strip()
    if not username or not data.password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")

    now = int(time.time())
    with get_auth_db() as conn:
        user = conn.execute(
            """
            SELECT id, username, password_hash, role, is_active
            FROM users
            WHERE username = ?
            """,
            (username,),
        ).fetchone()

        if user is None or not verify_password(data.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        if not user["is_active"]:
            raise HTTPException(status_code=403, detail="用户已禁用")

        token = secrets.token_urlsafe(32)
        token_hash = hash_token(token)
        expires_at = now + AUTH_TOKEN_TTL_SECONDS

        # 同一账号只保留一个有效登录，避免多人同时持有控制权限。
        conn.execute(
            "UPDATE auth_tokens SET revoked = 1 WHERE user_id = ? AND revoked = 0",
            (user["id"],),
        )
        conn.execute(
            """
            INSERT INTO auth_tokens (
                user_id, token_hash, client_ip, user_agent,
                expires_at, revoked, created_at
            )
            VALUES (?, ?, ?, ?, ?, 0, ?)
            """,
            (
                user["id"],
                token_hash,
                get_client_ip(request),
                request.headers.get("User-Agent", ""),
                expires_at,
                now,
            ),
        )
        conn.commit()

    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        max_age=AUTH_TOKEN_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path=AUTH_COOKIE_PATH,
    )

    return {
        "authenticated": True,
        "expires_in": AUTH_TOKEN_TTL_SECONDS,
        "user": {
            "username": user["username"],
            "role": user["role"],
        },
    }


# ------------------------------------------------------------
# POST /auth/logout
# 撤销当前 token。只退出控制权限，不改变 LED/FAN 硬件状态。
# ------------------------------------------------------------
@app.post("/auth/logout")
def logout(response: Response, user=Depends(require_control_auth)):
    with get_auth_db() as conn:
        conn.execute(
            "UPDATE auth_tokens SET revoked = 1 WHERE token_hash = ?",
            (user["token_hash"],),
        )
        conn.commit()
    response.delete_cookie(
        key=AUTH_COOKIE_NAME,
        path=AUTH_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return {"ok": True}


# ------------------------------------------------------------
# GET /auth/me
# 前端刷新页面后可用它检查 HttpOnly Cookie token 是否仍有效。
# ------------------------------------------------------------
@app.get("/auth/me")
def auth_me(user=Depends(require_control_auth)):
    return {
        "authenticated": True,
        "user": {
            "username": user["username"],
            "role": user["role"],
        },
    }


# ------------------------------------------------------------
# GET /temp
# 读取温度传感器数据
# 通道：0x42（YL-40 模块上 CH2 接的是热敏电阻）
# 返回：{ "temperature": 25.67, "raw": 128 }
#   temperature: 换算后的摄氏度
#   raw:         ADC 原始值（0~255）
# 现在从 sensor_cache 读取，不再在请求过程中直接访问 I2C。
# 额外返回 cached/updated_at/age_ms/error，便于调试缓存状态。
# ------------------------------------------------------------
@app.get("/temp")
def temp():
    # 读取缓存快照，避免高并发请求重复读硬件。
    data = get_sensor_cache_snapshot()

    return {
        "temperature": data["temperature"],
        "raw": data["temp_raw"],
        "cached": True,
        "updated_at": data["updated_at"],
        "age_ms": data["age_ms"],
        "error": data["error"],
    }


# ------------------------------------------------------------
# GET /light
# 读取光照传感器数据
# 通道：0x40（YL-40 模块上 CH0 接的是光敏电阻）
# 返回：{ "light_percent": 78.5, "raw": 55 }
#   light_percent: 光照百分比（ADC 值越低光照越强，所以用 255-raw）
#   raw:           ADC 原始值
# 现在从 sensor_cache 读取，不再在请求过程中直接访问 I2C。
# 额外返回 cached/updated_at/age_ms/error，便于调试缓存状态。
# ------------------------------------------------------------
@app.get("/light")
def light():
    # 读取缓存快照，避免高并发请求重复读硬件。
    data = get_sensor_cache_snapshot()

    return {
        "light_percent": data["light_percent"],
        "raw": data["light_raw"],
        "cached": True,
        "updated_at": data["updated_at"],
        "age_ms": data["age_ms"],
        "error": data["error"],
    }


# ------------------------------------------------------------
# GET /sensor
# 综合传感器接口，一次请求同时返回温度和光照
# 前端 dashboard 用这个接口，每秒刷新一次
# 返回：{ "temperature": 25.67, "light_percent": 78.5 }
# 这是并发访问最多的接口，所以优先改成读 sensor_cache。
# 保留原字段，保证前端 app.js 不需要跟着改。
# ------------------------------------------------------------
@app.get("/sensor")
def sensor():
    # 读取缓存快照，避免多人同时刷新页面时反复读 I2C。
    data = get_sensor_cache_snapshot()

    return {
        "temperature": data["temperature"],
        "light_percent": data["light_percent"],
        "cached": True,
        "updated_at": data["updated_at"],
        "age_ms": data["age_ms"],
        "error": data["error"],
    }


# ------------------------------------------------------------
# GET /led
# 查询 LED 当前状态
# 返回：{ "on": true/false, "available": true/false }
#   on:        LED 当前是开还是关
#   available: LoRa 模块是否可用
# 如果 LoRa 不可用，available=false，on 固定为 false
# ------------------------------------------------------------
@app.get("/led")
def get_led():
    if lora is None or not lora.available:
        return {"on": False, "available": False}
    state = lora.get_device_state_snapshot()
    return {
        "on": state["led_on"],
        "available": True,
        "cached": True,
        "updated_at": state["updated_at"],
        "age_ms": state["age_ms"],
        "error": state["error"],
    }


# ------------------------------------------------------------
# POST /led
# 控制 LED 开关
# 请求体 JSON: { "on": true } 或 { "on": false }
#
# 工作流程：
#   1. 检查 LoRa 是否可用，不可用直接返回错误
#   2. 把布尔值转成 "ON"/"OFF" 字符串
#   3. 调用 lora.send_command() 发送 LoRa 命令
#      send_command 内部调用 send_device_command("LED", action)
#      构造 CMD,LED,ON,<crc> 消息，等待 ACK,LED,ON,<crc>
#      超时重试 3 次
#   4. 收到 ACK → 返回成功
#   5. 没收到 ACK → 返回当前 LED 状态 + 错误信息
# ------------------------------------------------------------
@app.post("/led")
def set_led(state: LedState, user=Depends(require_control_auth)):
    if lora is None or not lora.available:
        return {"on": False, "available": False, "error": "LoRa not available"}

    action = "ON" if state.on else "OFF"
    result = lora.send_command(action)

    if result["success"]:
        return {"on": state.on, "available": True}
    else:
        cached_state = lora.get_device_state_snapshot()
        return {"on": cached_state["led_on"], "available": True, "error": result["message"]}


# ------------------------------------------------------------
# GET /fan
# 查询风扇当前状态
# 返回：{ "on": true/false, "available": true/false }
#   on:        风扇当前是开还是关
#   available: LoRa 模块是否可用
# 如果 LoRa 不可用，available=false，on 固定为 false
# ------------------------------------------------------------
@app.get("/fan")
def get_fan():
    if lora is None or not lora.available:
        return {"on": False, "available": False}
    state = lora.get_device_state_snapshot()
    return {
        "on": state["fan_on"],
        "available": True,
        "cached": True,
        "updated_at": state["updated_at"],
        "age_ms": state["age_ms"],
        "error": state["error"],
    }


# ------------------------------------------------------------
# POST /fan
# 控制风扇开关
# 请求体 JSON: { "on": true } 或 { "on": false }
#
# 工作流程与 /led 相同，只是调用 lora.send_fan_command()
# 内部发送 CMD,FAN,ON,<crc> 或 CMD,FAN,OFF,<crc>
# 树莓派 B 收到后通过 GPIO17 控制继电器
# 注意：树莓派 B 关风扇用的是 GPIO.setup(IN) 而不是 LOW
# ------------------------------------------------------------
@app.post("/fan")
def set_fan(state: FanState, user=Depends(require_control_auth)):
    if lora is None or not lora.available:
        return {"on": False, "available": False, "error": "LoRa not available"}

    action = "ON" if state.on else "OFF"
    result = lora.send_fan_command(action)

    if result["success"]:
        return {"on": state.on, "available": True}
    else:
        cached_state = lora.get_device_state_snapshot()
        return {"on": cached_state["fan_on"], "available": True, "error": result["message"]}


# ------------------------------------------------------------
# GET /uptime
# 系统运行时长接口
# 返回后端服务自启动以来的运行时间
# {
#   "uptime_seconds": 3600,
#   "uptime": "01:00:00"
# }
# ------------------------------------------------------------
@app.get("/uptime")
def get_uptime():
    elapsed = int(time.time() - APP_START_TIME)
    h = elapsed // 3600
    m = (elapsed % 3600) // 60
    s = elapsed % 60
    return {
        "uptime_seconds": elapsed,
        "uptime": f"{h:02d}:{m:02d}:{s:02d}"
    }


# ------------------------------------------------------------
# GET /lora/status
# LoRa 心跳状态查询接口
# 前端每 3 秒调用一次，用来判断树莓派 B 是否在线
#
# 返回格式：
# {
#   "online": true/false,        // 当前是否认为设备在线
#   "fail_count": 0~N,           // 连续失败的次数
#   "last_pong_time": 12345.67,  // 上次收到 PONG 的 Unix 时间戳（秒）
#   "message": "设备在线" 或 "设备连接失败"
# }
#
# 加锁原因：
#   heartbeat_loop 后台线程和 /lora/status 接口会并发读写这些变量
#   用 with heartbeat_lock 保证读取时状态是一致的
# ------------------------------------------------------------
@app.get("/lora/status")
def lora_status():
    with heartbeat_lock:
        if lora is None or not lora.available:
            return {
                "online": False,
                "fail_count": lora_fail_count,
                "last_pong_time": last_pong_time,
                "message": "设备连接失败"
            }
        return {
            "online": lora_online,
            "fail_count": lora_fail_count,
            "last_pong_time": last_pong_time,
            "message": "设备在线" if lora_online else "设备连接失败"
        }

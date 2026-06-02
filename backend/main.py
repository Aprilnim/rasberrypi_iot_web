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
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import smbus2 as smbus
import math
import threading
import time

# ------------------------------------------------------------
# 创建 FastAPI 应用实例
# add_middleware(CORSMiddleware, ...):
#   允许任意来源的前端页面调用本后端接口
#   开发调试阶段开"*"，生产环境建议改成具体域名
# ------------------------------------------------------------
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
# GET /temp
# 读取温度传感器数据
# 通道：0x42（YL-40 模块上 CH2 接的是热敏电阻）
# 返回：{ "temperature": 25.67, "raw": 128 }
#   temperature: 换算后的摄氏度
#   raw:         ADC 原始值（0~255）
# ------------------------------------------------------------
@app.get("/temp")
def temp():

    # CH2 热敏电阻
    raw = get_adc(0x42)

    return {
        "temperature": get_temp(raw),
        "raw": raw
    }


# ------------------------------------------------------------
# GET /light
# 读取光照传感器数据
# 通道：0x40（YL-40 模块上 CH0 接的是光敏电阻）
# 返回：{ "light_percent": 78.5, "raw": 55 }
#   light_percent: 光照百分比（ADC 值越低光照越强，所以用 255-raw）
#   raw:           ADC 原始值
# ------------------------------------------------------------
@app.get("/light")
def light():

    # CH0 光敏电阻
    raw = get_adc(0x40)

    if raw is None:
        raw = 0

    # 转换为百分比（0=最亮，100=最暗）
    percent = round(
        ((255.0 - raw) / 255.0) * 100.0,
        1
    )

    return {
        "light_percent": percent,
        "raw": raw
    }


# ------------------------------------------------------------
# GET /sensor
# 综合传感器接口，一次请求同时返回温度和光照
# 前端 dashboard 用这个接口，每秒刷新一次
# 返回：{ "temperature": 25.67, "light_percent": 78.5 }
# ------------------------------------------------------------
@app.get("/sensor")
def sensor():

    # 读取光照
    light_raw = get_adc(0x40)

    # 读取温度
    temp_raw = get_adc(0x42)

    if light_raw is None:
        light_raw = 0

    return {
        "temperature": get_temp(temp_raw),
        "light_percent": round(
            ((255.0 - light_raw) / 255.0) * 100.0,
            1
        )
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
    return {"on": lora.get_led_state(), "available": True}


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
def set_led(state: LedState):
    if lora is None or not lora.available:
        return {"on": False, "available": False, "error": "LoRa not available"}

    action = "ON" if state.on else "OFF"
    result = lora.send_command(action)

    if result["success"]:
        return {"on": state.on, "available": True}
    else:
        return {"on": lora.get_led_state(), "available": True, "error": result["message"]}


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
    return {"on": lora.get_fan_state(), "available": True}


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
def set_fan(state: FanState):
    if lora is None or not lora.available:
        return {"on": False, "available": False, "error": "LoRa not available"}

    action = "ON" if state.on else "OFF"
    result = lora.send_fan_command(action)

    if result["success"]:
        return {"on": state.on, "available": True}
    else:
        return {"on": lora.get_fan_state(), "available": True, "error": result["message"]}


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

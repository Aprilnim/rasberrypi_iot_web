from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import smbus2 as smbus
import math

# 创建 FastAPI 实例
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# I2C总线1（树莓派默认I2C接口）
bus = smbus.SMBus(1)

# PCF8591 I2C地址
ADDR = 0x48

# =========================
# LoRa 模块初始化
# =========================
from lora import LoRaNode

try:
    lora = LoRaNode()
    LORA_AVAILABLE = lora.available
except Exception as e:
    print(f"[LoRa] Failed to initialize: {e}")
    lora = None
    LORA_AVAILABLE = False


class LedState(BaseModel):
    on: bool


# =========================
# 读取PCF8591 ADC通道
# =========================
def get_adc(ch):
    try:
        # 选择ADC通道
        bus.write_byte(ADDR, ch)

        # 第一次读取无效（PCF8591特性）
        bus.read_byte(ADDR)

        # 第二次读取才是真实数据
        return bus.read_byte(ADDR)

    except:
        return None


# =========================
# 热敏电阻ADC值转换为温度
# =========================
def get_temp(v):

    # 读取失败
    if v is None:
        return 0

    # 异常值过滤
    if v <= 5 or v >= 250:
        return 0

    try:
        # 热敏电阻阻值换算
        r = (255.0 / v) - 1.0

        # Beta公式计算温度
        t = 1.0 / (
            math.log(r) / 3950.0 +
            1.0 / 298.15
        ) - 273.15

        return round(t, 2)

    except:
        return 0


# =========================
# 首页接口
# =========================
@app.get("/")
def root():

    return {
        "status": "running",
        "device": "YL-40"
    }


# =========================
# 温度接口
# URL: /temp
# =========================
@app.get("/temp")
def temp():

    # CH2 热敏电阻
    raw = get_adc(0x42)

    return {
        "temperature": get_temp(raw),
        "raw": raw
    }


# =========================
# 光照接口
# URL: /light
# =========================
@app.get("/light")
def light():

    # CH0 光敏电阻
    raw = get_adc(0x40)

    if raw is None:
        raw = 0

    # 转换为百分比
    percent = round(
        ((255.0 - raw) / 255.0) * 100.0,
        1
    )

    return {
        "light_percent": percent,
        "raw": raw
    }


# =========================
# 综合传感器接口
# URL: /sensor
# =========================
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


# =========================
# LED 状态接口
# URL: /led
# =========================
@app.get("/led")
def get_led():
    if not LORA_AVAILABLE or lora is None:
        return {"on": False, "available": False}
    return {"on": lora.get_led_state(), "available": True}


# =========================
# LED 控制接口
# URL: /led
# =========================
@app.post("/led")
def set_led(state: LedState):
    if not LORA_AVAILABLE or lora is None:
        return {"on": False, "available": False, "error": "LoRa not available"}

    action = "ON" if state.on else "OFF"
    result = lora.send_command(action)

    if result["success"]:
        return {"on": state.on, "available": True}
    else:
        return {"on": lora.get_led_state(), "available": True, "error": result["message"]}

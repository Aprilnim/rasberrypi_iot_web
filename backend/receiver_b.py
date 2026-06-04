#!/usr/bin/env python3
"""
树莓派B LoRa 接收端脚本
接收 CMD,LED,ON/OFF 命令，控制 GPIO18 LED，并返回 ACK
接收 CMD,FAN,ON/OFF 命令，控制 GPIO24 风扇继电器，并返回 ACK
接收 PING 命令，返回 PONG
"""

import sys
import time
import traceback
from typing import Tuple

try:
    import serial
    import RPi.GPIO as GPIO
except Exception:
    print("[Receiver B] FATAL: import error", file=sys.stderr, flush=True)
    traceback.print_exc()
    sys.exit(1)

PORT = "/dev/ttyS0"
BAUDRATE = 9600
M0 = 22
M1 = 27
LED_PIN = 18
FAN_PIN = 24


def calc_crc(payload: str) -> int:
    return sum(payload.encode()) % 256


def build_message(payload: str) -> str:
    crc = calc_crc(payload)
    return f"{payload},{crc}"


def verify_crc(message: str) -> Tuple[bool, str]:
    parts = message.split(",")
    if len(parts) < 2:
        return False, ""
    received_crc = parts[-1]
    payload = ",".join(parts[:-1])
    try:
        calculated_crc = calc_crc(payload)
        if str(calculated_crc) == received_crc:
            return True, payload
        return False, ""
    except Exception:
        return False, ""


def fan_on():
    """开风扇：设为输出 LOW

    风扇继电器 IN 接 GPIO24，模块需要 5V 高电平条件，
    但树莓派 GPIO 只能输出 3.3V；实测用 GPIO24 拉低可触发风扇打开。
    """
    GPIO.setup(FAN_PIN, GPIO.OUT)
    GPIO.output(FAN_PIN, GPIO.LOW)


def fan_off():
    """关风扇：释放引脚为输入（悬空）

    对这个继电器模块，GPIO HIGH 不是可靠的 5V 关闭信号，
    GPIO LOW 又会触发风扇打开；输入悬空状态用于停止风扇。
    """
    GPIO.setup(FAN_PIN, GPIO.IN)
    time.sleep(0.5) # 确保继电器有时间响应
    

def main():
    print("[Receiver B] Starting...", flush=True)
    led_is_on = False
    fan_is_on = False
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(M0, GPIO.OUT)
    GPIO.setup(M1, GPIO.OUT)
    GPIO.setup(LED_PIN, GPIO.OUT)
    GPIO.output(M0, GPIO.LOW)
    GPIO.output(M1, GPIO.LOW)
    GPIO.output(LED_PIN, GPIO.LOW)
    # 风扇默认关闭（输入模式 = 悬空 = 风扇停）
    fan_off()
    time.sleep(1)
    print("[Receiver B] GPIO initialized", flush=True)

    ser = serial.Serial(PORT, BAUDRATE, timeout=1)
    print(f"[Receiver B] Listening on {PORT} at {BAUDRATE} baud", flush=True)
    heartbeat_connected = False

    try:
        while True:
            if ser.in_waiting > 0:
                try:
                    raw = ser.readline().decode().strip()
                except UnicodeDecodeError:
                    continue

                if not raw:
                    continue

                ok, payload = verify_crc(raw)
                if not ok:
                    print(f"CRC ERROR: raw={raw}")
                    continue

                # 处理 PING
                if payload == "PING":
                    pong = build_message("PONG")
                    ser.write((pong + "\n").encode())
                    ser.flush()
                    if not heartbeat_connected:
                        heartbeat_connected = True
                        print(f"RX: {raw}", flush=True)
                        print(f"TX: {pong}", flush=True)
                        print("[Receiver B] Heartbeat connected", flush=True)
                    continue

                # 处理状态查询：QUERY,STATE
                if payload == "QUERY,STATE":
                    led_state = "ON" if led_is_on else "OFF"
                    fan_state = "ON" if fan_is_on else "OFF"
                    state = build_message(f"STATE,LED,{led_state},FAN,{fan_state}")
                    ser.write((state + "\n").encode())
                    ser.flush()
                    continue

                print(f"RX: {raw}")

                # 处理命令：CMD,<DEVICE>,<ACTION>
                parts = payload.split(",")
                if len(parts) != 3 or parts[0] != "CMD":
                    print(f"INVALID: {payload}")
                    continue

                device = parts[1]
                action = parts[2]

                if device == "LED":
                    if action == "ON":
                        GPIO.output(LED_PIN, GPIO.HIGH)
                        led_is_on = True
                        ack = build_message("ACK,LED,ON")
                        ser.write((ack + "\n").encode())
                        ser.flush()
                        print("LED ON")
                    elif action == "OFF":
                        GPIO.output(LED_PIN, GPIO.LOW)
                        led_is_on = False
                        ack = build_message("ACK,LED,OFF")
                        ser.write((ack + "\n").encode())
                        ser.flush()
                        print("LED OFF")
                    else:
                        print(f"UNKNOWN LED ACTION: {action}")

                elif device == "FAN":
                    if action == "ON":
                        fan_on()
                        fan_is_on = True
                        ack = build_message("ACK,FAN,ON")
                        ser.write((ack + "\n").encode())
                        ser.flush()
                        print("FAN ON")
                    elif action == "OFF":
                        fan_off()
                        fan_is_on = False
                        ack = build_message("ACK,FAN,OFF")
                        ser.write((ack + "\n").encode())
                        ser.flush()
                        print("FAN OFF")
                    else:
                        print(f"UNKNOWN FAN ACTION: {action}")

                else:
                    print(f"UNKNOWN DEVICE: {device}")

            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\n[Receiver B] Shutting down...")
    finally:
        # 安全退出：关风扇、关 LED、关串口、清理 GPIO
        fan_off()
        GPIO.output(LED_PIN, GPIO.LOW)
        ser.close()
        GPIO.cleanup()
        print("[Receiver B] Cleaned up")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("[Receiver B] FATAL: startup/runtime error", file=sys.stderr, flush=True)
        traceback.print_exc()
        sys.exit(1)

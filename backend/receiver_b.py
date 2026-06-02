#!/usr/bin/env python3
"""
树莓派B LoRa 接收端脚本
接收 CMD,LED,ON/OFF 命令，控制 GPIO18 LED，并返回 ACK
接收 CMD,FAN,ON/OFF 命令，控制 GPIO17 风扇继电器，并返回 ACK
接收 PING 命令，返回 PONG
"""

import serial
import time
import RPi.GPIO as GPIO
from typing import Tuple

PORT = "/dev/ttyS0"
BAUDRATE = 9600
M0 = 22
M1 = 27
LED_PIN = 18
FAN_PIN = 17


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
    """开风扇：设为输出 HIGH

    风扇继电器 IN 接 GPIO17，实测 HIGH 时风扇转。
    """
    GPIO.setup(FAN_PIN, GPIO.OUT)
    GPIO.output(FAN_PIN, GPIO.HIGH)


def fan_off():
    """关风扇：设为输入（悬空），不能用 LOW

    实测该继电器模块 GPIO LOW 也会触发风扇转，
    只有输入/悬空状态才能停止风扇。
    """
    GPIO.setup(FAN_PIN, GPIO.IN)


def main():
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

    ser = serial.Serial(PORT, BAUDRATE, timeout=1)
    print(f"[Receiver B] Listening on {PORT} at {BAUDRATE} baud")

    try:
        while True:
            if ser.in_waiting > 0:
                try:
                    raw = ser.readline().decode().strip()
                except UnicodeDecodeError:
                    continue

                if not raw:
                    continue

                print(f"RX: {raw}")

                ok, payload = verify_crc(raw)
                if not ok:
                    print(f"CRC ERROR: raw={raw}")
                    continue

                # 处理 PING
                if payload == "PING":
                    pong = build_message("PONG")
                    ser.write((pong + "\n").encode())
                    ser.flush()
                    print(f"TX: {pong}")
                    continue

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
                        ack = build_message("ACK,LED,ON")
                        ser.write((ack + "\n").encode())
                        ser.flush()
                        print(f"TX: {ack}")
                        print("LED ON")
                    elif action == "OFF":
                        GPIO.output(LED_PIN, GPIO.LOW)
                        ack = build_message("ACK,LED,OFF")
                        ser.write((ack + "\n").encode())
                        ser.flush()
                        print(f"TX: {ack}")
                        print("LED OFF")
                    else:
                        print(f"UNKNOWN LED ACTION: {action}")

                elif device == "FAN":
                    if action == "ON":
                        fan_on()
                        ack = build_message("ACK,FAN,ON")
                        ser.write((ack + "\n").encode())
                        ser.flush()
                        print(f"TX: {ack}")
                        print("FAN ON")
                    elif action == "OFF":
                        fan_off()
                        ack = build_message("ACK,FAN,OFF")
                        ser.write((ack + "\n").encode())
                        ser.flush()
                        print(f"TX: {ack}")
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
    main()

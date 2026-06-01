#!/usr/bin/env python3
"""
树莓派B LoRa 接收端脚本
接收 CMD,LED,ON/OFF 命令，控制 GPIO18 LED，并返回 ACK
"""

import serial
import time
import RPi.GPIO as GPIO

PORT = "/dev/ttyS0"
BAUDRATE = 9600
M0 = 22
M1 = 27
LED_PIN = 18


def calc_crc(payload: str) -> int:
    return sum(payload.encode()) % 256


def build_message(payload: str) -> str:
    crc = calc_crc(payload)
    return f"{payload},{crc}"


def verify_crc(message: str) -> tuple[bool, str]:
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


def main():
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(M0, GPIO.OUT)
    GPIO.setup(M1, GPIO.OUT)
    GPIO.setup(LED_PIN, GPIO.OUT)
    GPIO.output(M0, GPIO.LOW)
    GPIO.output(M1, GPIO.LOW)
    GPIO.output(LED_PIN, GPIO.LOW)
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

                parts = payload.split(",")
                if len(parts) != 3 or parts[0] != "CMD" or parts[1] != "LED":
                    print(f"INVALID: {payload}")
                    continue

                action = parts[2]
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
                    print(f"UNKNOWN ACTION: {action}")

            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\n[Receiver B] Shutting down...")
    finally:
        GPIO.output(LED_PIN, GPIO.LOW)
        ser.close()
        GPIO.cleanup()
        print("[Receiver B] Cleaned up")


if __name__ == "__main__":
    main()

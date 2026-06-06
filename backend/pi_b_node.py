#!/usr/bin/env python3
"""树莓派 B 边缘节点：永不上 MQTT，只通过 LoRa 上报 YL40 和执行控制。"""

import hashlib
import hmac
import os
import sys
import time
import traceback
from collections import OrderedDict

import RPi.GPIO as GPIO
import serial
import smbus2 as smbus


PORT = "/dev/ttyS0"
BAUDRATE = 9600
M0 = 22
M1 = 27
LED_PIN = 18
FAN_PIN = 24
PCF8591_ADDR = 0x48
HMAC_SECRET = os.environ.get("LORA_HMAC_SECRET", "").strip()
START_TIME = time.time()

if not HMAC_SECRET:
    print("[Pi B] FATAL: missing LORA_HMAC_SECRET", file=sys.stderr, flush=True)
    sys.exit(1)

next_b_seq_value = int(time.time() * 1000)
last_a_seq = 0
last_a_raw = None
last_a_response = None


def calc_crc(payload):
    crc = 0xFFFF
    for byte in payload.encode():
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def calc_hmac(signed_payload):
    return hmac.new(
        HMAC_SECRET.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:16]


def next_b_seq():
    global next_b_seq_value
    now_ms = int(time.time() * 1000)
    next_b_seq_value = max(next_b_seq_value + 1, now_ms)
    return next_b_seq_value


def build_message(payload):
    seq = next_b_seq()
    signed_payload = f"{payload},{seq}"
    signature = calc_hmac(signed_payload)
    crc_payload = f"{signed_payload},{signature}"
    return f"{crc_payload},{calc_crc(crc_payload)}"


def verify_message(raw):
    global last_a_seq, last_a_raw
    parts = raw.split(",")
    if len(parts) < 4:
        return None, False
    try:
        received_crc = parts[-1]
        received_hmac = parts[-2]
        seq = int(parts[-3])
        payload = ",".join(parts[:-3])
        signed_payload = f"{payload},{seq}"
        crc_payload = f"{signed_payload},{received_hmac}"
        if str(calc_crc(crc_payload)) != received_crc:
            return None, False
        if not hmac.compare_digest(calc_hmac(signed_payload), received_hmac):
            return None, False
        if seq < last_a_seq:
            print(f"[Pi B] REPLAY BLOCKED: seq={seq}, last={last_a_seq}", flush=True)
            return None, False
        if seq == last_a_seq:
            if raw != last_a_raw:
                print(f"[Pi B] Duplicate sequence mismatch: seq={seq}", flush=True)
                return None, False
            return payload, True
        last_a_seq = seq
        last_a_raw = raw
        return payload, False
    except Exception:
        return None, False


def fan_on():
    """低电平触发继电器，打开风扇。"""
    GPIO.setup(FAN_PIN, GPIO.OUT)
    GPIO.output(FAN_PIN, GPIO.LOW)


def fan_off():
    """释放 GPIO24 为输入悬空，关闭风扇。"""
    GPIO.setup(FAN_PIN, GPIO.IN)


def read_light_raw(bus):
    try:
        bus.write_byte(PCF8591_ADDR, 0x40)
        bus.read_byte(PCF8591_ADDR)
        return bus.read_byte(PCF8591_ADDR)
    except Exception as exc:
        print(f"[Pi B] YL40 read failed: {exc}", flush=True)
        return None


def send_payload(ser, payload):
    message = build_message(payload)
    ser.write((message + "\n").encode("utf-8"))
    ser.flush()
    return message


def main():
    global last_a_response

    GPIO.setmode(GPIO.BCM)
    GPIO.setup(M0, GPIO.OUT)
    GPIO.setup(M1, GPIO.OUT)
    GPIO.setup(LED_PIN, GPIO.OUT)
    GPIO.output(M0, GPIO.LOW)
    GPIO.output(M1, GPIO.LOW)
    GPIO.output(LED_PIN, GPIO.LOW)
    fan_off()

    bus = smbus.SMBus(1)
    ser = serial.Serial(PORT, BAUDRATE, timeout=0.2)
    led_is_on = False
    fan_is_on = False
    handled_commands = OrderedDict()
    heartbeat_connected = False

    print("[Pi B] GPIO, I2C and LoRa initialized", flush=True)
    try:
        while True:
            if ser.in_waiting <= 0:
                time.sleep(0.02)
                continue

            try:
                raw = ser.readline().decode("utf-8").strip()
            except UnicodeDecodeError:
                continue
            if not raw:
                continue

            payload, duplicate_seq = verify_message(raw)
            if payload is None:
                print(f"[Pi B] Invalid LoRa message: {raw}", flush=True)
                continue
            if duplicate_seq:
                if last_a_response:
                    send_payload(ser, last_a_response)
                continue
            last_a_response = None

            if payload == "PING":
                last_a_response = f"PONG,{int(time.time() - START_TIME)}"
                send_payload(ser, last_a_response)
                if not heartbeat_connected:
                    heartbeat_connected = True
                    print("[Pi B] LoRa gateway connected", flush=True)
                continue

            if payload == "QUERY,STATE":
                led_state = "ON" if led_is_on else "OFF"
                fan_state = "ON" if fan_is_on else "OFF"
                last_a_response = f"STATE,LED,{led_state},FAN,{fan_state}"
                send_payload(ser, last_a_response)
                continue

            if payload == "QUERY,YL40":
                raw_light = read_light_raw(bus)
                if raw_light is not None:
                    last_a_response = f"TELEMETRY,YL40,{raw_light}"
                    send_payload(ser, last_a_response)
                continue

            parts = payload.split(",")
            if len(parts) != 4 or parts[0] != "CMD":
                print(f"[Pi B] Invalid command: {payload}", flush=True)
                continue

            _, device, action, cmd_id = parts
            if device not in ("LED", "FAN") or action not in ("ON", "OFF"):
                print(f"[Pi B] Rejected command: {payload}", flush=True)
                continue

            cached_ack = handled_commands.get(cmd_id)
            if cached_ack:
                last_a_response = cached_ack
                send_payload(ser, last_a_response)
                continue

            if device == "LED":
                GPIO.output(LED_PIN, GPIO.HIGH if action == "ON" else GPIO.LOW)
                led_is_on = action == "ON"
            else:
                fan_on() if action == "ON" else fan_off()
                fan_is_on = action == "ON"

            ack_payload = f"ACK,{device},{action},{cmd_id}"
            handled_commands[cmd_id] = ack_payload
            handled_commands.move_to_end(cmd_id)
            while len(handled_commands) > 200:
                handled_commands.popitem(last=False)

            last_a_response = ack_payload
            send_payload(ser, last_a_response)
            print(f"[Pi B] {device} {action}", flush=True)
    finally:
        fan_off()
        GPIO.output(LED_PIN, GPIO.LOW)
        ser.close()
        bus.close()
        GPIO.cleanup()
        print("[Pi B] Cleaned up", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("[Pi B] FATAL", file=sys.stderr, flush=True)
        traceback.print_exc()
        sys.exit(1)

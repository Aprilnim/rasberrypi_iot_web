#!/usr/bin/env python3
"""树莓派 C 采集节点：读取 SHT35-485 和 YL40，并直接向局域网 EMQX 发布数据。"""

import json
import os
import threading
import time

import paho.mqtt.client as mqtt
import serial
import smbus2 as smbus


TOPIC_PREFIX = "yl40iot/v1/nodes/pi-c"
MQTT_HOST = os.environ.get("MQTT_HOST", "192.168.10.70")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_PI_C_USERNAME", "pi-c")
MQTT_PASSWORD = os.environ.get("MQTT_PI_C_PASSWORD", "123")
SERIAL_PORT = os.environ.get("SHT35_SERIAL_PORT", "/dev/ttyS0")
I2C_BUS = int(os.environ.get("YL40_I2C_BUS", "1"))
PCF8591_ADDR = int(os.environ.get("YL40_PCF8591_ADDR", "0x48"), 0)
YL40_INTERVAL_SECONDS = float(os.environ.get("YL40_INTERVAL_SECONDS", "0.5"))
SHT35_INTERVAL_SECONDS = float(os.environ.get("SHT35_INTERVAL_SECONDS", "1.5"))
HEARTBEAT_INTERVAL_SECONDS = float(os.environ.get("PI_C_HEARTBEAT_INTERVAL_SECONDS", "2"))
REQUEST_FRAME = bytes.fromhex("01 03 00 00 00 02 C4 0B")
START_TIME = time.time()

if not MQTT_USERNAME or not MQTT_PASSWORD:
    raise RuntimeError("MQTT pi-c username/password must be configured")


def now_ms():
    return int(time.time() * 1000)


def encode(data):
    return json.dumps(data, separators=(",", ":"))


def modbus_crc(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def parse_response(frame):
    if len(frame) != 9 or frame[:3] != b"\x01\x03\x04":
        raise ValueError("invalid SHT35 Modbus response")
    expected_crc = modbus_crc(frame[:-2])
    received_crc = frame[-2] | (frame[-1] << 8)
    if expected_crc != received_crc:
        raise ValueError("SHT35 Modbus CRC mismatch")

    raw_temperature = int.from_bytes(frame[3:5], "big")
    raw_humidity = int.from_bytes(frame[5:7], "big")
    if raw_temperature >= 0x8000:
        raw_temperature -= 0x10000
    return raw_temperature, raw_humidity


def read_light_raw(bus):
    bus.write_byte(PCF8591_ADDR, 0x40)
    bus.read_byte(PCF8591_ADDR)
    return bus.read_byte(PCF8591_ADDR)


def publish_error(client, device, error):
    client.publish(
        f"{TOPIC_PREFIX}/events/error",
        encode(
            {
                "node": "pi-c",
                "device": device,
                "error": str(error),
                "online": True,
                "ts_ms": now_ms(),
            }
        ),
        qos=1,
        retain=False,
    )


def publish_sht35_loop(client, stop_event):
    ser = None
    while not stop_event.is_set():
        started_at = time.time()
        try:
            if ser is None:
                ser = serial.Serial(
                    SERIAL_PORT,
                    9600,
                    bytesize=8,
                    parity=serial.PARITY_NONE,
                    stopbits=1,
                    timeout=1,
                )
            ser.reset_input_buffer()
            ser.write(REQUEST_FRAME)
            ser.flush()
            frame = ser.read(9)
            raw_temperature, raw_humidity = parse_response(frame)
            client.publish(
                f"{TOPIC_PREFIX}/telemetry/sht35",
                encode(
                    {
                        "node": "pi-c",
                        "device": "sht35",
                        "temperature": round(raw_temperature / 10.0, 1),
                        "humidity": round(raw_humidity / 10.0, 1),
                        "raw_temperature": raw_temperature,
                        "raw_humidity": raw_humidity,
                        "ts_ms": now_ms(),
                    }
                ),
                qos=1,
                retain=True,
            )
        except Exception as exc:
            publish_error(client, "sht35", exc)
            try:
                if ser is not None:
                    ser.close()
            except Exception:
                pass
            ser = None

        elapsed = time.time() - started_at
        stop_event.wait(max(0.0, SHT35_INTERVAL_SECONDS - elapsed))


def publish_yl40_loop(client, stop_event):
    bus = None
    while not stop_event.is_set():
        started_at = time.time()
        try:
            if bus is None:
                bus = smbus.SMBus(I2C_BUS)
            raw_light = read_light_raw(bus)
            light_percent = round(((255.0 - raw_light) / 255.0) * 100.0, 1)
            client.publish(
                f"{TOPIC_PREFIX}/telemetry/yl40",
                encode(
                    {
                        "node": "pi-c",
                        "device": "yl40",
                        "light_percent": light_percent,
                        "raw_light": raw_light,
                        "ts_ms": now_ms(),
                    }
                ),
                qos=1,
                retain=True,
            )
        except Exception as exc:
            publish_error(client, "yl40", exc)
            try:
                if bus is not None:
                    bus.close()
            except Exception:
                pass
            bus = None

        elapsed = time.time() - started_at
        stop_event.wait(max(0.0, YL40_INTERVAL_SECONDS - elapsed))


def main():
    client = mqtt.Client(client_id="pi-c", clean_session=True)
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.will_set(
        f"{TOPIC_PREFIX}/availability",
        encode({"node": "pi-c", "online": False, "ts_ms": now_ms()}),
        qos=1,
        retain=True,
    )
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_start()
    client.publish(
        f"{TOPIC_PREFIX}/availability",
        encode({"node": "pi-c", "online": True, "ts_ms": now_ms()}),
        qos=1,
        retain=True,
    )

    stop_event = threading.Event()
    threading.Thread(target=publish_sht35_loop, args=(client, stop_event), daemon=True).start()
    threading.Thread(target=publish_yl40_loop, args=(client, stop_event), daemon=True).start()

    try:
        while True:
            client.publish(
                f"{TOPIC_PREFIX}/heartbeat",
                encode(
                    {
                        "node": "pi-c",
                        "online": True,
                        "uptime": int(time.time() - START_TIME),
                        "ts_ms": now_ms(),
                    }
                ),
                qos=0,
                retain=False,
            )
            time.sleep(HEARTBEAT_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        stop_event.set()
    finally:
        client.publish(
            f"{TOPIC_PREFIX}/availability",
            encode({"node": "pi-c", "online": False, "ts_ms": now_ms()}),
            qos=1,
            retain=True,
        )
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()

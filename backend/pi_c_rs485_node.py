#!/usr/bin/env python3
"""树莓派 C SHT35-485 采集节点，直接向局域网 EMQX 发布数据。"""

import json
import os
import time

import paho.mqtt.client as mqtt
import serial


TOPIC_PREFIX = "yl40iot/v1/nodes/pi-c"
MQTT_HOST = os.environ.get("MQTT_HOST", "192.168.10.70")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_PI_C_USERNAME", "pi-c")
MQTT_PASSWORD = os.environ.get("MQTT_PI_C_PASSWORD", "")
SERIAL_PORT = os.environ.get("SHT35_SERIAL_PORT", "/dev/ttyS0")
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

    while True:
        try:
            with serial.Serial(
                SERIAL_PORT,
                9600,
                bytesize=8,
                parity=serial.PARITY_NONE,
                stopbits=1,
                timeout=1,
            ) as ser:
                while True:
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
                    time.sleep(2)
        except Exception as exc:
            client.publish(
                f"{TOPIC_PREFIX}/events/error",
                encode(
                    {
                        "node": "pi-c",
                        "device": "sht35",
                        "error": str(exc),
                        "online": False,
                        "ts_ms": now_ms(),
                    }
                ),
                qos=1,
                retain=False,
            )
            time.sleep(2)


if __name__ == "__main__":
    main()

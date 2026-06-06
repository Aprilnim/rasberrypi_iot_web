#!/usr/bin/env python3
"""MQTT 与树莓派 B LoRa 链路之间的唯一网关进程。"""

import hashlib
import hmac
import json
import os
import queue
import threading
import time
import uuid
from collections import OrderedDict

import paho.mqtt.client as mqtt
import RPi.GPIO as GPIO
import serial


TOPIC_PREFIX = "yl40iot/v1"
PI_B_PREFIX = f"{TOPIC_PREFIX}/nodes/pi-b"
GATEWAY_PREFIX = f"{TOPIC_PREFIX}/gateways/lora-a"

PORT = os.environ.get("LORA_PORT", "/dev/ttyS0")
BAUDRATE = int(os.environ.get("LORA_BAUDRATE", "9600"))
M0 = 22
M1 = 27
HMAC_SECRET = os.environ.get("LORA_HMAC_SECRET", "").strip()

MQTT_HOST = os.environ.get("MQTT_HOST", "192.168.10.70")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_GATEWAY_USERNAME", "lora-gateway")
MQTT_PASSWORD = os.environ.get("MQTT_GATEWAY_PASSWORD", "")
MQTT_CLIENT_ID = os.environ.get("MQTT_GATEWAY_CLIENT_ID", "lora-a")
START_TIME = time.time()
CONTROL_ACK_RETRIES = 3
CONTROL_ACK_TIMEOUT = 1.5
PI_B_OFFLINE_GRACE = 12.0
STATE_QUERY_INTERVAL = 10.0
LOCK_BUSY = object()

if not HMAC_SECRET:
    raise RuntimeError("missing LORA_HMAC_SECRET")
if not MQTT_USERNAME or not MQTT_PASSWORD:
    raise RuntimeError("MQTT gateway username/password must be configured")


class LoRaMQTTGateway:
    def __init__(self):
        self._next_seq_value = int(time.time() * 1000)
        self._seq_lock = threading.Lock()
        self._last_b_seq = 0
        self._last_b_raw = None
        self._response_queue = queue.Queue()
        self._transaction_lock = threading.Lock()
        self._command_lock = threading.Lock()
        self._pending_command_lock = threading.Lock()
        self._pending_command_count = 0
        self._stop_event = threading.Event()
        self._last_b_message_at = None
        # None 强制首次探测结果写入 retained availability，清除 Broker 中可能残留的旧状态。
        self._b_online = None
        self._last_cmd_id = {"led": None, "fan": None}
        self._recent_results = OrderedDict()
        self._recent_results_lock = threading.Lock()
        self._last_state_query_at = 0.0

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(M0, GPIO.OUT)
        GPIO.setup(M1, GPIO.OUT)
        GPIO.output(M0, GPIO.LOW)
        GPIO.output(M1, GPIO.LOW)
        time.sleep(1)
        self.ser = serial.Serial(PORT, BAUDRATE, timeout=0.2)

        self.mqtt = mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=True)
        if MQTT_USERNAME:
            self.mqtt.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        self.mqtt.will_set(
            f"{GATEWAY_PREFIX}/availability",
            self._json({"online": False, "ts_ms": self._now_ms()}),
            qos=1,
            retain=True,
        )
        self.mqtt.on_connect = self._on_mqtt_connect
        self.mqtt.on_message = self._on_mqtt_message

    @staticmethod
    def _now_ms():
        return int(time.time() * 1000)

    @staticmethod
    def _json(data):
        return json.dumps(data, separators=(",", ":"))

    @staticmethod
    def calc_crc(payload):
        crc = 0xFFFF
        for byte in payload.encode():
            crc ^= byte << 8
            for _ in range(8):
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        return crc

    @staticmethod
    def calc_hmac(signed_payload):
        return hmac.new(
            HMAC_SECRET.encode("utf-8"),
            signed_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:16]

    def next_seq(self):
        with self._seq_lock:
            now_ms = self._now_ms()
            self._next_seq_value = max(self._next_seq_value + 1, now_ms)
            return self._next_seq_value

    def build_message(self, payload):
        seq = self.next_seq()
        signed_payload = f"{payload},{seq}"
        signature = self.calc_hmac(signed_payload)
        crc_payload = f"{signed_payload},{signature}"
        return f"{crc_payload},{self.calc_crc(crc_payload)}"

    def verify_message(self, raw):
        parts = raw.split(",")
        if len(parts) < 4:
            return None
        try:
            received_crc = parts[-1]
            received_hmac = parts[-2]
            seq = int(parts[-3])
            payload = ",".join(parts[:-3])
            signed_payload = f"{payload},{seq}"
            crc_payload = f"{signed_payload},{received_hmac}"
            if str(self.calc_crc(crc_payload)) != received_crc:
                return None
            if not hmac.compare_digest(self.calc_hmac(signed_payload), received_hmac):
                return None
            if seq < self._last_b_seq:
                print(f"[LoRa] Replay blocked: seq={seq}, last={self._last_b_seq}")
                return None
            if seq == self._last_b_seq and raw != self._last_b_raw:
                print(f"[LoRa] Duplicate sequence mismatch: seq={seq}")
                return None
            if seq > self._last_b_seq:
                self._last_b_seq = seq
                self._last_b_raw = raw
            return payload
        except Exception:
            return None

    def start(self):
        self.mqtt.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
        threading.Thread(target=self._serial_reader_loop, daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        self.mqtt.loop_forever()

    def _on_mqtt_connect(self, client, _userdata, _flags, rc, _properties=None):
        if rc != 0:
            print(f"[MQTT] Gateway connection failed: rc={rc}")
            return
        client.subscribe(f"{PI_B_PREFIX}/commands/led/set", qos=1)
        client.subscribe(f"{PI_B_PREFIX}/commands/fan/set", qos=1)
        client.publish(
            f"{GATEWAY_PREFIX}/availability",
            self._json({"online": True, "ts_ms": self._now_ms()}),
            qos=1,
            retain=True,
        )
        client.publish(
            f"{PI_B_PREFIX}/availability",
            self._json(
                {
                    "node": "pi-b",
                    "online": self._b_online is True,
                    "ts_ms": self._now_ms(),
                }
            ),
            qos=1,
            retain=True,
        )
        print("[MQTT] Gateway connected")

    def _on_mqtt_message(self, _client, _userdata, message):
        threading.Thread(
            target=self._process_command_message,
            args=(message,),
            daemon=True,
        ).start()

    def _process_command_message(self, message):
        # 串行校验和执行 MQTT 控制命令，避免同一 cmd_id 并发到达时重复操作硬件。
        self._mark_command_pending()
        try:
            with self._command_lock:
                self._process_command_message_locked(message)
        finally:
            self._mark_command_done()

    def _mark_command_pending(self):
        with self._pending_command_lock:
            self._pending_command_count += 1

    def _mark_command_done(self):
        with self._pending_command_lock:
            self._pending_command_count = max(0, self._pending_command_count - 1)

    def _has_pending_command(self):
        with self._pending_command_lock:
            return self._pending_command_count > 0

    def _process_command_message_locked(self, message):
        device = message.topic.split("/")[-2]
        rejection = None
        data = None
        try:
            data = json.loads(message.payload.decode("utf-8"))
            if not isinstance(data, dict):
                rejection = "payload must be a JSON object"
        except Exception:
            rejection = "invalid JSON"

        cmd_id = data.get("cmd_id") if isinstance(data, dict) else None
        requested_state = data.get("state") if isinstance(data, dict) else None

        if message.retain:
            rejection = "retained commands are forbidden"
        elif rejection is None and data.get("source") != "backend":
            rejection = "invalid command source"
        elif rejection is None and data.get("target") != device:
            rejection = "topic and target mismatch"
        elif rejection is None and requested_state not in ("on", "off"):
            rejection = "state must be on or off"
        elif rejection is None and not self._valid_uuid(cmd_id):
            rejection = "invalid cmd_id"
        elif rejection is None:
            issued_at_ms = data.get("issued_at_ms")
            ttl_ms = data.get("ttl_ms")
            if not isinstance(issued_at_ms, int) or not isinstance(ttl_ms, int):
                rejection = "invalid ttl"
            elif ttl_ms <= 0 or ttl_ms > 10000 or self._now_ms() > issued_at_ms + ttl_ms:
                rejection = "command expired"

        if cmd_id:
            cached = self._get_recent_result(cmd_id)
            if cached:
                self._publish_result(device, cached)
                return

        if rejection:
            result = self._make_result(cmd_id, device, requested_state, "rejected", None, rejection)
            self._remember_and_publish(device, result)
            return

        if not self._b_online:
            result = self._make_result(cmd_id, device, requested_state, "failed", None, "pi-b offline")
            self._remember_and_publish(device, result)
            return

        action = requested_state.upper()
        expected = f"ACK,{device.upper()},{action},{cmd_id}"
        response = self._transact(
            f"CMD,{device.upper()},{action},{cmd_id}",
            expected,
            retries=CONTROL_ACK_RETRIES,
            timeout=CONTROL_ACK_TIMEOUT,
        )
        if response:
            result = self._make_result(cmd_id, device, requested_state, "success", requested_state, None)
            self._publish_state(device, requested_state, cmd_id)
            self._publish_pi_b_heartbeat()
        else:
            actual_state = self._confirm_state_after_ack_timeout(device, requested_state)
            if actual_state == requested_state:
                result = self._make_result(cmd_id, device, requested_state, "success", actual_state, None)
                self._publish_state(device, actual_state, cmd_id)
                self._publish_pi_b_heartbeat()
            else:
                result = self._make_result(
                    cmd_id,
                    device,
                    requested_state,
                    "failed",
                    actual_state,
                    "LoRa ACK timeout",
                )
        self._remember_and_publish(device, result)

    @staticmethod
    def _valid_uuid(value):
        try:
            return str(uuid.UUID(value)) == value
        except Exception:
            return False

    def _make_result(self, cmd_id, device, requested_state, result, actual_state, error):
        return {
            "cmd_id": cmd_id,
            "target": device,
            "requested_state": requested_state,
            "result": result,
            "actual_state": actual_state,
            "error": error,
            "ts_ms": self._now_ms(),
        }

    def _get_recent_result(self, cmd_id):
        with self._recent_results_lock:
            return self._recent_results.get(cmd_id)

    def _remember_and_publish(self, device, result):
        cmd_id = result.get("cmd_id")
        if cmd_id:
            with self._recent_results_lock:
                self._recent_results[cmd_id] = result
                self._recent_results.move_to_end(cmd_id)
                while len(self._recent_results) > 200:
                    self._recent_results.popitem(last=False)
        self._publish_result(device, result)

    def _publish_result(self, device, result):
        self.mqtt.publish(
            f"{PI_B_PREFIX}/commands/{device}/result",
            self._json(result),
            qos=1,
            retain=False,
        )

    def _publish_state(self, device, state, cmd_id=None):
        if cmd_id is not None:
            self._last_cmd_id[device] = cmd_id
        self.mqtt.publish(
            f"{PI_B_PREFIX}/state/{device}",
            self._json(
                {
                    "node": "pi-b",
                    "device": device,
                    "state": state,
                    "last_cmd_id": self._last_cmd_id.get(device),
                    "ts_ms": self._now_ms(),
                }
            ),
            qos=1,
            retain=True,
        )

    def _publish_error(self, error):
        self.mqtt.publish(
            f"{GATEWAY_PREFIX}/events/error",
            self._json(
                {
                    "node": "lora-a",
                    "error": str(error),
                    "online": True,
                    "ts_ms": self._now_ms(),
                }
            ),
            qos=1,
            retain=False,
        )

    def _publish_pi_b_error(self, device, error):
        self.mqtt.publish(
            f"{PI_B_PREFIX}/events/error",
            self._json(
                {
                    "node": "pi-b",
                    "device": device,
                    "error": str(error),
                    "online": self._b_online is True,
                    "ts_ms": self._now_ms(),
                }
            ),
            qos=1,
            retain=False,
        )

    def _publish_pi_b_heartbeat(self, uptime=None):
        self.mqtt.publish(
            f"{PI_B_PREFIX}/heartbeat",
            self._json(
                {
                    "node": "pi-b",
                    "online": True,
                    "uptime": uptime,
                    "ts_ms": self._now_ms(),
                }
            ),
            qos=0,
            retain=False,
        )

    def _publish_b_availability(self, online):
        if online == self._b_online:
            return
        self._b_online = online
        self.mqtt.publish(
            f"{PI_B_PREFIX}/availability",
            self._json({"node": "pi-b", "online": online, "ts_ms": self._now_ms()}),
            qos=1,
            retain=True,
        )

    def _serial_reader_loop(self):
        while not self._stop_event.is_set():
            try:
                raw = self.ser.readline().decode("utf-8").strip()
                if not raw:
                    continue
                payload = self.verify_message(raw)
                if payload is None:
                    print(f"[LoRa] Invalid message: {raw}")
                    self._publish_error("invalid or unauthenticated LoRa message")
                    continue
                self._last_b_message_at = time.time()
                self._publish_b_availability(True)
                self._handle_lora_payload(payload)
            except UnicodeDecodeError:
                continue
            except Exception as exc:
                print(f"[LoRa] Reader error: {exc}")
                self._publish_error(exc)
                time.sleep(0.2)

    def _handle_lora_payload(self, payload):
        parts = payload.split(",")
        if len(parts) == 5 and parts[0] == "STATE" and parts[1] == "LED" and parts[3] == "FAN":
            if parts[2] in ("ON", "OFF") and parts[4] in ("ON", "OFF"):
                self._publish_state("led", parts[2].lower())
                self._publish_state("fan", parts[4].lower())
            self._response_queue.put(payload)
            return

        self._response_queue.put(payload)

    @staticmethod
    def _state_from_payload(payload, device):
        parts = payload.split(",")
        if len(parts) != 5 or parts[0] != "STATE" or parts[1] != "LED" or parts[3] != "FAN":
            return None
        if parts[2] not in ("ON", "OFF") or parts[4] not in ("ON", "OFF"):
            return None
        states = {"led": parts[2].lower(), "fan": parts[4].lower()}
        return states.get(device)

    def _confirm_state_after_ack_timeout(self, device, requested_state):
        response = self._transact("QUERY,STATE", "STATE", retries=2, timeout=1.0)
        if response and response is not LOCK_BUSY:
            actual_state = self._state_from_payload(response, device)
            if actual_state:
                print(
                    f"[LoRa] ACK timeout confirmed by STATE: {device} requested={requested_state} actual={actual_state}",
                    flush=True,
                )
                return actual_state
        return None

    def _transact(self, payload, expected, retries=1, timeout=2.0, blocking=True):
        message = self.build_message(payload)
        lock_acquired = self._transaction_lock.acquire(blocking=blocking)
        if not lock_acquired:
            return LOCK_BUSY
        try:
            self._drain_response_queue()
            for _attempt in range(retries):
                self.ser.write((message + "\n").encode("utf-8"))
                self.ser.flush()
                deadline = time.time() + timeout
                while time.time() < deadline:
                    try:
                        response = self._response_queue.get(timeout=max(0.01, deadline - time.time()))
                    except queue.Empty:
                        break
                    if response == expected or response.startswith(expected + ","):
                        return response
            return None
        finally:
            self._transaction_lock.release()

    def _drain_response_queue(self):
        while True:
            try:
                self._response_queue.get_nowait()
            except queue.Empty:
                return

    def _heartbeat_loop(self):
        while not self._stop_event.wait(2):
            self.mqtt.publish(
                f"{GATEWAY_PREFIX}/heartbeat",
                self._json(
                    {
                        "node": "lora-a",
                        "online": True,
                        "uptime": int(time.time() - START_TIME),
                        "ts_ms": self._now_ms(),
                    }
                ),
                qos=0,
                retain=False,
            )
            # 控制命令优先：有用户命令排队时，本轮跳过 LoRa 后台探测，
            # 避免 PING / YL40 / STATE 查询抢占同一条串口事务链路。
            if self._has_pending_command():
                continue

            pong = self._transact("PING", "PONG", retries=1, timeout=1.2)
            if pong:
                parts = pong.split(",")
                pi_b_uptime = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else None
                self._publish_pi_b_heartbeat(pi_b_uptime)
            if self._last_b_message_at is None or time.time() - self._last_b_message_at > PI_B_OFFLINE_GRACE:
                self._publish_b_availability(False)

            now = time.time()
            if self._b_online and now - self._last_state_query_at >= STATE_QUERY_INTERVAL:
                self._last_state_query_at = now
                self._transact("QUERY,STATE", "STATE", retries=1, timeout=1.2, blocking=False)


if __name__ == "__main__":
    gateway = LoRaMQTTGateway()
    try:
        gateway.start()
    finally:
        gateway.ser.close()
        GPIO.cleanup()

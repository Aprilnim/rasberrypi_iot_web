import json
import os
import queue
import threading
import time
import uuid

import paho.mqtt.client as mqtt


TOPIC_PREFIX = "yl40iot/v1"
PI_B_PREFIX = f"{TOPIC_PREFIX}/nodes/pi-b"
PI_C_PREFIX = f"{TOPIC_PREFIX}/nodes/pi-c"
GATEWAY_PREFIX = f"{TOPIC_PREFIX}/gateways/lora-a"
BACKEND_AVAILABILITY_TOPIC = f"{TOPIC_PREFIX}/services/backend/availability"
HEARTBEAT_GRACE_MS = {
    "pi_b": 12000,
    "pi_c": 8000,
    "gateway": 8000,
}
WATCHDOG_INTERVAL_SECONDS = 2.0


class MQTTService:
    """FastAPI 的 MQTT 数据入口。

    该类只读写 MQTT 和内存缓存，不访问 GPIO、I2C、RS485 或 LoRa 串口。
    """

    def __init__(self):
        self.host = os.environ.get("MQTT_HOST", "192.168.10.70")
        self.port = int(os.environ.get("MQTT_PORT", "1883"))
        self.username = os.environ.get("MQTT_BACKEND_USERNAME", "backend")
        self.password = os.environ.get("MQTT_BACKEND_PASSWORD", "")
        self.client_id = os.environ.get("MQTT_BACKEND_CLIENT_ID", "backend")
        self.command_timeout = float(os.environ.get("MQTT_COMMAND_TIMEOUT", "7"))
        if not self.username or not self.password:
            raise RuntimeError("MQTT backend username/password must be configured")

        self._lock = threading.Lock()
        self._connected = False
        self._pending_commands = {}
        self._sse_lock = threading.Lock()
        self._sse_clients = set()
        self._last_online_snapshot = None
        self._cache = {
            "yl40": {
                "light_percent": None,
                "raw_light": None,
                "updated_at": None,
                "error": "pi-c YL40 data not received",
            },
            "sht35": {
                "temperature": None,
                "humidity": None,
                "raw_temperature": None,
                "raw_humidity": None,
                "updated_at": None,
                "error": "pi-c SHT35 data not received",
            },
            "led": {
                "on": False,
                "updated_at": None,
                "last_cmd_id": None,
                "error": "pi-b LED state not received",
            },
            "fan": {
                "on": False,
                "updated_at": None,
                "last_cmd_id": None,
                "error": "pi-b FAN state not received",
            },
            "pi_b": {"online": False, "updated_at": None, "heartbeat_at": None},
            "pi_c": {"online": False, "updated_at": None, "heartbeat_at": None},
            "gateway": {"online": False, "updated_at": None, "heartbeat_at": None},
        }

        self.client = mqtt.Client(client_id=self.client_id, clean_session=True)
        if self.username:
            self.client.username_pw_set(self.username, self.password)
        self.client.will_set(
            BACKEND_AVAILABILITY_TOPIC,
            payload=json.dumps({"online": False, "ts_ms": self._now_ms()}),
            qos=1,
            retain=True,
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        try:
            self.client.connect_async(self.host, self.port, keepalive=30)
            self.client.loop_start()
            print(f"[MQTT] Backend connecting to {self.host}:{self.port}")
        except Exception as exc:
            print(f"[MQTT] Backend initialization failed: {exc}")

        threading.Thread(target=self._availability_watchdog_loop, daemon=True).start()

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _age_ms(updated_at):
        if updated_at is None:
            return None
        return max(0, int((time.time() - updated_at) * 1000))

    @staticmethod
    def _queue_sse_event(client_queue, event, data):
        item = {"event": event, "data": data}
        while True:
            try:
                client_queue.put_nowait(item)
                return
            except queue.Full:
                try:
                    client_queue.get_nowait()
                except queue.Empty:
                    return

    @staticmethod
    def _decode_payload(message):
        try:
            data = json.loads(message.payload.decode("utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _on_connect(self, client, _userdata, _flags, rc, _properties=None):
        if rc != 0:
            print(f"[MQTT] Backend connection failed: rc={rc}")
            return

        with self._lock:
            self._connected = True

        subscriptions = [
            (f"{PI_C_PREFIX}/telemetry/yl40", 1),
            (f"{PI_C_PREFIX}/telemetry/sht35", 1),
            (f"{PI_B_PREFIX}/state/+", 1),
            (f"{PI_B_PREFIX}/commands/+/result", 1),
            (f"{PI_B_PREFIX}/availability", 1),
            (f"{PI_C_PREFIX}/availability", 1),
            (f"{GATEWAY_PREFIX}/availability", 1),
            (f"{PI_B_PREFIX}/heartbeat", 0),
            (f"{PI_C_PREFIX}/heartbeat", 0),
            (f"{GATEWAY_PREFIX}/heartbeat", 0),
            (f"{PI_B_PREFIX}/events/error", 1),
            (f"{PI_C_PREFIX}/events/error", 1),
            (f"{GATEWAY_PREFIX}/events/error", 1),
        ]
        for topic, qos in subscriptions:
            client.subscribe(topic, qos=qos)

        client.publish(
            BACKEND_AVAILABILITY_TOPIC,
            json.dumps({"online": True, "ts_ms": self._now_ms()}),
            qos=1,
            retain=True,
        )
        print("[MQTT] Backend connected")

    def _on_disconnect(self, _client, _userdata, rc, _properties=None):
        with self._lock:
            self._connected = False
        if rc:
            print(f"[MQTT] Backend disconnected unexpectedly: rc={rc}")

    def _on_message(self, _client, _userdata, message):
        data = self._decode_payload(message)
        if data is None:
            print(f"[MQTT] Ignored invalid JSON on {message.topic}")
            return

        now = time.time()
        events = []
        with self._lock:
            if message.topic == f"{PI_C_PREFIX}/telemetry/yl40":
                self._mark_node_seen("pi_c", now)
                self._cache["yl40"].update(
                    {
                        "light_percent": data.get("light_percent"),
                        "raw_light": data.get("raw_light"),
                        "updated_at": now,
                        "error": None,
                    }
                )
                events.append(("sensor", self._get_sensor_snapshot_locked()))
            elif message.topic == f"{PI_C_PREFIX}/telemetry/sht35":
                self._mark_node_seen("pi_c", now)
                self._cache["sht35"].update(
                    {
                        "temperature": data.get("temperature"),
                        "humidity": data.get("humidity"),
                        "raw_temperature": data.get("raw_temperature"),
                        "raw_humidity": data.get("raw_humidity"),
                        "updated_at": now,
                        "error": None,
                    }
                )
                events.append(("sensor", self._get_sensor_snapshot_locked()))
            elif message.topic in (
                f"{PI_B_PREFIX}/state/led",
                f"{PI_B_PREFIX}/state/fan",
            ):
                device = message.topic.rsplit("/", 1)[-1]
                state = data.get("state")
                if state in ("on", "off"):
                    self._cache[device].update(
                        {
                            "on": state == "on",
                            "updated_at": now,
                            "last_cmd_id": data.get("last_cmd_id"),
                            "error": None,
                        }
                    )
                    events.append(("device", self._get_device_states_snapshot_locked()))
            elif message.topic.endswith("/availability"):
                self._update_availability(message.topic, data, now)
                events.append(("lora", self._get_lora_status_locked()))
                events.append(("device", self._get_device_states_snapshot_locked()))
            elif message.topic.endswith("/heartbeat"):
                self._update_heartbeat(message.topic, now)
                events.append(("lora", self._get_lora_status_locked()))
            elif message.topic.endswith("/result"):
                cmd_id = data.get("cmd_id")
                pending = self._pending_commands.get(cmd_id)
                target = data.get("target")
                actual_state = data.get("actual_state")
                if data.get("result") == "success" and target in ("led", "fan") and actual_state in ("on", "off"):
                    self._cache[target].update(
                        {
                            "on": actual_state == "on",
                            "updated_at": now,
                            "last_cmd_id": cmd_id,
                            "error": None,
                        }
                    )
                    events.append(("device", self._get_device_states_snapshot_locked()))
                if (
                    pending
                    and target == pending["device"]
                    and data.get("requested_state") == pending["state"]
                    and data.get("result") in ("success", "failed", "rejected")
                ):
                    pending["result"] = data
                    pending["event"].set()
            elif message.topic.endswith("/events/error"):
                if message.topic == f"{PI_C_PREFIX}/events/error" and data.get("device") == "yl40":
                    self._cache["yl40"]["error"] = data.get("error") or "pi-c YL40 error"
                    events.append(("sensor", self._get_sensor_snapshot_locked()))
                elif message.topic == f"{PI_C_PREFIX}/events/error" and data.get("device") == "sht35":
                    self._cache["sht35"]["error"] = data.get("error") or "pi-c SHT35 error"
                    events.append(("sensor", self._get_sensor_snapshot_locked()))
                print(f"[MQTT] Device error on {message.topic}: {data.get('error')}")

        for event, event_data in events:
            self.broadcast_sse_event(event, event_data)

    def _update_availability(self, topic, data, now):
        online = data.get("online") is True
        if topic == f"{PI_B_PREFIX}/availability":
            key = "pi_b"
        elif topic == f"{PI_C_PREFIX}/availability":
            key = "pi_c"
        elif topic == f"{GATEWAY_PREFIX}/availability":
            key = "gateway"
        else:
            return
        self._cache[key].update({"online": online, "updated_at": now})

    def _mark_node_seen(self, key, now):
        self._cache[key].update({"online": True, "updated_at": now, "heartbeat_at": now})

    def _update_heartbeat(self, topic, now):
        if topic == f"{PI_B_PREFIX}/heartbeat":
            key = "pi_b"
        elif topic == f"{PI_C_PREFIX}/heartbeat":
            key = "pi_c"
        elif topic == f"{GATEWAY_PREFIX}/heartbeat":
            key = "gateway"
        else:
            return
        self._cache[key].update({"online": True, "updated_at": now, "heartbeat_at": now})

    @property
    def connected(self):
        with self._lock:
            return self._connected

    def _node_online_locked(self, key):
        state = self._cache[key]
        age_ms = self._age_ms(state["heartbeat_at"])
        grace_ms = HEARTBEAT_GRACE_MS.get(key, 5000)
        return state["online"] and age_ms is not None and age_ms <= grace_ms

    def _get_sensor_snapshot_locked(self):
        yl40 = dict(self._cache["yl40"])
        sht35 = dict(self._cache["sht35"])
        pi_b_online = self._node_online_locked("pi_b")
        pi_c_online = self._node_online_locked("pi_c")

        updated_values = [
            value for value in (yl40["updated_at"], sht35["updated_at"]) if value is not None
        ]
        updated_at = max(updated_values) if updated_values else None
        errors = []
        if not pi_c_online:
            errors.append("pi-c offline")
        if yl40["error"]:
            errors.append(yl40["error"])
        if sht35["error"]:
            errors.append(sht35["error"])

        return {
            "temperature": sht35["temperature"],
            "humidity": sht35["humidity"],
            "light_percent": yl40["light_percent"],
            "temp_raw": sht35["raw_temperature"],
            "humidity_raw": sht35["raw_humidity"],
            "light_raw": yl40["raw_light"],
            "updated_at": updated_at,
            "age_ms": self._age_ms(updated_at),
            "cached": True,
            "error": "; ".join(dict.fromkeys(errors)) if errors else None,
            "pi_b_online": pi_b_online,
            "pi_c_online": pi_c_online,
        }

    def _get_device_snapshot_locked(self, device):
        state = dict(self._cache[device])
        pi_b_online = self._node_online_locked("pi_b")
        gateway_online = self._node_online_locked("gateway")
        available = self._connected and pi_b_online and gateway_online
        return {
            "on": state["on"],
            "available": available,
            "cached": True,
            "updated_at": state["updated_at"],
            "age_ms": self._age_ms(state["updated_at"]),
            "last_cmd_id": state["last_cmd_id"],
            "error": state["error"] if available else "MQTT gateway or pi-b offline",
        }

    def _get_device_states_snapshot_locked(self):
        return {
            "led": self._get_device_snapshot_locked("led"),
            "fan": self._get_device_snapshot_locked("fan"),
        }

    def _get_lora_status_locked(self):
        pi_b = dict(self._cache["pi_b"])
        gateway = dict(self._cache["gateway"])
        pi_b_online = self._node_online_locked("pi_b")
        gateway_online = self._node_online_locked("gateway")
        online = self._connected and pi_b_online and gateway_online
        return {
            "online": online,
            "fail_count": 0 if online else 1,
            "last_pong_time": pi_b["heartbeat_at"],
            "gateway_online": gateway_online,
            "broker_online": self._connected,
            "message": "设备在线" if online else "设备连接失败",
        }

    def _get_online_snapshot_locked(self):
        return {
            "pi_b": self._node_online_locked("pi_b"),
            "pi_c": self._node_online_locked("pi_c"),
            "gateway": self._node_online_locked("gateway"),
            "broker": self._connected,
        }

    def _availability_watchdog_loop(self):
        while True:
            time.sleep(WATCHDOG_INTERVAL_SECONDS)
            events = []
            with self._lock:
                snapshot = self._get_online_snapshot_locked()
                if self._last_online_snapshot is None:
                    self._last_online_snapshot = snapshot
                    continue

                previous = self._last_online_snapshot
                if snapshot == previous:
                    continue

                self._last_online_snapshot = snapshot
                if snapshot["pi_c"] != previous["pi_c"]:
                    events.append(("sensor", self._get_sensor_snapshot_locked()))
                if (
                    snapshot["pi_b"] != previous["pi_b"]
                    or snapshot["gateway"] != previous["gateway"]
                    or snapshot["broker"] != previous["broker"]
                ):
                    events.append(("lora", self._get_lora_status_locked()))
                    events.append(("device", self._get_device_states_snapshot_locked()))

            for event, event_data in events:
                self.broadcast_sse_event(event, event_data)

    def get_sensor_snapshot(self):
        with self._lock:
            return self._get_sensor_snapshot_locked()

    def get_device_snapshot(self, device):
        with self._lock:
            return self._get_device_snapshot_locked(device)

    def get_lora_status(self):
        with self._lock:
            return self._get_lora_status_locked()

    def register_sse_client(self):
        client_queue = queue.Queue(maxsize=8)
        with self._sse_lock:
            self._sse_clients.add(client_queue)
        with self._lock:
            initial_events = [
                ("sensor", self._get_sensor_snapshot_locked()),
                ("device", self._get_device_states_snapshot_locked()),
                ("lora", self._get_lora_status_locked()),
            ]
        for event, data in initial_events:
            self._queue_sse_event(client_queue, event, data)
        return client_queue

    def unregister_sse_client(self, client_queue):
        with self._sse_lock:
            self._sse_clients.discard(client_queue)

    def broadcast_sse_event(self, event, data):
        with self._sse_lock:
            clients = list(self._sse_clients)
        for client_queue in clients:
            self._queue_sse_event(client_queue, event, data)

    def send_device_command(self, device, state):
        device = device.lower()
        state = state.lower()
        if device not in ("led", "fan") or state not in ("on", "off"):
            return {"success": False, "message": "Invalid device command"}

        snapshot = self.get_device_snapshot(device)
        if not snapshot["available"]:
            return {"success": False, "message": snapshot["error"]}

        cmd_id = str(uuid.uuid4())
        topic = f"{PI_B_PREFIX}/commands/{device}/set"
        payload = {
            "cmd_id": cmd_id,
            "target": device,
            "state": state,
            "source": "backend",
            "issued_at_ms": self._now_ms(),
            "ttl_ms": 3000,
        }
        pending = {
            "event": threading.Event(),
            "result": None,
            "device": device,
            "state": state,
        }
        with self._lock:
            self._pending_commands[cmd_id] = pending

        publish_info = self.client.publish(
            topic,
            json.dumps(payload, separators=(",", ":")),
            qos=1,
            retain=False,
        )
        if publish_info.rc != mqtt.MQTT_ERR_SUCCESS:
            with self._lock:
                self._pending_commands.pop(cmd_id, None)
            return {"success": False, "message": f"MQTT publish failed: rc={publish_info.rc}"}

        completed = pending["event"].wait(self.command_timeout)
        with self._lock:
            result = pending["result"]
            self._pending_commands.pop(cmd_id, None)

        if not completed or result is None:
            return {"success": False, "message": "Timeout waiting for LoRa gateway result", "cmd_id": cmd_id}

        return {
            "success": result.get("result") == "success",
            "message": result.get("error") or result.get("result", "failed"),
            "cmd_id": cmd_id,
            "actual_state": result.get("actual_state"),
        }

import time
import threading


class LoRaNode:
    def __init__(self, port="/dev/ttyS0", baudrate=9600, m0=22, m1=27, timeout=2, retries=3):
        self.port = port
        self.baudrate = baudrate
        self.m0 = m0
        self.m1 = m1
        self.timeout = timeout
        self.retries = retries
        self.ser = None
        self._led_state = False
        self._fan_state = False
        self._available = False
        self._lock = threading.Lock()

        try:
            import RPi.GPIO as GPIO
            import serial

            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.m0, GPIO.OUT)
            GPIO.setup(self.m1, GPIO.OUT)
            GPIO.output(self.m0, GPIO.LOW)
            GPIO.output(self.m1, GPIO.LOW)
            time.sleep(1)

            self.ser = serial.Serial(self.port, self.baudrate, timeout=1)
            self._available = True
            print("[LoRa] Initialized successfully")
        except Exception as e:
            print(f"[LoRa] Initialization failed: {e}")
            self._available = False

    @property
    def available(self):
        return self._available

    @staticmethod
    def calc_crc(payload: str) -> int:
        return sum(payload.encode()) % 256

    def build_message(self, payload: str) -> str:
        crc = self.calc_crc(payload)
        return f"{payload},{crc}"

    def verify_crc(self, message: str) -> tuple[bool, str]:
        parts = message.split(",")
        if len(parts) < 2:
            return False, ""
        received_crc = parts[-1]
        payload = ",".join(parts[:-1])
        try:
            calculated_crc = self.calc_crc(payload)
            if str(calculated_crc) == received_crc:
                return True, payload
            return False, ""
        except Exception:
            return False, ""

    def send_device_command(self, device: str, action: str) -> dict:
        """通用设备命令发送：支持 LED、FAN 等任意设备类型

        device: 设备名，如 "LED" / "FAN"
        action: "ON" / "OFF"
        发送格式：CMD,<DEVICE>,<ACTION>,<CRC>
        等待 ACK：ACK,<DEVICE>,<ACTION>,<CRC>
        """
        if not self._available:
            return {"success": False, "message": "LoRa not available"}

        payload = f"CMD,{device},{action}"
        message = self.build_message(payload)
        expected_ack_payload = f"ACK,{device},{action}"

        for attempt in range(1, self.retries + 1):
            print(f"TX: {message} (attempt {attempt}/{self.retries})")

            with self._lock:
                try:
                    self.ser.write((message + "\n").encode())
                    self.ser.flush()
                except Exception as e:
                    print(f"TX ERROR: {e}")
                    continue

                start = time.time()
                while time.time() - start < self.timeout:
                    try:
                        if self.ser.in_waiting > 0:
                            raw = self.ser.readline().decode().strip()
                            if not raw:
                                continue

                            print(f"RX: {raw}")

                            ok, recv_payload = self.verify_crc(raw)
                            if not ok:
                                print(f"CRC ERROR: raw={raw}")
                                continue

                            if recv_payload == expected_ack_payload:
                                # 更新对应设备状态
                                is_on = (action == "ON")
                                if device == "LED":
                                    self._led_state = is_on
                                elif device == "FAN":
                                    self._fan_state = is_on
                                print(f"ACK OK: {recv_payload}")
                                return {"success": True, "message": f"ACK: {recv_payload}"}
                            else:
                                print(f"ACK MISMATCH: expected={expected_ack_payload}, got={recv_payload}")
                                continue
                    except Exception as e:
                        print(f"RX ERROR: {e}")
                        continue

            print(f"TIMEOUT: No ACK received (attempt {attempt}/{self.retries})")

        return {"success": False, "message": f"Timeout after {self.retries} retries, no valid ACK"}

    def send_command(self, action: str) -> dict:
        """兼容旧接口：发送 LED 控制命令"""
        return self.send_device_command("LED", action)

    def send_fan_command(self, action: str) -> dict:
        """发送风扇控制命令"""
        return self.send_device_command("FAN", action)

    def ping_once(self) -> bool:
        if not self._available:
            return False

        payload = "PING"
        message = self.build_message(payload)
        expected = "PONG"

        with self._lock:
            print(f"TX: {message} (PING)")
            try:
                self.ser.write((message + "\n").encode())
                self.ser.flush()
            except Exception as e:
                print(f"TX ERROR (PING): {e}")
                return False

            start = time.time()
            while time.time() - start < self.timeout:
                try:
                    if self.ser.in_waiting > 0:
                        raw = self.ser.readline().decode().strip()
                        if not raw:
                            continue

                        print(f"RX: {raw}")

                        ok, recv_payload = self.verify_crc(raw)
                        if not ok:
                            print(f"CRC ERROR: raw={raw}")
                            continue

                        if recv_payload == expected:
                            print(f"PONG OK: {recv_payload}")
                            return True
                        # 非 PONG 消息跳过（可能是 LED/FAN ACK 等）
                        continue
                except Exception as e:
                    print(f"RX ERROR (PING): {e}")
                    continue

        print("TIMEOUT (PING): No PONG received")
        return False

    def get_led_state(self) -> bool:
        return self._led_state

    def get_fan_state(self) -> bool:
        return self._fan_state

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass

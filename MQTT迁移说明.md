# MQTT + LoRa 网关迁移说明

## 1. 两种运行模式

项目保留两种互斥模式：

- `DEVICE_TRANSPORT=legacy_lora`：FastAPI 直接使用旧 LoRa/I2C 链路，作为回滚模式。
- `DEVICE_TRANSPORT=mqtt`：FastAPI 只访问 MQTT 和 SQLite，`lora-gateway` 独占 A 端 LoRa 串口。

不要同时让旧 FastAPI LoRa 链路和 `lora-gateway` 打开 `/dev/ttyS0`。
`legacy_lora` 必须搭配 B 端 `receiver_b.py`；`mqtt` 必须搭配 B 端 `pi_b_node.py`，两者的控制报文格式不同。

MQTT 模式的唯一控制链路：

```text
浏览器 -> FastAPI -> MQTT -> lora-gateway -> LoRa -> pi-b
```

树莓派 B 不连接 MQTT。

## 2. MQTT Topic

统一前缀：

```text
yl40iot/v1
```

主要 Topic：

```text
yl40iot/v1/nodes/pi-b/telemetry/yl40
yl40iot/v1/nodes/pi-c/telemetry/sht35
yl40iot/v1/nodes/pi-b/commands/led/set
yl40iot/v1/nodes/pi-b/commands/led/result
yl40iot/v1/nodes/pi-b/commands/fan/set
yl40iot/v1/nodes/pi-b/commands/fan/result
yl40iot/v1/nodes/pi-b/state/led
yl40iot/v1/nodes/pi-b/state/fan
yl40iot/v1/nodes/pi-b/availability
yl40iot/v1/gateways/lora-a/availability
```

控制命令永不 Retain。遥测、最终状态和 availability 使用 Retain。

## 3. EMQX 用户与 ACL

在 EMQX Dashboard 中关闭匿名访问，并创建：

```text
backend
lora-gateway
pi-c
```

权限原则：

| 用户 | Publish | Subscribe |
|---|---|---|
| backend | `yl40iot/v1/nodes/pi-b/commands/+/set`、自身 availability | pi-b/pi-c telemetry、state、result、availability、heartbeat |
| lora-gateway | pi-b telemetry、state、result、availability、heartbeat、error；网关 availability/heartbeat/error | `yl40iot/v1/nodes/pi-b/commands/+/set` |
| pi-c | `yl40iot/v1/nodes/pi-c/#` | 无 |

任何匿名客户端都不能发布硬件控制 Topic。

仓库中的 `emqx/acl.conf.example` 是可粘贴到 EMQX 5.x File Authorization 的最小权限规则。启用后确认授权设置的 `no_match` 为 `deny`，并保留文件末尾的 `{deny, all}.`。

## 4. 树莓派 A 环境变量

在 `/home/pi/temp_web/.env` 增加：

```text
MQTT_HOST=192.168.10.70
MQTT_PORT=1883
MQTT_BACKEND_USERNAME=backend
MQTT_BACKEND_PASSWORD=替换成backend账号密码
MQTT_GATEWAY_USERNAME=lora-gateway
MQTT_GATEWAY_PASSWORD=替换成网关账号密码
LORA_HMAC_SECRET=和树莓派B一致的密钥
CSRF_SIGNING_SECRET=后端CSRF密钥
```

保存后执行 `chmod 600 /home/pi/temp_web/.env`，不要把真实密码提交到 Git。

启动 MQTT 模式：

```bash
cd /home/pi/temp_web
docker compose -f docker-compose.yml -f docker-compose.mqtt.yml config
docker compose down
docker compose -f docker-compose.yml -f docker-compose.mqtt.yml up -d --build
```

`docker-compose.mqtt.yml` 使用 Compose 的 `!reset` 标签移除 backend 硬件权限。若 `config` 不认识 `!reset`，先升级 Docker Compose。
先执行 `docker compose down` 是为了避免旧 backend 与新网关在容器切换瞬间同时打开 LoRa 串口。

检查：

```bash
docker logs --tail=100 yl40-backend
docker logs --tail=100 yl40-lora-gateway
docker inspect yl40-backend --format '{{json .HostConfig.Devices}}'
```

最后一条在 MQTT 模式应输出空设备列表。

回滚旧模式：

```bash
cd /home/pi/temp_web
docker compose -f docker-compose.yml -f docker-compose.mqtt.yml down
docker compose up -d --build
```

回滚时还要在树莓派 B 停止 `pi_b_node.py`，重新启动 `receiver_b.py`。

## 5. 树莓派 B

同步并启动新节点：

```powershell
scp .\backend\pi_b_node.py pi@192.168.10.84:~/pi_b_node.py
```

```bash
sudo apt install -y python3-smbus2 python3-serial python3-rpi.gpio
source ~/.receiver_b.env
python3 ~/pi_b_node.py
```

切换前必须停止旧 `receiver_b.py`，避免两个进程争抢串口和 GPIO。

## 6. 树莓派 C

第二阶段同步：

```powershell
scp .\backend\pi_c_rs485_node.py pi@树莓派C地址:~/pi_c_rs485_node.py
```

树莓派 C 配置 MQTT 账号后运行：

```bash
sudo apt install -y python3-paho-mqtt python3-serial
export MQTT_HOST=192.168.10.70
export MQTT_PI_C_USERNAME=pi-c
export MQTT_PI_C_PASSWORD=替换成pi-c账号密码
python3 ~/pi_c_rs485_node.py
```

## 7. 验收

- EMQX Dashboard 能看到 `backend`、`lora-a`，接入 C 后还能看到 `pi-c`。
- B 不会出现在 MQTT 客户端列表。
- `/api/sensor` 从 MQTT 缓存返回数据。
- `/api/led`、`/api/fan` 只有收到匹配 LoRa ACK 后才返回控制成功。
- B、网关或 Broker 离线时，控制接口失败但 API 不崩溃。
- 未登录、CSRF 错误或被限流的请求不会发布 MQTT 控制命令。

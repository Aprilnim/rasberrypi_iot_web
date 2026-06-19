# MQTT 主题订阅发布集合表

本表记录当前项目 MQTT 模式下各客户端的主题职责。统一前缀：

```text
yl40iot/v1
```

## 客户端总览

| 客户端 | 运行位置 | MQTT 用户 | 主要职责 |
|---|---|---|---|
| `backend` | 树莓派 A / K3S 后端 | `backend` | 发布控制命令，订阅设备状态、遥测、在线状态和执行结果 |
| `lora-a` / `lora-gateway` | 树莓派 A / LoRa 网关 | `lora-gateway` | 订阅 Pi B 控制命令，通过 LoRa 控制 Pi B，并发布结果/状态 |
| `pi-c` | 树莓派 C | `pi-c` | 发布 SHT35 温湿度、YL40 光照、Pi C 在线状态 |
| `pi-b` | 树莓派 B | 无 MQTT 客户端 | 不连接 MQTT，只通过 LoRa 和网关通信 |

## 遥测 Telemetry

| Topic | 发布者 | 订阅者 | QoS | Retain | 说明 |
|---|---|---|---:|---:|---|
| `yl40iot/v1/nodes/pi-c/telemetry/sht35` | `pi-c` | `backend` | 1 | 是 | SHT35 温度、湿度 |
| `yl40iot/v1/nodes/pi-c/telemetry/yl40` | `pi-c` | `backend` | 1 | 是 | YL40 光照 |

Pi B 不再通过 LoRa 上报 YL40，YL40 已迁移到 Pi C。

## 硬件控制命令

| Topic | 发布者 | 订阅者 | QoS | Retain | 说明 |
|---|---|---|---:|---:|---|
| `yl40iot/v1/nodes/pi-b/commands/led/set` | `backend` | `lora-gateway` | 1 | 否 | 请求控制 LED |
| `yl40iot/v1/nodes/pi-b/commands/fan/set` | `backend` | `lora-gateway` | 1 | 否 | 请求控制风扇继电器 |

控制命令禁止 Retain。网关收到 retained 控制消息必须拒绝。

## 命令执行结果

| Topic | 发布者 | 订阅者 | QoS | Retain | 说明 |
|---|---|---|---:|---:|---|
| `yl40iot/v1/nodes/pi-b/commands/led/result` | `lora-gateway` | `backend` | 1 | 否 | LED 命令执行结果 |
| `yl40iot/v1/nodes/pi-b/commands/fan/result` | `lora-gateway` | `backend` | 1 | 否 | FAN 命令执行结果 |

结果只表示某一条 `cmd_id` 的执行结果，不等同于最终状态缓存。

## 设备最终状态

| Topic | 发布者 | 订阅者 | QoS | Retain | 说明 |
|---|---|---|---:|---:|---|
| `yl40iot/v1/nodes/pi-b/state/led` | `lora-gateway` | `backend` | 1 | 是 | LED 最终状态 |
| `yl40iot/v1/nodes/pi-b/state/fan` | `lora-gateway` | `backend` | 1 | 是 | 风扇最终状态 |

`state` 表示设备当前最终状态，用于前端数字孪生同步。

## 在线状态 Availability

| Topic | 发布者 | 订阅者 | QoS | Retain | 说明 |
|---|---|---|---:|---:|---|
| `yl40iot/v1/services/backend/availability` | `backend` | 运维/调试客户端 | 1 | 是 | 后端在线状态 |
| `yl40iot/v1/gateways/lora-a/availability` | `lora-gateway` | `backend` | 1 | 是 | LoRa 网关在线状态 |
| `yl40iot/v1/nodes/pi-b/availability` | `lora-gateway` | `backend` | 1 | 是 | Pi B 通过 LoRa 派生的在线状态 |
| `yl40iot/v1/nodes/pi-c/availability` | `pi-c` | `backend` | 1 | 是 | Pi C MQTT 在线状态 |

Pi B 不出现在 EMQX 客户端列表中，是否在线由 `pi-b/availability` 和心跳派生。

## 心跳 Heartbeat

| Topic | 发布者 | 订阅者 | QoS | Retain | 说明 |
|---|---|---|---:|---:|---|
| `yl40iot/v1/gateways/lora-a/heartbeat` | `lora-gateway` | `backend` | 0 | 否 | 网关心跳 |
| `yl40iot/v1/nodes/pi-b/heartbeat` | `lora-gateway` | `backend` | 0 | 否 | Pi B LoRa PONG 后由网关发布 |
| `yl40iot/v1/nodes/pi-c/heartbeat` | `pi-c` | `backend` | 0 | 否 | Pi C 心跳 |

心跳不 Retain。后端 watchdog 会根据最近 heartbeat 或 telemetry 判断在线状态。

## 错误事件

| Topic | 发布者 | 订阅者 | QoS | Retain | 说明 |
|---|---|---|---:|---:|---|
| `yl40iot/v1/gateways/lora-a/events/error` | `lora-gateway` | `backend` | 1 | 否 | 网关错误 |
| `yl40iot/v1/nodes/pi-b/events/error` | `lora-gateway` | `backend` | 1 | 否 | Pi B / LoRa 链路错误 |
| `yl40iot/v1/nodes/pi-c/events/error` | `pi-c` | `backend` | 1 | 否 | Pi C 传感器错误 |

## ACL 摘要

| MQTT 用户 | 允许发布 | 允许订阅 |
|---|---|---|
| `backend` | `yl40iot/v1/nodes/pi-b/commands/+/set`，`yl40iot/v1/services/backend/availability` | Pi C telemetry；Pi B state/result/availability/heartbeat/events；LoRa gateway availability/heartbeat/events |
| `lora-gateway` | Pi B state/result/availability/heartbeat/events；LoRa gateway availability/heartbeat/events | `yl40iot/v1/nodes/pi-b/commands/+/set` |
| `pi-c` | `yl40iot/v1/nodes/pi-c/telemetry/#`，`yl40iot/v1/nodes/pi-c/availability`，`yl40iot/v1/nodes/pi-c/heartbeat`，`yl40iot/v1/nodes/pi-c/events/#` | 无 |

实际 ACL 示例见：

```text
emqx/acl.conf.example
```

注意：EMQX ACL 规则顺序很重要，所有 allow 必须放在最终 deny 之前。

## 调试订阅建议

超级管理员或临时调试客户端可以订阅：

```text
yl40iot/v1/nodes/pi-b/#
yl40iot/v1/nodes/pi-c/#
yl40iot/v1/gateways/lora-a/#
yl40iot/v1/services/backend/#
```

普通业务客户端不建议直接订阅 MQTT。前端应继续通过后端 HTTP API 和 SSE 获取缓存状态。

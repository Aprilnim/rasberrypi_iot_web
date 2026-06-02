// ============================================================
// 前端 JavaScript —— YL-40 IoT 监控台
// 功能：
//   1. 每秒从后端获取温度/光照传感器数据并刷新显示
//   2. 通过 LED 开关按钮控制树莓派 B 的 GPIO18 LED
//   3. 通过风扇开关按钮控制树莓派 B 的 GPIO17 风扇继电器
//   4. 每 3 秒查询一次 LoRa 心跳状态，判断树莓派 B 是否在线
//   5. 操作日志记录（LED/风扇 开关、网络错误等）
// ============================================================

// ------------------------------------------------------------
// els：页面 DOM 元素缓存
// 把常用的 HTML 元素一次性取出来，避免每次操作都重复 getElementById
// 这样代码更短、运行更快
// ------------------------------------------------------------
const els = {
    temp: document.getElementById("temp"),           // 温度数值显示区域
    light: document.getElementById("light"),         // 光照数值显示区域
    ledSwitch: document.getElementById("led-switch"), // LED 滑动开关（input checkbox）
    ledStatus: document.getElementById("led-status"), // LED 状态文字（开/关/不可用）
    ledBulb: document.getElementById("led-bulb"),    // LED 灯泡图形
    ledGlow: document.getElementById("led-glow"),    // LED 发光光晕
    fanSwitch: document.getElementById("fan-switch"), // 风扇滑动开关
    fanStatus: document.getElementById("fan-status"), // 风扇状态文字
    fanBulb: document.getElementById("fan-bulb"),    // 风扇图标
    fanGlow: document.getElementById("fan-glow"),    // 风扇发光光晕
    backendStatus: document.getElementById("backend-status"), // "后端服务" 状态行
    deviceStatus: document.getElementById("device-status"),   // 顶部徽章内的文字
    deviceBadge: document.getElementById("device-badge"),     // 顶部设备状态徽章（带圆点）
    logList: document.getElementById("log-list"),    // 操作日志列表容器
    clearLog: document.getElementById("clear-log"),  // 清空日志按钮
    loraStatus: document.getElementById("lora-status"), // LoRa 连接状态行
};

// ------------------------------------------------------------
// addLog(message, type)
// 往页面底部的操作日志区域追加一条日志
// message: 要显示的文本内容
// type:    日志类型，决定左边竖条颜色
//           "info"   → 蓝色（普通信息）
//           "action" → 绿色（用户操作）
//           "error"  → 红色（错误信息）
// 日志会自动显示当前时间，最多保留 50 条，超出的旧日志自动删除
// ------------------------------------------------------------
function addLog(message, type = "info") {
    const entry = document.createElement("div");
    // 为日志条目设置 CSS 类，包含通用样式和颜色类型
    entry.className = `log-entry ${type}`;
    // 生成当前时间字符串，格式如 "14:32:05"，24小时制
    const time = new Date().toLocaleTimeString("zh-CN", { hour12: false });
    // innerHTML 插入时间和消息文本
    entry.innerHTML = `<span class="log-time">${time}</span><span class="log-text">${message}</span>`;
    // prepend 把新日志插到最前面（最新日志在最上面）
    els.logList.prepend(entry);
    // 当日志超过 50 条时，删除最旧的一条（也就是最后一个子元素）
    if (els.logList.children.length > 50) {
        els.logList.removeChild(els.logList.lastChild);
    }
}

// ------------------------------------------------------------
// setBackendOnline(online)
// 根据后端 HTTP 连接状态，更新"后端服务"那一行的显示
// online = true  → 显示绿色"正常"
// online = false → 显示红色"断开"
// 注意：这个只反映"浏览器能不能连上 FastAPI 后端"，
//       不反映树莓派 B 的 LoRa 是否在线
// ------------------------------------------------------------
function setBackendOnline(online) {
    if (online) {
        els.backendStatus.innerText = "正常";
        els.backendStatus.className = "info-val status-ok";   // 绿色样式
    } else {
        els.backendStatus.innerText = "断开";
        els.backendStatus.className = "info-val status-err";  // 红色样式
    }
}

// ------------------------------------------------------------
// setLoraOnline(online)
// 根据树莓派 B 的 LoRa 心跳状态，更新前端多处显示
// online = true  时：
//   - "LoRa连接状态" 显示绿色"设备在线"
//   - 顶部徽章显示"设备在线"（绿色圆点）
//   - LED 开关、风扇开关都启用（可以点击）
// online = false 时：
//   - "LoRa连接状态" 显示红色"设备离线"
//   - 顶部徽章显示"设备离线"（红色圆点）
//   - LED 开关、风扇开关都禁用（防止离线时误操作）
// ------------------------------------------------------------
function setLoraOnline(online) {
    if (online) {
        els.loraStatus.innerText = "设备在线";
        els.loraStatus.className = "info-val status-ok";      // 绿色
        els.deviceStatus.innerText = "设备在线";
        els.deviceBadge.classList.add("online");              // 加绿色 CSS 类
        els.deviceBadge.classList.remove("offline");          // 去掉红色 CSS 类
        els.ledSwitch.disabled = false;                       // LED 开关可用
        els.fanSwitch.disabled = false;                       // 风扇开关可用
    } else {
        els.loraStatus.innerText = "设备离线";
        els.loraStatus.className = "info-val status-err";     // 红色
        els.deviceStatus.innerText = "设备离线";
        els.deviceBadge.classList.add("offline");             // 加红色 CSS 类
        els.deviceBadge.classList.remove("online");           // 去掉绿色 CSS 类
        els.ledSwitch.disabled = true;                        // LED 开关禁用
        els.fanSwitch.disabled = true;                        // 风扇开关禁用
    }
}

// ------------------------------------------------------------
// updateSensor()
// 异步获取传感器数据（温度和光照）
// 请求路径：/api/sensor（nginx 代理到后端 /sensor）
// 成功：把 temperature 和 light_percent 显示到页面上，标记后端在线
// 失败：标记后端断开
// 这个函数每秒调用一次（见页面底部 setInterval）
// ------------------------------------------------------------
async function updateSensor() {
    try {
        const response = await fetch("/api/sensor");
        const data = await response.json();
        // 把后端返回的数字更新到页面上的 <span> 里
        els.temp.innerText = data.temperature;
        els.light.innerText = data.light_percent;
        setBackendOnline(true);
    } catch (err) {
        // fetch 失败（后端没响应、网络断开等）
        setBackendOnline(false);
    }
}

// ------------------------------------------------------------
// updateLedVisual(isOn)
// 根据 LED 开关状态，更新页面上的灯泡视觉效果
// isOn = true  → 显示"开启"文字，灯泡变蓝并带发光光晕
// isOn = false → 显示"关闭"文字，灯泡恢复灰色
// 注意：这个只是纯 UI 更新，不涉及网络请求
// ------------------------------------------------------------
function updateLedVisual(isOn) {
    if (isOn) {
        els.ledStatus.innerText = "开启";
        els.ledBulb.classList.add("on");   // 蓝色灯泡 + 阴影
        els.ledGlow.classList.add("on");   // 蓝色光晕
    } else {
        els.ledStatus.innerText = "关闭";
        els.ledBulb.classList.remove("on");
        els.ledGlow.classList.remove("on");
    }
}

// ------------------------------------------------------------
// updateLed()
// 页面刚加载时，获取一次 LED 的当前状态
// 请求路径：/api/led（GET 方式）
// 后端返回：{ "on": true/false, "available": true/false }
// 如果 available 为 false（比如 LoRa 模块初始化失败），显示"不可用"
// 否则同步滑动开关位置和灯泡视觉效果
// 这个函数只在页面加载时调用一次（见底部）
// ------------------------------------------------------------
async function updateLed() {
    try {
        const response = await fetch("/api/led");
        const data = await response.json();
        if (!data.available) {
            els.ledStatus.innerText = "不可用";
            return;
        }
        // 把 checkbox 的选中状态和服务器同步
        els.ledSwitch.checked = data.on;
        updateLedVisual(data.on);
    } catch (err) {
        // 初始化时失败静默处理，不弹报错
    }
}

// ------------------------------------------------------------
// toggleLed()
// 用户点击 LED 滑动开关时触发
// 请求路径：/api/led（POST 方式，发送 JSON {on: true/false}）
// 成功：更新灯泡视觉效果，记录一条操作日志
// 失败（后端报错或网络错误）：
//   - 显示错误日志
//   - 把开关状态回滚到操作前的状态
// 注意：如果 LoRa 离线，开关会被 disabled，这个函数不会被触发
// ------------------------------------------------------------
async function toggleLed() {
    const newState = els.ledSwitch.checked;
    try {
        const response = await fetch("/api/led", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ on: newState }),
        });
        const data = await response.json();
        if (data.available) {
            // 后端确认成功，更新页面上的灯泡显示
            updateLedVisual(data.on);
            addLog(`LED 已${data.on ? "开启" : "关闭"}`, "action");
        } else {
            // 后端返回 available=false（比如 LoRa 不可用）
            addLog("LED 控制失败：硬件不可用", "error");
            els.ledSwitch.checked = !newState;  // 回滚开关状态
        }
    } catch (err) {
        // fetch 网络错误（后端没响应）
        addLog("LED 控制失败：网络错误", "error");
        els.ledSwitch.checked = !newState;      // 回滚开关状态
    }
}

// ------------------------------------------------------------
// updateFanVisual(isOn)
// 根据风扇开关状态，更新页面上的风扇视觉效果
// isOn = true  → 显示"开启"文字，图标变绿并带发光光晕，添加旋转动画
// isOn = false → 显示"关闭"文字，图标恢复灰色
// 注意：这个只是纯 UI 更新，不涉及网络请求
// ------------------------------------------------------------
function updateFanVisual(isOn) {
    if (isOn) {
        els.fanStatus.innerText = "开启";
        els.fanBulb.classList.add("on");   // 绿色风扇图标 + 阴影
        els.fanGlow.classList.add("on");   // 绿色光晕
    } else {
        els.fanStatus.innerText = "关闭";
        els.fanBulb.classList.remove("on");
        els.fanGlow.classList.remove("on");
    }
}

// ------------------------------------------------------------
// updateFan()
// 页面刚加载时，获取一次风扇的当前状态
// 请求路径：/api/fan（GET 方式）
// 后端返回：{ "on": true/false, "available": true/false }
// 如果 available 为 false，显示"不可用"
// 否则同步滑动开关位置和风扇视觉效果
// ------------------------------------------------------------
async function updateFan() {
    try {
        const response = await fetch("/api/fan");
        const data = await response.json();
        if (!data.available) {
            els.fanStatus.innerText = "不可用";
            return;
        }
        // 把 checkbox 的选中状态和服务器同步
        els.fanSwitch.checked = data.on;
        updateFanVisual(data.on);
    } catch (err) {
        // 初始化时失败静默处理
    }
}

// ------------------------------------------------------------
// toggleFan()
// 用户点击风扇滑动开关时触发
// 请求路径：/api/fan（POST 方式，发送 JSON {on: true/false}）
// 成功：更新风扇视觉效果，记录一条操作日志
// 失败：显示错误日志，回滚开关状态
// 注意：如果 LoRa 离线，开关会被 disabled，这个函数不会被触发
// ------------------------------------------------------------
async function toggleFan() {
    const newState = els.fanSwitch.checked;
    try {
        const response = await fetch("/api/fan", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ on: newState }),
        });
        const data = await response.json();
        if (data.available) {
            updateFanVisual(data.on);
            addLog(`风扇已${data.on ? "开启" : "关闭"}`, "action");
        } else {
            addLog("风扇控制失败：硬件不可用", "error");
            els.fanSwitch.checked = !newState;
        }
    } catch (err) {
        addLog("风扇控制失败：网络错误", "error");
        els.fanSwitch.checked = !newState;
    }
}

// ------------------------------------------------------------
// updateLoraStatus()
// 每 3 秒查询一次 LoRa 心跳状态
// 请求路径：/api/lora/status（GET 方式）
// 后端返回：{ "online": true/false, "fail_count": 数字, ... }
// 根据 online 字段更新：
//   - LoRa 连接状态行文字颜色
//   - 顶部设备徽章
//   - LED 开关、风扇开关是否可点击
// 如果请求本身失败（fetch 抛异常），把后端和 LoRa 都标记为离线
// ------------------------------------------------------------
async function updateLoraStatus() {
    try {
        const response = await fetch("/api/lora/status");
        const data = await response.json();
        setLoraOnline(data.online);
        if (data.online) {
            // LoRa 在线时，顺便把后端状态也刷新为正常
            setBackendOnline(true);
        }
    } catch (err) {
        // fetch 失败（后端挂了或网络断开）
        setLoraOnline(false);
        setBackendOnline(false);
    }
}

// ------------------------------------------------------------
// 事件绑定
// 1. LED 开关被用户切换时 → 调用 toggleLed() 发送控制命令
// 2. 风扇开关被用户切换时 → 调用 toggleFan() 发送控制命令
// 3. 清空日志按钮被点击时 → 清空日志列表并记录一条"日志已清空"
// ------------------------------------------------------------
els.ledSwitch.addEventListener("change", toggleLed);
els.fanSwitch.addEventListener("change", toggleFan);
els.clearLog.addEventListener("click", () => {
    els.logList.innerHTML = "";
    addLog("日志已清空", "info");
});

// ------------------------------------------------------------
// 页面初始化
// 顺序：
// 1. 记录系统启动日志
// 2. 立即获取一次传感器数据
// 3. 启动定时器，每秒自动刷新传感器
// 4. 立即获取一次 LED 当前状态
// 5. 立即获取一次风扇当前状态
// 6. 立即获取一次 LoRa 连接状态
// 7. 启动定时器，每 3 秒自动刷新 LoRa 状态
// ------------------------------------------------------------
addLog("系统初始化完成，开始连接设备...", "info");
updateSensor();                               // 第一次获取温度/光照
setInterval(updateSensor, 1000);              // 之后每秒刷新
updateLed();                                  // 第一次获取 LED 状态
updateFan();                                  // 第一次获取风扇状态
updateLoraStatus();                           // 第一次获取 LoRa 状态
setInterval(updateLoraStatus, 3000);          // 之后每 3 秒刷新

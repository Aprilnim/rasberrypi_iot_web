// ============================================================
// 前端 JavaScript —— YL-40 IoT 监控台
// 功能：
//   1. 每秒从后端获取温度/光照传感器数据并刷新显示
//   2. 通过 LED 开关按钮控制树莓派 B 的 GPIO18 LED
//   3. 通过风扇开关按钮控制树莓派 B 的 GPIO24 风扇继电器
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
    ledLastTime: document.getElementById("led-last-time"), // LED 最近操作时间
    fanSwitch: document.getElementById("fan-switch"), // 风扇滑动开关
    fanStatus: document.getElementById("fan-status"), // 风扇状态文字
    fanBulb: document.getElementById("fan-bulb"),    // 风扇图标
    fanGlow: document.getElementById("fan-glow"),    // 风扇发光光晕
    fanLastTime: document.getElementById("fan-last-time"), // 风扇最近操作时间
    backendStatus: document.getElementById("backend-status"), // "后端服务" 状态行
    deviceStatus: document.getElementById("device-status"),   // 顶部徽章内的文字
    deviceBadge: document.getElementById("device-badge"),     // 顶部设备状态徽章（带圆点）
    overviewDeviceStatus: document.getElementById("overview-device-status"), // 设备概览卡片里的状态
    lastUpdateTime: document.getElementById("last-update-time"), // 传感器最近更新时间
    uptime: document.getElementById("uptime"),       // 系统运行时长
    logList: document.getElementById("log-list"),    // 操作日志列表容器
    clearLog: document.getElementById("clear-log"),  // 清空日志按钮
    auditLogList: document.getElementById("audit-log-list"), // 数据库审计日志列表
    auditRefresh: document.getElementById("audit-refresh"), // 审计日志刷新按钮
    auditHint: document.getElementById("audit-hint"), // 审计日志状态提示
    loraStatus: document.getElementById("lora-status"), // LoRa 连接状态行
    themeToggle: document.getElementById("theme-toggle"), // 深色/浅色主题切换按钮
    themeIcon: document.getElementById("theme-icon"), // 主题按钮图标
    themeLabel: document.getElementById("theme-label"), // 主题按钮文字
    loginButton: document.getElementById("login-button"), // 登录按钮
    loginLabel: document.getElementById("login-label"), // 登录按钮文字
    loginModal: document.getElementById("login-modal"), // 登录弹窗
    loginForm: document.getElementById("login-form"), // 登录表单
    loginUsername: document.getElementById("login-username"), // 登录用户名
    loginPassword: document.getElementById("login-password"), // 登录密码
    loginError: document.getElementById("login-error"), // 登录错误提示
    loginClose: document.getElementById("login-close"), // 登录弹窗关闭按钮
    loginSubmit: document.getElementById("login-submit"), // 登录提交按钮
    authToast: document.getElementById("auth-toast"), // 权限状态提示
    authToastTitle: document.getElementById("auth-toast-title"), // 权限提示标题
    authToastMessage: document.getElementById("auth-toast-message"), // 权限提示内容
};

let authState = {
    authenticated: false,
    user: null,
};
localStorage.removeItem("yl40iot_access_token");
let authToastTimer = null;
let ledControlPending = false;
let fanControlPending = false;
let sensorPolling = false;
let uptimePolling = false;
let loraPolling = false;
let deviceStatesPolling = false;

// ------------------------------------------------------------
// 主题模式：手动优先 + 系统默认
// localStorage 有 theme 时优先使用用户选择；
// 没有保存过时，跟随浏览器/系统 prefers-color-scheme。
// ------------------------------------------------------------
const themeQuery = window.matchMedia("(prefers-color-scheme: light)");

function getPreferredTheme() {
    const savedTheme = localStorage.getItem("theme");
    if (savedTheme === "light" || savedTheme === "dark") {
        return savedTheme;
    }
    return themeQuery.matches ? "light" : "dark";
}

function applyTheme(theme) {
    const nextTheme = theme === "light" ? "light" : "dark";
    document.body.dataset.theme = nextTheme;

    if (!els.themeToggle) return;

    const targetTheme = nextTheme === "light" ? "dark" : "light";
    const targetText = targetTheme === "light" ? "浅色" : "深色";
    els.themeLabel.innerText = targetText;
    els.themeIcon.innerText = targetTheme === "light" ? "☀" : "☾";
    els.themeToggle.setAttribute("aria-label", `切换${targetText}模式`);
}

function toggleTheme() {
    const currentTheme = document.body.dataset.theme === "light" ? "light" : "dark";
    const nextTheme = currentTheme === "light" ? "dark" : "light";
    localStorage.setItem("theme", nextTheme);
    applyTheme(nextTheme);
}

function syncSystemTheme(event) {
    if (localStorage.getItem("theme")) return;
    applyTheme(event.matches ? "light" : "dark");
}

function setAuthState(authenticated, user = null) {
    authState = {
        authenticated: Boolean(authenticated),
        user: authenticated ? user : null,
    };
    updateAuthButton();
    if (!authenticated) {
        renderAuditLoggedOut();
    }
}

function isAuthenticated() {
    return authState.authenticated;
}

function getCookieValue(name) {
    const prefix = `${name}=`;
    return document.cookie
        .split(";")
        .map((item) => item.trim())
        .find((item) => item.startsWith(prefix))
        ?.slice(prefix.length) || "";
}

function getCsrfHeaders(extraHeaders = {}) {
    return {
        ...extraHeaders,
        "X-CSRF-Token": getCookieValue("csrf_token"),
    };
}

function updateAuthButton() {
    if (!els.loginButton || !els.loginLabel) return;
    if (isAuthenticated()) {
        els.loginLabel.innerText = "登出";
        els.loginButton.classList.add("authed");
        els.loginButton.setAttribute("aria-label", "退出控制权限");
    } else {
        els.loginLabel.innerText = "登录";
        els.loginButton.classList.remove("authed");
        els.loginButton.setAttribute("aria-label", "登录控制权限");
    }
}

function showAuthToast(title, message, type = "info") {
    if (!els.authToast) return;
    window.clearTimeout(authToastTimer);
    els.authToastTitle.innerText = title;
    els.authToastMessage.innerText = message;
    els.authToast.classList.remove("hidden", "success", "error", "info");
    els.authToast.classList.add(type, "show");
    authToastTimer = window.setTimeout(() => {
        els.authToast.classList.remove("show");
        window.setTimeout(() => els.authToast.classList.add("hidden"), 220);
    }, 2800);
}

window.showAuthToast = showAuthToast;

function flashAuthButton() {
    if (!els.loginButton) return;
    els.loginButton.classList.add("auth-flash");
    window.setTimeout(() => els.loginButton.classList.remove("auth-flash"), 900);
}

function showLoginError(message) {
    els.loginError.innerText = message;
    els.loginError.classList.remove("hidden");
}

function clearLoginError() {
    els.loginError.innerText = "";
    els.loginError.classList.add("hidden");
}

function openLoginModal() {
    clearLoginError();
    els.loginModal.classList.remove("hidden");
    els.loginModal.setAttribute("aria-hidden", "false");
    setTimeout(() => els.loginUsername.focus(), 0);
}

function closeLoginModal() {
    els.loginModal.classList.add("hidden");
    els.loginModal.setAttribute("aria-hidden", "true");
    els.loginForm.reset();
    clearLoginError();
}

async function login(event) {
    event.preventDefault();
    clearLoginError();
    els.loginSubmit.disabled = true;
    els.loginSubmit.innerText = "登录中...";

    try {
        const response = await fetch("/api/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                username: els.loginUsername.value.trim(),
                password: els.loginPassword.value,
            }),
        });
        const data = await response.json();

        if (!response.ok) {
            showLoginError(data.detail || "登录失败");
            return;
        }

        setAuthState(true, data.user || null);
        closeLoginModal();
        flashAuthButton();
        showAuthToast("控制权限已登录", "现在可以控制 LED 与风扇", "success");
        addLog("控制权限登录成功", "info");
        updateAuditLogs();
    } catch (err) {
        showLoginError("登录失败：网络错误");
    } finally {
        els.loginSubmit.disabled = false;
        els.loginSubmit.innerText = "登录";
    }
}

async function logout() {
    if (!isAuthenticated()) {
        openLoginModal();
        return;
    }

    try {
        await fetch("/api/auth/logout", {
            method: "POST",
            headers: getCsrfHeaders(),
        });
    } catch (err) {
        // 即使后端退出失败，本地也切回未登录，避免继续显示控制权限。
    }
    // 登出只清理控制权限 Cookie，不改变 LED/FAN 的硬件状态。
    setAuthState(false);
    flashAuthButton();
    showAuthToast("控制权限已退出", "LED 与风扇状态保持不变", "success");
    addLog("控制权限已退出", "info");
}

async function syncAuthState() {
    try {
        const response = await fetch("/api/auth/me");

        if (response.status === 401) {
            setAuthState(false);
            return;
        }

        const data = await response.json();

        if (response.ok && data.authenticated) {
            setAuthState(true, data.user);
            updateAuditLogs();
        } else {
            setAuthState(false);
        }
    } catch (err) {
        setAuthState(false);
    }
}

// ------------------------------------------------------------
// formatTime(date)
// 把时间对象格式化为 "HH:MM:SS" 字符串
// ------------------------------------------------------------
function formatTime(date) {
    return date.toLocaleTimeString("zh-CN", { hour12: false });
}

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
    entry.className = `log-entry ${type}`;
    const time = formatTime(new Date());
    entry.innerHTML = `<span class="log-time">${time}</span><span class="log-text">${message}</span>`;
    els.logList.prepend(entry);
    if (els.logList.children.length > 50) {
        els.logList.removeChild(els.logList.lastChild);
    }
}

function setAuditHint(message) {
    if (els.auditHint) {
        els.auditHint.innerText = message;
    }
}

function renderAuditMessage(message) {
    if (!els.auditLogList) return;
    els.auditLogList.innerHTML = "";
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 7;
    cell.className = "audit-empty";
    cell.textContent = message;
    row.appendChild(cell);
    els.auditLogList.appendChild(row);
}

function renderAuditLoggedOut() {
    setAuditHint("登录后查看控制审计记录");
    renderAuditMessage("登录后查看控制审计记录");
}

function createAuditCell(text, className = "") {
    const cell = document.createElement("td");
    if (className) {
        cell.className = className;
    }
    cell.textContent = text || "--";
    return cell;
}

function createAuditBadge(text, type) {
    const badge = document.createElement("span");
    badge.className = `audit-badge ${type}`;
    badge.textContent = text || "--";
    return badge;
}

function renderAuditLogs(logs) {
    if (!els.auditLogList) return;
    els.auditLogList.innerHTML = "";

    if (!logs.length) {
        setAuditHint("暂无控制审计记录");
        renderAuditMessage("暂无控制审计记录");
        return;
    }

    setAuditHint(`最近 ${logs.length} 条控制记录`);

    logs.forEach((item) => {
        const row = document.createElement("tr");
        row.appendChild(createAuditCell(item.created_time || "--", "audit-time"));
        row.appendChild(createAuditCell(item.username || "--"));

        const deviceCell = document.createElement("td");
        deviceCell.appendChild(createAuditBadge(item.device, "device"));
        row.appendChild(deviceCell);

        const actionCell = document.createElement("td");
        actionCell.appendChild(createAuditBadge(item.action, "action"));
        row.appendChild(actionCell);

        const resultCell = document.createElement("td");
        const resultType = item.result === "SUCCESS" ? "success" : "failed";
        resultCell.appendChild(createAuditBadge(item.result, resultType));
        row.appendChild(resultCell);

        row.appendChild(createAuditCell(item.client_ip || "--"));
        row.appendChild(createAuditCell(item.error || "--", item.error ? "audit-error-text" : ""));
        els.auditLogList.appendChild(row);
    });
}

async function updateAuditLogs() {
    if (!isAuthenticated()) {
        renderAuditLoggedOut();
        return;
    }

    try {
        const response = await fetch("/api/control/logs?limit=50");
        if (response.status === 401) {
            setAuthState(false);
            return;
        }
        const data = await response.json();
        if (response.status === 403) {
            setAuditHint("仅管理员可查看审计记录");
            renderAuditMessage(data.detail || "仅管理员可查看审计记录");
            return;
        }
        if (!response.ok) {
            renderAuditMessage(data.detail || "审计记录读取失败");
            return;
        }
        renderAuditLogs(data.logs || []);
    } catch (err) {
        renderAuditMessage("审计记录读取失败：网络错误");
    }
}

// ------------------------------------------------------------
// updateLastTime(type)
// 记录 LED 或风扇的最近操作时间，显示在控制卡片中
// type: "led" 或 "fan"
// 这是纯前端功能，不需要后端支持
// ------------------------------------------------------------
function updateLastTime(type) {
    const now = formatTime(new Date());
    if (type === "led" && els.ledLastTime) {
        els.ledLastTime.innerText = now;
    }
    if (type === "fan" && els.fanLastTime) {
        els.fanLastTime.innerText = now;
    }
}

// ------------------------------------------------------------
// updateUptimeFromBackend()
// 从后端获取真实的系统运行时长（Docker 容器启动后的时间）
// 请求路径：/api/uptime（GET 方式）
// 后端返回：{ "uptime_seconds": 3600, "uptime": "01:00:00" }
// 成功：用后端返回的格式化字符串更新"系统运行"
// 失败：静默处理，不覆盖已有显示
// 这个函数每秒调用一次（见页面底部 setInterval）
// ------------------------------------------------------------
async function updateUptimeFromBackend() {
    if (!els.uptime) return;
    if (uptimePolling) return;

    uptimePolling = true;
    try {
        const response = await fetch("/api/uptime");
        const data = await response.json();
        if (data.uptime) {
            els.uptime.innerText = data.uptime;
        }
    } catch (err) {
        // 请求失败时静默处理，保留上一次显示
    } finally {
        uptimePolling = false;
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
        els.backendStatus.className = "overview-value status-ok";
    } else {
        els.backendStatus.innerText = "断开";
        els.backendStatus.className = "overview-value status-err";
    }
}

// ------------------------------------------------------------
// setLoraOnline(online)
// 根据树莓派 B 的 LoRa 心跳状态，更新前端多处显示
// online = true  时：
//   - "LoRa连接状态" 显示绿色"设备在线"
//   - 顶部徽章显示"设备在线"（绿色圆点）
//   - 设备概览显示"设备在线"
//   - LED 开关、风扇开关都启用（可以点击）
// online = false 时：
//   - "LoRa连接状态" 显示红色"设备离线"
//   - 顶部徽章显示"设备离线"（红色圆点）
//   - 设备概览显示"设备离线"
//   - LED 开关、风扇开关都禁用（防止离线时误操作）
// ------------------------------------------------------------
function setLoraOnline(online) {
    if (online) {
        els.loraStatus.innerText = "设备在线";
        els.loraStatus.className = "info-val status-ok";
        els.deviceStatus.innerText = "设备在线";
        els.deviceBadge.classList.add("online");
        els.deviceBadge.classList.remove("offline");
        els.ledSwitch.disabled = false;
        els.fanSwitch.disabled = false;
        if (els.overviewDeviceStatus) {
            els.overviewDeviceStatus.innerText = "设备在线";
            els.overviewDeviceStatus.className = "overview-value status-ok";
        }
    } else {
        els.loraStatus.innerText = "设备离线";
        els.loraStatus.className = "info-val status-err";
        els.deviceStatus.innerText = "设备离线";
        els.deviceBadge.classList.add("offline");
        els.deviceBadge.classList.remove("online");
        els.ledSwitch.disabled = true;
        els.fanSwitch.disabled = true;
        if (els.overviewDeviceStatus) {
            els.overviewDeviceStatus.innerText = "设备离线";
            els.overviewDeviceStatus.className = "overview-value status-err";
        }
    }
}

// ------------------------------------------------------------
// updateSensor()
// 异步获取传感器数据（温度和光照）
// 请求路径：/api/sensor（nginx 代理到后端 /sensor）
// 成功：把 temperature 和 light_percent 显示到页面上，标记后端在线，记录更新时间
// 失败：标记后端断开
// 这个函数每秒调用一次（见页面底部 setInterval）
// ------------------------------------------------------------
async function updateSensor() {
    if (sensorPolling) return;

    sensorPolling = true;
    try {
        const response = await fetch("/api/sensor");
        const data = await response.json();
        els.temp.innerText = data.temperature;
        els.light.innerText = data.light_percent;
        setBackendOnline(true);
        if (els.lastUpdateTime) {
            els.lastUpdateTime.innerText = formatTime(new Date());
        }
    } catch (err) {
        setBackendOnline(false);
    } finally {
        sensorPolling = false;
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
        els.ledBulb.classList.add("on");
        els.ledGlow.classList.add("on");
    } else {
        els.ledStatus.innerText = "关闭";
        els.ledBulb.classList.remove("on");
        els.ledGlow.classList.remove("on");
    }
}

// ------------------------------------------------------------
// updateLed()
// 从后端缓存同步 LED 当前状态
// 请求路径：/api/led（GET 方式）
// 后端返回：{ "on": true/false, "available": true/false }
// 如果 available 为 false（比如 LoRa 模块初始化失败），显示"不可用"
// 否则同步滑动开关位置和灯泡视觉效果
// 当本浏览器正在发控制请求时跳过，避免轮询覆盖用户刚点击的开关
// ------------------------------------------------------------
async function updateLed() {
    if (ledControlPending) return;

    try {
        const response = await fetch("/api/led");
        const data = await response.json();
        if (!data.available) {
            els.ledStatus.innerText = "不可用";
            return;
        }
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
// 成功：更新灯泡视觉效果，记录操作日志和最近操作时间
// 失败（后端报错或网络错误）：
//   - 显示错误日志
//   - 把开关状态回滚到操作前的状态
// 注意：如果 LoRa 离线，开关会被 disabled，这个函数不会被触发
// ------------------------------------------------------------
async function toggleLed() {
    const newState = els.ledSwitch.checked;
    if (!isAuthenticated()) {
        els.ledSwitch.checked = !newState;
        addLog("LED 控制需要先登录", "error");
        openLoginModal();
        return;
    }

    ledControlPending = true;
    try {
        const response = await fetch("/api/led", {
            method: "POST",
            headers: getCsrfHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ on: newState }),
        });
        const data = await response.json();
        if (response.status === 401 || response.status === 403) {
            setAuthState(false);
            addLog(data.detail || "LED 控制需要重新登录", "error");
            els.ledSwitch.checked = !newState;
            openLoginModal();
        } else if (response.status === 429) {
            addLog(data.detail || "LED 操作过于频繁，请稍后再试", "error");
            els.ledSwitch.checked = !newState;
        } else if (data.error) {
            addLog(`LED 控制失败：${data.error}`, "error");
            els.ledSwitch.checked = !newState;
            updateAuditLogs();
        } else if (data.available) {
            updateLedVisual(data.on);
            addLog(`LED 已${data.on ? "开启" : "关闭"}`, "action");
            updateLastTime("led");
            updateAuditLogs();
        } else {
            addLog("LED 控制失败：硬件不可用", "error");
            els.ledSwitch.checked = !newState;
            updateAuditLogs();
        }
    } catch (err) {
        addLog("LED 控制失败：网络错误", "error");
        els.ledSwitch.checked = !newState;
    } finally {
        ledControlPending = false;
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
        els.fanBulb.classList.add("on");
        els.fanGlow.classList.add("on");
    } else {
        els.fanStatus.innerText = "关闭";
        els.fanBulb.classList.remove("on");
        els.fanGlow.classList.remove("on");
    }
}

// ------------------------------------------------------------
// updateFan()
// 从后端缓存同步风扇当前状态
// 请求路径：/api/fan（GET 方式）
// 后端返回：{ "on": true/false, "available": true/false }
// 如果 available 为 false，显示"不可用"
// 否则同步滑动开关位置和风扇视觉效果
// 当本浏览器正在发控制请求时跳过，避免轮询覆盖用户刚点击的开关
// ------------------------------------------------------------
async function updateFan() {
    if (fanControlPending) return;

    try {
        const response = await fetch("/api/fan");
        const data = await response.json();
        if (!data.available) {
            els.fanStatus.innerText = "不可用";
            return;
        }
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
// 成功：更新风扇视觉效果，记录操作日志和最近操作时间
// 失败：显示错误日志，回滚开关状态
// 注意：如果 LoRa 离线，开关会被 disabled，这个函数不会被触发
// ------------------------------------------------------------
async function toggleFan() {
    const newState = els.fanSwitch.checked;
    if (!isAuthenticated()) {
        els.fanSwitch.checked = !newState;
        addLog("风扇控制需要先登录", "error");
        openLoginModal();
        return;
    }

    fanControlPending = true;
    try {
        const response = await fetch("/api/fan", {
            method: "POST",
            headers: getCsrfHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ on: newState }),
        });
        const data = await response.json();
        if (response.status === 401 || response.status === 403) {
            setAuthState(false);
            addLog(data.detail || "风扇控制需要重新登录", "error");
            els.fanSwitch.checked = !newState;
            openLoginModal();
        } else if (response.status === 429) {
            addLog(data.detail || "风扇操作过于频繁，请稍后再试", "error");
            els.fanSwitch.checked = !newState;
        } else if (data.error) {
            addLog(`风扇控制失败：${data.error}`, "error");
            els.fanSwitch.checked = !newState;
            updateAuditLogs();
        } else if (data.available) {
            updateFanVisual(data.on);
            addLog(`风扇已${data.on ? "开启" : "关闭"}`, "action");
            updateLastTime("fan");
            updateAuditLogs();
        } else {
            addLog("风扇控制失败：硬件不可用", "error");
            els.fanSwitch.checked = !newState;
            updateAuditLogs();
        }
    } catch (err) {
        addLog("风扇控制失败：网络错误", "error");
        els.fanSwitch.checked = !newState;
    } finally {
        fanControlPending = false;
    }
}

// ------------------------------------------------------------
// updateDeviceStates()
// 周期性同步 LED/FAN 状态。后端 GET 只读缓存，不直接访问串口，
// 所以多个浏览器同时打开时可以用它同步别人刚完成的硬件操作。
// 如果上一轮设备状态同步还没结束，跳过本轮，避免慢网络下请求堆积。
// ------------------------------------------------------------
async function updateDeviceStates() {
    if (deviceStatesPolling) return;

    deviceStatesPolling = true;
    try {
        await Promise.all([updateLed(), updateFan()]);
    } finally {
        deviceStatesPolling = false;
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
//   - 设备概览卡片
//   - LED 开关、风扇开关是否可点击
// 如果请求本身失败（fetch 抛异常），把后端和 LoRa 都标记为离线
// ------------------------------------------------------------
async function updateLoraStatus() {
    if (loraPolling) return;

    loraPolling = true;
    try {
        const response = await fetch("/api/lora/status");
        const data = await response.json();
        setLoraOnline(data.online);
        if (data.online) {
            setBackendOnline(true);
        }
    } catch (err) {
        setLoraOnline(false);
        setBackendOnline(false);
    } finally {
        loraPolling = false;
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
els.themeToggle.addEventListener("click", toggleTheme);
els.loginButton.addEventListener("click", () => {
    if (isAuthenticated()) {
        logout();
    } else {
        openLoginModal();
    }
});
els.loginForm.addEventListener("submit", login);
els.loginClose.addEventListener("click", closeLoginModal);
els.loginModal.addEventListener("click", (event) => {
    if (event.target === els.loginModal) {
        closeLoginModal();
    }
});
document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !els.loginModal.classList.contains("hidden")) {
        closeLoginModal();
    }
});
els.clearLog.addEventListener("click", () => {
    els.logList.innerHTML = "";
    addLog("日志已清空", "info");
});
els.auditRefresh.addEventListener("click", updateAuditLogs);
themeQuery.addEventListener("change", syncSystemTheme);

// ------------------------------------------------------------
// 页面初始化
// 顺序：
// 1. 记录系统启动日志
// 2. 立即获取一次传感器数据
// 3. 启动定时器，每秒自动刷新传感器
// 4. 立即获取一次 LED/FAN 当前状态
// 5. 每 2 秒从后端缓存同步 LED/FAN 状态，支持多浏览器状态一致
// 6. 立即获取一次 LoRa 连接状态
// 7. 启动定时器，每 3 秒自动刷新 LoRa 状态
// 8. 启动定时器，每秒刷新系统运行时长
// ------------------------------------------------------------
applyTheme(getPreferredTheme());
syncAuthState();
renderAuditLoggedOut();
addLog("系统初始化完成，开始连接设备...", "info");
updateSensor();
setInterval(updateSensor, 1000);
updateDeviceStates();
setInterval(updateDeviceStates, 2000);
updateLoraStatus();
setInterval(updateLoraStatus, 3000);
updateUptimeFromBackend();
setInterval(updateUptimeFromBackend, 1000);

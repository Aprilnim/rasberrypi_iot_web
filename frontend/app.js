const els = {
    temp: document.getElementById("temp"),
    light: document.getElementById("light"),
    ledSwitch: document.getElementById("led-switch"),
    ledStatus: document.getElementById("led-status"),
    ledBulb: document.getElementById("led-bulb"),
    ledGlow: document.getElementById("led-glow"),
    backendStatus: document.getElementById("backend-status"),
    deviceStatus: document.getElementById("device-status"),
    deviceBadge: document.getElementById("device-badge"),
    logList: document.getElementById("log-list"),
    clearLog: document.getElementById("clear-log"),
};

function addLog(message, type = "info") {
    const entry = document.createElement("div");
    entry.className = `log-entry ${type}`;
    const time = new Date().toLocaleTimeString("zh-CN", { hour12: false });
    entry.innerHTML = `<span class="log-time">${time}</span><span class="log-text">${message}</span>`;
    els.logList.prepend(entry);
    if (els.logList.children.length > 50) {
        els.logList.removeChild(els.logList.lastChild);
    }
}

function setBackendOnline(online) {
    if (online) {
        els.backendStatus.innerText = "正常";
        els.backendStatus.className = "info-val status-ok";
        els.deviceStatus.innerText = "设备在线";
        els.deviceBadge.classList.add("online");
        els.deviceBadge.classList.remove("offline");
    } else {
        els.backendStatus.innerText = "断开";
        els.backendStatus.className = "info-val status-err";
        els.deviceStatus.innerText = "设备离线";
        els.deviceBadge.classList.add("offline");
        els.deviceBadge.classList.remove("online");
    }
}

async function updateSensor() {
    try {
        const response = await fetch("/api/sensor");
        const data = await response.json();
        els.temp.innerText = data.temperature;
        els.light.innerText = data.light_percent;
        setBackendOnline(true);
    } catch (err) {
        setBackendOnline(false);
    }
}

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

async function updateLed() {
    try {
        const response = await fetch("/api/led");
        const data = await response.json();
        if (!data.available) {
            els.ledStatus.innerText = "不可用";
            els.ledSwitch.disabled = true;
            return;
        }
        els.ledSwitch.checked = data.on;
        updateLedVisual(data.on);
    } catch (err) {
        // silent fail on init
    }
}

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
            updateLedVisual(data.on);
            addLog(`LED 已${data.on ? "开启" : "关闭"}`, "action");
        } else {
            addLog("LED 控制失败：硬件不可用", "error");
            els.ledSwitch.checked = !newState;
        }
    } catch (err) {
        addLog("LED 控制失败：网络错误", "error");
        els.ledSwitch.checked = !newState;
    }
}

els.ledSwitch.addEventListener("change", toggleLed);
els.clearLog.addEventListener("click", () => {
    els.logList.innerHTML = "";
    addLog("日志已清空", "info");
});

addLog("系统初始化完成，开始连接设备...", "info");
updateSensor();
setInterval(updateSensor, 1000);
updateLed();

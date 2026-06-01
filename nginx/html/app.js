async function updateSensor() {
    const response = await fetch("http://192.168.10.70:8080/sensor");
    const data = await response.json();

    document.getElementById("temp").innerText = data.temperature;
    document.getElementById("light").innerText = data.light_percent;
}

updateSensor();
setInterval(updateSensor, 1000);

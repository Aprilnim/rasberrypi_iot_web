FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y gcc python3-dev lgpio \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt

COPY lora_mqtt.py .

CMD ["python", "-u", "lora_mqtt.py"]

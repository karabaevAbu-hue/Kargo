FROM python:3.12-slim
WORKDIR /app
# libzbar0 — чтение штрихкодов и QR с фото (приёмка и выдача); без неё бот просит ввести значение вручную
RUN apt-get update && apt-get install -y --no-install-recommends libzbar0 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/cargo_platform/requirements.txt
RUN pip install --no-cache-dir -r /app/cargo_platform/requirements.txt
COPY . /app/cargo_platform
CMD ["python", "-m", "cargo_platform.main"]

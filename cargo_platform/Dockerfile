FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt /app/cargo_platform/requirements.txt
RUN pip install --no-cache-dir -r /app/cargo_platform/requirements.txt
COPY . /app/cargo_platform
CMD ["python", "-m", "cargo_platform.main"]

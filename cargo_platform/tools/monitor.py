"""Мониторинг (ТЗ, раздел 10): раз в 5 минут проверяет /health, при падении пишет владельцу платформы в Telegram.

Работает отдельным процессом (отдельный контейнер в docker-compose), поэтому видит и тот случай,
когда упал сам сервер. Только стандартная библиотека.

    python -m cargo_platform.tools.monitor
"""
import os
import time
import urllib.parse
import urllib.request

URL = os.getenv("HEALTH_URL") or (os.getenv("BASE_URL", "").rstrip("/") + "/health")
TOKEN = os.getenv("MASTER_BOT_TOKEN", "")
ADMINS = [x for x in os.getenv("PLATFORM_ADMINS", "").replace(" ", "").split(",") if x]
INTERVAL = int(os.getenv("MONITOR_INTERVAL", "300"))
FAILS_BEFORE_ALERT = 2   # одна неудача — может быть сеть; две подряд — тревога


def healthy() -> bool:
    try:
        with urllib.request.urlopen(URL, timeout=10) as r:
            return r.status == 200 and r.read().strip() == b"ok"
    except Exception:
        return False


def alert(text: str) -> None:
    for chat_id in ADMINS:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        try:
            urllib.request.urlopen(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=data, timeout=10)
        except Exception as e:
            print("alert failed:", e)


def main() -> None:
    fails, down = 0, False
    while True:
        if healthy():
            if down:
                alert("✅ Сервер платформы снова работает.")
            fails, down = 0, False
        else:
            fails += 1
            if fails >= FAILS_BEFORE_ALERT and not down:
                down = True
                alert(f"🚨 Сервер платформы не отвечает: {URL}. Все карго недоступны.")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()

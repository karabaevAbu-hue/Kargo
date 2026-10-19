"""Тарифы платформы (ТЗ, 3.4)."""

# ключ: (название, лимит клиентов, цена в месяц, лимит менеджеров); None = без лимита
ALL = {
    "trial": ("Пробный", 100, 0, 1),
    "start": ("Старт", 500, 3000, 2),
    "business": ("Бизнес", 2000, 5000, 5),
    "pro": ("Про", None, 8000, None),
}
PAID = {k: v for k, v in ALL.items() if k != "trial"}

START_BROADCASTS_PER_MONTH = 4   # ТЗ, 7.2: на «Старт» не больше 4 массовых рассылок в месяц


def client_limit(tariff: str) -> int | None:
    return ALL.get(tariff, ALL["trial"])[1]


def staff_limit(tariff: str) -> int | None:
    return ALL.get(tariff, ALL["trial"])[3]


def broadcast_limit(tariff: str) -> int | None:
    return START_BROADCASTS_PER_MONTH if tariff == "start" else None

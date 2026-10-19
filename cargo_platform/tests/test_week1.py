"""Проверка недели 1 без реального Telegram.

Запуск из папки, где лежит cargo_platform:
    python -m cargo_platform.tests.test_week1

Что проверяется (ТЗ, раздел 11, этап 1):
- валидаторы полей, шифрование токена, статусы подписки;
- полная регистрация карго через главный бот → подключение токена → мастер настройки;
- две компании, два бота, оба отвечают, данные не смешиваются;
- приглашение менеджера по ссылке и вход в /admin.
"""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal

_tmp = tempfile.mkdtemp()
os.environ.update({
    "MASTER_BOT_TOKEN": "111111:" + "M" * 35,
    "BASE_URL": "https://test.example",
    "DATABASE_URL": f"sqlite+aiosqlite:///{_tmp}/test.db",
    "FERNET_KEY": "pXlyrCqA2ZcOfnS7yp2c9N2VjfV5lW3l8Vh3X0bHk0c=",
    "PLATFORM_ADMINS": "999",
})

from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from aiogram.methods import GetMe, SendMessage, SendPhoto  # noqa: E402
from aiogram.types import Chat, Message, Update, User  # noqa: E402
from sqlalchemy import select  # noqa: E402

from cargo_platform.client_bot.handlers import router as client_router  # noqa: E402
from cargo_platform.db.base import Session, init_db  # noqa: E402
from cargo_platform.db.models import (  # noqa: E402
    Company, CompanyBot, CompanySettings, CompanyStatus, Staff, StaffInvite,
)
from cargo_platform.master_bot import menu, registration, settings  # noqa: E402
from cargo_platform.services import bot_manager as bm  # noqa: E402
from cargo_platform.services.crypto import decrypt, encrypt  # noqa: E402
from cargo_platform.services.subscriptions import next_status  # noqa: E402
from cargo_platform.services.validators import (  # noqa: E402
    clean_phone, format_code, has_code_placeholder, parse_decimal, valid_prefix, valid_token,
)

OK = 0


def check(cond, name):
    global OK
    if not cond:
        print("❌", name)
        sys.exit(1)
    OK += 1
    print("✅", name)


class FakeSession(BaseSession):
    """Вместо Telegram: запоминает отправленные сообщения."""
    def __init__(self, username="test_bot", bot_id=500):
        super().__init__()
        self.sent: list = []
        self.username, self.bot_id = username, bot_id

    async def make_request(self, bot, method, timeout=None):
        self.sent.append(method)
        if isinstance(method, GetMe):
            return User(id=self.bot_id, is_bot=True, first_name="b", username=self.username)
        if isinstance(method, (SendMessage, SendPhoto)):
            return Message(message_id=len(self.sent), date=datetime.now(),
                           chat=Chat(id=int(method.chat_id), type="private"),
                           text=getattr(method, "text", None))
        return True

    async def stream_content(self, *a, **kw):
        yield b""

    async def close(self):
        pass

    def texts(self):
        return [m.text for m in self.sent if isinstance(m, SendMessage)]


SESSIONS: dict[str, FakeSession] = {}


def fake_make_bot(token: str) -> Bot:
    if token not in SESSIONS:
        n = len(SESSIONS) + 1
        SESSIONS[token] = FakeSession(username=f"cargo{n}_bot", bot_id=500 + n)
    return Bot(token, session=SESSIONS[token], default=DefaultBotProperties(parse_mode="HTML"))


bm.make_bot = fake_make_bot
registration.make_bot = fake_make_bot

_uid = 0


def upd(user_id: int, text=None, contact_phone=None, callback=None) -> Update:
    global _uid
    _uid += 1
    user = {"id": user_id, "is_bot": False, "first_name": "U"}
    msg = {"message_id": _uid, "date": 1, "chat": {"id": user_id, "type": "private"}, "from": user}
    if callback:
        return Update.model_validate({"update_id": _uid, "callback_query": {
            "id": str(_uid), "from": user, "chat_instance": "x", "data": callback, "message": msg}})
    if contact_phone:
        msg["contact"] = {"phone_number": contact_phone, "first_name": "U", "user_id": user_id}
    if text is not None:
        msg["text"] = text
        if text.startswith("/"):
            msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    return Update.model_validate({"update_id": _uid, "message": msg})


def test_pure():
    check(clean_phone("0555 12 34 56") == "+996555123456", "телефон 0555… → +996…")
    check(clean_phone("+996 555 123 456") == "+996555123456", "телефон +996 с пробелами")
    check(clean_phone("12345") is None, "плохой телефон отклонён")
    check(valid_prefix("osh") == "OSH" and valid_prefix("O1") is None, "префикс кода")
    check(format_code("OSH", 7) == "OSH-0007", "формат кода OSH-0007")
    check(has_code_placeholder("广州 {code}") and not has_code_placeholder("广州"), "адрес обязан содержать {CODE}")
    check(parse_decimal("280,5", Decimal(1), Decimal(1000)) == Decimal("280.5"), "цена с запятой")
    check(valid_token("123456789:" + "A" * 35) and not valid_token("abc"), "формат токена")
    check(decrypt(encrypt("secret")) == "secret" and encrypt("secret") != "secret", "шифрование токена")
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    check(next_status(CompanyStatus.trial, now + timedelta(days=1), now) == CompanyStatus.trial, "пробный идёт")
    check(next_status(CompanyStatus.trial, now - timedelta(days=1), now) == CompanyStatus.grace, "после пробного — льгота")
    check(next_status(CompanyStatus.active, now - timedelta(days=4), now) == CompanyStatus.paused, "после льготы — пауза")
    check(next_status(CompanyStatus.blocked, now + timedelta(days=9), now) == CompanyStatus.blocked, "блок не снимается сам")


async def register_company(master_dp, master, owner_id, name, token, prefix, price):
    steps = [
        upd(owner_id, "/start"),
        upd(owner_id, callback="lang:ru"),
        upd(owner_id, "Абу Тестов"),
        upd(owner_id, contact_phone="996555000" + str(owner_id)[-3:].zfill(3)),
        upd(owner_id, name),
        upd(owner_id, "Ош"),
        upd(owner_id, "ул. Ленина 1"),
        upd(owner_id, "Пн–Сб 10–19"),
        upd(owner_id, "Пропустить ➡️"),
        upd(owner_id, callback="reg:ok"),
        upd(owner_id, token),
        # мастер настройки
        upd(owner_id, prefix),
        upd(owner_id, "1"),
        upd(owner_id, "广东省广州市白云区 {code}"),
        upd(owner_id, "Пропустить ➡️"),
        upd(owner_id, "Пропустить ➡️"),
        upd(owner_id, "13800000000"),
        upd(owner_id, str(price)),
        upd(owner_id, "1"),
        upd(owner_id, "12–18 дней"),
        upd(owner_id, "14"),
        upd(owner_id, "20"),
        upd(owner_id, "Батареи, жидкости, оружие"),
        upd(owner_id, "MBank, получатель ИП Тест, +996555000000"),
    ]
    for u in steps:
        await master_dp.feed_update(master, u)


async def test_flow():
    await init_db()
    master_session = FakeSession(username="master_bot", bot_id=1)
    master = Bot("111111:" + "M" * 35, session=master_session, default=DefaultBotProperties(parse_mode="HTML"))
    master_dp = Dispatcher(storage=MemoryStorage())
    master_dp.include_routers(registration.router, settings.router, menu.router)
    tenant_dp = Dispatcher(storage=MemoryStorage())
    tenant_dp.include_router(client_router)
    manager = bm.BotManager(tenant_dp)
    manager.master = master
    master_dp["manager"] = manager

    t1, t2 = "222222:" + "A" * 35, "333333:" + "B" * 35
    await register_company(master_dp, master, 101, "Карго Альфа", t1, "osh", 280)
    await register_company(master_dp, master, 102, "Карго Бета", t2, "kg", 300)

    async with Session() as s:
        companies = (await s.execute(select(Company).order_by(Company.id))).scalars().all()
        check(len(companies) == 2, "создано 2 компании")
        a, b = companies
        sa, sb = await s.get(CompanySettings, a.id), await s.get(CompanySettings, b.id)
        check(sa.setup_done and sb.setup_done, "мастер настройки завершён у обеих")
        check(sa.code_prefix == "OSH" and sb.code_prefix == "KG", "префиксы сохранены раздельно")
        check(sa.address_taobao.endswith("{CODE}"), "шаблон адреса нормализован в {CODE}")
        check(sa.receiver_template == "{CODE}", "получатель по умолчанию = {CODE}")
        check(sa.price_per_kg == Decimal("280") and sb.price_per_kg == Decimal("300"), "цены у каждой свои")
        bots = (await s.execute(select(CompanyBot))).scalars().all()
        check(len(bots) == 2 and all(decrypt(x.token_encrypted) in (t1, t2) for x in bots), "токены зашифрованы и расшифровываются")
        check(t1 not in [x.token_encrypted for x in bots], "токен не хранится открытым текстом")
        check(a.status == CompanyStatus.trial, "статус пробный")

    check(manager.is_running(a.id) and manager.is_running(b.id), "оба бота запущены")
    check(any("Настройка завершена" in (x or "") for x in master_session.texts()), "владелец получил «Настройка завершена»")

    # Повторное подключение чужого токена отклоняется
    await master_dp.feed_update(master, upd(103, "/start"))
    for u in [upd(103, callback="lang:ru"), upd(103, "Иван Иванов"), upd(103, contact_phone="996555000777"),
              upd(103, "Карго Гамма"), upd(103, "Ош"), upd(103, "ул. Тест 5"), upd(103, "Пн–Пт"),
              upd(103, "Пропустить ➡️"), upd(103, callback="reg:ok"), upd(103, t1)]:
        await master_dp.feed_update(master, u)
    check(any("уже подключён к другой компании" in (x or "") for x in master_session.texts()), "чужой токен отклонён")

    # Оба бота карго отвечают, каждый своими данными
    bot_a = manager._bots[a.id][0]
    bot_b = manager._bots[b.id][0]
    await tenant_dp.feed_update(bot_a, upd(201, "/start"), company_id=a.id)
    await tenant_dp.feed_update(bot_b, upd(201, "/start"), company_id=b.id)
    text_a = " ".join(SESSIONS[t1].texts())
    text_b = " ".join(SESSIONS[t2].texts())
    check("Карго Альфа" in text_a and "280" in text_a and "Бета" not in text_a, "бот A показывает только данные A")
    check("Карго Бета" in text_b and "300" in text_b and "Альфа" not in text_b, "бот B показывает только данные B")

    # Приглашение менеджера
    await master_dp.feed_update(master, upd(101, callback="staff:invite"))
    async with Session() as s:
        inv = await s.scalar(select(StaffInvite).where(StaffInvite.company_id == a.id))
    await tenant_dp.feed_update(bot_b, upd(301, f"/start staff_{inv.token}"), company_id=b.id)
    check("недействительна" in SESSIONS[t2].texts()[-1], "ссылка компании A не работает в боте B")
    await tenant_dp.feed_update(bot_a, upd(301, f"/start staff_{inv.token}"), company_id=a.id)
    check("добавлены как менеджер" in SESSIONS[t1].texts()[-1], "менеджер добавлен по ссылке")
    await tenant_dp.feed_update(bot_a, upd(302, f"/start staff_{inv.token}"), company_id=a.id)
    check("недействительна" in SESSIONS[t1].texts()[-1], "ссылка одноразовая")
    before = len(SESSIONS[t2].texts())
    await tenant_dp.feed_update(bot_b, upd(301, "/admin"), company_id=b.id)
    check(len(SESSIONS[t2].texts()) == before, "менеджер A не видит админку B")
    await tenant_dp.feed_update(bot_a, upd(301, "/admin"), company_id=a.id)
    check("менеджер" in SESSIONS[t1].texts()[-1], "менеджер A входит в /admin")
    async with Session() as s:
        staff = (await s.execute(select(Staff).where(Staff.company_id == a.id))).scalars().all()
    check(len(staff) == 2, "у компании A владелец + 1 менеджер")

    # Пауза за неоплату
    async with Session() as s:
        (await s.get(Company, a.id)).status = CompanyStatus.paused
        await s.commit()
    await tenant_dp.feed_update(bot_a, upd(202, "/start"), company_id=a.id)
    check("временно недоступен" in SESSIONS[t1].texts()[-1], "приостановленный бот отвечает заглушкой")

    # Правка одного поля из меню
    await master_dp.feed_update(master, upd(102, callback="set:price_per_kg"))
    await master_dp.feed_update(master, upd(102, "350"))
    async with Session() as s:
        check((await s.get(CompanySettings, b.id)).price_per_kg == Decimal("350"), "цена изменена из меню")


async def main():
    test_pure()
    await test_flow()
    print(f"\nВсе проверки пройдены: {OK}")


if __name__ == "__main__":
    asyncio.run(main())

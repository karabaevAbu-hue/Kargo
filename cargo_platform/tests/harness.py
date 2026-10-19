"""Общий стенд для тестов без настоящего Telegram: поддельная сессия, сборка диспетчеров, помощники.

Импортировать ПЕРВЫМ: он задаёт переменные окружения до загрузки cargo_platform.
"""
import os
import sys
import tempfile
from datetime import datetime

_tmp = tempfile.mkdtemp()
os.environ.update({
    "MASTER_BOT_TOKEN": "111111:" + "M" * 35,
    "BASE_URL": "https://test.example",
    "DATABASE_URL": f"sqlite+aiosqlite:///{_tmp}/test.db",
    "FERNET_KEY": "pXlyrCqA2ZcOfnS7yp2c9N2VjfV5lW3l8Vh3X0bHk0c=",
    "PLATFORM_ADMINS": "999",
    "TZ_OFFSET_HOURS": "6",
})
os.chdir(_tmp)   # media/ создаётся рядом с временной базой

from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.exceptions import TelegramForbiddenError  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from aiogram.methods import (  # noqa: E402
    GetFile, GetMe, SendDocument, SendMessage, SendPhoto, SendVideo,
)
from aiogram.types import Chat, File, Message, Update, User  # noqa: E402

from cargo_platform.client_bot.handlers import router as client_router  # noqa: E402
from cargo_platform.master_bot import menu, registration, settings  # noqa: E402
from cargo_platform.services import bot_manager as bm  # noqa: E402

OK = 0


def check(cond, name):
    global OK
    if not cond:
        print("❌", name)
        sys.exit(1)
    OK += 1
    print("✅", name)


class FakeSession(BaseSession):
    """Вместо Telegram: запоминает отправленное, отдаёт файлы, умеет «блокировать» бота пользователем."""

    def __init__(self, username="test_bot", bot_id=500):
        super().__init__()
        self.sent: list = []
        self.username, self.bot_id = username, bot_id
        self.files: dict[str, bytes] = {}
        self.blocked: set[int] = set()

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, GetMe):
            return User(id=self.bot_id, is_bot=True, first_name="b", username=self.username)
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id=method.file_id + "u", file_path=method.file_id)
        if isinstance(method, (SendMessage, SendPhoto, SendVideo, SendDocument)):
            if int(method.chat_id) in self.blocked:
                raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
            self.sent.append(method)
            return Message(message_id=len(self.sent), date=datetime.now(),
                           chat=Chat(id=int(method.chat_id), type="private"), text=getattr(method, "text", None))
        self.sent.append(method)
        return True

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        yield self.files[url.rsplit("/", 1)[-1]]

    async def close(self):
        pass

    def texts(self, chat_id=None):
        """Тексты и подписи сообщений (всем или одному чату)."""
        out = []
        for m in self.sent:
            if chat_id is not None and int(getattr(m, "chat_id", -1)) != chat_id:
                continue
            t = getattr(m, "text", None) or getattr(m, "caption", None)
            if t:
                out.append(t)
        return out

    def last(self, chat_id):
        t = self.texts(chat_id)
        return t[-1] if t else ""

    def all_text(self, chat_id):
        return "\n".join(self.texts(chat_id))

    def documents(self, chat_id=None):
        return [m for m in self.sent if isinstance(m, SendDocument)
                and (chat_id is None or int(m.chat_id) == chat_id)]

    def photos(self, chat_id=None):
        return [m for m in self.sent if isinstance(m, SendPhoto)
                and (chat_id is None or int(m.chat_id) == chat_id)]

    def reply_buttons(self, chat_id):
        """callback_data всех inline-кнопок последнего сообщения с кнопками этому чату."""
        for m in reversed(self.sent):
            if int(getattr(m, "chat_id", -1)) == chat_id and getattr(m, "reply_markup", None) is not None:
                kb = m.reply_markup
                rows = getattr(kb, "inline_keyboard", None)
                if rows:
                    return [b.callback_data for r in rows for b in r if b.callback_data]
        return []


SESSIONS: dict[str, FakeSession] = {}


def fake_make_bot(token: str) -> Bot:
    if token not in SESSIONS:
        n = len(SESSIONS) + 1
        SESSIONS[token] = FakeSession(username=f"cargo{n}_bot", bot_id=500 + n)
    return Bot(token, session=SESSIONS[token], default=DefaultBotProperties(parse_mode="HTML"))


bm.make_bot = fake_make_bot
registration.make_bot = fake_make_bot

_uid = 0


def upd(user_id: int, text=None, contact_phone=None, callback=None, photo=None, document=None,
        caption=None, username=None) -> Update:
    global _uid
    _uid += 1
    user = {"id": user_id, "is_bot": False, "first_name": "U", "username": username or f"u{user_id}"}
    msg = {"message_id": _uid, "date": 1, "chat": {"id": user_id, "type": "private"}, "from": user}
    if callback:
        return Update.model_validate({"update_id": _uid, "callback_query": {
            "id": str(_uid), "from": user, "chat_instance": "x", "data": callback, "message": msg}})
    if contact_phone:
        msg["contact"] = {"phone_number": contact_phone, "first_name": "U", "user_id": user_id}
    if photo:
        msg["photo"] = [{"file_id": photo, "file_unique_id": photo + "u", "width": 10, "height": 10}]
    if document:
        fid, name, data = document
        msg["document"] = {"file_id": fid, "file_unique_id": fid + "u", "file_name": name,
                           "mime_type": "application/octet-stream", "file_size": len(data)}
    if caption:
        msg["caption"] = caption
    if text is not None:
        msg["text"] = text
        if text.startswith("/"):
            msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    return Update.model_validate({"update_id": _uid, "message": msg})


async def register_company(master_dp, master, owner_id, name, token, prefix, price):
    steps = [
        upd(owner_id, "/start"), upd(owner_id, callback="lang:ru"), upd(owner_id, "Абу Тестов"),
        upd(owner_id, contact_phone="996555000" + str(owner_id)[-3:].zfill(3)),
        upd(owner_id, name), upd(owner_id, "Ош"), upd(owner_id, "ул. Ленина 1"), upd(owner_id, "Пн–Сб 10–19"),
        upd(owner_id, "Пропустить ➡️"), upd(owner_id, callback="reg:ok"), upd(owner_id, token),
        upd(owner_id, prefix), upd(owner_id, "1"), upd(owner_id, "广东省广州市白云区 {code}"),
        upd(owner_id, "Пропустить ➡️"), upd(owner_id, "Пропустить ➡️"), upd(owner_id, "13800000000"),
        upd(owner_id, str(price)), upd(owner_id, "1"), upd(owner_id, "12–18 дней"), upd(owner_id, "14"),
        upd(owner_id, "20"), upd(owner_id, "Батареи, жидкости, оружие"),
        upd(owner_id, "MBank, получатель ИП Тест, +996555000000"),
    ]
    for u in steps:
        await master_dp.feed_update(master, u)


class World:
    """Две карго, главный бот и оба диспетчера."""

    async def setup(self):
        from cargo_platform.db.base import init_db
        await init_db()
        self.master_session = FakeSession(username="master_bot", bot_id=1)
        self.master = Bot("111111:" + "M" * 35, session=self.master_session,
                          default=DefaultBotProperties(parse_mode="HTML"))
        self.master_dp = Dispatcher(storage=MemoryStorage())
        self.master_dp.include_routers(registration.router, settings.router, menu.router)
        self.tenant_dp = Dispatcher(storage=MemoryStorage())
        self.tenant_dp.include_router(client_router)
        self.manager = bm.BotManager(self.tenant_dp)
        self.manager.master = self.master
        self.master_dp["manager"] = self.manager
        self.tokens = {"A": "222222:" + "A" * 35, "B": "333333:" + "B" * 35}
        await register_company(self.master_dp, self.master, 101, "Карго Альфа", self.tokens["A"], "osh", 280)
        await register_company(self.master_dp, self.master, 102, "Карго Бета", self.tokens["B"], "kg", 300)
        from sqlalchemy import select
        from cargo_platform.db.base import Session
        from cargo_platform.db.models import Company
        async with Session() as s:
            a, b = (await s.execute(select(Company).order_by(Company.id))).scalars().all()
        self.cid = {"A": a.id, "B": b.id}
        self.bot = {k: self.manager._bots[v][0] for k, v in self.cid.items()}
        self.session = {k: SESSIONS[t] for k, t in self.tokens.items()}

    async def send(self, co: str, user_id: int, *a, **kw):
        await self.tenant_dp.feed_update(self.bot[co], upd(user_id, *a, **kw), company_id=self.cid[co])

    async def say(self, co: str, user_id: int, text: str) -> str:
        await self.send(co, user_id, text)
        return self.session[co].last(user_id)

    async def click(self, co: str, user_id: int, data: str) -> str:
        await self.send(co, user_id, callback=data)
        return self.session[co].last(user_id)

    async def register_client(self, co: str, user_id: int, name: str, phone: str, city="Ош"):
        await self.send(co, user_id, "/start")
        await self.send(co, user_id, callback="clang:ru")
        await self.send(co, user_id, name)
        await self.send(co, user_id, contact_phone=phone)
        await self.send(co, user_id, city)
        await self.send(co, user_id, callback="reg:agree")

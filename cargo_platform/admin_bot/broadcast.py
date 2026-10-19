"""Ручная рассылка клиентам (ТЗ, 7.2)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from cargo_platform.admin_bot.common import AdminFSM, IsStaff
from cargo_platform.config import config
from cargo_platform.db.base import Session
from cargo_platform.db.models import Batch, Broadcast, Client, Company, Staff
from cargo_platform.master_bot.common import utcnow
from cargo_platform.services import audit, broadcasts, notify
from cargo_platform.services.notifications import kb
from cargo_platform.services.tariffs import broadcast_limit

router = Router(name="admin_broadcast")
router.message.filter(F.chat.type == "private", IsStaff())
router.callback_query.filter(IsStaff())

_TASKS: set[asyncio.Task] = set()     # держим ссылки, иначе фоновая рассылка может быть собрана GC


def pending_tasks() -> set[asyncio.Task]:
    return _TASKS


def spawn(bot: Bot, company_id: int, broadcast_id: int) -> None:
    task = asyncio.create_task(broadcasts.run(bot, company_id, broadcast_id))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


@router.callback_query(F.data == "a:bc")
async def bc_menu(call: CallbackQuery, company: Company):
    await call.answer()
    async with Session() as s:
        rows = (await s.execute(select(Broadcast).where(Broadcast.company_id == company.id)
                                .order_by(Broadcast.id.desc()).limit(5))).scalars().all()
    lines = ["📣 <b>Рассылка</b>"]
    names = {"draft": "черновик", "scheduled": "запланирована", "sending": "отправляется", "done": "отправлена"}
    for b in rows:
        when = f", на {b.scheduled_at + timedelta(hours=config.tz_offset_hours):%d.%m %H:%M}" if b.status == "scheduled" and b.scheduled_at else ""
        lines.append(f"• #{b.id} {names.get(b.status, b.status)}{when}: доставлено {b.sent}, не доставлено {b.failed} — {b.text[:40]}")
    await call.message.answer("\n".join(lines), reply_markup=kb([("➕ Новая рассылка", "a:bcn")], [("⬅️ Меню", "a:menu")]))


@router.callback_query(F.data == "a:bcn")
async def bc_new(call: CallbackQuery, state: FSMContext, company: Company):
    await call.answer()
    limit = broadcast_limit(company.tariff)
    if limit is not None and await broadcasts.month_count(company.id) >= limit:
        return await call.message.answer(f"На тарифе «Старт» не больше {limit} массовых рассылок в месяц. "
                                         "Лимит исчерпан — он обновится в начале месяца или перейдите на «Бизнес».")
    await state.set_state(AdminFSM.bc_text)
    await state.set_data({})
    await call.message.answer("Пришлите сообщение для рассылки: текст, можно с фото или видео (текст — в подписи).\n/admin — отмена.")


@router.message(AdminFSM.bc_text, F.photo | F.video | (F.text & ~F.text.startswith("/")))
async def bc_text(message: Message, state: FSMContext):
    media = None
    if message.photo:
        media = f"photo:{message.photo[-1].file_id}"
    elif message.video:
        media = f"video:{message.video.file_id}"
    text = (message.caption if media else message.text) or ""
    text = text.strip()
    if not text and not media:
        return await message.answer("Нужен текст или фото/видео с подписью.")
    if len(text) > 3500:
        return await message.answer("Слишком длинный текст (максимум 3500 символов).")
    await state.update_data(text=text, media=media)
    await state.set_state(AdminFSM.bc_button)
    await message.answer("Добавить кнопку-ссылку? Формат: <code>Текст кнопки | https://ссылка</code>",
                         reply_markup=kb([("Без кнопки", "a:bcskip")]))


async def _ask_audience(message: Message) -> None:
    await message.answer("Кому отправить?", reply_markup=kb(
        [("👥 Все клиенты", "a:bca:all")], [("📦 Клиенты партии", "a:bca:batch")],
        [("💸 Должники", "a:bca:debtors")], [("🏙 Клиенты города", "a:bca:city")]))


@router.callback_query(AdminFSM.bc_button, F.data == "a:bcskip")
async def bc_skip_button(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await _ask_audience(call.message)


@router.message(AdminFSM.bc_button, F.text, ~F.text.startswith("/"))
async def bc_button(message: Message, state: FSMContext):
    if "|" not in message.text:
        return await message.answer("Формат: Текст кнопки | https://ссылка — или нажмите «Без кнопки».")
    title, url = (x.strip() for x in message.text.split("|", 1))
    if not title or len(title) > 40 or not url.startswith(("https://", "http://", "https://t.me/")):
        return await message.answer("Ссылка должна начинаться с https://, текст кнопки — до 40 символов.")
    await state.update_data(button={"text": title, "url": url})
    await _ask_audience(message)


@router.callback_query(F.data.startswith("a:bca:"))
async def bc_audience(call: CallbackQuery, state: FSMContext, company: Company):
    await call.answer()
    kind = call.data.split(":")[2]
    if kind in ("all", "debtors"):
        await state.update_data(audience={"type": kind})
        return await _preview(call.message, state, company)
    async with Session() as s:
        if kind == "batch":
            rows = (await s.execute(select(Batch).where(Batch.company_id == company.id)
                                    .order_by(Batch.id.desc()).limit(10))).scalars().all()
            buttons = [[(b.number, f"a:bcv:{b.id}")] for b in rows]
        else:
            cities = (await s.execute(select(Client.city).where(Client.company_id == company.id, Client.city.is_not(None))
                                      .distinct().limit(10))).scalars().all()
            await state.update_data(cities=list(cities))
            buttons = [[(c, f"a:bcc:{i}")] for i, c in enumerate(cities)]
    if not buttons:
        return await call.message.answer("Выбирать пока не из чего. Выберите другую аудиторию.")
    await call.message.answer("Выберите:", reply_markup=kb(*buttons))


@router.callback_query(F.data.startswith("a:bcv:"))
async def bc_batch(call: CallbackQuery, state: FSMContext, company: Company):
    await call.answer()
    await state.update_data(audience={"type": "batch", "value": int(call.data.split(":")[2])})
    await _preview(call.message, state, company)


@router.callback_query(F.data.startswith("a:bcc:"))
async def bc_city(call: CallbackQuery, state: FSMContext, company: Company):
    await call.answer()
    d = await state.get_data()
    try:
        city = d["cities"][int(call.data.split(":")[2])]
    except (KeyError, IndexError):
        return
    await state.update_data(audience={"type": "city", "value": city})
    await _preview(call.message, state, company)


async def _preview(message: Message, state: FSMContext, company: Company) -> None:
    d = await state.get_data()
    aud = dict(d["audience"])
    if d.get("button"):
        aud["button"] = d["button"]
    people = await broadcasts.recipients(company.id, aud)
    await message.answer("👁 <b>Предпросмотр</b> — так увидит клиент:")
    await notify.send(message.bot, message.chat.id, d["text"] or None, reply_markup=broadcasts.button_markup(aud),
                      **broadcasts.media_args(d.get("media")))
    await message.answer(f"Получателей: <b>{len(people)}</b> ({broadcasts.AUDIENCES[aud['type']]}).",
                         reply_markup=kb([("🚀 Отправить", "a:bcgo"), ("🕒 Запланировать", "a:bcsch")],
                                         [("✖️ Отмена", "a:menu")]))


async def _create(company: Company, staff: Staff, d: dict, status: str, when: datetime | None = None) -> Broadcast:
    aud = dict(d["audience"], by=staff.tg_id)
    if d.get("button"):
        aud["button"] = d["button"]
    async with Session() as s:
        b = Broadcast(company_id=company.id, text=d["text"], media=d.get("media"), audience=aud,
                      scheduled_at=when, status=status)
        s.add(b)
        audit.log(s, company.id, staff.tg_id, "broadcast_" + status, d["text"][:50], None, aud.get("type"))
        await s.commit()
    return b


@router.callback_query(F.data == "a:bcgo")
async def bc_go(call: CallbackQuery, state: FSMContext, company: Company, staff: Staff, bot: Bot):
    await call.answer()
    d = await state.get_data()
    await state.clear()
    if "audience" not in d:
        return await call.message.answer("Черновик устарел. Начните заново: «Рассылка».")
    b = await _create(company, staff, d, "sending")
    await call.message.answer("🚀 Рассылка запущена. Сообщу, когда закончится.")
    spawn(bot, company.id, b.id)


@router.callback_query(F.data == "a:bcsch")
async def bc_schedule_ask(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(AdminFSM.bc_schedule)
    await call.message.answer("Когда отправить? Время карго, формат <code>ДД.ММ ЧЧ:ММ</code>, например 25.12 10:00:")


@router.message(AdminFSM.bc_schedule, F.text, ~F.text.startswith("/"))
async def bc_schedule(message: Message, state: FSMContext, company: Company, staff: Staff):
    local = None
    for fmt in ("%d.%m %H:%M", "%d.%m.%Y %H:%M"):
        try:
            local = datetime.strptime(message.text.strip(), fmt)
            break
        except ValueError:
            continue
    if local is None:
        return await message.answer("Не понял. Формат: 25.12 10:00")
    if local.year == 1900:
        local = local.replace(year=utcnow().year)
    when = (local - timedelta(hours=config.tz_offset_hours)).replace(tzinfo=utcnow().tzinfo)
    if when <= utcnow():
        return await message.answer("Это время уже прошло. Укажите будущее.")
    d = await state.get_data()
    await state.clear()
    if "audience" not in d:
        return await message.answer("Черновик устарел. Начните заново: «Рассылка».")
    await _create(company, staff, d, "scheduled", when)
    await message.answer(f"🕒 Рассылка запланирована на {local:%d.%m.%Y %H:%M}.")

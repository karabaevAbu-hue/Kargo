"""Админка: меню, клиенты, посылки, неопознанные, должники, сообщения (ТЗ, 5.7)."""
from __future__ import annotations

from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import func, or_, select

from cargo_platform.admin_bot.common import STATUS_NAMES, AdminFSM, IsStaff
from cargo_platform.client_bot.texts import ct
from cargo_platform.db.base import Session
from cargo_platform.db.models import (
    Batch, Charge, Client, ClientStatus, Company, CompanySettings, Message as DbMessage, Parcel,
    ParcelStatus, Payment, PaymentStatus, Staff,
)
from cargo_platform.master_bot.common import as_utc, utcnow
from cargo_platform.services import audit, barcode, notifications, notify
from cargo_platform.services.billing import fmt_money, fmt_price, fmt_weight, parse_weight
from cargo_platform.services.codes import find_client_by_code, normalize_code, suggest_codes
from cargo_platform.services.notifications import kb
from cargo_platform.services.parcels import (
    AT_PVZ, BILLABLE, client_debt, mark_arrived, refresh_all, refresh_client,
)
from cargo_platform.services.validators import clean_phone

router = Router(name="admin_panel")
router.message.filter(F.chat.type == "private", IsStaff())
router.callback_query.filter(IsStaff())

PS = ParcelStatus


def menu_markup(limited: bool = False) -> InlineKeyboardMarkup:
    if limited:
        return kb([("📤 Выгрузить данные", "a:export")])
    return kb(
        [("📦 Партии", "a:batches"), ("🧾 Приёмка", "a:recv")],
        [("🤝 Выдача", "a:issue"), ("👥 Клиенты", "a:clients")],
        [("📮 Посылки", "a:parcels"), ("❓ Неопознанные", "a:unk")],
        [("💸 Должники", "a:debt"), ("💬 Сообщения", "a:msgs")],
        [("📣 Рассылка", "a:bc"), ("📊 Отчёты", "a:rep")],
        [("📜 Журнал", "a:audit"), ("📤 Выгрузить данные", "a:export")],
    )


async def show_menu(message: Message, staff: Staff, limited: bool = False) -> None:
    role = "владелец" if staff.role.value == "owner" else "менеджер"
    note = ("\n\n⚠️ Подписка карго не оплачена: доступна только выгрузка данных. Оплатите подписку в главном боте платформы."
            if limited else "")
    await message.answer(f"🛠 <b>Админка</b> ({role}){note}", reply_markup=menu_markup(limited))


@router.callback_query(F.data == "a:menu")
async def menu_cb(call: CallbackQuery, state: FSMContext, staff: Staff, limited: bool):
    await state.clear()
    await call.answer()
    await show_menu(call.message, staff, limited)


# ================= клиенты =================

async def _client(s, company_id: int, client_id: int) -> Client | None:
    c = await s.get(Client, client_id)
    return c if c and c.company_id == company_id else None


@router.callback_query(F.data == "a:clients")
async def clients_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(AdminFSM.client_search)
    await call.message.answer("Введите код, имя или телефон клиента (или /admin для выхода):")


@router.message(AdminFSM.client_search, F.text, ~F.text.startswith("/"))
async def clients_search(message: Message, state: FSMContext, company: Company, st: CompanySettings):
    q = message.text.strip()
    digits = "".join(ch for ch in q if ch.isdigit())
    code, _ = normalize_code(q, st.code_prefix, st.code_digits)
    conds = [Client.full_name.ilike(f"%{q}%"), Client.code.ilike(f"%{q}%")]
    if code:
        conds.append(Client.code == code)
    if len(digits) >= 4:
        conds.append(Client.phone.like(f"%{digits}%"))
    async with Session() as s:
        rows = (await s.execute(select(Client).where(Client.company_id == company.id, or_(*conds))
                                .order_by(Client.id).limit(10))).scalars().all()
    if not rows:
        return await message.answer("Никого не нашёл. Попробуйте код (OSH-0123), имя или часть телефона.")
    await state.clear()
    await message.answer("Найдено:", reply_markup=kb(*[[(f"{c.code} · {c.full_name}", f"a:cl:{c.id}")] for c in rows]))


async def client_card(company: Company, client_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
    async with Session() as s:
        c = await _client(s, company.id, client_id)
        if not c:
            return None
        debt = await client_debt(s, company.id, c.id)
        counts = dict((await s.execute(select(Parcel.status, func.count()).where(
            Parcel.company_id == company.id, Parcel.client_id == c.id).group_by(Parcel.status))).all())
        pays = (await s.execute(select(Payment).where(Payment.company_id == company.id, Payment.client_id == c.id)
                                .order_by(Payment.id.desc()).limit(5))).scalars().all()
    status = {"active": "активен", "blocked": "🚫 заблокирован", "left": "заблокировал бота"}[c.status.value]
    lines = [f"👤 <b>{c.full_name}</b> · <b>{c.code}</b>", f"📱 {c.phone}",
             f"Telegram: {('@' + c.username) if c.username else (c.tg_id or 'не привязан')}", f"Статус: {status}"]
    if c.city:
        lines.append(f"Город: {c.city}")
    if c.extra.get("delivery_address"):
        lines.append(f"Адрес доставки: {c.extra['delivery_address']}")
    if c.price_per_kg_custom:
        lines.append(f"Цена за кг (индивидуальная): {fmt_price(c.price_per_kg_custom)}")
    if counts:
        lines.append("Посылки: " + ", ".join(f"{STATUS_NAMES[k.value]} — {v}" for k, v in counts.items()))
    lines.append("Долг: " + (fmt_money(debt) if debt > 0 else ("нет" if debt == 0 else f"переплата {fmt_money(-debt)}")))
    if pays:
        lines.append("\nОплаты:")
        for p in pays:
            mark = {"pending": "⏳", "confirmed": "✅", "rejected": "❌"}[p.status.value]
            lines.append(f"{mark} {p.created_at:%d.%m} {fmt_money(p.amount)} ({'нал.' if p.method == 'cash' else 'QR'})")
    markup = kb(
        [("📮 Посылки", f"a:clpar:{c.id}"), ("💬 Написать", f"a:reply:{c.id}")],
        [("🚫 Разблокировать" if c.status == ClientStatus.blocked else "🚫 Заблокировать", f"a:clb:{c.id}"),
         ("💰 Цена за кг", f"a:clp:{c.id}")],
        [("🔁 Отвязать Telegram", f"a:clu:{c.id}"), ("📞 Сменить телефон", f"a:clph:{c.id}")],
        [("⬅️ Меню", "a:menu")])
    return "\n".join(lines), markup


@router.callback_query(F.data.regexp(r"^a:cl:\d+$"))
async def client_open(call: CallbackQuery, company: Company):
    await call.answer()
    card = await client_card(company, int(call.data.split(":")[2]))
    if card:
        await call.message.answer(card[0], reply_markup=card[1])


@router.callback_query(F.data.startswith("a:clpar:"))
async def client_parcels(call: CallbackQuery, company: Company):
    await call.answer()
    cid = int(call.data.split(":")[2])
    async with Session() as s:
        c = await _client(s, company.id, cid)
        if not c:
            return
        rows = (await s.execute(select(Parcel).where(Parcel.company_id == company.id, Parcel.client_id == cid)
                                .order_by(Parcel.id.desc()).limit(20))).scalars().all()
    if not rows:
        return await call.message.answer("У клиента нет посылок.")
    await call.message.answer(f"Посылки {c.code}:", reply_markup=kb(
        *[[(f"{p.track_number or '—'} · {STATUS_NAMES[p.status.value]}", f"a:pc:{p.id}")] for p in rows]))


@router.callback_query(F.data.startswith("a:clb:"))
async def client_block(call: CallbackQuery, company: Company, staff: Staff):
    await call.answer()
    async with Session() as s:
        c = await _client(s, company.id, int(call.data.split(":")[2]))
        if not c:
            return
        old = c.status.value
        c.status = ClientStatus.active if c.status == ClientStatus.blocked else ClientStatus.blocked
        audit.log(s, company.id, staff.tg_id, "client_status", c.code, old, c.status.value)
        await s.commit()
    await call.message.answer(f"{c.code}: {'заблокирован' if c.status == ClientStatus.blocked else 'разблокирован'}.")


@router.callback_query(F.data.startswith("a:clu:"))
async def client_unlink(call: CallbackQuery, company: Company, staff: Staff):
    """Клиент потерял Telegram: менеджер по коду и документу отвязывает старый tg_id (ТЗ, раздел 9)."""
    await call.answer()
    async with Session() as s:
        c = await _client(s, company.id, int(call.data.split(":")[2]))
        if not c:
            return
        old = c.tg_id
        c.tg_id, c.username = None, None
        audit.log(s, company.id, staff.tg_id, "client_unlink_tg", c.code, old, None)
        await s.commit()
    await call.message.answer(
        f"Telegram клиента {c.code} отвязан. Пусть клиент откроет бота с нового аккаунта и поделится номером "
        f"{c.phone} кнопкой — код и посылки вернутся.")


@router.callback_query(F.data.startswith("a:clp:"))
async def client_price_ask(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(AdminFSM.edit_price)
    await state.set_data({"client_id": int(call.data.split(":")[2])})
    await call.message.answer("Индивидуальная цена за кг для клиента (сом). «0» — вернуть общую цену:")


@router.message(AdminFSM.edit_price, F.text, ~F.text.startswith("/"))
async def client_price_save(message: Message, state: FSMContext, company: Company, st: CompanySettings,
                            staff: Staff, bot: Bot):
    from cargo_platform.services.validators import parse_decimal
    value = parse_decimal(message.text, Decimal("0"), Decimal("100000"))
    if value is None:
        return await message.answer("Введите число, например 250 (или 0 — сбросить).")
    cid = (await state.get_data())["client_id"]
    await state.clear()
    async with Session() as s:
        c = await _client(s, company.id, cid)
        if not c:
            return
        old = c.price_per_kg_custom
        c.price_per_kg_custom = value or None
        audit.log(s, company.id, staff.tg_id, "client_price", c.code, old, c.price_per_kg_custom)
        changes = await refresh_all(s, st, c)
        await s.commit()
    await message.answer(f"✅ Цена для {c.code}: {fmt_price(value)} сом/кг." if value else f"✅ {c.code}: общая цена.")
    await notifications.notify_amount_changes(bot, c, changes)


@router.callback_query(F.data.startswith("a:clph:"))
async def client_phone_ask(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(AdminFSM.edit_phone)
    await state.set_data({"client_id": int(call.data.split(":")[2])})
    await call.message.answer("Новый телефон клиента (+996XXXXXXXXX):")


@router.message(AdminFSM.edit_phone, F.text, ~F.text.startswith("/"))
async def client_phone_save(message: Message, state: FSMContext, company: Company, staff: Staff):
    phone = clean_phone(message.text)
    if not phone:
        return await message.answer("Номер не похож на кыргызский. Формат: +996XXXXXXXXX")
    cid = (await state.get_data())["client_id"]
    async with Session() as s:
        taken = await s.scalar(select(Client.id).where(Client.company_id == company.id, Client.phone == phone,
                                                       Client.id != cid))
        if taken:
            return await message.answer("Этот номер уже у другого клиента.")
        c = await _client(s, company.id, cid)
        if not c:
            return
        old, c.phone = c.phone, phone
        audit.log(s, company.id, staff.tg_id, "client_phone", c.code, old, phone)
        await s.commit()
    await state.clear()
    await message.answer(f"✅ Телефон {c.code}: {phone}")


# ================= посылки =================

async def _parcel(s, company_id: int, parcel_id: int) -> Parcel | None:
    p = await s.get(Parcel, parcel_id)
    return p if p and p.company_id == company_id else None


@router.callback_query(F.data == "a:parcels")
async def parcels_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(AdminFSM.parcel_search)
    await call.message.answer("Введите трек-номер (можно часть) или пришлите фото штрихкода:")


@router.message(AdminFSM.parcel_search, F.photo | (F.text & ~F.text.startswith("/")))
async def parcels_search(message: Message, state: FSMContext, company: Company, bot: Bot):
    text = await _text_or_scan(message, bot)
    if not text:
        return await message.answer("Не смог прочитать штрихкод. Введите трек вручную:")
    q = "".join(text.split()).upper()
    async with Session() as s:
        rows = (await s.execute(select(Parcel).where(Parcel.company_id == company.id,
                                                     Parcel.track_number.like(f"%{q}%"))
                                .order_by(Parcel.id.desc()).limit(10))).scalars().all()
    if not rows:
        return await message.answer("Посылка не найдена.")
    await state.clear()
    await message.answer("Найдено:", reply_markup=kb(
        *[[(f"{p.track_number} · {STATUS_NAMES[p.status.value]}", f"a:pc:{p.id}")] for p in rows]))


async def _text_or_scan(message: Message, bot: Bot) -> str | None:
    """Текст сообщения или значение, распознанное на фото штрихкода/QR."""
    if message.photo:
        import io
        buf = io.BytesIO()
        await bot.download(message.photo[-1], destination=buf)
        return barcode.decode_image(buf.getvalue())
    return (message.text or "").strip()


async def parcel_card(company: Company, parcel_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
    async with Session() as s:
        p = await _parcel(s, company.id, parcel_id)
        if not p:
            return None
        client = await s.get(Client, p.client_id) if p.client_id else None
        batch = await s.get(Batch, p.batch_id) if p.batch_id else None
    lines = [f"📮 <code>{p.track_number or '—'}</code>", f"Статус: <b>{STATUS_NAMES[p.status.value]}</b>"]
    if p.problem_note:
        lines.append(f"Пометка: {p.problem_note}")
    lines.append(f"Клиент: {client.full_name + ' · ' + client.code if client else '— (неопознанная)'}")
    if p.raw_code and not client:
        lines.append(f"Код в файле: {p.raw_code}")
    lines.append(f"Партия: {batch.number if batch else '—'}")
    lines.append(f"Вес: {fmt_weight(p.weight)}" + (f", объёмный {fmt_weight(p.volume_weight)}" if p.volume_weight else "")
                 + f", мест: {p.places}")
    if p.description:
        lines.append(f"Описание: {p.description}")
    markup = kb([("⚖️ Вес", f"a:pcw:{p.id}"), ("🔁 Статус", f"a:pcs:{p.id}")],
                [("👤 Другому клиенту", f"a:pcm:{p.id}"), ("⚠️ Проблема", f"a:pcp:{p.id}")],
                [("⬅️ Меню", "a:menu")])
    return "\n".join(lines), markup


@router.callback_query(F.data.regexp(r"^a:pc:\d+$"))
async def parcel_open(call: CallbackQuery, company: Company):
    await call.answer()
    card = await parcel_card(company, int(call.data.split(":")[2]))
    if card:
        await call.message.answer(card[0], reply_markup=card[1])


@router.callback_query(F.data.startswith("a:pcw:"))
async def parcel_weight_ask(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(AdminFSM.edit_weight)
    await state.set_data({"parcel_id": int(call.data.split(":")[2])})
    await call.message.answer("Новый вес в кг (например 1,25):")


@router.message(AdminFSM.edit_weight, F.text, ~F.text.startswith("/"))
async def parcel_weight_save(message: Message, state: FSMContext, company: Company, st: CompanySettings,
                             staff: Staff, bot: Bot):
    w = parse_weight(message.text)
    if w is None:
        return await message.answer("Введите вес числом, например 1,25")
    pid = (await state.get_data())["parcel_id"]
    await state.clear()
    async with Session() as s:
        p = await _parcel(s, company.id, pid)
        if not p:
            return
        old, p.weight = p.weight, w
        audit.log(s, company.id, staff.tg_id, "parcel_weight", p.track_number, old, w)
        client = await s.get(Client, p.client_id) if p.client_id else None
        changes = await refresh_client(s, st, client, {p.batch_id}) if client and p.status in BILLABLE else {}
        await s.commit()
    await message.answer(f"✅ Вес {p.track_number}: {fmt_weight(w)}. Начисление пересчитано.")
    if client:
        await notifications.notify_amount_changes(bot, client, changes)


@router.callback_query(F.data.startswith("a:pcs:"))
async def parcel_status_ask(call: CallbackQuery):
    await call.answer()
    pid = call.data.split(":")[2]
    opts = [(1, "На складе в Китае"), (2, "Отправлено"), (3, "Прибыло на ПВЗ"), (5, "Выдано")]
    await call.message.answer("Новый статус (оплата ставится автоматически после подтверждения чека):",
                              reply_markup=kb(*[[(name, f"a:pcss:{pid}:{n}")] for n, name in opts]))


@router.callback_query(F.data.startswith("a:pcss:"))
async def parcel_status_set(call: CallbackQuery, company: Company, st: CompanySettings, staff: Staff, bot: Bot):
    await call.answer()
    _, _, pid, n = call.data.split(":")
    new = PS(int(n))
    async with Session() as s:
        p = await _parcel(s, company.id, int(pid))
        if not p:
            return
        old = p.status
        client = await s.get(Client, p.client_id) if p.client_id else None
        if new == PS.arrived:
            await mark_arrived(s, st, [p], utcnow())
        elif new == PS.issued:
            p.status, p.issued_at, p.issued_by = PS.issued, utcnow(), staff.id
        else:
            p.status = new
        audit.log(s, company.id, staff.tg_id, "parcel_status", p.track_number, old.name, new.name)
        await s.commit()
    await call.message.answer(f"✅ {p.track_number}: {STATUS_NAMES[new.value]}")
    if client:
        if new == PS.arrived:
            await notifications.notify_arrival(bot, company, st, client, p.batch_id)
        elif new == PS.issued:
            await notifications.notify_issued(bot, client, 1)


@router.callback_query(F.data.startswith("a:pcm:"))
async def parcel_move_ask(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(AdminFSM.move_parcel)
    await state.set_data({"parcel_id": int(call.data.split(":")[2])})
    await call.message.answer("Код клиента, которому перенести посылку (например OSH-0123):")


@router.message(AdminFSM.move_parcel, F.text, ~F.text.startswith("/"))
async def parcel_move_save(message: Message, state: FSMContext, company: Company, st: CompanySettings,
                           staff: Staff, bot: Bot):
    pid = (await state.get_data())["parcel_id"]
    async with Session() as s:
        target = await find_client_by_code(s, company.id, message.text, st.code_prefix, st.code_digits)
        if not target:
            return await message.answer("Клиент с таким кодом не найден. Введите ещё раз:")
        p = await _parcel(s, company.id, pid)
        if not p:
            return
        old = await s.get(Client, p.client_id) if p.client_id else None
        p.client_id, p.problem_note = target.id, None
        audit.log(s, company.id, staff.tg_id, "parcel_move", p.track_number, old.code if old else None, target.code)
        ch_old = await refresh_all(s, st, old) if old else {}
        if p.status in BILLABLE:
            await refresh_client(s, st, target, {p.batch_id})
        await s.commit()
    await state.clear()
    await message.answer(f"✅ {p.track_number} → {target.code} · {target.full_name}")
    if old:
        await notify.send_to_client(bot, old, ct(old.lang, "n_moved_from", track=p.track_number))
        await notifications.notify_amount_changes(bot, old, ch_old)
    await notify.send_to_client(bot, target, ct(target.lang, "n_moved_to", track=p.track_number))
    if p.status in AT_PVZ:
        await notifications.notify_arrival(bot, company, st, target, p.batch_id)


@router.callback_query(F.data.startswith("a:pcp:"))
async def parcel_problem_ask(call: CallbackQuery):
    await call.answer()
    pid = call.data.split(":")[2]
    await call.message.answer("Что случилось с посылкой?", reply_markup=kb(
        [("Не найден клиент", f"a:pcpr:{pid}:0")], [("Повреждена", f"a:pcpr:{pid}:1")],
        [("Задержана", f"a:pcpr:{pid}:2")], [("Другое (написать)", f"a:pcpr:{pid}:3")]))


PROBLEMS = ["не найден клиент", "посылка повреждена", "посылка задержана"]


async def _set_problem(message: Message, company: Company, staff: Staff, bot: Bot, pid: int, note: str) -> None:
    async with Session() as s:
        p = await _parcel(s, company.id, pid)
        if not p:
            return
        old = p.status
        p.status, p.problem_note = PS.problem, note[:200]
        audit.log(s, company.id, staff.tg_id, "parcel_problem", p.track_number, old.name, note)
        client = await s.get(Client, p.client_id) if p.client_id else None
        await s.commit()
    await message.answer(f"⚠️ {p.track_number}: проблема — {note}")
    if client:
        await notify.send_to_client(bot, client, ct(client.lang, "n_problem", track=p.track_number, note=note))


@router.callback_query(F.data.startswith("a:pcpr:"))
async def parcel_problem_set(call: CallbackQuery, state: FSMContext, company: Company, staff: Staff, bot: Bot):
    await call.answer()
    _, _, pid, n = call.data.split(":")
    if int(n) == 3:
        await state.set_state(AdminFSM.problem_note)
        await state.set_data({"parcel_id": int(pid)})
        return await call.message.answer("Опишите проблему одним сообщением:")
    await _set_problem(call.message, company, staff, bot, int(pid), PROBLEMS[int(n)])


@router.message(AdminFSM.problem_note, F.text, ~F.text.startswith("/"))
async def parcel_problem_text(message: Message, state: FSMContext, company: Company, staff: Staff, bot: Bot):
    pid = (await state.get_data())["parcel_id"]
    await state.clear()
    await _set_problem(message, company, staff, bot, pid, message.text.strip())


# ================= неопознанные =================

@router.callback_query(F.data == "a:unk")
async def unrecognized(call: CallbackQuery, company: Company):
    await call.answer()
    async with Session() as s:
        rows = (await s.execute(select(Parcel).where(Parcel.company_id == company.id, Parcel.client_id.is_(None))
                                .order_by(Parcel.id.desc()).limit(20))).scalars().all()
        total = await s.scalar(select(func.count()).select_from(Parcel).where(
            Parcel.company_id == company.id, Parcel.client_id.is_(None)))
    if not rows:
        return await call.message.answer("✅ Неопознанных посылок нет.")
    lines = [f"❓ <b>Неопознанные: {total}</b> (показаны последние {len(rows)})\n"]
    for p in rows:
        lines.append(f"• <code>{p.track_number or '—'}</code> · код «{p.raw_code or '—'}» · {fmt_weight(p.weight)}"
                     + (f" · {p.problem_note}" if p.problem_note else ""))
    await call.message.answer("\n".join(lines), reply_markup=kb(
        *[[(f"🔗 {p.track_number or p.raw_code or p.id}", f"a:unkl:{p.id}")] for p in rows[:10]]))


@router.callback_query(F.data.startswith("a:unkl:"))
async def unrecognized_link_ask(call: CallbackQuery, state: FSMContext, company: Company):
    await call.answer()
    pid = int(call.data.split(":")[2])
    async with Session() as s:
        p = await _parcel(s, company.id, pid)
        codes = list((await s.execute(select(Client.code).where(Client.company_id == company.id))).scalars().all())
    if not p:
        return
    hint = suggest_codes(p.raw_code, codes)
    await state.set_state(AdminFSM.link_parcel)
    await state.set_data({"parcel_id": pid})
    await call.message.answer(
        f"Посылка <code>{p.track_number or '—'}</code> (код в файле «{p.raw_code or '—'}»).\n"
        + (f"Похожие коды: {', '.join(hint)}\n" if hint else "") + "Введите код клиента для привязки:")


@router.message(AdminFSM.link_parcel, F.text, ~F.text.startswith("/"))
async def unrecognized_link_save(message: Message, state: FSMContext, company: Company, st: CompanySettings,
                                 staff: Staff, bot: Bot):
    pid = (await state.get_data())["parcel_id"]
    async with Session() as s:
        client = await find_client_by_code(s, company.id, message.text, st.code_prefix, st.code_digits)
        if not client:
            return await message.answer("Клиент не найден. Введите код ещё раз:")
        p = await _parcel(s, company.id, pid)
        if not p:
            return
        p.client_id, p.problem_note = client.id, None
        audit.log(s, company.id, staff.tg_id, "parcel_link", p.track_number, None, client.code)
        if p.status in BILLABLE:
            await refresh_client(s, st, client, {p.batch_id})
        await s.commit()
    await state.clear()
    await message.answer(f"✅ {p.track_number or p.raw_code} → {client.code} · {client.full_name}")
    if p.status in AT_PVZ:
        await notifications.notify_arrival(bot, company, st, client, p.batch_id)
    elif p.status == PS.china_warehouse and st.use_china_statuses and p.track_number:
        await notifications.notify_china(bot, client, [p.track_number])


# ================= должники =================

async def debtors(company_id: int) -> list[tuple[Client, int, int | None]]:
    """[(клиент, долг, дней на ПВЗ)] по убыванию долга."""
    async with Session() as s:
        charged = dict((await s.execute(select(Charge.client_id, func.sum(Charge.amount + Charge.storage_amount - Charge.discount))
                                        .where(Charge.company_id == company_id).group_by(Charge.client_id))).all())
        paid = dict((await s.execute(select(Payment.client_id, func.sum(Payment.amount)).where(
            Payment.company_id == company_id, Payment.status == PaymentStatus.confirmed)
            .group_by(Payment.client_id))).all())
        debts = {cid: int(v) - int(paid.get(cid) or 0) for cid, v in charged.items()}
        debts = {cid: d for cid, d in debts.items() if d > 0}
        if not debts:
            return []
        clients = {c.id: c for c in (await s.execute(select(Client).where(
            Client.company_id == company_id, Client.id.in_(debts)))).scalars().all()}
        first = dict((await s.execute(select(Parcel.client_id, func.min(Parcel.arrived_at)).where(
            Parcel.company_id == company_id, Parcel.status.in_(AT_PVZ), Parcel.client_id.in_(debts))
            .group_by(Parcel.client_id))).all())
    now = utcnow()
    out = []
    for cid, d in sorted(debts.items(), key=lambda x: -x[1]):
        f = first.get(cid)
        out.append((clients[cid], d, (now - as_utc(f)).days if f else None))
    return out


@router.callback_query(F.data == "a:debt")
async def debtors_view(call: CallbackQuery, company: Company):
    await call.answer()
    rows = await debtors(company.id)
    if not rows:
        return await call.message.answer("✅ Должников нет.")
    lines = [f"💸 <b>Должники: {len(rows)}</b>, всего {fmt_money(sum(d for _, d, _ in rows))}\n"]
    for c, d, days in rows[:30]:
        lines.append(f"• {c.code} {c.full_name} {c.phone} — <b>{fmt_money(d)}</b>"
                     + (f", на ПВЗ {days} дн." if days is not None else ""))
    await call.message.answer("\n".join(lines))


# ================= сообщения =================

@router.callback_query(F.data == "a:msgs")
async def messages_view(call: CallbackQuery, company: Company):
    await call.answer()
    async with Session() as s:
        last_id = (select(func.max(DbMessage.id)).where(DbMessage.company_id == company.id)
                   .group_by(DbMessage.client_id)).scalar_subquery()
        rows = (await s.execute(select(DbMessage, Client).join(Client, Client.id == DbMessage.client_id).where(
            DbMessage.id.in_(last_id), DbMessage.direction == "in").order_by(DbMessage.id.desc()).limit(10))).all()
    if not rows:
        return await call.message.answer("✅ Неотвеченных сообщений нет.")
    lines = ["💬 <b>Вопросы клиентов без ответа</b>\n"]
    for m, c in rows:
        lines.append(f"• {c.code} {c.full_name}: {m.text[:150]}")
    await call.message.answer("\n".join(lines), reply_markup=kb(
        *[[(f"↩️ {c.code}", f"a:reply:{c.id}")] for _, c in rows]))


@router.callback_query(F.data.startswith("a:reply:"))
async def reply_ask(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(AdminFSM.reply)
    await state.set_data({"client_id": int(call.data.split(":")[2])})
    await call.message.answer("Напишите ответ клиенту одним сообщением:")


@router.message(AdminFSM.reply, F.text, ~F.text.startswith("/"))
async def reply_send(message: Message, state: FSMContext, company: Company, staff: Staff, bot: Bot):
    cid = (await state.get_data())["client_id"]
    await state.clear()
    text = message.text.strip()[:2000]
    async with Session() as s:
        c = await _client(s, company.id, cid)
        if not c:
            return
        s.add(DbMessage(company_id=company.id, client_id=c.id, direction="out", text=text, staff_id=staff.id))
        await s.commit()
    ok = await notify.send_to_client(bot, c, ct(c.lang, "manager_reply", text=text))
    await message.answer("✅ Ответ отправлен." if ok else "Ответ сохранён, но клиент недоступен (не привязан Telegram или заблокировал бота).")

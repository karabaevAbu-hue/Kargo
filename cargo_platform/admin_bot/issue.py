"""Выдача на ПВЗ (ТЗ, 5.6)."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from cargo_platform.admin_bot.common import STATUS_NAMES, AdminFSM, IsStaff
from cargo_platform.admin_bot.panel import _text_or_scan
from cargo_platform.client_bot.texts import ct
from cargo_platform.db.base import Session
from cargo_platform.db.models import Client, Company, CompanySettings, Parcel, ParcelStatus, Payment, PaymentStatus, Staff
from cargo_platform.master_bot.common import utcnow
from cargo_platform.services import audit, notifications, notify
from cargo_platform.services.billing import fmt_money, fmt_weight
from cargo_platform.services.codes import find_client_by_code
from cargo_platform.services.notifications import kb
from cargo_platform.services.parcels import AT_PVZ, client_debt, settle_if_paid

router = Router(name="admin_issue")
router.message.filter(F.chat.type == "private", IsStaff())
router.callback_query.filter(IsStaff())

PS = ParcelStatus


@router.callback_query(F.data == "a:issue")
async def issue_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(AdminFSM.issue_code)
    await call.message.answer("🤝 <b>Выдача</b>. Введите код клиента или пришлите фото его QR-кода:")


@router.message(AdminFSM.issue_code, F.photo | (F.text & ~F.text.startswith("/")))
async def issue_code(message: Message, state: FSMContext, company: Company, st: CompanySettings, bot: Bot):
    text = await _text_or_scan(message, bot)
    if not text:
        return await message.answer("Не смог прочитать QR. Введите код вручную:")
    async with Session() as s:
        client = await find_client_by_code(s, company.id, text, st.code_prefix, st.code_digits)
    if not client:
        return await message.answer("Клиент не найден. Введите код ещё раз:")
    await state.set_data({"client_id": client.id, "sel": []})
    await _show(message, company, client.id, [])


async def _show(message: Message, company: Company, client_id: int, sel: list[int]) -> None:
    async with Session() as s:
        client = await s.get(Client, client_id)
        parcels = (await s.execute(select(Parcel).where(
            Parcel.company_id == company.id, Parcel.client_id == client_id, Parcel.status.in_(AT_PVZ))
            .order_by(Parcel.id))).scalars().all()
        debt = await client_debt(s, company.id, client_id)
        pending = await s.scalar(select(Payment.id).where(
            Payment.company_id == company.id, Payment.client_id == client_id, Payment.status == PaymentStatus.pending))
    lines = [f"👤 <b>{client.full_name}</b> · {client.code} · {client.phone}", ""]
    if not parcels:
        lines.append("Посылок «на ПВЗ» нет.")
    for p in parcels:
        mark = "☑️" if p.id in sel else "▫️"
        lines.append(f"{mark} <code>{p.track_number or '—'}</code> · {fmt_weight(p.weight)} · {STATUS_NAMES[p.status.value]}")
    lines.append("")
    lines.append("💳 " + (f"К оплате: <b>{fmt_money(debt)}</b>" if debt > 0 else
                          "Оплачено ✅" + (f" (переплата {fmt_money(-debt)})" if debt < 0 else "")))
    if pending:
        lines.append("⏳ Есть чек на проверке — подтвердите его в сообщении с чеком.")
    rows = []
    for p in parcels[:12]:
        rows.append([(("☑️ " if p.id in sel else "▫️ ") + (p.track_number or str(p.id))[-12:], f"a:ist:{client_id}:{p.id}")])
    if parcels:
        if debt > 0:
            rows.append([("💵 Оплатил наличными", f"a:isc:{client_id}"), ("⏳ Ждать перевод", f"a:isw:{client_id}")])
            rows.append([("⚠️ Выдать в долг (всё)", f"a:isd:{client_id}")])
        else:
            rows.append([("✅ Выдать всё", f"a:isa:{client_id}")])
            if sel:
                rows.append([(f"✅ Выдать выбранные ({len(sel)})", f"a:isg:{client_id}")])
    rows.append([("⬅️ Меню", "a:menu")])
    await message.answer("\n".join(lines), reply_markup=kb(*rows))


def _ids(d: dict) -> list[int]:
    return list(d.get("sel", []))


@router.callback_query(F.data.startswith("a:ist:"))
async def toggle(call: CallbackQuery, state: FSMContext, company: Company):
    await call.answer()
    _, _, cid, pid = call.data.split(":")
    d = await state.get_data()
    sel = _ids(d)
    sel.remove(int(pid)) if int(pid) in sel else sel.append(int(pid))
    await state.update_data(client_id=int(cid), sel=sel)
    await _show(call.message, company, int(cid), sel)


async def _issue(call: CallbackQuery, company: Company, staff: Staff, bot: Bot, client_id: int,
                 only: list[int] | None, on_debt: bool = False) -> None:
    async with Session() as s:
        client = await s.get(Client, client_id)
        if not client or client.company_id != company.id:
            return
        q = select(Parcel).where(Parcel.company_id == company.id, Parcel.client_id == client_id,
                                 Parcel.status.in_(AT_PVZ))
        if only is not None:
            q = q.where(Parcel.id.in_(only))
        parcels = (await s.execute(q)).scalars().all()
        if not parcels:
            return await call.message.answer("Нечего выдавать: выберите посылки.")
        debt = await client_debt(s, company.id, client_id)
        if debt > 0 and not on_debt:
            return await call.message.answer("У клиента долг. Сначала оплата или «Выдать в долг».")
        now = utcnow()
        for p in parcels:
            p.status, p.issued_at, p.issued_by = PS.issued, now, staff.id
        audit.log(s, company.id, staff.tg_id, "issue_on_debt" if debt > 0 else "issue", client.code,
                  None, f"{len(parcels)} посылок")
        await s.commit()
    await call.message.answer(f"✅ Выдано посылок: {len(parcels)} · {client.code} {client.full_name}")
    await notifications.notify_issued(bot, client, len(parcels))


@router.callback_query(F.data.startswith("a:isa:"))
async def issue_all(call: CallbackQuery, state: FSMContext, company: Company, staff: Staff, bot: Bot):
    await call.answer()
    await _issue(call, company, staff, bot, int(call.data.split(":")[2]), None)
    await state.clear()


@router.callback_query(F.data.startswith("a:isg:"))
async def issue_selected(call: CallbackQuery, state: FSMContext, company: Company, staff: Staff, bot: Bot):
    await call.answer()
    sel = _ids(await state.get_data())
    await _issue(call, company, staff, bot, int(call.data.split(":")[2]), sel)
    await state.clear()


@router.callback_query(F.data.startswith("a:isd:"))
async def issue_on_debt(call: CallbackQuery, state: FSMContext, company: Company, staff: Staff, bot: Bot):
    await call.answer()
    await _issue(call, company, staff, bot, int(call.data.split(":")[2]), None, on_debt=True)
    await state.clear()


@router.callback_query(F.data.startswith("a:isc:"))
async def paid_cash(call: CallbackQuery, state: FSMContext, company: Company, staff: Staff, bot: Bot):
    """Оплата наличными на ПВЗ: фиксируем платёж на всю сумму долга."""
    await call.answer()
    cid = int(call.data.split(":")[2])
    async with Session() as s:
        client = await s.get(Client, cid)
        if not client or client.company_id != company.id:
            return
        debt = await client_debt(s, company.id, cid)
        if debt <= 0:
            return await call.message.answer("Долга уже нет.")
        s.add(Payment(company_id=company.id, client_id=cid, amount=debt, method="cash",
                      status=PaymentStatus.confirmed, confirmed_by=staff.id))
        await s.flush()
        await settle_if_paid(s, company.id, cid)
        audit.log(s, company.id, staff.tg_id, "payment_cash", client.code, None, debt)
        await s.commit()
    from cargo_platform.services.billing import fmt_money as fm
    await notify.send_to_client(bot, client, ct(client.lang, "pay_cash", sum=fm(debt)))
    await _show(call.message, company, cid, _ids(await state.get_data()))


@router.callback_query(F.data.startswith("a:isw:"))
async def wait_transfer(call: CallbackQuery, company: Company, bot: Bot):
    await call.answer()
    async with Session() as s:
        client = await s.get(Client, int(call.data.split(":")[2]))
    if not client or client.company_id != company.id:
        return
    markup = kb([(ct(client.lang, "btn_pay"), "pay:start")])
    ok = await notify.send_to_client(bot, client, ct(client.lang, "pay_wait"), reply_markup=markup)
    await call.message.answer("Клиенту отправлены реквизиты и просьба прислать чек." if ok
                              else "Клиент недоступен в боте. Попросите оплатить наличными или по реквизитам устно.")

"""Подтверждение оплаты менеджером (ТЗ, 6.2): подтвердить, отклонить с причиной, частичная оплата."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import update

from cargo_platform.admin_bot.common import AdminFSM, IsStaff
from cargo_platform.client_bot.texts import ct
from cargo_platform.db.base import Session
from cargo_platform.db.models import Client, Company, Payment, PaymentStatus, Staff
from cargo_platform.services import audit, notify
from cargo_platform.services.billing import fmt_money
from cargo_platform.services.notifications import kb
from cargo_platform.services.parcels import client_debt, settle_if_paid

router = Router(name="admin_payments")
router.message.filter(F.chat.type == "private", IsStaff())
router.callback_query.filter(IsStaff())

REASONS = ["не та сумма", "чек не найден", "чек сомнительный"]


async def _finalize(s, payment: Payment, status: PaymentStatus, staff: Staff, amount: int | None = None,
                    reason: str | None = None) -> bool:
    """Атомарно: pending → итоговый статус. False — другой менеджер уже обработал чек."""
    values = {"status": status, "confirmed_by": staff.id}
    if amount is not None:
        values["amount"] = amount
    if reason:
        values["reject_reason"] = reason
    res = await s.execute(update(Payment).where(
        Payment.id == payment.id, Payment.company_id == payment.company_id,
        Payment.status == PaymentStatus.pending).values(**values))
    return res.rowcount == 1


async def _load(s, company_id: int, payment_id: int):
    p = await s.get(Payment, payment_id)
    return p if p and p.company_id == company_id else None


async def _accepted(bot: Bot, client: Client, paid: int, debt_after: int) -> None:
    rest = ct(client.lang, "pay_rest", sum=fmt_money(debt_after)) if debt_after > 0 else ""
    if debt_after < 0:
        rest = ct(client.lang, "credit_note", sum=fmt_money(-debt_after))
    await notify.send_to_client(bot, client, ct(client.lang, "pay_accepted", sum=fmt_money(paid), rest=rest))


async def _mark_done(call: CallbackQuery, text: str) -> None:
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer(text)


@router.callback_query(F.data.startswith("pm:ok:"))
async def confirm(call: CallbackQuery, company: Company, staff: Staff, bot: Bot):
    async with Session() as s:
        p = await _load(s, company.id, int(call.data.split(":")[2]))
        if not p:
            return await call.answer()
        if not await _finalize(s, p, PaymentStatus.confirmed, staff):
            await s.rollback()
            return await call.answer("Этот чек уже обработан.", show_alert=True)
        await s.flush()
        client = await s.get(Client, p.client_id)
        await settle_if_paid(s, company.id, p.client_id)
        debt = await client_debt(s, company.id, p.client_id)
        audit.log(s, company.id, staff.tg_id, "payment_confirm", client.code, None, p.amount)
        await s.commit()
    await call.answer("Подтверждено")
    await _mark_done(call, f"✅ Оплата #{p.id} подтверждена: {client.code}, {fmt_money(p.amount)}.")
    await _accepted(bot, client, p.amount, debt)


@router.callback_query(F.data.startswith("pm:no:"))
async def reject_ask(call: CallbackQuery):
    await call.answer()
    pid = call.data.split(":")[2]
    await call.message.answer("Причина отказа:", reply_markup=kb(
        [(REASONS[0].capitalize(), f"pm:nr:{pid}:0")], [(REASONS[1].capitalize(), f"pm:nr:{pid}:1")],
        [("Другая причина (написать)", f"pm:nr:{pid}:3")]))


async def _reject(message: Message, company: Company, staff: Staff, bot: Bot, pid: int, reason: str) -> None:
    async with Session() as s:
        p = await _load(s, company.id, pid)
        if not p:
            return
        if not await _finalize(s, p, PaymentStatus.rejected, staff, reason=reason[:200]):
            await s.rollback()
            return await message.answer("Этот чек уже обработан.")
        client = await s.get(Client, p.client_id)
        audit.log(s, company.id, staff.tg_id, "payment_reject", client.code, None, reason)
        await s.commit()
    await message.answer(f"❌ Оплата #{p.id} отклонена: {reason}.")
    await notify.send_to_client(bot, client, ct(client.lang, "pay_rejected", reason=reason))


@router.callback_query(F.data.startswith("pm:nr:"))
async def reject_reason(call: CallbackQuery, state: FSMContext, company: Company, staff: Staff, bot: Bot):
    await call.answer()
    _, _, pid, n = call.data.split(":")
    if int(n) == 3:
        await state.set_state(AdminFSM.reject_reason)
        await state.set_data({"payment_id": int(pid)})
        return await call.message.answer("Напишите причину одним сообщением (клиент её увидит):")
    await _reject(call.message, company, staff, bot, int(pid), REASONS[int(n)])


@router.message(AdminFSM.reject_reason, F.text, ~F.text.startswith("/"))
async def reject_text(message: Message, state: FSMContext, company: Company, staff: Staff, bot: Bot):
    pid = (await state.get_data())["payment_id"]
    await state.clear()
    await _reject(message, company, staff, bot, pid, message.text.strip())


@router.callback_query(F.data.startswith("pm:part:"))
async def partial_ask(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(AdminFSM.partial_amount)
    await state.set_data({"payment_id": int(call.data.split(":")[2])})
    await call.message.answer("Сколько сом фактически получено? Остаток останется в долге клиента:")


@router.message(AdminFSM.partial_amount, F.text, ~F.text.startswith("/"))
async def partial_save(message: Message, state: FSMContext, company: Company, staff: Staff, bot: Bot):
    raw = message.text.replace(" ", "").replace(",", ".")
    try:
        amount = int(round(float(raw)))
    except ValueError:
        amount = 0
    if amount <= 0 or amount > 10_000_000:
        return await message.answer("Введите сумму числом, например 500")
    pid = (await state.get_data())["payment_id"]
    await state.clear()
    async with Session() as s:
        p = await _load(s, company.id, pid)
        if not p:
            return
        if not await _finalize(s, p, PaymentStatus.confirmed, staff, amount=amount):
            await s.rollback()
            return await message.answer("Этот чек уже обработан.")
        await s.flush()
        client = await s.get(Client, p.client_id)
        await settle_if_paid(s, company.id, p.client_id)
        debt = await client_debt(s, company.id, p.client_id)
        audit.log(s, company.id, staff.tg_id, "payment_partial", client.code, None, amount)
        await s.commit()
    await message.answer(f"✅ Принято {fmt_money(amount)} от {client.code}. "
                         + (f"Остаток долга: {fmt_money(debt)}." if debt > 0 else "Долг закрыт."))
    await _accepted(bot, client, amount, debt)

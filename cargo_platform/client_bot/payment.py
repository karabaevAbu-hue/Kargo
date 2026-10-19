"""Оплата клиентом (ТЗ, 6.2): сумма и QR бизнес-счёта → чек → менеджер подтверждает."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from cargo_platform.client_bot.common import PayFSM, cancel_kb, menu_kb, payment_qr_file
from cargo_platform.client_bot.texts import ct, variants
from cargo_platform.db.base import Session
from cargo_platform.db.models import Client, Company, CompanySettings, Payment, PaymentStatus
from cargo_platform.services import notifications, notify
from cargo_platform.services.billing import fmt_money
from cargo_platform.services.parcels import client_debt

router = Router(name="client_payment")
router.message.filter(F.chat.type == "private")


@router.callback_query(F.data == "pay:start")
async def pay_start(call: CallbackQuery, state: FSMContext, client: Client | None, st: CompanySettings,
                    lang: str, bot: Bot):
    await call.answer()
    if client is None:
        return
    async with Session() as s:
        debt = await client_debt(s, client.company_id, client.id)
    if debt <= 0:
        return await call.message.answer(ct(lang, "pay_nothing"))
    requisites = st.payment_text or (ct(lang, "pay_no_requisites") if not payment_qr_file(st) else "")
    text = ct(lang, "pay_instructions", sum=fmt_money(debt), requisites=requisites)
    await state.set_state(PayFSM.receipt)
    qr = payment_qr_file(st)
    if qr:
        await notify.send(bot, call.message.chat.id, text, photo=qr, reply_markup=cancel_kb(lang))
    else:
        await call.message.answer(text, reply_markup=cancel_kb(lang))


@router.message(PayFSM.receipt, F.photo | F.document)
async def receipt(message: Message, state: FSMContext, client: Client, company: Company, lang: str, bot: Bot):
    if message.photo:
        file_id, unique_id = message.photo[-1].file_id, message.photo[-1].file_unique_id
    else:
        doc = message.document
        if not (doc.mime_type or "").startswith("image/"):
            return await message.answer(ct(lang, "pay_send_receipt"))
        file_id, unique_id = doc.file_id, doc.file_unique_id
    async with Session() as s:
        debt = await client_debt(s, company.id, client.id)
        if debt <= 0:
            await state.clear()
            return await message.answer(ct(lang, "pay_nothing"), reply_markup=menu_kb(lang))
        same = (await s.execute(select(Payment.id, Payment.client_id, Payment.status).where(
            Payment.company_id == company.id, Payment.receipt_unique_id == unique_id))).all()
        payment = Payment(company_id=company.id, client_id=client.id, amount=debt, method="qr",
                          receipt_file_id=file_id, receipt_unique_id=unique_id, status=PaymentStatus.pending)
        s.add(payment)
        await s.commit()
    await state.clear()
    await message.answer(ct(lang, "pay_receipt_ok"), reply_markup=menu_kb(lang))
    warn = ""
    if same:
        warn = "\n\n⚠️ <b>Такая же картинка чека уже присылалась</b> (повторный или чужой чек). Проверьте внимательно."
    caption = (f"💳 <b>Новый чек</b> #{payment.id}\nКлиент: {client.full_name} ({client.code})\n"
               f"Сумма долга: <b>{fmt_money(debt)}</b>{warn}")
    markup = notifications.kb([("✅ Подтвердить", f"pm:ok:{payment.id}"), ("❌ Отклонить", f"pm:no:{payment.id}")],
                              [("➗ Частичная оплата", f"pm:part:{payment.id}")])
    await notifications.notify_staff(bot, company.id, caption, photo=file_id, reply_markup=markup)


@router.message(PayFSM.receipt, F.text)
async def receipt_text(message: Message, state: FSMContext, lang: str):
    if message.text in variants("cancel"):
        await state.clear()
        return await message.answer(ct(lang, "cancelled"), reply_markup=menu_kb(lang))
    await message.answer(ct(lang, "pay_send_receipt"))

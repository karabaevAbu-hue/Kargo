"""Уведомления клиентам и менеджерам (ТЗ, раздел 7.1). Все идут через очередь notify.send."""
from __future__ import annotations

from datetime import datetime

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from cargo_platform.client_bot.texts import ct
from cargo_platform.db.base import Session
from cargo_platform.db.models import Charge, Client, Company, CompanySettings, Parcel, Staff
from cargo_platform.services import notify
from cargo_platform.services.billing import fmt_money, fmt_price, fmt_weight
from cargo_platform.services.parcels import AT_PVZ, client_debt, total_of


def kb(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) if not d.startswith("http")
         else InlineKeyboardButton(text=t, url=d) for t, d in row] for row in rows])


async def staff_tg_ids(company_id: int) -> list[int]:
    async with Session() as s:
        return list((await s.execute(select(Staff.tg_id).where(
            Staff.company_id == company_id, Staff.active))).scalars().all())


async def notify_staff(bot: Bot, company_id: int, text: str | None = None, **kw) -> None:
    for tg_id in await staff_tg_ids(company_id):
        await notify.send(bot, tg_id, text, **kw)


async def notify_china(bot: Bot, client: Client, tracks: list[str]) -> bool:
    shown = tracks[:10]
    items = "\n".join(f"• <code>{t}</code>" for t in shown)
    if len(tracks) > len(shown):
        items += f"\n… и ещё {len(tracks) - len(shown)}"
    return await notify.send_to_client(bot, client, ct(client.lang, "n_china", items=items))


async def notify_shipped(bot: Bot, client: Client, n: int, eta: datetime | None) -> bool:
    eta_text = ct(client.lang, "n_eta", date=f"{eta:%d.%m}") if eta else ""
    return await notify.send_to_client(bot, client, ct(client.lang, "n_shipped", eta=eta_text, n=n))


async def notify_arrival(bot: Bot, company: Company, st: CompanySettings, client: Client,
                         batch_id: int | None) -> bool:
    """Одно сообщение на клиента и партию: сколько посылок, вес, тариф, сумма (ТЗ, 5.5)."""
    async with Session() as s:
        charge = await s.scalar(select(Charge).where(
            Charge.company_id == company.id, Charge.client_id == client.id, Charge.batch_id == batch_id))
        n = len((await s.execute(select(Parcel.id).where(
            Parcel.company_id == company.id, Parcel.client_id == client.id, Parcel.batch_id == batch_id,
            Parcel.status.in_(AT_PVZ)))).all())
        debt = await client_debt(s, company.id, client.id)
    if charge is None or n == 0:
        return False
    from cargo_platform.services.parcels import price_for
    text = ct(client.lang, "n_arrived", code=client.code, n=n, weight=fmt_weight(charge.weight_total),
              price=f"{fmt_price(price_for(st, client))}", sum=fmt_money(total_of(charge)),
              address=company.pvz_address, schedule=company.schedule)
    if debt > total_of(charge):
        text += ct(client.lang, "n_total_debt", sum=fmt_money(debt))
    markup = kb([(ct(client.lang, "btn_pay"), "pay:start"), (ct(client.lang, "m_parcels"), "par:all:0")])
    return await notify.send_to_client(bot, client, text, reply_markup=markup)


async def notify_issued(bot: Bot, client: Client, n: int) -> bool:
    return await notify.send_to_client(bot, client, ct(client.lang, "n_issued", n=n))


async def notify_amount_updated(bot: Bot, client: Client, new_total: int) -> bool:
    return await notify.send_to_client(bot, client, ct(client.lang, "n_amount_updated", sum=fmt_money(new_total)))


async def notify_amount_changes(bot: Bot, client: Client, changes: dict) -> int:
    """Клиенту «Сумма уточнена», если начисление, которое он уже видел, изменилось."""
    sent = 0
    for charge, old in changes.values():
        if charge is not None and old is not None and total_of(charge) != old:
            if await notify_amount_updated(bot, client, total_of(charge)):
                sent += 1
    return sent

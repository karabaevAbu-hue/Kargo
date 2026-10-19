"""Ручная рассылка (ТЗ, 7.2): аудитория, отправка через очередь, продолжение после перезапуска."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select

from cargo_platform.db.base import Session
from cargo_platform.db.models import Broadcast, Client, ClientStatus, Parcel
from cargo_platform.master_bot.common import utcnow
from cargo_platform.services import notify
from cargo_platform.services.reports import local_now

log = logging.getLogger(__name__)

AUDIENCES = {"all": "все клиенты", "batch": "клиенты партии", "debtors": "должники", "city": "клиенты города"}


async def recipients(company_id: int, audience: dict) -> list[Client]:
    """Клиенты, которым реально можно написать: активные и с привязанным Telegram."""
    kind = audience.get("type", "all")
    async with Session() as s:
        q = select(Client).where(Client.company_id == company_id, Client.status == ClientStatus.active,
                                 Client.tg_id.is_not(None))
        if kind == "batch":
            ids = select(Parcel.client_id).where(Parcel.company_id == company_id,
                                                 Parcel.batch_id == int(audience["value"]), Parcel.client_id.is_not(None))
            q = q.where(Client.id.in_(ids))
        elif kind == "city":
            q = q.where(Client.city == audience["value"])
        elif kind == "debtors":
            from cargo_platform.admin_bot.panel import debtors
            ids = [c.id for c, _, _ in await debtors(company_id)]
            q = q.where(Client.id.in_(ids or [0]))
        return list((await s.execute(q.order_by(Client.id))).scalars().all())


def button_markup(audience: dict) -> InlineKeyboardMarkup | None:
    b = audience.get("button")
    if not b:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=b["text"], url=b["url"])]])


def media_args(media: str | None) -> dict:
    if not media:
        return {}
    kind, file_id = media.split(":", 1)
    return {kind: file_id}


async def run(bot: Bot, company_id: int, broadcast_id: int) -> None:
    """Отправляет рассылку. Список получателей фиксируется при первом запуске, sent+failed — сколько уже
    обработано: после перезапуска сервера рассылка продолжается с того же места (ТЗ, раздел 9)."""
    async with Session() as s:
        b = await s.get(Broadcast, broadcast_id)
        if not b or b.company_id != company_id or b.status == "done":
            return
        audience = dict(b.audience)
        base_sent, base_failed = b.sent, b.failed
        text, media = b.text, b.media
    if "ids" not in audience:
        audience["ids"] = [c.id for c in await recipients(company_id, audience)]
    async with Session() as s:
        b = await s.get(Broadcast, broadcast_id)
        b.status, b.audience = "sending", audience
        await s.commit()
        ids = audience["ids"][base_sent + base_failed:]
        clients = {c.id: c for c in (await s.execute(select(Client).where(
            Client.company_id == company_id, Client.id.in_(ids)))).scalars().all()} if ids else {}
    markup, args = button_markup(audience), media_args(media)
    sent = failed = 0
    for i, cid in enumerate(ids, start=1):
        client = clients.get(cid)
        ok = bool(client) and await notify.send_to_client(bot, client, text, reply_markup=markup, **args)
        sent += ok
        failed += not ok
        if i % 20 == 0:
            await _save(broadcast_id, base_sent + sent, base_failed + failed)
    await _save(broadcast_id, base_sent + sent, base_failed + failed, status="done")
    if audience.get("by"):
        await notify.send(bot, audience["by"],
                          f"📣 Рассылка завершена: доставлено {base_sent + sent}, не доставлено {base_failed + failed} "
                          "(заблокировавшие бота помечены как неактивные).")


async def _save(broadcast_id: int, sent: int, failed: int, status: str | None = None) -> None:
    async with Session() as s:
        b = await s.get(Broadcast, broadcast_id)
        b.sent, b.failed = sent, failed
        if status:
            b.status = status
        await s.commit()


async def month_count(company_id: int) -> int:
    """Сколько массовых рассылок уже запущено или запланировано в этом месяце (лимит тарифа «Старт»)."""
    start = local_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start_utc = start.astimezone(utcnow().tzinfo)
    async with Session() as s:
        return await s.scalar(select(func.count()).select_from(Broadcast).where(
            Broadcast.company_id == company_id, Broadcast.status.in_(("scheduled", "sending", "done")),
            Broadcast.created_at >= start_utc)) or 0

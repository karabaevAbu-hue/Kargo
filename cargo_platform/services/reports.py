"""Отчёты, отчёт дня и выгрузка в Excel (ТЗ, 5.7, 7.1 и раздел 9)."""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from cargo_platform.config import config
from cargo_platform.db.base import Session
from cargo_platform.db.models import (
    Batch, Charge, Client, CompanySettings, Parcel, Payment, PaymentStatus,
)
from cargo_platform.master_bot.common import utcnow
from cargo_platform.services.billing import fmt_money, fmt_weight
from cargo_platform.services.parcels import AT_PVZ


def local_tz() -> timezone:
    return timezone(timedelta(hours=config.tz_offset_hours))


def local_now() -> datetime:
    return utcnow().astimezone(local_tz())


def period_start(days: int) -> datetime:
    """Начало периода в UTC: days=1 — сегодняшние сутки по времени карго, 7 — последние 7 дней и т.д."""
    start_local = (local_now() - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(timezone.utc)


@dataclass
class Report:
    since: datetime
    received: int
    received_kg: Decimal
    issued: int
    charged: int
    paid: int
    debt_total: int
    debtors: int
    new_clients: int
    stale: int           # «залежавшиеся»: на ПВЗ дольше бесплатного срока
    unmatched: int


async def build_report(company_id: int, days: int) -> Report:
    from cargo_platform.admin_bot.panel import debtors
    since = period_start(days)
    async with Session() as s:
        st = await s.get(CompanySettings, company_id)
        rec = (await s.execute(select(func.count(), func.coalesce(func.sum(Parcel.weight), 0)).where(
            Parcel.company_id == company_id, Parcel.arrived_at >= since))).one()
        issued = await s.scalar(select(func.count()).select_from(Parcel).where(
            Parcel.company_id == company_id, Parcel.issued_at >= since))
        charged = await s.scalar(select(func.coalesce(func.sum(Charge.amount + Charge.storage_amount - Charge.discount), 0))
                                 .where(Charge.company_id == company_id, Charge.created_at >= since))
        paid = await s.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.company_id == company_id, Payment.status == PaymentStatus.confirmed, Payment.created_at >= since))
        new_clients = await s.scalar(select(func.count()).select_from(Client).where(
            Client.company_id == company_id, Client.created_at >= since))
        unmatched = await s.scalar(select(func.count()).select_from(Parcel).where(
            Parcel.company_id == company_id, Parcel.client_id.is_(None)))
        stale = 0
        if st and st.free_storage_days is not None:
            limit = utcnow() - timedelta(days=st.free_storage_days)
            stale = await s.scalar(select(func.count()).select_from(Parcel).where(
                Parcel.company_id == company_id, Parcel.status.in_(AT_PVZ), Parcel.arrived_at < limit))
    debts = await debtors(company_id)
    return Report(since=since, received=rec[0], received_kg=Decimal(rec[1]), issued=issued or 0,
                  charged=int(charged or 0), paid=int(paid or 0), debt_total=sum(d for _, d, _ in debts),
                  debtors=len(debts), new_clients=new_clients or 0, stale=stale or 0, unmatched=unmatched or 0)


def report_text(r: Report, title: str) -> str:
    return (f"📊 <b>{title}</b>\n\n"
            f"📥 Принято на ПВЗ: {r.received} посылок, {fmt_weight(r.received_kg)}\n"
            f"🤝 Выдано: {r.issued}\n"
            f"🧾 Начислено: {fmt_money(r.charged)}\n"
            f"💰 Оплачено: {fmt_money(r.paid)}\n"
            f"💸 Долги сейчас: {fmt_money(r.debt_total)} ({r.debtors} клиентов)\n"
            f"👥 Новых клиентов: {r.new_clients}\n"
            f"📦 Залежавшиеся на ПВЗ: {r.stale}\n"
            f"❓ Неопознанные посылки: {r.unmatched}")


def make_xlsx(sheets: dict[str, list[list]]) -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title[:31])
        for row in rows:
            ws.append([None if v is None else (float(v) if isinstance(v, Decimal) else
                       (v.replace(tzinfo=None) if isinstance(v, datetime) else v)) for v in row])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def export_all(company_id: int) -> bytes:
    """Все клиенты, посылки, начисления и платежи компании — для выгрузки и ухода с платформы."""
    async with Session() as s:
        clients = (await s.execute(select(Client).where(Client.company_id == company_id).order_by(Client.id))).scalars().all()
        by_id = {c.id: c for c in clients}
        batches = dict((await s.execute(select(Batch.id, Batch.number).where(Batch.company_id == company_id))).all())
        parcels = (await s.execute(select(Parcel).where(Parcel.company_id == company_id).order_by(Parcel.id))).scalars().all()
        charges = (await s.execute(select(Charge).where(Charge.company_id == company_id).order_by(Charge.id))).scalars().all()
        payments = (await s.execute(select(Payment).where(Payment.company_id == company_id).order_by(Payment.id))).scalars().all()
    code = lambda cid: by_id[cid].code if cid in by_id else None   # noqa: E731
    return make_xlsx({
        "Клиенты": [["Код", "Имя", "Телефон", "Telegram", "Город", "Статус", "Создан"]] + [
            [c.code, c.full_name, c.phone, c.username, c.city, c.status.value, c.created_at] for c in clients],
        "Посылки": [["Трек", "Код клиента", "Партия", "Вес", "Мест", "Статус", "Описание", "Прибыла", "Выдана"]] + [
            [p.track_number, code(p.client_id), batches.get(p.batch_id), p.weight, p.places, p.status.name,
             p.description, p.arrived_at, p.issued_at] for p in parcels],
        "Начисления": [["Код клиента", "Партия", "Вес", "Сумма", "Хранение", "Скидка", "Дата"]] + [
            [code(c.client_id), batches.get(c.batch_id), c.weight_total, c.amount, c.storage_amount, c.discount,
             c.created_at] for c in charges],
        "Платежи": [["Код клиента", "Сумма", "Способ", "Статус", "Причина отказа", "Дата"]] + [
            [code(p.client_id), p.amount, p.method, p.status.value, p.reject_reason, p.created_at] for p in payments],
    })


async def report_xlsx(company_id: int, days: int) -> bytes:
    since = period_start(days)
    async with Session() as s:
        clients = {c.id: c for c in (await s.execute(select(Client).where(Client.company_id == company_id))).scalars().all()}
        parcels = (await s.execute(select(Parcel).where(Parcel.company_id == company_id,
                                                        Parcel.arrived_at >= since).order_by(Parcel.id))).scalars().all()
        payments = (await s.execute(select(Payment).where(Payment.company_id == company_id, Payment.created_at >= since,
                                                          Payment.status == PaymentStatus.confirmed))).scalars().all()
    return make_xlsx({
        "Принято": [["Трек", "Код клиента", "Вес", "Статус", "Прибыла"]] + [
            [p.track_number, clients[p.client_id].code if p.client_id in clients else None, p.weight,
             p.status.name, p.arrived_at] for p in parcels],
        "Оплаты": [["Код клиента", "Сумма", "Способ", "Дата"]] + [
            [clients[p.client_id].code if p.client_id in clients else None, p.amount, p.method, p.created_at]
            for p in payments],
    })

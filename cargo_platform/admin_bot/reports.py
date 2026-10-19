"""Отчёты, журнал действий и выгрузка данных (ТЗ, 5.7)."""
from __future__ import annotations

from datetime import timedelta

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery
from sqlalchemy import select

from cargo_platform.admin_bot.common import IsStaff
from cargo_platform.config import config
from cargo_platform.db.base import Session
from cargo_platform.db.models import AuditLog, Company, Staff
from cargo_platform.master_bot.common import utcnow
from cargo_platform.services import audit, reports
from cargo_platform.services.notifications import kb

router = Router(name="admin_reports")
router.message.filter(F.chat.type == "private", IsStaff())
router.callback_query.filter(IsStaff())

PERIODS = {1: "Сегодня", 7: "7 дней", 30: "30 дней"}


@router.callback_query(F.data == "a:rep")
async def rep_menu(call: CallbackQuery):
    await call.answer()
    await call.message.answer("📊 Отчёт за период:", reply_markup=kb(
        [(name, f"a:rp:{days}") for days, name in PERIODS.items()], [("⬅️ Меню", "a:menu")]))


@router.callback_query(F.data.startswith("a:rp:"))
async def rep_show(call: CallbackQuery, company: Company):
    await call.answer()
    days = int(call.data.split(":")[2])
    if days not in PERIODS:
        return
    r = await reports.build_report(company.id, days)
    await call.message.answer(reports.report_text(r, f"Отчёт: {PERIODS[days].lower()}"),
                              reply_markup=kb([("📥 Выгрузить в Excel", f"a:rpx:{days}")]))


@router.callback_query(F.data.startswith("a:rpx:"))
async def rep_xlsx(call: CallbackQuery, company: Company):
    await call.answer()
    days = int(call.data.split(":")[2])
    if days not in PERIODS:
        return
    data = await reports.report_xlsx(company.id, days)
    await call.message.answer_document(BufferedInputFile(data, f"report_{days}d.xlsx"))


@router.callback_query(F.data == "a:audit")
async def audit_view(call: CallbackQuery, company: Company):
    await call.answer()
    async with Session() as s:
        rows = (await s.execute(select(AuditLog).where(AuditLog.company_id == company.id)
                                .order_by(AuditLog.id.desc()).limit(25))).scalars().all()
        names = dict((await s.execute(select(Staff.tg_id, Staff.name).where(Staff.company_id == company.id))).all())
    if not rows:
        return await call.message.answer("Журнал пока пуст.")
    lines = ["📜 <b>Журнал действий</b> (последние 25)\n"]
    for a in rows:
        when = a.created_at + timedelta(hours=config.tz_offset_hours)
        change = f": {a.old_value} → {a.new_value}" if a.old_value or a.new_value else ""
        lines.append(f"{when:%d.%m %H:%M} · {names.get(a.staff_tg_id, a.staff_tg_id)} · {a.action} {a.object or ''}{change}")
    await call.message.answer("\n".join(lines))


@router.callback_query(F.data == "a:export")
async def export(call: CallbackQuery, company: Company, staff: Staff):
    await call.answer()
    data = await reports.export_all(company.id)
    async with Session() as s:
        audit.log(s, company.id, staff.tg_id, "export_all")
        await s.commit()
    await call.message.answer_document(
        BufferedInputFile(data, f"{company.name}_{utcnow():%Y%m%d}.xlsx"),
        caption="📤 Все клиенты, посылки, начисления и платежи.")

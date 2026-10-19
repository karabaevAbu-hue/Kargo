"""Партии, загрузка Excel и приёмка с телефона (ТЗ, 5.2–5.5)."""
from __future__ import annotations

import io
import re
import time
from datetime import datetime, timedelta
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from cargo_platform.admin_bot.common import STATUS_NAMES, AdminFSM, IsStaff
from cargo_platform.admin_bot.panel import _text_or_scan
from cargo_platform.db.base import Session
from cargo_platform.db.models import (
    Batch, Client, Company, CompanySettings, ExcelTemplate, Parcel, ParcelStatus, Staff,
)
from cargo_platform.master_bot.common import utcnow
from cargo_platform.services import audit, excel, notifications, notify
from cargo_platform.services.billing import (
    calc_amount, effective_weight, fmt_money, fmt_weight, parse_weight, volume_weight,
)
from cargo_platform.services.codes import find_client_by_code, normalize_code
from cargo_platform.services.notifications import kb
from cargo_platform.services.parcels import (
    BILLABLE, mark_arrived, norm_track, price_for, recalc_charge,
)

router = Router(name="admin_batches")
router.message.filter(F.chat.type == "private", IsStaff())
router.callback_query.filter(IsStaff())

PS = ParcelStatus
MAX_FILE_BYTES = 15 * 1024 * 1024

# Разобранные файлы ждут подтверждения в памяти: в состоянии диалога им не место (могут быть тысячи строк)
_UPLOADS: dict[tuple[int, int], dict] = {}
_TTL = 3600


def _put(company_id: int, tg_id: int, **data) -> None:
    now = time.time()
    for k in [k for k, v in _UPLOADS.items() if now - v["ts"] > _TTL]:
        del _UPLOADS[k]
    _UPLOADS[(company_id, tg_id)] = {"ts": now, **data}


def _get(company_id: int, tg_id: int) -> dict | None:
    return _UPLOADS.get((company_id, tg_id))


def _letter(i: int) -> str:
    return chr(ord("A") + i)


# ================= партии =================

@router.callback_query(F.data == "a:batches")
async def batches_list(call: CallbackQuery, company: Company):
    await call.answer()
    async with Session() as s:
        rows = (await s.execute(select(Batch).where(Batch.company_id == company.id)
                                .order_by(Batch.id.desc()).limit(10))).scalars().all()
    buttons = [[("➕ Новая партия (Excel)", "a:bnew")]]
    buttons += [[(f"{b.number} · {STATUS_NAMES[b.status.value]}", f"a:b:{b.id}")] for b in rows]
    buttons.append([("⬅️ Меню", "a:menu")])
    await call.message.answer("📦 <b>Партии</b>" + ("" if rows else "\nПока нет ни одной."), reply_markup=kb(*buttons))


async def batch_card(company: Company, batch_id: int) -> tuple[str, object] | None:
    async with Session() as s:
        b = await s.get(Batch, batch_id)
        if not b or b.company_id != company.id:
            return None
        counts = dict((await s.execute(select(Parcel.status, func.count()).where(
            Parcel.company_id == company.id, Parcel.batch_id == b.id).group_by(Parcel.status))).all())
        clients = await s.scalar(select(func.count(func.distinct(Parcel.client_id))).where(
            Parcel.company_id == company.id, Parcel.batch_id == b.id))
        unmatched = await s.scalar(select(func.count()).select_from(Parcel).where(
            Parcel.company_id == company.id, Parcel.batch_id == b.id, Parcel.client_id.is_(None)))
    lines = [f"📦 <b>Партия {b.number}</b>", f"Статус: <b>{STATUS_NAMES[b.status.value]}</b>",
             f"Отправлена: {b.sent_at:%d.%m.%Y}" if b.sent_at else "Отправлена: —",
             f"Ожидается: {b.eta:%d.%m.%Y}" if b.eta else "Ожидается: —",
             f"Мест: {b.places or 0}, вес: {fmt_weight(b.total_weight)}",
             f"Клиентов: {clients}, неопознанных посылок: {unmatched}"]
    if counts:
        lines.append("Посылки: " + ", ".join(f"{STATUS_NAMES[k.value]} — {v}" for k, v in counts.items()))
    buttons = []
    if b.status.value < PS.shipped.value:
        buttons.append([("🚚 Отправлено из Китая", f"a:bsh:{b.id}")])
    if b.status.value < PS.arrived.value:
        buttons.append([("🏁 Прибыла на ПВЗ", f"a:bar:{b.id}")])
    buttons.append([("⬅️ Партии", "a:batches")])
    return "\n".join(lines), kb(*buttons)


@router.callback_query(F.data.regexp(r"^a:b:\d+$"))
async def batch_open(call: CallbackQuery, company: Company):
    await call.answer()
    card = await batch_card(company, int(call.data.split(":")[2]))
    if card:
        await call.message.answer(card[0], reply_markup=card[1])


# ---- отправлено из Китая ----

def _auto_eta(st: CompanySettings) -> datetime:
    days = [int(x) for x in re.findall(r"\d+", st.delivery_days or "")]
    return utcnow() + timedelta(days=max(days) if days else 15)


def parse_date(text: str) -> datetime | None:
    text = (text or "").strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%d.%m"):
        try:
            d = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == "%d.%m":
            d = d.replace(year=utcnow().year)
        return d.replace(tzinfo=utcnow().tzinfo)
    return None


@router.callback_query(F.data.startswith("a:bsh:"))
async def batch_shipped_ask(call: CallbackQuery, state: FSMContext, st: CompanySettings):
    await call.answer()
    bid = call.data.split(":")[2]
    eta = _auto_eta(st)
    await state.set_state(AdminFSM.eta)
    await state.set_data({"batch_id": int(bid)})
    await call.message.answer(
        "Ожидаемая дата прибытия на ПВЗ: выберите или введите дату (ДД.ММ.ГГГГ).",
        reply_markup=kb([(f"Авто: {eta:%d.%m.%Y}", f"a:bsha:{bid}")]))


@router.callback_query(F.data.startswith("a:bsha:"))
async def batch_shipped_auto(call: CallbackQuery, state: FSMContext, company: Company, st: CompanySettings,
                             staff: Staff, bot: Bot):
    await call.answer()
    await state.clear()
    await _ship(call.message, company, st, staff, bot, int(call.data.split(":")[2]), _auto_eta(st))


@router.message(AdminFSM.eta, F.text, ~F.text.startswith("/"))
async def batch_shipped_date(message: Message, state: FSMContext, company: Company, st: CompanySettings,
                             staff: Staff, bot: Bot):
    eta = parse_date(message.text)
    if eta is None:
        return await message.answer("Не понял дату. Формат: 25.12.2026")
    bid = (await state.get_data())["batch_id"]
    await state.clear()
    await _ship(message, company, st, staff, bot, bid, eta)


async def _ship(message: Message, company: Company, st: CompanySettings, staff: Staff, bot: Bot,
                batch_id: int, eta: datetime) -> None:
    async with Session() as s:
        b = await s.get(Batch, batch_id)
        if not b or b.company_id != company.id:
            return
        parcels = (await s.execute(select(Parcel).where(
            Parcel.company_id == company.id, Parcel.batch_id == b.id,
            Parcel.status.in_((PS.expected, PS.china_warehouse))))).scalars().all()
        per_client: dict[int, int] = {}
        for p in parcels:
            p.status = PS.shipped
            if p.client_id:
                per_client[p.client_id] = per_client.get(p.client_id, 0) + 1
        b.status, b.sent_at, b.eta = PS.shipped, utcnow(), eta
        audit.log(s, company.id, staff.tg_id, "batch_shipped", b.number, None, f"{eta:%d.%m.%Y}")
        clients = {c.id: c for c in (await s.execute(select(Client).where(
            Client.company_id == company.id, Client.id.in_(per_client)))).scalars().all()} if per_client else {}
        await s.commit()
    sent = 0
    if st.use_china_statuses:
        for cid, n in per_client.items():
            if await notifications.notify_shipped(bot, clients[cid], n, eta):
                sent += 1
    await message.answer(f"✅ Партия {b.number} отправлена. Посылок: {len(parcels)}, ожидаем {eta:%d.%m.%Y}. "
                         + (f"Уведомлено клиентов: {sent}." if st.use_china_statuses else
                            "Уведомления о статусе «Отправлено» отключены в настройках."))


# ---- прибыла на ПВЗ ----

@router.callback_query(F.data.startswith("a:bar:"))
async def batch_arrived(call: CallbackQuery, company: Company, st: CompanySettings, staff: Staff, bot: Bot):
    await call.answer()
    batch_id = int(call.data.split(":")[2])
    async with Session() as s:
        b = await s.get(Batch, batch_id)
        if not b or b.company_id != company.id:
            return
        parcels = (await s.execute(select(Parcel).where(
            Parcel.company_id == company.id, Parcel.batch_id == b.id,
            Parcel.status.in_((PS.expected, PS.china_warehouse, PS.shipped))))).scalars().all()
        touched = await mark_arrived(s, st, parcels, utcnow())
        b.status = PS.arrived
        audit.log(s, company.id, staff.tg_id, "batch_arrived", b.number, None, f"{len(parcels)} посылок")
        no_weight = sum(1 for p in parcels if p.weight is None)
        client_ids = {c for c, _ in touched}
        clients = {c.id: c for c in (await s.execute(select(Client).where(
            Client.company_id == company.id, Client.id.in_(client_ids)))).scalars().all()} if client_ids else {}
        await s.commit()
    sent = 0
    for cid, bid in touched:
        if await notifications.notify_arrival(bot, company, st, clients[cid], bid):
            sent += 1
    text = (f"🏁 Партия {b.number} прибыла на ПВЗ. Посылок: {len(parcels)}, клиентов: {len(client_ids)}, "
            f"уведомлено: {sent}.")
    if no_weight:
        text += f"\n⚠️ Без веса: {no_weight} посылок — сумма по ним не начислена. Взвесьте их через «🧾 Приёмка» или правку веса."
    await call.message.answer(text)


# ================= загрузка Excel =================

@router.callback_query(F.data == "a:bnew")
async def upload_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(AdminFSM.upload)
    await call.message.answer("Пришлите файл со склада (.xlsx, .xls или .csv) как документ.\n/admin — отмена.")


@router.message(AdminFSM.upload, F.document)
async def upload_file(message: Message, state: FSMContext, company: Company, staff: Staff, bot: Bot):
    doc = message.document
    if (doc.file_size or 0) > MAX_FILE_BYTES:
        return await message.answer("Файл слишком большой (максимум 15 МБ).")
    buf = io.BytesIO()
    await bot.download(doc, destination=buf)
    try:
        rows = excel.read_table(buf.getvalue(), doc.file_name or "")
    except excel.ExcelError as e:
        return await message.answer(f"{e}\nПришлите .xlsx или .csv")
    _put(company.id, staff.tg_id, rows=rows, filename=doc.file_name)
    async with Session() as s:
        tpl = await s.scalar(select(ExcelTemplate).where(ExcelTemplate.company_id == company.id)
                             .order_by(ExcelTemplate.id.desc()))
    ncols = max(len(r) for r in rows)
    saved = excel.fit_mapping(tpl.column_map.get("map", {}), ncols) if tpl else {}
    if tpl and excel.valid_mapping(saved, ncols) and saved.keys() >= {k for k in ("code", "track") if k in tpl.column_map["map"]}:
        await state.clear()
        return await _report(message, company, staff, saved, tpl.column_map["header"])
    guessed = excel.guess_mapping(rows)          # шаблон не подошёл: пробуем угадать по заголовку
    if tpl and excel.valid_mapping(guessed, ncols) and ("weight" in guessed):
        await state.clear()
        return await _report(message, company, staff, guessed, True)
    await _start_mapping(message, state, rows)


@router.message(AdminFSM.upload)
async def upload_wrong(message: Message):
    await message.answer("Нужен файл .xlsx / .xls / .csv, отправленный как документ.")


# ---- сопоставление колонок ----

async def _start_mapping(message: Message, state: FSMContext, rows: list[list[str]]) -> None:
    ncols = min(26, max(len(r) for r in rows))
    preview = ["Первые строки файла:"]
    for n, r in enumerate(rows[:3], start=1):
        preview.append(f"<b>Строка {n}:</b> " + " | ".join(f"{_letter(i)}={(r[i] if i < len(r) else '')[:18] or '∅'}"
                                                         for i in range(ncols)))
    await state.set_state(AdminFSM.map_column)
    await state.set_data({"idx": 0, "map": {}})
    await message.answer("\n".join(preview))
    await _ask_column(message, 0, ncols)


async def _ask_column(message: Message, idx: int, ncols: int) -> None:
    key, title = excel.FIELDS[idx]
    letters = [(_letter(i), f"a:mc:{i}") for i in range(ncols)]
    rows = [letters[i:i + 6] for i in range(0, len(letters), 6)]
    rows.append([("— такой колонки нет —", "a:mc:-")])
    await message.answer(f"В какой колонке «<b>{title}</b>»?"
                         + ("\n(нужен хотя бы код клиента или трек-номер)" if key in ("code", "track") else ""),
                         reply_markup=kb(*rows))


@router.callback_query(AdminFSM.map_column, F.data.startswith("a:mc:"))
async def map_column(call: CallbackQuery, state: FSMContext, company: Company, staff: Staff):
    await call.answer()
    d = await state.get_data()
    up = _get(company.id, staff.tg_id)
    if not up:
        await state.clear()
        return await call.message.answer("Файл устарел, загрузите его заново.")
    ncols = min(26, max(len(r) for r in up["rows"]))
    value = call.data.split(":")[2]
    key, _ = excel.FIELDS[d["idx"]]
    mapping = dict(d["map"])
    if value != "-":
        mapping[key] = int(value)
    idx = d["idx"] + 1
    if idx < len(excel.FIELDS):
        await state.update_data(idx=idx, map=mapping)
        return await _ask_column(call.message, idx, ncols)
    if "code" not in mapping and "track" not in mapping:
        await call.message.answer("Нужна хотя бы одна из колонок: код клиента или трек-номер. Начнём сначала.")
        return await _start_mapping(call.message, state, up["rows"])
    header = excel.detect_header(up["rows"], mapping)
    async with Session() as s:
        tpl = await s.scalar(select(ExcelTemplate).where(ExcelTemplate.company_id == company.id)
                             .order_by(ExcelTemplate.id.desc()))
        data = {"map": mapping, "header": header}
        if tpl:
            tpl.column_map = data
        else:
            s.add(ExcelTemplate(company_id=company.id, name="Склад", column_map=data))
        await s.commit()
    await state.clear()
    await call.message.answer("✅ Колонки запомнены: со следующего файла бот разберётся сам.")
    await _report(call.message, company, staff, mapping, header)


# ---- отчёт до сохранения ----

async def _build_analysis(company: Company, staff: Staff, mapping, header) -> excel.Analysis:
    up = _get(company.id, staff.tg_id)
    rows = up["rows"]
    async with Session() as s:
        st = await s.get(CompanySettings, company.id)
        clients = dict((await s.execute(select(Client.code, Client.id).where(Client.company_id == company.id))).all())
        ctx = await excel.load_ctx(s, company.id, st.code_prefix, st.code_digits, st.code_next_number, clients,
                                   excel.file_tracks(rows, mapping, header))
    return excel.analyze(rows, mapping, header, ctx)


async def _report(message: Message, company: Company, staff: Staff, mapping: dict, header: bool) -> None:
    analysis = await _build_analysis(company, staff, mapping, header)
    up = _get(company.id, staff.tg_id)
    up.update(mapping=mapping, header=header, analysis=analysis)
    found, unm, dups = analysis.found, analysis.unmatched, analysis.dups
    lines = ["📊 <b>Разбор файла</b>",
             f"✅ Найдено: {len(found)} посылок, {len(analysis.client_ids)} клиентов, {fmt_weight(analysis.weight_total)}",
             f"❓ Не найдено: {len(unm)} строк"]
    for r in unm[:15]:
        hint = f" → похоже: {', '.join(r.suggestions)}" if r.suggestions else ""
        lines.append(f"   • стр. {r.line}: код «{r.raw_code or '—'}», трек {r.track or '—'} — {r.note}{hint}")
    if len(unm) > 15:
        lines.append(f"   … и ещё {len(unm) - 15}")
    lines.append(f"♻️ Дубли: {len(dups)} — эти треки уже есть в базе, повторно не начисляются")
    if analysis.merged:
        lines.append(f"🔗 Объединено строк одного трека (несколько мест): {analysis.merged}")
    if analysis.no_weight:
        lines.append(f"⚠️ Без веса: {analysis.no_weight} посылок")
    if not analysis.stored:
        return await message.answer("\n".join(lines) + "\n\nСохранять нечего.")
    await message.answer("\n".join(lines), reply_markup=kb(
        [("✅ Сохранить и уведомить", "a:bs:1")], [("💾 Сохранить без уведомлений", "a:bs:0")],
        [("🔧 Изменить колонки", "a:bm"), ("✖️ Отмена", "a:bx")]))


@router.callback_query(F.data == "a:bm")
async def remap(call: CallbackQuery, state: FSMContext, company: Company, staff: Staff):
    await call.answer()
    up = _get(company.id, staff.tg_id)
    if not up:
        return await call.message.answer("Файл устарел, загрузите его заново.")
    await _start_mapping(call.message, state, up["rows"])


@router.callback_query(F.data == "a:bx")
async def upload_cancel(call: CallbackQuery, company: Company, staff: Staff):
    await call.answer()
    _UPLOADS.pop((company.id, staff.tg_id), None)
    await call.message.answer("Отменено, ничего не сохранено.")


@router.callback_query(F.data.startswith("a:bs:"))
async def upload_save(call: CallbackQuery, company: Company, st: CompanySettings, staff: Staff, bot: Bot):
    await call.answer()
    up = _UPLOADS.pop((company.id, staff.tg_id), None)     # pop: двойной клик не сохранит партию дважды
    if not up or "analysis" not in up:
        return await call.message.answer("Файл устарел или уже сохранён. Загрузите его заново.")
    do_notify = call.data.endswith(":1")
    analysis: excel.Analysis = up["analysis"]
    async with Session() as s:
        today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        n_today = await s.scalar(select(func.count()).select_from(Batch).where(
            Batch.company_id == company.id, Batch.created_at >= today)) or 0
        batch = Batch(company_id=company.id, number=f"{utcnow():%d.%m.%y}-{n_today + 1}", status=PS.china_warehouse)
        s.add(batch)
        await s.flush()
        await excel.save(s, company.id, batch, analysis)
        audit.log(s, company.id, staff.tg_id, "batch_upload", batch.number, None,
                  f"{len(analysis.stored)} посылок, файл {up.get('filename')}")
        clients = {c.id: c for c in (await s.execute(select(Client).where(
            Client.company_id == company.id, Client.id.in_(analysis.client_ids)))).scalars().all()} if analysis.client_ids else {}
        await s.commit()
        batch_id, number = batch.id, batch.number

    sent = 0
    per_client: dict[int, list[excel.Row]] = {}
    for r in analysis.found:
        per_client.setdefault(r.client_id, []).append(r)
    if do_notify and st.use_china_statuses:
        for cid, rows in per_client.items():
            tracks = [r.track for r in rows if r.track]
            if tracks and await notifications.notify_china(bot, clients[cid], tracks):
                sent += 1
    # ориентировочная сумма для владельца
    estimate = 0
    for cid, rows in per_client.items():
        weights = [w for r in rows if (w := effective_weight(r.weight, r.volume, st.volume_divisor)) is not None]
        if weights:
            estimate += calc_amount(weights, price_for(st, clients[cid]), st.min_weight)[1]
    await call.message.answer(
        f"✅ Партия <b>{number}</b> сохранена: {len(analysis.stored)} посылок"
        + (f", неопознанных {len(analysis.unmatched)} (раздел «Неопознанные»)" if analysis.unmatched else "") + ". "
        + (f"Уведомлено клиентов: {sent}." if do_notify and st.use_china_statuses else
           "Уведомления не отправлялись."),
        reply_markup=kb([("📦 Открыть партию", f"a:b:{batch_id}")]))
    if staff.tg_id != company.owner_tg_id:
        await notify.send(bot, company.owner_tg_id,
                          f"📥 Загружена партия {number}: посылок {len(analysis.stored)}, {fmt_weight(analysis.weight_total)}, "
                          f"ориентировочно {fmt_money(estimate)}.")


# ================= приёмка с телефона =================

@router.callback_query(F.data == "a:recv")
async def recv_pick_batch(call: CallbackQuery, company: Company):
    await call.answer()
    async with Session() as s:
        rows = (await s.execute(select(Batch).where(Batch.company_id == company.id)
                                .order_by(Batch.id.desc()).limit(5))).scalars().all()
    buttons = [[(f"{b.number} · {STATUS_NAMES[b.status.value]}", f"a:rcb:{b.id}")] for b in rows]
    buttons.append([("➕ Новая партия для приёмки", "a:rcn")])
    await call.message.answer("🧾 <b>Приёмка</b>: в какую партию принимать посылки?", reply_markup=kb(*buttons))


@router.callback_query(F.data == "a:rcn")
async def recv_new_batch(call: CallbackQuery, state: FSMContext, company: Company, staff: Staff):
    await call.answer()
    async with Session() as s:
        today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        n = await s.scalar(select(func.count()).select_from(Batch).where(
            Batch.company_id == company.id, Batch.created_at >= today)) or 0
        b = Batch(company_id=company.id, number=f"{utcnow():%d.%m.%y}-{n + 1}", status=PS.arrived, sent_at=None)
        s.add(b)
        audit.log(s, company.id, staff.tg_id, "batch_create", b.number)
        await s.commit()
    await _recv_begin(call.message, state, b.id, b.number)


@router.callback_query(F.data.startswith("a:rcb:"))
async def recv_use_batch(call: CallbackQuery, state: FSMContext, company: Company):
    await call.answer()
    async with Session() as s:
        b = await s.get(Batch, int(call.data.split(":")[2]))
    if not b or b.company_id != company.id:
        return
    await _recv_begin(call.message, state, b.id, b.number)


async def _recv_begin(message: Message, state: FSMContext, batch_id: int, number: str) -> None:
    await state.set_state(AdminFSM.recv_input)
    await state.set_data({"batch_id": batch_id, "touched": []})
    await message.answer(f"Приёмка в партию <b>{number}</b>.\nВведите код клиента или трек-номер "
                         "(можно прислать фото штрихкода). /admin — выйти без уведомлений.")


@router.message(AdminFSM.recv_input, F.photo | (F.text & ~F.text.startswith("/")))
async def recv_input(message: Message, state: FSMContext, company: Company, st: CompanySettings, bot: Bot):
    text = await _text_or_scan(message, bot)
    if not text:
        return await message.answer("Не смог прочитать штрихкод. Введите вручную:")
    text = text.strip()
    async with Session() as s:
        code, _ = normalize_code(text, st.code_prefix, st.code_digits)
        client = await find_client_by_code(s, company.id, text, st.code_prefix, st.code_digits) if code else None
        if client:     # ввели код клиента
            await state.update_data(client_id=client.id, parcel_id=None, track=None)
            await state.set_state(AdminFSM.recv_weight)
            return await message.answer(f"Клиент: <b>{client.full_name}</b> ({client.code}).\n" + _weight_prompt(st))
        track = norm_track(text, strict=False)
        if not track:
            return await message.answer("Не похоже ни на код клиента, ни на трек. Введите ещё раз:")
        parcel = await s.scalar(select(Parcel).where(Parcel.company_id == company.id, Parcel.track_number == track))
        client_id = parcel.client_id if parcel else None
        if client_id is None and parcel is None:
            from cargo_platform.db.models import Track
            client_id = await s.scalar(select(Track.client_id).where(
                Track.company_id == company.id, Track.track_number == track))
        client = await s.get(Client, client_id) if client_id else None
    await state.update_data(client_id=client.id if client else None, parcel_id=parcel.id if parcel else None, track=track)
    if client:
        await state.set_state(AdminFSM.recv_weight)
        return await message.answer(f"Трек <code>{track}</code> — клиент <b>{client.full_name}</b> ({client.code}).\n"
                                    + _weight_prompt(st))
    await state.set_state(AdminFSM.recv_track_client)
    await message.answer(f"Трек <code>{track}</code> не найден ни у одного клиента.\n"
                         "Введите код клиента или «-», чтобы принять как неопознанную:")


def _weight_prompt(st: CompanySettings) -> str:
    extra = " Можно добавить габариты: <code>2,3 40x30x20</code> (Д×Ш×В, см)." if st.volume_divisor else ""
    return f"Введите вес в кг (например 2,3).{extra}"


@router.message(AdminFSM.recv_track_client, F.text, ~F.text.startswith("/"))
async def recv_track_client(message: Message, state: FSMContext, company: Company, st: CompanySettings):
    text = message.text.strip()
    if text == "-":
        await state.update_data(client_id=None)
    else:
        async with Session() as s:
            client = await find_client_by_code(s, company.id, text, st.code_prefix, st.code_digits)
        if not client:
            return await message.answer("Клиент не найден. Введите код ещё раз или «-»:")
        await state.update_data(client_id=client.id)
    await state.set_state(AdminFSM.recv_weight)
    await message.answer(_weight_prompt(st))


_DIMS = re.compile(r"^(\d+(?:[.,]\d+)?)[xх*×](\d+(?:[.,]\d+)?)[xх*×](\d+(?:[.,]\d+)?)$", re.I)


@router.message(AdminFSM.recv_weight, F.text, ~F.text.startswith("/"))
async def recv_weight(message: Message, state: FSMContext, company: Company, st: CompanySettings, staff: Staff):
    parts = message.text.split()
    w = parse_weight(parts[0]) if parts else None
    if w is None:
        return await message.answer("Введите вес числом, например 2,3")
    vol = None
    if len(parts) > 1 and st.volume_divisor and (m := _DIMS.match(parts[1])):
        l, wd, h = (Decimal(x.replace(",", ".")) for x in m.groups())
        vol = volume_weight(l, wd, h, st.volume_divisor)
    d = await state.get_data()
    now = utcnow()
    async with Session() as s:
        client = await s.get(Client, d["client_id"]) if d.get("client_id") else None
        if d.get("parcel_id"):
            p = await s.get(Parcel, d["parcel_id"])
            p.batch_id = d["batch_id"]
            if client:
                p.client_id, p.problem_note = client.id, None
        else:
            p = Parcel(company_id=company.id, batch_id=d["batch_id"], client_id=client.id if client else None,
                       track_number=d.get("track"), status=PS.arrived,
                       problem_note=None if client else "принята без клиента")
            s.add(p)
        p.weight, p.volume_weight = w, vol
        await s.flush()
        await mark_arrived(s, st, [p], now)
        audit.log(s, company.id, staff.tg_id, "reception", p.track_number or (client.code if client else "?"), None, w)
        summary = ""
        if client:
            charge, _ = await recalc_charge(s, st, client, d["batch_id"])
            n = await s.scalar(select(func.count()).select_from(Parcel).where(
                Parcel.company_id == company.id, Parcel.client_id == client.id, Parcel.batch_id == d["batch_id"],
                Parcel.status.in_(BILLABLE)))
            if charge:
                from cargo_platform.services.parcels import total_of
                summary = (f"\nУ клиента в партии: {n} посылок, {fmt_weight(charge.weight_total)} → "
                           f"<b>{fmt_money(total_of(charge))}</b>")
        await s.commit()
    touched = set(d.get("touched", []))
    if client:
        touched.add(client.id)
    await state.update_data(touched=list(touched), client_id=None, parcel_id=None, track=None)
    await state.set_state(AdminFSM.recv_input)
    who = f"{client.full_name} ({client.code})" if client else "без клиента (раздел «Неопознанные»)"
    await message.answer(f"✅ {who}: {fmt_weight(w)}" + (f", объёмный {fmt_weight(vol)}" if vol else "") + summary,
                         reply_markup=kb([("➡️ Следующая", "a:rcnext")],
                                         [("✔️ Завершить и уведомить", "a:rcend:1"), ("🔕 Завершить без уведомлений", "a:rcend:0")]))


@router.callback_query(F.data == "a:rcnext")
async def recv_next(call: CallbackQuery):
    await call.answer()
    await call.message.answer("Следующая посылка: код клиента, трек или фото штрихкода.")


@router.callback_query(F.data.startswith("a:rcend:"))
async def recv_end(call: CallbackQuery, state: FSMContext, company: Company, st: CompanySettings, staff: Staff, bot: Bot):
    await call.answer()
    d = await state.get_data()
    await state.clear()
    if "batch_id" not in d:
        return await call.message.answer("Приёмка уже завершена.")
    do_notify = call.data.endswith(":1")
    batch_id = d["batch_id"]
    async with Session() as s:
        b = await s.get(Batch, batch_id)
        agg = (await s.execute(select(func.coalesce(func.sum(Parcel.places), 0), func.coalesce(func.sum(Parcel.weight), 0))
                               .where(Parcel.company_id == company.id, Parcel.batch_id == batch_id))).one()
        b.places, b.total_weight = int(agg[0]), Decimal(agg[1])
        clients = list((await s.execute(select(Client).where(
            Client.company_id == company.id, Client.id.in_(d.get("touched", []))))).scalars().all()) if d.get("touched") else []
        await s.commit()
    sent = 0
    if do_notify:
        for c in clients:
            if await notifications.notify_arrival(bot, company, st, c, batch_id):
                sent += 1
    await call.message.answer(f"✔️ Приёмка завершена. Клиентов: {len(clients)}"
                              + (f", уведомлено: {sent}." if do_notify else ", уведомления не отправлялись."))

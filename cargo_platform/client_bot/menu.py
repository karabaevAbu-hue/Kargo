"""Главное меню клиента (ТЗ, 4.3) и поиск по треку без регистрации (ТЗ, 4.4)."""
from __future__ import annotations

from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from cargo_platform.client_bot.common import (
    AddTrack, Calc, Contact, all_menu_texts, cancel_kb, menu_kb, send_address,
)
from cargo_platform.client_bot.registration import begin as begin_registration
from cargo_platform.client_bot.texts import ct, variants
from cargo_platform.db.base import Session
from cargo_platform.db.models import (
    Batch, Charge, Client, Company, CompanySettings, Message as DbMessage, Parcel, ParcelStatus,
)
from cargo_platform.services import notifications
from cargo_platform.services.barcode import make_qr_png
from cargo_platform.services.billing import calc_amount, fmt_money, fmt_price, fmt_weight, parse_weight
from cargo_platform.services.parcels import (
    AT_PVZ, BILLABLE, TRANSIT, claim_tracks, client_debt, norm_track, parse_tracks, price_for,
)

router = Router(name="client_menu")
router.message.filter(F.chat.type == "private")

PAGE = 8
FILTERS = {"transit": TRANSIT, "pvz": AT_PVZ, "issued": (ParcelStatus.issued,)}


def _btn(key: str):
    return F.text.in_(variants(key))


async def _need_client(message: Message, client: Client | None, lang: str) -> bool:
    if client is None:
        await message.answer(ct(lang, "need_register"))
        return True
    return False


# ---------- код и адрес ----------

@router.message(_btn("m_code"), StateFilter("*"))
async def my_code(message: Message, state: FSMContext, client: Client | None, st: CompanySettings, lang: str, bot: Bot):
    await state.clear()
    if await _need_client(message, client, lang):
        return
    await message.answer(f"🏷 <b>{client.code}</b>", reply_markup=menu_kb(lang))
    await send_address(bot, message.chat.id, st, client)


@router.callback_query(F.data == "qr:me")
async def my_qr(call: CallbackQuery, client: Client | None, bot: Bot):
    await call.answer()
    if client is None:
        return
    png = make_qr_png(client.code)
    await bot.send_photo(call.message.chat.id, BufferedInputFile(png, "qr.png"),
                         caption=ct(client.lang, "qr_caption", code=client.code))


# ---------- добавить трек ----------

@router.message(_btn("m_add"), StateFilter("*"))
async def add_track_start(message: Message, state: FSMContext, client: Client | None, lang: str):
    await state.clear()
    if await _need_client(message, client, lang):
        return
    await state.set_state(AddTrack.text)
    await message.answer(ct(lang, "add_tracks_prompt"), reply_markup=cancel_kb(lang))


@router.message(AddTrack.text, F.text)
async def add_track_save(message: Message, state: FSMContext, client: Client | None, company: Company,
                         st: CompanySettings, lang: str, bot: Bot):
    if message.text in variants("cancel"):
        await state.clear()
        return await message.answer(ct(lang, "cancelled"), reply_markup=menu_kb(lang))
    items, bad = parse_tracks(message.text)
    if not items:
        return await message.answer(ct(lang, "tracks_none"))
    async with Session() as s:
        res = await claim_tracks(s, company.id, client, items)
        linked = res["linked"]
        touched = {p.batch_id for p in linked if p.status in BILLABLE}
        if touched:
            from cargo_platform.services.parcels import refresh_client
            await refresh_client(s, st, client, touched)
        await s.commit()
    await state.clear()
    lines = []
    if res["added"]:
        lines.append(ct(lang, "tracks_result", added=len(res["added"])))
    if res["dup"]:
        lines.append(ct(lang, "tracks_dup", n=len(res["dup"])))
    if res["foreign"]:
        lines.append(ct(lang, "tracks_foreign", n=len(res["foreign"])))
    if bad:
        lines.append(ct(lang, "tracks_bad", items=", ".join(f"<code>{b}</code>" for b in bad[:5])))
    if linked:
        lines.append(ct(lang, "tracks_linked", n=len(linked)))
    await message.answer("\n".join(lines), reply_markup=menu_kb(lang))
    # посылка уже была на ПВЗ без клиента — теперь сообщаем сумму
    for b in {p.batch_id for p in linked if p.status in AT_PVZ}:
        await notifications.notify_arrival(bot, company, st, client, b)


# ---------- мои посылки ----------

def _status_text(lang: str, p: Parcel) -> str:
    if p.status == ParcelStatus.problem:
        return ct(lang, "st_9") + (f": {p.problem_note}" if p.problem_note else "")
    return ct(lang, f"st_{p.status.value}")


def _fmt_parcel(lang: str, p: Parcel) -> str:
    when = p.arrived_at or p.created_at
    weight = f", {fmt_weight(p.weight)}" if p.weight else ""
    note = f" «{p.description}»" if p.description else ""
    return ct(lang, "parcel_line", track=p.track_number or "—", status=_status_text(lang, p),
              weight=weight, note=note, date=f"{when:%d.%m}" if when else "—")


async def _parcels_text(client: Client, flt: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    lang = client.lang
    async with Session() as s:
        q = select(Parcel).where(Parcel.company_id == client.company_id, Parcel.client_id == client.id)
        if flt in FILTERS:
            q = q.where(Parcel.status.in_(FILTERS[flt]))
        rows = (await s.execute(q.order_by(Parcel.id.desc()).offset(page * PAGE).limit(PAGE + 1))).scalars().all()
    has_next = len(rows) > PAGE
    rows = rows[:PAGE]
    title = ct(lang, "parcels_title", flt=ct(lang, f"flt_{flt}"))
    body = "\n".join(_fmt_parcel(lang, p) for p in rows) or ct(lang, "parcels_empty")
    filt = [InlineKeyboardButton(text=("• " if k == flt else "") + ct(lang, f"flt_{k}"), callback_data=f"par:{k}:0")
            for k in ("transit", "pvz", "issued", "all")]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"par:{flt}:{page - 1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"par:{flt}:{page + 1}"))
    return f"{title}\n\n{body}", InlineKeyboardMarkup(inline_keyboard=[filt] + ([nav] if nav else []))


@router.message(_btn("m_parcels"), StateFilter("*"))
async def my_parcels(message: Message, state: FSMContext, client: Client | None, lang: str):
    await state.clear()
    if await _need_client(message, client, lang):
        return
    text, kb = await _parcels_text(client, "all", 0)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("par:"))
async def parcels_page(call: CallbackQuery, client: Client | None):
    await call.answer()
    if client is None:
        return
    _, flt, page = call.data.split(":")
    if flt not in (*FILTERS, "all"):
        return
    text, kb = await _parcels_text(client, flt, max(0, int(page)))
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass   # сообщение не изменилось


# ---------- к оплате ----------

async def debt_view(client: Client) -> tuple[str, InlineKeyboardMarkup | None]:
    lang = client.lang
    async with Session() as s:
        debt = await client_debt(s, client.company_id, client.id)
        charges = (await s.execute(select(Charge, Batch.number).outerjoin(Batch, Charge.batch_id == Batch.id).where(
            Charge.company_id == client.company_id, Charge.client_id == client.id)
            .order_by(Charge.id.desc()).limit(10))).all()
    if debt <= 0:
        credit = ct(lang, "credit_note", sum=fmt_money(-debt)) if debt < 0 else ""
        return ct(lang, "debt_none", credit=credit), None
    lines = [ct(lang, "debt_title", sum=fmt_money(debt)), ""]
    for c, number in charges:
        storage = f" + {fmt_money(c.storage_amount)}" if c.storage_amount else ""
        lines.append(ct(lang, "debt_line", batch=number or "—", weight=fmt_weight(c.weight_total),
                        amount=fmt_money(c.amount), storage=storage))
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=ct(lang, "btn_pay"), callback_data="pay:start")]])
    return "\n".join(lines), kb


@router.message(_btn("m_pay"), StateFilter("*"))
async def to_pay(message: Message, state: FSMContext, client: Client | None, lang: str):
    await state.clear()
    if await _need_client(message, client, lang):
        return
    text, kb = await debt_view(client)
    await message.answer(text, reply_markup=kb)


# ---------- калькулятор, тарифы, запрещённые, ПВЗ ----------

@router.message(_btn("m_calc"), StateFilter("*"))
async def calc_start(message: Message, state: FSMContext, lang: str):
    await state.clear()
    await state.set_state(Calc.weight)
    await message.answer(ct(lang, "calc_prompt"), reply_markup=cancel_kb(lang))


@router.message(Calc.weight, F.text)
async def calc_result(message: Message, state: FSMContext, st: CompanySettings, client: Client | None, lang: str):
    if message.text in variants("cancel"):
        await state.clear()
        return await message.answer(ct(lang, "cancelled"), reply_markup=menu_kb(lang))
    w = parse_weight(message.text)
    if w is None:
        return await message.answer(ct(lang, "calc_bad"))
    price = price_for(st, client) if client else Decimal(st.price_per_kg)
    total, amount = calc_amount([w], price, st.min_weight)
    min_note = ct(lang, "calc_min", w=fmt_weight(st.min_weight)) if st.min_weight and total < st.min_weight else ""
    await state.clear()
    await message.answer(ct(lang, "calc_result", w=fmt_weight(w), price=f"{fmt_price(price)}", sum=fmt_money(amount),
                            min_note=min_note), reply_markup=menu_kb(lang))


@router.message(_btn("m_tariffs"), StateFilter("*"))
async def tariffs(message: Message, state: FSMContext, st: CompanySettings, lang: str):
    await state.clear()
    storage = ""
    if st.free_storage_days is not None and st.storage_price_day:
        storage = ct(lang, "storage_text", days=st.free_storage_days, price=f"{fmt_price(st.storage_price_day)}")
    await message.answer(ct(lang, "tariffs_text", price=f"{fmt_price(st.price_per_kg)}", min_w=fmt_weight(st.min_weight),
                            days=st.delivery_days, storage=storage))


@router.message(_btn("m_forbidden"), StateFilter("*"))
async def forbidden(message: Message, state: FSMContext, st: CompanySettings, lang: str):
    await state.clear()
    await message.answer(ct(lang, "forbidden_title", text=st.forbidden_text or "—"))


@router.message(_btn("m_pvz"), StateFilter("*"))
async def pvz(message: Message, state: FSMContext, company: Company, lang: str):
    await state.clear()
    await message.answer(ct(lang, "pvz_text", address=company.pvz_address, schedule=company.schedule,
                            contact=company.contact or "—"), disable_web_page_preview=True)


# ---------- связаться с менеджером ----------

@router.message(_btn("m_contact"), StateFilter("*"))
async def contact_start(message: Message, state: FSMContext, client: Client | None, lang: str):
    await state.clear()
    if await _need_client(message, client, lang):
        return
    await state.set_state(Contact.text)
    await message.answer(ct(lang, "contact_prompt"), reply_markup=cancel_kb(lang))


@router.message(Contact.text, F.text)
async def contact_send(message: Message, state: FSMContext, client: Client, company: Company, lang: str, bot: Bot):
    if message.text in variants("cancel"):
        await state.clear()
        return await message.answer(ct(lang, "cancelled"), reply_markup=menu_kb(lang))
    text = message.text.strip()[:2000]
    async with Session() as s:
        s.add(DbMessage(company_id=company.id, client_id=client.id, direction="in", text=text))
        await s.commit()
    await state.clear()
    await message.answer(ct(lang, "contact_sent"), reply_markup=menu_kb(lang))
    markup = notifications.kb([("↩️ Ответить", f"a:reply:{client.id}")])
    await notifications.notify_staff(
        bot, company.id, f"💬 <b>{client.full_name}</b> ({client.code}):\n{text}", reply_markup=markup)


# ---------- профиль ----------

@router.message(_btn("m_profile"), StateFilter("*"))
async def profile(message: Message, state: FSMContext, client: Client | None, lang: str):
    await state.clear()
    if await _need_client(message, client, lang):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=ct(lang, "btn_change_lang"), callback_data="prof:lang")]])
    await message.answer(ct(lang, "profile", name=client.full_name, code=client.code, phone=client.phone)
                         + "\n\n" + ct(lang, "profile_phone_note"), reply_markup=kb)


@router.callback_query(F.data == "prof:lang")
async def change_lang(call: CallbackQuery, client: Client | None):
    await call.answer()
    if client is None:
        return
    new = "ky" if client.lang == "ru" else "ru"
    async with Session() as s:
        row = await s.get(Client, client.id)
        row.lang = new
        await s.commit()
    await call.message.answer(ct(new, "lang_changed"), reply_markup=menu_kb(new))


# ---------- поиск по треку (без регистрации) и прочий текст ----------

@router.message(F.text, StateFilter(None), ~F.text.startswith("/"), ~F.text.in_(all_menu_texts()))
async def track_lookup(message: Message, state: FSMContext, client: Client | None, company: Company, lang: str):
    track = norm_track(message.text)
    if track is None:
        if client:
            return await message.answer(ct(lang, "menu_title"), reply_markup=menu_kb(lang))
        return await begin_registration(message, state, lang)
    async with Session() as s:
        parcel = await s.scalar(select(Parcel).where(Parcel.company_id == company.id, Parcel.track_number == track))
    if parcel is None:
        return await message.answer(ct(lang, "track_not_found"))
    # только статус: без имени и суммы
    await message.answer(ct(lang, "track_found", track=track, status=_status_text(lang, parcel), company=company.name))

"""Мастер настройки компании (ТЗ, раздел 3.3).

Все поля описаны одним списком FIELDS. Мастер проходит их по порядку,
а из меню «Настройки» любое поле можно изменить отдельно.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select

from cargo_platform.db.base import Session
from cargo_platform.db.models import Client, CompanyBot, CompanySettings
from cargo_platform.master_bot.common import (
    REMOVE, SettingsFSM, cancel_kb, main_menu, owner_company, skip_kb,
)
from cargo_platform.services.validators import (
    format_code, has_code_placeholder, normalize_template, parse_decimal, valid_prefix,
)
from cargo_platform.texts import all_variants, t

router = Router(name="settings")
MEDIA_DIR = Path("media/qr")

Parsed = tuple[Any, str | None]   # (значение, текст ошибки)


def _prefix(raw: str) -> Parsed:
    p = valid_prefix(raw)
    return (p, None) if p else (None, "Нужно 2–5 латинских букв, например OSH или KG.")


def _start_number(raw: str) -> Parsed:
    raw = raw.strip()
    if raw.isdigit() and 1 <= int(raw) <= 999_999:
        return int(raw), None
    return None, "Введите число от 1 до 999999, например 1."


def _address(raw: str) -> Parsed:
    if not has_code_placeholder(raw):
        return None, "В адресе обязательно должно быть {CODE} — туда бот подставит код клиента. Пример:\n<code>广东省广州市白云区XX路XX号 {CODE}</code>"
    if len(raw) > 500:
        return None, "Слишком длинный адрес (максимум 500 символов)."
    return normalize_template(raw), None


def _receiver(raw: str) -> Parsed:
    if not has_code_placeholder(raw):
        return None, "Имя получателя должно содержать {CODE}, например <code>{CODE}</code> или <code>{CODE} Asia</code>."
    return normalize_template(raw)[:100], None


def _cn_phone(raw: str) -> Parsed:
    digits = "".join(ch for ch in raw if ch.isdigit())
    return (digits, None) if 8 <= len(digits) <= 15 else (None, "Номер склада: 8–15 цифр, например 13800000000.")


def _price(raw: str) -> Parsed:
    v = parse_decimal(raw, Decimal("1"), Decimal("100000"))
    return (v, None) if v is not None else (None, "Введите цену за кг числом, например 280.")


def _min_weight(raw: str) -> Parsed:
    v = parse_decimal(raw, Decimal("0"), Decimal("100"))
    return (v, None) if v is not None else (None, "Введите минимальный вес в кг, например 0,5 или 1.")


def _short_text(limit: int) -> Callable[[str], Parsed]:
    def parse(raw: str) -> Parsed:
        raw = raw.strip()
        return (raw, None) if 1 <= len(raw) <= limit else (None, f"От 1 до {limit} символов.")
    return parse


def _days(raw: str) -> Parsed:
    raw = raw.strip()
    return (int(raw), None) if raw.isdigit() and int(raw) <= 365 else (None, "Введите число дней, например 14.")


def _storage_price(raw: str) -> Parsed:
    v = parse_decimal(raw, Decimal("0"), Decimal("10000"))
    return (v, None) if v is not None else (None, "Введите цену хранения в сомах за день, например 20.")


@dataclass
class Field:
    key: str
    title: str
    prompt: str
    parse: Callable[[str], Parsed] | None   # None = особая обработка (QR оплаты)
    optional: bool = False


WIZARD_FIELDS: list[Field] = [
    Field("code_prefix", "Префикс кода", "🔤 Префикс кода клиентов: 2–5 латинских букв. Например <code>OSH</code> → коды OSH-0001, OSH-0002…", _prefix),
    Field("code_next_number", "Первый номер кода", "🔢 С какого номера начинать коды? Обычно 1. Если у вас уже есть клиенты с кодами до 350, введите 351.", _start_number),
    Field("address_taobao", "Адрес Taobao / 1688", "🇨🇳 Адрес склада для Taobao / 1688. Вместо кода клиента напишите <code>{CODE}</code>, бот подставит его сам.\nПример: <code>广东省广州市白云区XX路XX号 {CODE}</code>", _address),
    Field("address_pdd", "Адрес Pinduoduo", "🇨🇳 Адрес для Pinduoduo, если отличается (с <code>{CODE}</code>). Если такой же — пропустите.", _address, optional=True),
    Field("receiver_template", "Имя получателя", "👤 Что писать в поле «получатель»? Обычно <code>{CODE}</code>. Если пропустить — будет просто код.", _receiver, optional=True),
    Field("warehouse_phone", "Телефон склада", "📞 Телефон склада в Китае (его клиент вписывает в заказ), например 13800000000:", _cn_phone),
    Field("price_per_kg", "Цена за кг, сом", "💰 Цена за 1 кг в сомах, например 280:", _price),
    Field("min_weight", "Минимальный вес, кг", "⚖️ Минимальный вес к оплате, кг (например 0,5 или 1). Если посылка легче — считаем по минимуму:", _min_weight),
    Field("delivery_days", "Срок доставки", "🚚 Срок доставки, например «12–18 дней»:", _short_text(30)),
    Field("free_storage_days", "Бесплатное хранение, дней", "📦 Сколько дней посылка хранится на ПВЗ бесплатно? Например 14. Можно пропустить.", _days, optional=True),
    Field("storage_price_day", "Хранение после срока, сом/день", "📦 Цена хранения после бесплатного срока, сом в день. Можно пропустить.", _storage_price, optional=True),
    Field("forbidden_text", "Запрещённые товары", "⛔ Список запрещённых товаров (текстом, как покажете клиентам):", _short_text(3000)),
    Field("payment", "Реквизиты оплаты", "💳 Пришлите картинку QR бизнес-счёта (можно с подписью: банк, получатель) или текст с реквизитами для перевода:", None),
]
# Необязательные настройки: в мастере их нет, меняются потом из меню «Настройки»
FIELDS: list[Field] = WIZARD_FIELDS + [
    Field("welcome_text", "Приветствие клиента", "👋 Текст приветствия для клиентов (показывается при регистрации). Можно пропустить — будет стандартное.", _short_text(500), optional=True),
]
FIELD_BY_KEY = {f.key: f for f in FIELDS}

TOGGLES = {   # ключ: (название, где хранится)
    "city": ("Спрашивать город у клиента", "client_fields"),
    "delivery_address": ("Спрашивать адрес доставки", "client_fields"),
    "china": ("Уведомлять о статусах «Склад Китая» и «Отправлено»", "use_china_statuses"),
}


def _toggle_on(st: CompanySettings, key: str) -> bool:
    where = TOGGLES[key][1]
    return bool(st.use_china_statuses) if where == "use_china_statuses" else bool((st.client_fields or {}).get(key))


def _show(s: CompanySettings, f: Field) -> str:
    if f.key == "payment":
        parts = []
        if s.payment_qr_path:
            parts.append("QR загружен")
        if s.payment_text:
            parts.append(s.payment_text[:60])
        return ", ".join(parts) or "—"
    v = getattr(s, f.key)
    if v is None or v == "":
        return "—"
    text = str(v)
    return text if len(text) <= 60 else text[:57] + "…"


async def _ask(message: Message, state: FSMContext, field: Field, lang: str, single: bool):
    if field.optional:
        kb = skip_kb(lang, cancel=single)
    elif single:
        kb = cancel_kb(lang)
    else:
        kb = REMOVE
    await message.answer(field.prompt, reply_markup=kb)


async def start_wizard(message: Message, state: FSMContext, company_id: int, lang: str):
    await state.set_data({"lang": lang, "company_id": company_id, "idx": 0, "single": False})
    await state.set_state(SettingsFSM.value)
    await message.answer(f"⚙️ Настройка компании: {len(WIZARD_FIELDS)} коротких вопросов.")
    await _ask(message, state, FIELDS[0], lang, single=False)


async def _save(company_id: int, key: str, value: Any) -> None:
    async with Session() as s:
        st = await s.get(CompanySettings, company_id)
        setattr(st, key, value)
        await s.commit()


async def _next(message: Message, state: FSMContext):
    d = await state.get_data()
    lang = d["lang"]
    if d.get("single"):
        await state.clear()
        company = await owner_company(message.from_user.id)
        await message.answer(t(lang, "saved"), reply_markup=main_menu(lang, message.from_user.id))
        return await show_settings(message, company.id)
    idx = d["idx"] + 1
    if idx < len(WIZARD_FIELDS):
        await state.update_data(idx=idx)
        return await _ask(message, state, FIELDS[idx], lang, single=False)
    # мастер закончен
    company_id = d["company_id"]
    async with Session() as s:
        st = await s.get(CompanySettings, company_id)
        st.setup_done = True
        if not st.receiver_template:
            st.receiver_template = "{CODE}"
        bot_row = await s.scalar(select(CompanyBot).where(CompanyBot.company_id == company_id))
        await s.commit()
    await state.clear()
    await message.answer(t(lang, "settings_done", username=bot_row.bot_username if bot_row else ""),
                         reply_markup=main_menu(lang, message.from_user.id))
    await show_settings(message, company_id)


@router.message(SettingsFSM.value, F.text.func(lambda x: x in all_variants("btn_cancel")))
async def cancel(message: Message, state: FSMContext):
    d = await state.get_data()
    lang = d.get("lang", "ru")
    if not d.get("single"):
        return await message.answer(t(lang, "need_settings_first"))
    await state.clear()
    await message.answer(t(lang, "cancelled"), reply_markup=main_menu(lang, message.from_user.id))


@router.message(SettingsFSM.value, F.photo)
async def payment_photo(message: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    field = FIELDS[d["idx"]]
    if field.key != "payment":
        return await message.answer("Здесь нужен текст. " + field.prompt)
    company_id = d["company_id"]
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    path = MEDIA_DIR / f"{company_id}.jpg"
    # file_id главного бота не работает в боте карго, поэтому храним копию файла на диске
    await bot.download(message.photo[-1], destination=path)
    async with Session() as s:
        st = await s.get(CompanySettings, company_id)
        st.payment_qr_file_id = message.photo[-1].file_id
        st.payment_qr_path = str(path)
        if message.caption:
            st.payment_text = message.caption.strip()[:1000]
        await s.commit()
    await _next(message, state)


@router.message(SettingsFSM.value, F.text)
async def field_value(message: Message, state: FSMContext):
    d = await state.get_data()
    lang = d["lang"]
    field = FIELDS[d["idx"]]
    company_id = d["company_id"]

    if field.optional and message.text in all_variants("btn_skip"):
        if d.get("single"):
            await _save(company_id, field.key, None)
        return await _next(message, state)

    if field.key == "payment":
        await _save(company_id, "payment_text", message.text.strip()[:1000])
        return await _next(message, state)

    if field.key == "code_next_number":
        async with Session() as s:
            clients = await s.scalar(select(func.count()).select_from(Client).where(Client.company_id == company_id))
        if clients:
            await state.clear()
            return await message.answer(
                "Нельзя менять стартовый номер: клиентам уже выданы коды. Новые коды продолжат нумерацию.",
                reply_markup=main_menu(lang, message.from_user.id),
            )

    value, error = field.parse(message.text)
    if error:
        return await message.answer("❗ " + error)
    await _save(company_id, field.key, value)

    if field.key == "code_prefix":
        async with Session() as s:
            st = await s.get(CompanySettings, company_id)
        await message.answer(f"Пример кода клиента: <b>{format_code(value, st.code_next_number, st.code_digits)}</b>")
    await _next(message, state)


# ---------- просмотр и правка из меню ----------

async def show_settings(message: Message, company_id: int):
    async with Session() as s:
        st = await s.get(CompanySettings, company_id)
    lines = ["⚙️ <b>Настройки компании</b>\n"]
    lines += [f"• {f.title}: {_show(st, f)}" for f in FIELDS]
    if st.code_prefix:
        lines.append(f"\nСледующий код клиента: <b>{format_code(st.code_prefix, st.code_next_number, st.code_digits)}</b>")
    lines.append("")
    lines += [f"{'🟢' if _toggle_on(st, k) else '⚪'} {title}" for k, (title, _) in TOGGLES.items()]
    buttons = [[InlineKeyboardButton(text=f"✏️ {f.title}", callback_data=f"set:{f.key}")] for f in FIELDS]
    buttons += [[InlineKeyboardButton(text=f"{'🟢' if _toggle_on(st, k) else '⚪'} {title}", callback_data=f"tg:{k}")]
                for k, (title, _) in TOGGLES.items()]
    await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                         disable_web_page_preview=True)


@router.callback_query(F.data.startswith("set:"))
async def edit_one(call: CallbackQuery, state: FSMContext):
    company = await owner_company(call.from_user.id)
    key = call.data.split(":", 1)[1]
    if not company or key not in FIELD_BY_KEY:
        return await call.answer()
    idx = FIELDS.index(FIELD_BY_KEY[key])
    await state.set_data({"lang": company.lang, "company_id": company.id, "idx": idx, "single": True})
    await state.set_state(SettingsFSM.value)
    await _ask(call.message, state, FIELDS[idx], company.lang, single=True)
    await call.answer()


@router.callback_query(F.data.startswith("tg:"))
async def toggle(call: CallbackQuery):
    company = await owner_company(call.from_user.id)
    key = call.data.split(":", 1)[1]
    if not company or key not in TOGGLES:
        return await call.answer()
    async with Session() as s:
        st = await s.get(CompanySettings, company.id)
        if TOGGLES[key][1] == "use_china_statuses":
            st.use_china_statuses = not st.use_china_statuses
        else:
            fields = dict(st.client_fields or {})
            fields[key] = not fields.get(key)
            st.client_fields = fields
        await s.commit()
    await call.answer("Сохранено")
    await show_settings(call.message, company.id)

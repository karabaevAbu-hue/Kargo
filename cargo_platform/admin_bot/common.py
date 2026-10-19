"""Общее для админки менеджеров (ТЗ, раздел 5). Админка только на русском."""
from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from cargo_platform.db.models import Staff


class AdminFSM(StatesGroup):
    # Excel
    upload = State()
    map_column = State()
    # приёмка с телефона
    recv_input = State()
    recv_track_client = State()
    recv_weight = State()
    # выдача
    issue_code = State()
    # поиск и правки
    client_search = State()
    parcel_search = State()
    edit_weight = State()
    edit_price = State()
    edit_phone = State()
    move_parcel = State()
    link_parcel = State()
    problem_note = State()
    eta = State()
    # оплаты
    partial_amount = State()
    reject_reason = State()
    # сообщения
    reply = State()
    # рассылка
    bc_text = State()
    bc_button = State()
    bc_audience_value = State()
    bc_schedule = State()


class IsStaff(BaseFilter):
    """Пропускает только менеджеров этой компании. В режиме limited (подписка не оплачена)
    разрешены только вход в /admin и выгрузка данных."""

    async def __call__(self, event: Message | CallbackQuery, staff: Staff | None = None, limited: bool = False):
        if staff is None:
            return False
        if limited:
            if isinstance(event, Message):
                return (event.text or "").startswith("/admin")
            return (event.data or "") in ("a:export", "a:menu")
        return {"staff": staff}


STATUS_NAMES = {0: "ожидается", 1: "на складе в Китае", 2: "отправлено из Китая",
                3: "прибыло на ПВЗ", 4: "оплачено", 5: "выдано", 9: "проблема"}

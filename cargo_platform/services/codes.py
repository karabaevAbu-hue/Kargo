"""Коды клиентов (ТЗ, 4.2 и раздел 8).

Новый код выдаётся атомарным UPDATE ... RETURNING внутри той транзакции,
которая создаёт клиента: два клиента не получат один код, а при ошибке
вставки номер не сгорает.
"""
from __future__ import annotations

import difflib
import re

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cargo_platform.db.models import Client, CompanySettings
from cargo_platform.services.validators import format_code


async def issue_code(s: AsyncSession, company_id: int) -> str:
    row = (await s.execute(
        update(CompanySettings)
        .where(CompanySettings.company_id == company_id)
        .values(code_next_number=CompanySettings.code_next_number + 1)
        .returning(CompanySettings.code_prefix, CompanySettings.code_next_number, CompanySettings.code_digits)
    )).one()
    prefix, next_number, digits = row
    return format_code(prefix, next_number - 1, digits)


def normalize_code(raw, prefix: str, digits: int = 4) -> tuple[str | None, bool]:
    """'osh 123', 'OSH123', 'OSH-0123' -> 'OSH-0123'. Только номер '0124' -> с префиксом компании.

    Возвращает (код, чужой_префикс). Если префикс не наш (OHS-0124), код = None, флаг True:
    строка уйдёт в «Неопознанные» с подсказкой похожих кодов.
    """
    if raw is None:
        return None, False
    text = str(raw).strip().upper().replace("О", "O").replace(" ", "").replace("_", "-")
    if not text:
        return None, False
    if re.fullmatch(r"\d{1,7}(\.0)?", text):       # Excel любит превращать 124 в 124.0
        return format_code(prefix, int(float(text)), digits), False
    m = re.fullmatch(r"([A-Z]{2,5})-?0*(\d{1,7})", text)
    if not m:
        return None, False
    if m.group(1) != prefix:
        return None, True
    return format_code(prefix, int(m.group(2)), digits), False


def suggest_codes(raw, codes: list[str], limit: int = 3) -> list[str]:
    """Похожие коды для строки с опечаткой: тот же номер или близкая запись."""
    text = str(raw or "").strip().upper().replace(" ", "")
    if not text:
        return []
    digits = re.sub(r"\D", "", text)
    out: list[str] = []
    if digits:
        n = int(digits)
        out += [c for c in codes if int(re.sub(r"\D", "", c)) == n]
    out += difflib.get_close_matches(text, codes, n=limit, cutoff=0.7)
    seen, res = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            res.append(c)
    return res[:limit]


async def find_client_by_code(s: AsyncSession, company_id: int, raw: str, prefix: str,
                              digits: int = 4) -> Client | None:
    code, _ = normalize_code(raw, prefix, digits)
    if not code:
        return None
    return await s.scalar(select(Client).where(Client.company_id == company_id, Client.code == code))

"""Бизнес-логика посылок, начислений и долга (ТЗ, разделы 5 и 6).

Долг клиента считается из таблиц начислений и платежей, а не хранится вручную (ТЗ, 6.4):
    долг = сумма начислений − сумма подтверждённых платежей.
Отрицательный долг — переплата, она автоматически учитывается в следующем начислении.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cargo_platform.db.models import (
    Charge, Client, CompanySettings, Parcel, ParcelStatus, Payment, PaymentStatus, Track,
)
from cargo_platform.services.billing import calc_amount, charge_total, effective_weight, storage_amount
from cargo_platform.master_bot.common import as_utc

PS = ParcelStatus
BILLABLE = (PS.arrived, PS.paid, PS.issued)     # посылки, по которым уже есть начисление
AT_PVZ = (PS.arrived, PS.paid)
TRANSIT = (PS.expected, PS.china_warehouse, PS.shipped)

_TRACK_STRICT = re.compile(r"^[A-Z0-9][A-Z0-9\-]{7,39}$")


def norm_track(raw, strict: bool = True) -> str | None:
    """Трек: без пробелов, в верхнем регистре. strict=True — для клиента (8–40 символов),
    strict=False — для файла склада, где бывают короткие номера."""
    text = re.sub(r"\s+", "", str(raw or "")).upper()
    if strict:
        return text if _TRACK_STRICT.match(text) else None
    return text if 4 <= len(text) <= 64 else None


def total_of(c: Charge) -> int:
    return charge_total(c.amount, c.storage_amount, c.discount)


async def client_totals(s: AsyncSession, company_id: int, client_id: int) -> tuple[int, int]:
    charged = await s.scalar(
        select(func.coalesce(func.sum(Charge.amount + Charge.storage_amount - Charge.discount), 0))
        .where(Charge.company_id == company_id, Charge.client_id == client_id))
    paid = await s.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0))
        .where(Payment.company_id == company_id, Payment.client_id == client_id,
               Payment.status == PaymentStatus.confirmed))
    return int(charged or 0), int(paid or 0)


async def client_debt(s: AsyncSession, company_id: int, client_id: int) -> int:
    """>0 долг, <0 переплата."""
    charged, paid = await client_totals(s, company_id, client_id)
    return charged - paid


def price_for(st: CompanySettings, client: Client) -> Decimal:
    return Decimal(client.price_per_kg_custom or st.price_per_kg or 0)


async def recalc_charge(s: AsyncSession, st: CompanySettings, client: Client,
                        batch_id: int | None) -> tuple[Charge | None, int | None]:
    """Пересчитывает начисление клиента по партии. Возвращает (начисление, прежняя сумма или None)."""
    cid = st.company_id
    parcels = (await s.execute(select(Parcel).where(
        Parcel.company_id == cid, Parcel.client_id == client.id, Parcel.batch_id == batch_id,
        Parcel.status.in_(BILLABLE)))).scalars().all()
    weights = [w for p in parcels if (w := effective_weight(p.weight, p.volume_weight, st.volume_divisor)) is not None]
    charge = await s.scalar(select(Charge).where(
        Charge.company_id == cid, Charge.client_id == client.id, Charge.batch_id == batch_id))
    if not weights:
        old = total_of(charge) if charge else None
        if charge:
            await s.delete(charge)
        return None, old
    total, amount = calc_amount(weights, price_for(st, client), st.min_weight)
    if charge:
        old = total_of(charge)
        charge.weight_total, charge.amount = total, amount
    else:
        old = None
        charge = Charge(company_id=cid, client_id=client.id, batch_id=batch_id, weight_total=total,
                        amount=amount, storage_amount=0, discount=0)
        s.add(charge)
    await s.flush()
    return charge, old


async def settle_if_paid(s: AsyncSession, company_id: int, client_id: int) -> bool:
    """Долг закрыт — посылки «на ПВЗ» становятся «Оплачено»."""
    if await client_debt(s, company_id, client_id) > 0:
        return False
    await s.execute(update(Parcel).where(
        Parcel.company_id == company_id, Parcel.client_id == client_id, Parcel.status == PS.arrived)
        .values(status=PS.paid))
    return True


async def reopen_if_unpaid(s: AsyncSession, company_id: int, client_id: int) -> None:
    """Сумма выросла (поправили вес): «Оплачено» → снова «К оплате», если долг появился."""
    if await client_debt(s, company_id, client_id) > 0:
        await s.execute(update(Parcel).where(
            Parcel.company_id == company_id, Parcel.client_id == client_id, Parcel.status == PS.paid)
            .values(status=PS.arrived))


async def refresh_client(s: AsyncSession, st: CompanySettings, client: Client,
                         batch_ids: set[int | None]) -> dict[int | None, tuple[Charge | None, int | None]]:
    """Пересчёт начислений клиента по партиям + согласование статусов оплаты."""
    out = {}
    for b in batch_ids:
        out[b] = await recalc_charge(s, st, client, b)
    if await client_debt(s, st.company_id, client.id) > 0:
        await reopen_if_unpaid(s, st.company_id, client.id)
    else:
        await settle_if_paid(s, st.company_id, client.id)
    return out


async def mark_arrived(s: AsyncSession, st: CompanySettings, parcels: list[Parcel], now: datetime) -> set[tuple[int, int | None]]:
    """Посылки прибыли на ПВЗ: статус 3, начисления по клиентам. Возвращает {(client_id, batch_id)}."""
    touched: set[tuple[int, int | None]] = set()
    for p in parcels:
        if p.status in (PS.paid, PS.issued):
            continue
        p.status, p.arrived_at = PS.arrived, p.arrived_at or now
        if p.client_id:
            touched.add((p.client_id, p.batch_id))
    await s.flush()
    for client_id, batch_id in touched:
        client = await s.get(Client, client_id)
        await recalc_charge(s, st, client, batch_id)
    return touched


async def update_storage(s: AsyncSession, st: CompanySettings, now: datetime) -> int:
    """Раз в сутки: начисляет платное хранение по партиям, где посылки лежат дольше срока (ТЗ, 6.1).
    Хранение считается раз на клиента и партию: от самой ранней неполученной посылки."""
    if st.free_storage_days is None or not st.storage_price_day:
        return 0
    rows = (await s.execute(
        select(Parcel.client_id, Parcel.batch_id, func.min(Parcel.arrived_at))
        .where(Parcel.company_id == st.company_id, Parcel.status.in_(AT_PVZ), Parcel.client_id.is_not(None),
               Parcel.arrived_at.is_not(None))
        .group_by(Parcel.client_id, Parcel.batch_id))).all()
    changed = 0
    for client_id, batch_id, first_arrived in rows:
        amount = storage_amount(as_utc(first_arrived), now, st.free_storage_days, st.storage_price_day)
        charge = await s.scalar(select(Charge).where(
            Charge.company_id == st.company_id, Charge.client_id == client_id, Charge.batch_id == batch_id))
        if charge and charge.storage_amount != amount:
            charge.storage_amount = amount
            changed += 1
    return changed


# ---------- треки клиента ----------

async def claim_tracks(s: AsyncSession, company_id: int, client: Client,
                       items: list[tuple[str, str | None]], added_by: str = "client") -> dict:
    """Клиент добавляет треки. Если посылка уже пришла без клиента («Неопознанные») — привязываем."""
    res = {"added": [], "dup": [], "foreign": [], "linked": []}
    for track, note in items:
        mine = await s.scalar(select(Track).where(Track.company_id == company_id, Track.track_number == track))
        parcel = await s.scalar(select(Parcel).where(Parcel.company_id == company_id, Parcel.track_number == track))
        owner = (mine.client_id if mine else None) or (parcel.client_id if parcel else None)
        if owner == client.id:
            res["dup"].append(track)
            continue
        if owner is not None:
            res["foreign"].append(track)
            continue
        s.add(Track(company_id=company_id, client_id=client.id, track_number=track,
                    note=(note or None) and note[:100], added_by=added_by))
        if parcel:
            parcel.client_id, parcel.problem_note = client.id, None
            res["linked"].append(parcel)
        else:
            s.add(Parcel(company_id=company_id, client_id=client.id, track_number=track,
                         description=(note or None) and note[:200], status=PS.expected))
        res["added"].append(track)
    await s.flush()
    return res


def parse_tracks(text: str) -> tuple[list[tuple[str, str | None]], list[str]]:
    """'YT123… кроссовки' по строкам или 'A, B, C' через запятую → ([(трек, заметка)], плохие)."""
    items: list[tuple[str, str | None]] = []
    bad: list[str] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")] if "," in line else [line]
        for part in parts:
            tokens = part.split(None, 1)
            if not tokens:
                continue
            track = norm_track(tokens[0])
            if track is None:
                bad.append(tokens[0][:30])
                continue
            if track not in seen:
                seen.add(track)
                items.append((track, tokens[1].strip() if len(tokens) > 1 else None))
    return items, bad


async def refresh_all(s: AsyncSession, st: CompanySettings, client: Client):
    """Пересчитывает все начисления клиента (после смены цены, веса, переноса посылки)."""
    ids: set[int | None] = set((await s.execute(select(Parcel.batch_id).where(
        Parcel.company_id == st.company_id, Parcel.client_id == client.id,
        Parcel.status.in_(BILLABLE)).distinct())).scalars().all())
    ids |= set((await s.execute(select(Charge.batch_id).where(
        Charge.company_id == st.company_id, Charge.client_id == client.id).distinct())).scalars().all())
    return await refresh_client(s, st, client, ids)

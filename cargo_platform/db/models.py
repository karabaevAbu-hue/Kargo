"""Таблицы базы данных (ТЗ, раздел 8).

Правило: в каждой таблице карго есть company_id, и любой запрос фильтруется по нему.
"""
from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON, BigInteger, Boolean, DateTime, Enum, ForeignKey, Index, Integer,
    Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def now_col():
    return mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------- Справочники статусов ----------

class CompanyStatus(str, enum.Enum):
    trial = "trial"
    active = "active"
    grace = "grace"        # льготные 3 дня после неоплаты
    paused = "paused"      # не оплачено: бот клиента отвечает заглушкой
    blocked = "blocked"    # заблокировано владельцем платформы


class BotStatus(str, enum.Enum):
    running = "running"
    stopped = "stopped"
    error = "error"


class StaffRole(str, enum.Enum):
    owner = "owner"
    admin = "admin"


class ClientStatus(str, enum.Enum):
    active = "active"
    blocked = "blocked"
    left = "left"          # заблокировал бота


class ParcelStatus(int, enum.Enum):
    expected = 0           # клиент добавил трек
    china_warehouse = 1    # принято на склад в Китае
    shipped = 2            # отправлено из Китая
    arrived = 3            # прибыло на ПВЗ, к оплате
    paid = 4
    issued = 5
    problem = 9


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    rejected = "rejected"


# ---------- Платформа ----------

class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    owner_name: Mapped[str] = mapped_column(String(60))
    owner_phone: Mapped[str] = mapped_column(String(20))
    owner_tg_id: Mapped[int] = mapped_column(BigInteger, index=True)
    city: Mapped[str] = mapped_column(String(60))
    pvz_address: Mapped[str] = mapped_column(Text)
    schedule: Mapped[str] = mapped_column(String(100))
    contact: Mapped[str | None] = mapped_column(String(100))
    lang: Mapped[str] = mapped_column(String(2), default="ru")
    status: Mapped[CompanyStatus] = mapped_column(Enum(CompanyStatus), default=CompanyStatus.trial)
    tariff: Mapped[str] = mapped_column(String(20), default="trial")
    paid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    notice_key: Mapped[str | None] = mapped_column(String(40))  # какое напоминание о подписке уже отправлено
    created_at: Mapped[datetime] = now_col()

    bot: Mapped["CompanyBot | None"] = relationship(back_populates="company", uselist=False)
    settings: Mapped["CompanySettings | None"] = relationship(back_populates="company", uselist=False)


class CompanyBot(Base):
    __tablename__ = "bots"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), unique=True)
    bot_tg_id: Mapped[int] = mapped_column(BigInteger)
    bot_username: Mapped[str] = mapped_column(String(64))
    token_encrypted: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256: проверка «токен уже подключён»
    webhook_secret: Mapped[str] = mapped_column(String(64))
    status: Mapped[BotStatus] = mapped_column(Enum(BotStatus), default=BotStatus.running)
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    company: Mapped[Company] = relationship(back_populates="bot")


class CompanySettings(Base):
    __tablename__ = "company_settings"

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), primary_key=True)
    code_prefix: Mapped[str | None] = mapped_column(String(5))
    code_next_number: Mapped[int] = mapped_column(Integer, default=1)
    code_digits: Mapped[int] = mapped_column(Integer, default=4)
    address_taobao: Mapped[str | None] = mapped_column(Text)
    address_pdd: Mapped[str | None] = mapped_column(Text)
    receiver_template: Mapped[str | None] = mapped_column(String(100))
    warehouse_phone: Mapped[str | None] = mapped_column(String(30))
    price_per_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    min_weight: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("1.0"))
    volume_divisor: Mapped[int | None] = mapped_column(Integer)          # 6000 или None
    delivery_days: Mapped[str | None] = mapped_column(String(30))
    free_storage_days: Mapped[int | None] = mapped_column(Integer)
    storage_price_day: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    forbidden_text: Mapped[str | None] = mapped_column(Text)
    payment_qr_file_id: Mapped[str | None] = mapped_column(String(200))  # file_id в ГЛАВНОМ боте
    payment_qr_path: Mapped[str | None] = mapped_column(String(300))     # копия файла на диске для ботов карго
    payment_text: Mapped[str | None] = mapped_column(Text)
    client_fields: Mapped[dict] = mapped_column(JSON, default=lambda: {"city": True})
    welcome_text: Mapped[str | None] = mapped_column(Text)
    setup_done: Mapped[bool] = mapped_column(Boolean, default=False)

    company: Mapped[Company] = relationship(back_populates="settings")


class Staff(Base):
    __tablename__ = "staff"
    __table_args__ = (UniqueConstraint("company_id", "tg_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    tg_id: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(String(60))
    role: Mapped[StaffRole] = mapped_column(Enum(StaffRole))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class StaffInvite(Base):
    """Одноразовая ссылка-приглашение менеджера, живёт 24 часа."""
    __tablename__ = "staff_invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    token: Mapped[str] = mapped_column(String(32), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    tariff: Mapped[str] = mapped_column(String(20))
    amount: Mapped[int] = mapped_column(Integer)
    period_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = now_col()


# ---------- Данные карго (недели 2–4, таблицы создаются сразу) ----------

class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (
        UniqueConstraint("company_id", "code"),
        UniqueConstraint("company_id", "phone"),
        UniqueConstraint("company_id", "tg_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(20))
    full_name: Mapped[str] = mapped_column(String(60))
    phone: Mapped[str] = mapped_column(String(20))
    tg_id: Mapped[int | None] = mapped_column(BigInteger)
    username: Mapped[str | None] = mapped_column(String(64))
    city: Mapped[str | None] = mapped_column(String(60))
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    lang: Mapped[str] = mapped_column(String(2), default="ru")
    price_per_kg_custom: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    status: Mapped[ClientStatus] = mapped_column(Enum(ClientStatus), default=ClientStatus.active)
    created_at: Mapped[datetime] = now_col()


class Track(Base):
    __tablename__ = "tracks"
    __table_args__ = (Index("ix_tracks_company_track", "company_id", "track_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    track_number: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(String(100))
    added_by: Mapped[str] = mapped_column(String(10), default="client")  # client / staff / excel
    created_at: Mapped[datetime] = now_col()


class Batch(Base):
    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    number: Mapped[str] = mapped_column(String(40))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    eta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    places: Mapped[int | None] = mapped_column(Integer)
    total_weight: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    status: Mapped[ParcelStatus] = mapped_column(Enum(ParcelStatus), default=ParcelStatus.china_warehouse)
    created_at: Mapped[datetime] = now_col()


class Parcel(Base):
    __tablename__ = "parcels"
    __table_args__ = (
        Index("ix_parcels_company_track", "company_id", "track_number"),
        Index("ix_parcels_company_code", "company_id", "raw_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("batches.id"), index=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), index=True)  # пусто = «Неопознанные»
    raw_code: Mapped[str | None] = mapped_column(String(40))
    track_number: Mapped[str | None] = mapped_column(String(64))
    weight: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    volume_weight: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    places: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[ParcelStatus] = mapped_column(Enum(ParcelStatus), default=ParcelStatus.expected)
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_by: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))
    created_at: Mapped[datetime] = now_col()


class Charge(Base):
    __tablename__ = "charges"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("batches.id"))
    weight_total: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    amount: Mapped[int] = mapped_column(Integer)
    storage_amount: Mapped[int] = mapped_column(Integer, default=0)
    discount: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = now_col()


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(10))  # qr / cash
    receipt_file_id: Mapped[str | None] = mapped_column(String(200))
    receipt_unique_id: Mapped[str | None] = mapped_column(String(100), index=True)  # поиск повторных чеков
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), default=PaymentStatus.pending)
    reject_reason: Mapped[str | None] = mapped_column(String(200))
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))
    created_at: Mapped[datetime] = now_col()


class ExcelTemplate(Base):
    __tablename__ = "excel_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    column_map: Mapped[dict] = mapped_column(JSON)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    direction: Mapped[str] = mapped_column(String(3))  # in / out
    text: Mapped[str] = mapped_column(Text)
    staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))
    created_at: Mapped[datetime] = now_col()


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    media: Mapped[str | None] = mapped_column(String(200))
    audience: Mapped[dict] = mapped_column(JSON)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(12), default="draft")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    staff_tg_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(50))
    object: Mapped[str | None] = mapped_column(String(100))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = now_col()

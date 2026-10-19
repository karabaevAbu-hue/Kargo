"""Проверки полей (ТЗ, разделы 3.1–3.3). Чистые функции — легко тестировать."""
import re
from decimal import Decimal, InvalidOperation

TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,50}$")
PHONE_RE = re.compile(r"^\+996\d{9}$")
NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁёҢңӨөҮү\-\s']{2,60}$")
PREFIX_RE = re.compile(r"^[A-Z]{2,5}$")


def clean_phone(raw: str) -> str | None:
    """'0555 12 34 56', '996555123456', '+996 555 123 456' -> '+996555123456'."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("0") and len(digits) == 10:
        digits = "996" + digits[1:]
    elif len(digits) == 9:
        digits = "996" + digits
    phone = "+" + digits
    return phone if PHONE_RE.match(phone) else None


def valid_name(raw: str) -> str | None:
    name = " ".join((raw or "").split())
    return name if NAME_RE.match(name) else None


def valid_company_name(raw: str) -> str | None:
    name = " ".join((raw or "").split())
    return name if 2 <= len(name) <= 40 else None


def valid_token(raw: str) -> str | None:
    token = (raw or "").strip()
    return token if TOKEN_RE.match(token) else None


def valid_prefix(raw: str) -> str | None:
    prefix = (raw or "").strip().upper()
    return prefix if PREFIX_RE.match(prefix) else None


def parse_decimal(raw: str, lo: Decimal, hi: Decimal) -> Decimal | None:
    """'280', '280,5', '0.5' -> Decimal в диапазоне [lo, hi]."""
    try:
        value = Decimal((raw or "").strip().replace(",", ".").replace(" ", ""))
    except InvalidOperation:
        return None
    return value if lo <= value <= hi else None


def has_code_placeholder(template: str) -> bool:
    """Шаблон адреса обязан содержать {CODE}, иначе склад не найдёт клиента."""
    return "{CODE}" in (template or "").upper()


def normalize_template(template: str) -> str:
    """Любой регистр {code}/{Code} -> {CODE}, {phone} -> {PHONE}."""
    t = re.sub(r"\{code\}", "{CODE}", template, flags=re.I)
    return re.sub(r"\{phone\}", "{PHONE}", t, flags=re.I).strip()


def format_code(prefix: str, number: int, digits: int = 4) -> str:
    return f"{prefix}-{number:0{digits}d}"

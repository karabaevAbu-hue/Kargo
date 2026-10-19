"""Готовый адрес склада в Китае для клиента (ТЗ, 4.2)."""
from __future__ import annotations

from cargo_platform.db.models import Client, CompanySettings


def _fill(template: str, code: str, phone: str, name: str = "") -> str:
    return template.replace("{CODE}", code).replace("{PHONE}", phone or "").replace("{NAME}", name)


def build_address(st: CompanySettings, client: Client) -> dict[str, str]:
    """Каждое значение клиент копирует одним нажатием."""
    first = client.full_name.split()[0] if client.full_name else ""
    wphone = st.warehouse_phone or ""
    out = {
        "receiver": _fill(st.receiver_template or "{CODE}", client.code, wphone, first),
        "phone": wphone,
        "address": _fill(st.address_taobao or "", client.code, wphone, first),
    }
    if st.address_pdd:
        out["address_pdd"] = _fill(st.address_pdd, client.code, wphone, first)
    return out

"""Распознавание штрихкода/QR по фото (ТЗ, 5.4). Нужны pyzbar и системная libzbar0.

Если их нет, функция возвращает None, и бот просит ввести значение вручную.
"""
from __future__ import annotations

import io


def decode_image(data: bytes) -> str | None:
    try:
        from PIL import Image
        from pyzbar.pyzbar import decode
    except Exception:
        return None
    try:
        results = decode(Image.open(io.BytesIO(data)))
    except Exception:
        return None
    for r in results:
        text = r.data.decode("utf-8", "ignore").strip()
        if text:
            return text
    return None


def make_qr_png(text: str) -> bytes:
    import qrcode
    buf = io.BytesIO()
    qrcode.make(text).save(buf, format="PNG")
    return buf.getvalue()

"""Проверка тестового файла склада testdata/sklad_test_20.xlsx на сценарии из docs/LIVE_TEST_CHECKLIST.md.

    python -m cargo_platform.tests.test_live_file

Цифры, которые здесь проверяются, написаны в чек-листе: если тест проходит, то чек-лист верен.
"""
from cargo_platform.tests.harness import World, check  # noqa: I001  (первым: задаёт окружение)
import asyncio
from pathlib import Path

from sqlalchemy import select

from cargo_platform.db.base import Session
from cargo_platform.db.models import Batch, Parcel, ParcelStatus

OWNER = 101
IVAN, AIBEK, NURIA = 201, 202, 203
FILE = Path(__file__).resolve().parent.parent / "testdata" / "sklad_test_20.xlsx"


class Live(World):
    prefixes = {"A": "tst", "B": "kg"}


async def main():
    w = Live()
    await w.setup()
    A, sA = w.cid["A"], w.session["A"]
    await w.register_client("A", IVAN, "Иван Петров", "996555111001")
    await w.register_client("A", AIBEK, "Айбек Уулу", "996555111002")
    await w.register_client("A", NURIA, "Нурия Ж", "996555111003")
    check("TST-0001" in sA.all_text(IVAN) and "TST-0003" in sA.all_text(NURIA), "три клиента: TST-0001 … TST-0003")
    await w.say("A", IVAN, "➕ Добавить трек-номер")
    await w.say("A", IVAN, "TRK10000001 кроссовки")
    await w.say("A", IVAN, "➕ Добавить трек-номер")
    await w.say("A", IVAN, "TRK10000013")       # без кода в файле — найдётся по этому треку

    data = FILE.read_bytes()
    sA.files["live"] = data

    async def upload():
        await w.click("A", OWNER, "a:bnew")
        await w.send("A", OWNER, document=("live", "sklad_test_20.xlsx", data))

    await upload()
    for col in ("0", "1", "2", "3", "4", "-"):
        await w.click("A", OWNER, f"a:mc:{col}")
    rep = sA.last(OWNER)
    check("Найдено: 15 посылок, 3 клиентов, 26,75 кг" in rep, "отчёт: найдено 15 посылок, 3 клиента, 26,75 кг")
    check("Не найдено: 4 строк" in rep, "отчёт: 4 строки не найдены")
    check("стр. 12" in rep and "TST-0099" in rep and "такой код не выдан" in rep, "строка 12 (Excel) — код TST-0099 не выдан")
    check("стр. 13" in rep and "TSZ-0001" in rep and "похоже: TST-0001" in rep, "строка 13 — ошибка в префиксе, подсказка TST-0001")
    check("стр. 15" in rep and "TRK10000014" in rep, "строка 15 — нет кода и трек не добавлен клиентом")
    check("стр. 20" in rep and "TST-0004" in rep, "строка 20 — клиента TST-0004 нет")
    check("Дубли: 0" in rep, "дублей в базе ещё нет")
    check("Объединено строк одного трека (несколько мест): 1" in rep, "TRK10000001 в двух строках → одна посылка, 2 места")
    check("Без веса: 1 посылок" in rep, "одна посылка без веса (строка 16)")
    await w.click("A", OWNER, "a:bs:1")
    check("сохранена" in sA.last(OWNER) or "сохранена" in sA.all_text(OWNER), "партия сохранена")
    china = [t for t in sA.texts(IVAN) if "на складе в Китае" in t][-1]
    check(all(f"TRK100000{n}" in china for n in ("01", "02", "03", "13", "16", "20")) and china.count("<code>") == 6,
          "Иван получил список из 6 треков «на складе в Китае»")
    async with Session() as s:
        p1 = await s.scalar(select(Parcel).where(Parcel.company_id == A, Parcel.track_number == "TRK10000001"))
        p16 = await s.scalar(select(Parcel).where(Parcel.company_id == A, Parcel.track_number == "TRK10000016"))
        p13 = await s.scalar(select(Parcel).where(Parcel.company_id == A, Parcel.track_number == "TRK10000013"))
        nobody = (await s.execute(select(Parcel).where(Parcel.company_id == A, Parcel.client_id.is_(None)))).scalars().all()
    check(p1.places == 2 and str(p1.weight) == "1.80", "TRK10000001: 2 места, вес 1,2 + 0,6 = 1,8")
    check(p16.places == 2, "TRK10000016: 2 места из колонки «мест»")
    check(p13.client_id is not None, "TRK10000013 найден по треку, который добавил Иван")
    check(len(nobody) == 4, "в «Неопознанных» 4 посылки")
    batch_id = (await _batch()).id
    await w.click("A", OWNER, f"a:bsh:{batch_id}")
    await w.click("A", OWNER, f"a:bsha:{batch_id}")
    check("в пути" in sA.all_text(AIBEK), "Айбек получил «в пути»")
    await w.click("A", OWNER, f"a:bar:{batch_id}")
    arrived = {u: [t for t in sA.texts(u) if "прибыли на ПВЗ" in t][-1] for u in (IVAN, AIBEK, NURIA)}
    check("Посылок: 6" in arrived[IVAN] and "Вес: 8,4 кг" in arrived[IVAN] and "К оплате: 2 352 сом" in arrived[IVAN],
          "Иван: 6 посылок, 8,4 кг, 2 352 сом")
    check("Посылок: 5" in arrived[AIBEK] and "Вес: 10,4 кг" in arrived[AIBEK] and "К оплате: 2 912 сом" in arrived[AIBEK],
          "Айбек: 5 посылок, 10,4 кг, 2 912 сом")
    check("Посылок: 4" in arrived[NURIA] and "Вес: 8,0 кг" in arrived[NURIA] and "К оплате: 2 240 сом" in arrived[NURIA],
          "Нурия: 4 посылки, 8,0 кг, 2 240 сом")
    check("Посылок: 19, клиентов: 3, уведомлено: 3" in sA.all_text(OWNER), "владельцу: 19 посылок, 3 клиента, уведомлено 3")
    await upload()
    check("Дубли: 19" in sA.last(OWNER) and "Сохранять нечего" in sA.last(OWNER), "второй раз тот же файл: 19 дублей, повторно не начисляется")
    # тестовые клиенты без Telegram (для проверки в одиночку)
    from cargo_platform.tools.dev_seed import seed
    check(await seed("Карго Альфа", 2) == ["TST-0004", "TST-0005"], "dev_seed: клиенты без Telegram получают следующие коды")
    from cargo_platform.tests.harness import OK
    print(f"\nВсе проверки пройдены: {OK}")


async def _batch():
    async with Session() as s:
        return await s.scalar(select(Batch).order_by(Batch.id.desc()))


if __name__ == "__main__":
    asyncio.run(main())

"""Сквозная проверка всего ТЗ без настоящего Telegram.

    python -m cargo_platform.tests.test_full

Сценарий: две карго → регистрация клиентов → треки → Excel склада → отправка и прибытие партии →
оплата чеком (полная, частичная, отказ) → выдача → правка веса → неопознанные → сообщения →
рассылка → отчёты → пауза за неоплату → напоминания и хранение → потеря Telegram → изоляция карго.
"""
from cargo_platform.tests.harness import World, check, upd  # noqa: I001  (первым: задаёт окружение)
import asyncio
import io
import time
from datetime import timedelta
from decimal import Decimal

from openpyxl import Workbook, load_workbook
from sqlalchemy import select

from cargo_platform.admin_bot import broadcast as bc_module
from cargo_platform.db.base import Session
from cargo_platform.db.models import (
    Batch, Broadcast, Charge, Client, ClientStatus, Company, CompanyStatus, ExcelTemplate, Parcel,
    ParcelStatus, Payment, PaymentStatus, StaffInvite,
)
from cargo_platform.master_bot.common import utcnow
from cargo_platform.services import billing, maintenance, reports
from cargo_platform.services.codes import normalize_code, suggest_codes
from cargo_platform.services.parcels import client_debt, parse_tracks

OWNER, MGR = 101, 301
IVAN, AIBEK, NURIA = 201, 202, 203


def xlsx(rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def parcel(track: str, co_id: int) -> Parcel:
    async with Session() as s:
        return await s.scalar(select(Parcel).where(Parcel.company_id == co_id, Parcel.track_number == track))


async def client_by_code(code: str, co_id: int) -> Client:
    async with Session() as s:
        return await s.scalar(select(Client).where(Client.company_id == co_id, Client.code == code))


def test_pure():
    # расчёт из ТЗ: 4,2 кг × 280 = 1 176 сом
    total, amount = billing.calc_amount([Decimal("1.5"), Decimal("2.7")], Decimal(280), Decimal(1))
    check(total == Decimal("4.2") and amount == 1176, "ТЗ 5.5: 4,2 кг × 280 = 1 176 сом")
    check(billing.calc_amount([Decimal("0.3")], Decimal(280), Decimal(1))[1] == 280, "минимальный вес к оплате")
    check(billing.ceil_tenth(Decimal("4.21")) == Decimal("4.3"), "вес итого округляется вверх до 0,1")
    check(billing.parse_weight("1,25") == Decimal("1.25") and billing.parse_weight("1.25 кг") == Decimal("1.25"),
          "вес: запятая и точка (1,25 = 1,25 кг)")
    check(billing.parse_weight("abc") is None and billing.parse_weight("0") is None, "плохой вес отклонён")
    check(billing.effective_weight(Decimal(2), Decimal(5), 6000) == Decimal(5)
          and billing.effective_weight(Decimal(2), Decimal(5), None) == Decimal(2), "объёмный вес: большее из двух")
    check(billing.volume_weight(Decimal(40), Decimal(30), Decimal(20), 6000) == Decimal("4.00"), "Д×Ш×В/6000")
    now = utcnow()
    check(billing.storage_amount(now - timedelta(days=20), now, 14, Decimal(20)) == 120, "хранение: 6 дней × 20 сом")
    check(billing.storage_amount(now - timedelta(days=10), now, 14, Decimal(20)) == 0, "бесплатный срок хранения")
    check(billing.fmt_money(1176) == "1 176 сом", "формат суммы")
    # коды
    check(normalize_code("osh 123", "OSH") == ("OSH-0123", False), "код «osh 123»")
    check(normalize_code("OSH123", "OSH") == ("OSH-0123", False), "код «OSH123»")
    check(normalize_code("OSH-0123", "OSH") == ("OSH-0123", False), "код «OSH-0123»")
    check(normalize_code("0124", "OSH") == ("OSH-0124", False), "только номер → префикс компании (ТЗ, раздел 9)")
    check(normalize_code("124.0", "OSH") == ("OSH-0124", False), "номер из Excel «124.0»")
    check(normalize_code("OHS-0124", "OSH") == (None, True), "чужой префикс → в «Неопознанные»")
    check("OSH-0124" in suggest_codes("OHS-0124", ["OSH-0124", "OSH-0001"]), "подсказка похожего кода")
    items, bad = parse_tracks("YT0000000001 кроссовки\nYT0000000002, YT0000000003\nabc")
    check([t for t, _ in items] == ["YT0000000001", "YT0000000002", "YT0000000003"]
          and items[0][1] == "кроссовки" and bad == ["abc"], "разбор треков: заметка, запятая, мусор")


async def test_flow():
    w = World()
    await w.setup()
    A, B = w.cid["A"], w.cid["B"]
    sA, sB = w.session["A"], w.session["B"]
    check(w.manager.is_running(A) and w.manager.is_running(B), "две карго подняты")

    # менеджер по приглашению
    await w.master_dp.feed_update(w.master, upd(OWNER, callback="staff:invite"))
    async with Session() as s:
        inv = await s.scalar(select(StaffInvite).where(StaffInvite.company_id == A))
    await w.send("A", MGR, f"/start staff_{inv.token}")

    # ---------- регистрация клиентов ----------
    await w.send("A", IVAN, "/start")
    check("Карго Альфа" in sA.all_text(IVAN) and "Выберите язык" in sA.last(IVAN), "/start: карточка карго и выбор языка")
    await w.send("A", IVAN, callback="clang:ru")
    await w.send("A", IVAN, "Иван Петров")
    await w.send("A", IVAN, contact_phone="996555111001")
    check("город" in sA.last(IVAN).lower(), "после телефона спрашивает город (поле включено по умолчанию)")
    await w.send("A", IVAN, "Ош")
    check("Согласен" in str(sA.sent[-1].reply_markup), "согласие с правилами и запретами")
    await w.send("A", IVAN, callback="reg:agree")
    all_ivan = sA.all_text(IVAN)
    check("OSH-0001" in all_ivan, "клиент получил код OSH-0001")
    check("<code>OSH-0001</code>" in all_ivan, "получатель копируется одним нажатием")
    check("<code>广东省广州市白云区 OSH-0001</code>" in all_ivan, "адрес склада содержит код клиента")
    check("<code>13800000000</code>" in all_ivan, "телефон склада отдельным сообщением")
    check("Без кода в адресе посылку невозможно найти" in all_ivan, "предупреждение про код")
    check("Taobao" in all_ivan and "Pinduoduo" in all_ivan, "инструкция «как вставить адрес»")

    await w.register_client("A", AIBEK, "Айбек Уулу", "996555111002")
    await w.register_client("A", NURIA, "Нурия Ж", "996555111003")
    async with Session() as s:
        codes = [c.code for c in (await s.execute(select(Client).where(Client.company_id == A).order_by(Client.id))).scalars()]
    check(codes == ["OSH-0001", "OSH-0002", "OSH-0003"], "коды идут по порядку без дублей")

    # повторная регистрация
    await w.send("A", 204, "/start")
    await w.send("A", 204, callback="clang:ru")
    await w.send("A", 204, "Другой Человек")
    await w.send("A", 204, "0555 111 001")
    check("OSH-0001" not in sA.last(204) and "уже зарегистрирован" in sA.last(204),
          "чужой номер текстом: код не раскрывается")
    await w.send("A", 205, "/start")
    await w.send("A", 205, callback="clang:ru")
    await w.send("A", 205, "Ещё Один")
    await w.send("A", 205, contact_phone="996555111001")
    check("другому Telegram" in sA.last(205), "свой контакт, но номер у другого Telegram → к менеджеру")
    await w.send("A", IVAN, "/start")
    check("OSH-0001" in sA.last(IVAN), "зарегистрированный видит меню с кодом")
    async with Session() as s:
        n = len((await s.execute(select(Client).where(Client.company_id == A))).scalars().all())
    check(n == 3, "двойной регистрации нет")

    # ---------- треки ----------
    await w.say("A", IVAN, "➕ Добавить трек-номер")
    out = await w.say("A", IVAN, "YT0000000001 кроссовки\nYT0000000002")
    check("Добавлено: 2" in out, "клиент добавил 2 трека")
    await w.say("A", IVAN, "➕ Добавить трек-номер")
    out = await w.say("A", IVAN, "YT0000000001\n123")
    check("Уже были у вас: 1" in out and "Не похоже на трек-номер" in out, "дубль и мусор распознаны")
    await w.say("A", AIBEK, "➕ Добавить трек-номер")
    out = await w.say("A", AIBEK, "YT0000000001")
    check("другим клиентом" in out, "чужой трек не отдаётся второму клиенту")
    await w.say("A", AIBEK, "➕ Добавить трек-номер")
    await w.say("A", AIBEK, "YT0000000003")
    await w.say("A", NURIA, "➕ Добавить трек-номер")
    await w.say("A", NURIA, "YT0000000004")        # потом придёт в файле с ошибочным кодом OHS-0003
    out = await w.say("A", IVAN, "📦 Мои посылки")
    check("YT0000000001" in out and "ожидается" in out and "кроссовки" in out, "«Мои посылки»: статус и название")

    # ---------- Excel склада ----------
    await w.say("A", MGR, "/admin")
    check("Админка" in sA.last(MGR), "менеджер входит в админку")
    await w.click("A", MGR, "a:batches")
    await w.click("A", MGR, "a:bnew")
    rows = [
        ["Код клиента", "Трек", "Вес", "Мест", "Описание"],
        ["osh 1", "YT0000000001", "1,25", 1, "кроссовки"],
        ["OSH0001", "YT0000000002", 3, 1, ""],
        ["OSH-0002", "YT0000000003", "2.5", 1, ""],
        ["OHS-0003", "YT0000000004", 1, 1, ""],
        ["0003", "YT0000000005", 1.4, 1, ""],
        ["", "YT0000000099", 1, 1, ""],
        ["OSH-0099", "YT0000000006", 1, 1, ""],
        ["OSH-0002", "YT0000000003", 1, 1, ""],
        ["OHS-0003", "YT0000000007", 1, 1, ""],
    ]
    data = xlsx(rows)
    sA.files["f1"] = data
    await w.send("A", MGR, document=("f1", "sklad.xlsx", data))
    check("В какой колонке" in sA.last(MGR), "первый файл: бот спрашивает, какая колонка что значит")
    check("A=osh 1" in sA.all_text(MGR) or "A=Код клиента" in sA.all_text(MGR), "показаны первые строки файла")
    for col in ("0", "1", "2", "3", "4", "-"):
        await w.click("A", MGR, f"a:mc:{col}")
    rep = sA.all_text(MGR).split("📊 <b>Разбор файла</b>")[-1]
    check("Найдено: 5 посылок, 3 клиентов" in rep, "отчёт: найдено 5 посылок у 3 клиентов (OHS-0003 найден по треку клиента)")
    check("Не найдено: 3 строк" in rep and "код с ошибкой" in rep, "отчёт: 3 строки не найдены, причина указана")
    check("похоже: OSH-0003" in rep, "для кода OHS-0003 предложен похожий OSH-0003")
    check("такой код не выдан" in rep, "код OSH-0099 помечен как невыданный")
    check("Дубли: 0" in rep and "Объединено строк" in rep, "строки одного трека объединены, дублей нет")
    async with Session() as s:
        tpl = await s.scalar(select(ExcelTemplate).where(ExcelTemplate.company_id == A))
    check(tpl is not None and tpl.column_map["map"]["weight"] == 2 and tpl.column_map["header"] is True,
          "сопоставление колонок сохранено как шаблон склада")
    await w.click("A", MGR, "a:bs:1")
    check("сохранена" in sA.last(MGR) and "неопознанных 3" in sA.all_text(MGR), "партия сохранена, 3 неопознанные")
    check("на складе в Китае" in sA.all_text(IVAN) and "YT0000000001" in sA.all_text(IVAN),
          "клиент уведомлён: посылка на складе в Китае")
    check("Загружена партия" in w.session["A"].all_text(OWNER), "владельцу сообщили об итоге партии")
    p3 = await parcel("YT0000000003", A)
    check(p3.places == 2 and p3.weight == Decimal("3.50"), "одна посылка двумя местами: места = 2, вес суммируется")
    p1 = await parcel("YT0000000001", A)
    check(p1.status == ParcelStatus.china_warehouse and p1.weight == Decimal("1.25"), "трек клиента обновлён, а не задвоен")
    async with Session() as s:
        cnt = len((await s.execute(select(Parcel).where(Parcel.company_id == A, Parcel.track_number == "YT0000000001"))).scalars().all())
    check(cnt == 1, "один трек = одна посылка")
    p5 = await parcel("YT0000000005", A)
    nuria = await client_by_code("OSH-0003", A)
    check(p5.client_id == nuria.id, "«0003» → OSH-0003 (подставлен префикс)")
    check((await parcel("YT0000000004", A)).client_id == nuria.id, "код OHS-0003 с ошибкой, но трек добавлен клиентом → найден по треку")
    unmatched = [t for t in ("YT0000000099", "YT0000000006", "YT0000000007") if (await parcel(t, A)).client_id is None]
    check(len(unmatched) == 3, "3 посылки без клиента лежат в «Неопознанных»")

    # тот же файл второй раз: шаблон угадывается сам, всё — дубли
    sA.files["f2"] = data
    await w.send("A", MGR, document=("f2", "sklad.xlsx", data))   # состояние после отчёта сброшено
    await w.click("A", MGR, "a:bnew")
    await w.send("A", MGR, document=("f2", "sklad.xlsx", data))
    rep2 = sA.last(MGR)
    check("Дубли: 8" in rep2 and "Сохранять нечего" in rep2, "повторный файл: все треки — дубли, повторно не начисляются")
    check("В какой колонке" not in rep2, "со второго раза колонки угаданы по шаблону")

    # ---------- отправка и прибытие ----------
    async with Session() as s:
        batch = await s.scalar(select(Batch).where(Batch.company_id == A))
    await w.click("A", MGR, f"a:bsh:{batch.id}")
    await w.click("A", MGR, f"a:bsha:{batch.id}")
    check("в пути" in sA.all_text(IVAN) and "ожидаем" in sA.all_text(IVAN), "партия отправлена: клиент получил «в пути, ожидаем ~дата»")
    check((await parcel("YT0000000001", A)).status == ParcelStatus.shipped, "статус посылок: отправлено")
    await w.click("A", MGR, f"a:bar:{batch.id}")
    arrival = sA.all_text(IVAN)
    check("OSH-0001, ваши посылки прибыли на ПВЗ" in arrival, "уведомление о прибытии")
    check("Посылок: 2" in arrival and "Вес: 4,3 кг" in arrival and "Тариф: 280 сом/кг" in arrival
          and "К оплате: 1 204 сом" in arrival, "одно сообщение: посылки, вес, тариф, сумма (1 204 сом)")
    check("К оплате: 980 сом" in sA.all_text(AIBEK), "Айбек: 3,5 кг → 980 сом")
    check("К оплате: 672 сом" in sA.all_text(NURIA), "Нурия: 2,4 кг → 672 сом")
    check("Без веса" not in sA.last(MGR), "у всех посылок есть вес")
    async with Session() as s:
        ivan = await s.scalar(select(Client).where(Client.company_id == A, Client.code == "OSH-0001"))
        check(await client_debt(s, A, ivan.id) == 1204, "долг клиента считается из начислений")
    check(any("par:all:0" in str(m.reply_markup) and "pay:start" in str(m.reply_markup)
              for m in sA.sent if getattr(m, "reply_markup", None) and int(m.chat_id) == IVAN),
          "под сообщением кнопки «Оплатить» и «Мои посылки»")

    # ---------- оплата ----------
    out = await w.say("A", IVAN, "💳 К оплате")
    check("К оплате: 1 204 сом" in out, "меню «К оплате» показывает долг")
    await w.click("A", IVAN, "pay:start")
    check(len(sA.photos(IVAN)) == 0 and "MBank" in sA.last(IVAN) and "1 204 сом" in sA.last(IVAN),
          "реквизиты оплаты и сумма (QR-файла нет — текст)")
    await w.send("A", IVAN, photo="receipt1")
    check("Чек получен" in sA.last(IVAN), "клиент отправил чек")
    check(bool(sA.photos(MGR)) and bool(sA.photos(OWNER)), "чек ушёл менеджеру и владельцу с фото")
    async with Session() as s:
        pay1 = await s.scalar(select(Payment).where(Payment.company_id == A).order_by(Payment.id))
    check(pay1.status == PaymentStatus.pending and pay1.amount == 1204, "платёж ждёт подтверждения")
    # частичная оплата
    await w.click("A", MGR, f"pm:part:{pay1.id}")
    await w.say("A", MGR, "1000")
    check("Остаток долга: 204 сом" in sA.last(IVAN), "частичная оплата: остаток 204 сом остаётся в долге")
    async with Session() as s:
        check(await client_debt(s, A, ivan.id) == 204, "долг после частичной оплаты 204")
    check((await parcel("YT0000000001", A)).status == ParcelStatus.arrived, "при остатке долга посылки ещё «к оплате»")
    await w.click("A", MGR, f"pm:ok:{pay1.id}")      # второй менеджер жмёт «Подтвердить» на уже обработанном чеке
    async with Session() as s:
        check(await client_debt(s, A, ivan.id) == 204, "повторный клик не засчитал платёж дважды")
    # вторая оплата с тем же чеком от другого клиента — предупреждение
    await w.click("A", IVAN, "pay:start")
    await w.send("A", IVAN, photo="receipt2")
    async with Session() as s:
        pay2 = (await s.execute(select(Payment).where(Payment.company_id == A, Payment.client_id == ivan.id)
                                .order_by(Payment.id.desc()))).scalars().first()
    await w.click("A", MGR, f"pm:ok:{pay2.id}")
    check("Оплата принята: 204 сом" in sA.all_text(IVAN), "полная оплата принята")
    check((await parcel("YT0000000001", A)).status == ParcelStatus.paid, "долг закрыт → посылки «Оплачено»")
    # повторный чек
    await w.click("A", AIBEK, "pay:start")
    await w.send("A", AIBEK, photo="receipt2")
    warn = [m for m in sA.photos(MGR) if "Такая же картинка" in (m.caption or "")]
    check(bool(warn), "бот предупредил: такая же картинка чека уже присылалась")
    async with Session() as s:
        pay3 = await s.scalar(select(Payment).where(Payment.company_id == A, Payment.client_id != ivan.id))
    await w.click("A", MGR, f"pm:no:{pay3.id}")
    await w.click("A", MGR, f"pm:nr:{pay3.id}:0")
    check("Причина: не та сумма" in sA.all_text(AIBEK), "отклонение с причиной дошло до клиента")
    async with Session() as s:
        check((await s.get(Payment, pay3.id)).status == PaymentStatus.rejected, "платёж отклонён")
        aibek = await s.scalar(select(Client).where(Client.company_id == A, Client.code == "OSH-0002"))
        check(await client_debt(s, A, aibek.id) == 980, "отклонённый платёж долг не меняет")

    # ---------- выдача ----------
    await w.click("A", MGR, "a:issue")
    await w.say("A", MGR, "osh-1")
    check("Оплачено" in sA.last(MGR) and "OSH-0001" in sA.last(MGR), "выдача: карточка клиента, оплачено")
    await w.click("A", MGR, f"a:isa:{ivan.id}")
    check("Выдано посылок: 2" in sA.all_text(IVAN), "клиент получил «Выдано 2 посылки»")
    check((await parcel("YT0000000002", A)).status == ParcelStatus.issued, "статус посылок: выдано")
    # у Айбека долг: сначала наличные
    await w.click("A", MGR, "a:issue")
    await w.say("A", MGR, "OSH0002")
    check("К оплате: <b>980 сом</b>" in sA.last(MGR), "выдача: долг виден менеджеру")
    out = await w.click("A", MGR, f"a:isa:{aibek.id}")
    check("долг" in out.lower(), "при долге «Выдать всё» не срабатывает")
    await w.click("A", MGR, f"a:isc:{aibek.id}")
    async with Session() as s:
        check(await client_debt(s, A, aibek.id) == 0, "оплата наличными закрыла долг")
        cash = await s.scalar(select(Payment).where(Payment.client_id == aibek.id, Payment.method == "cash"))
        check(cash is not None and cash.confirmed_by is not None, "наличные записаны в платежи с менеджером")
    await w.click("A", MGR, f"a:isa:{aibek.id}")
    check((await parcel("YT0000000003", A)).status == ParcelStatus.issued, "после наличных посылки выданы")

    # ---------- правка веса ----------
    p5 = await parcel("YT0000000005", A)
    await w.click("A", MGR, f"a:pcw:{p5.id}")
    await w.say("A", MGR, "4")
    check("Сумма уточнена: теперь к оплате по партии 1 400 сом" in sA.all_text(NURIA), "вес исправлен → клиенту «Сумма уточнена»")
    async with Session() as s:
        nuria = await s.scalar(select(Client).where(Client.company_id == A, Client.code == "OSH-0003"))
        check(await client_debt(s, A, nuria.id) == 1400, "начисление пересчитано")

    # ---------- неопознанные ----------
    p99 = await parcel("YT0000000099", A)
    await w.click("A", MGR, "a:unk")
    check("YT0000000099" in sA.last(MGR), "раздел «Неопознанные» показывает посылки без клиента")
    await w.click("A", MGR, f"a:unkl:{p99.id}")
    await w.say("A", MGR, "OSH-0003")
    p99 = await parcel("YT0000000099", A)
    check(p99.client_id == nuria.id, "ручная привязка неопознанной посылки к коду")
    check("Посылок: 2" in sA.all_text(NURIA) or "прибыли на ПВЗ" in sA.all_text(NURIA), "после привязки клиент получил расчёт")

    # ---------- перенос посылки ----------
    p6 = await parcel("YT0000000006", A)
    await w.click("A", MGR, f"a:pcm:{p6.id}")
    await w.say("A", MGR, "OSH-0002")
    p6 = await parcel("YT0000000006", A)
    check(p6.client_id == aibek.id, "посылка перенесена другому клиенту")
    check("передана посылка" in sA.all_text(AIBEK), "новый владелец получил уведомление")

    # ---------- калькулятор, поиск по треку, информация ----------
    await w.say("A", IVAN, "🧮 Калькулятор")
    out = await w.say("A", IVAN, "0,5")
    check("280 сом" in out and "Минимальный вес" in out, "калькулятор: минимальный вес 1 кг → 280 сом")
    out = await w.say("A", 777, "YT0000000001")
    check("Статус" in out and "Иван" not in out and "сом" not in out, "поиск по треку без регистрации: только статус")
    out = await w.say("A", IVAN, "💰 Тарифы и сроки")
    check("280 сом/кг" in out and "12–18 дней" in out and "14 дн." in out, "тарифы, сроки, хранение")
    out = await w.say("A", IVAN, "⛔ Запрещённые товары")
    check("Батареи" in out, "запрещённые товары из настроек")
    out = await w.say("A", IVAN, "📍 Адрес ПВЗ")
    check("ул. Ленина 1" in out and "Пн–Сб" in out, "адрес и график ПВЗ")

    # ---------- переписка с менеджером ----------
    await w.say("A", IVAN, "💬 Связаться с менеджером")
    await w.say("A", IVAN, "Когда придёт вторая посылка?")
    check("Когда придёт" in sA.all_text(MGR) and f"a:reply:{ivan.id}" in str(sA.reply_buttons(MGR)),
          "вопрос клиента ушёл менеджерам с кнопкой «Ответить»")
    await w.click("A", MGR, "a:msgs")
    check("Когда придёт" in sA.all_text(MGR), "раздел «Сообщения» показывает неотвеченное")
    await w.click("A", MGR, f"a:reply:{ivan.id}")
    await w.say("A", MGR, "Завтра")
    check("Ответ менеджера" in sA.all_text(IVAN) and "Завтра" in sA.all_text(IVAN), "ответ пришёл клиенту в бот")

    # ---------- рассылка ----------
    await w.click("A", MGR, "a:bc")
    await w.click("A", MGR, "a:bcn")
    await w.say("A", MGR, "Акция: скидка 10%")
    await w.click("A", MGR, "a:bcskip")
    await w.click("A", MGR, "a:bca:all")
    check("Получателей: <b>3</b>" in sA.last(MGR), "предпросмотр: число получателей")
    await w.click("A", MGR, "a:bcgo")
    await asyncio.gather(*bc_module.pending_tasks())
    check(all("Акция: скидка 10%" in sA.all_text(u) for u in (IVAN, AIBEK, NURIA)), "рассылка дошла всем клиентам")
    check("Рассылка завершена: доставлено 3" in sA.all_text(MGR), "итог: доставлено 3")
    # заблокировали бота
    sA.blocked.add(NURIA)
    await w.click("A", MGR, "a:bcn")
    await w.say("A", MGR, "Ещё одна")
    await w.click("A", MGR, "a:bcskip")
    await w.click("A", MGR, "a:bca:all")
    await w.click("A", MGR, "a:bcgo")
    await asyncio.gather(*bc_module.pending_tasks())
    check("доставлено 2, не доставлено 1" in sA.all_text(MGR), "итог: доставлено 2, заблокировали бота 1")
    async with Session() as s:
        check((await s.get(Client, nuria.id)).status == ClientStatus.left, "заблокировавший клиент помечен неактивным")
        check(await client_debt(s, A, nuria.id) > 0, "долг неактивного клиента сохранён")
    sA.blocked.discard(NURIA)
    await w.say("A", NURIA, "/start")
    async with Session() as s:
        check((await s.get(Client, nuria.id)).status == ClientStatus.active, "написал снова — снова активен")
    # лимит «Старт»: 4 рассылки в месяц
    async with Session() as s:
        (await s.get(Company, A)).tariff = "start"
        await s.commit()
    await w.click("A", MGR, "a:bcn")
    await w.say("A", MGR, "Третья")
    await w.click("A", MGR, "a:bcskip")
    await w.click("A", MGR, "a:bca:all")
    await w.click("A", MGR, "a:bcgo")
    await asyncio.gather(*bc_module.pending_tasks())
    await w.click("A", MGR, "a:bcn")
    await w.say("A", MGR, "Четвёртая")
    await w.click("A", MGR, "a:bcskip")
    await w.click("A", MGR, "a:bca:all")
    await w.click("A", MGR, "a:bcgo")
    await asyncio.gather(*bc_module.pending_tasks())
    out = await w.click("A", MGR, "a:bcn")
    check("не больше 4" in out, "тариф «Старт»: пятая рассылка в месяце отклонена")
    async with Session() as s:
        (await s.get(Company, A)).tariff = "trial"
        await s.commit()
    # запланированная
    async with Session() as s:
        s.add(Broadcast(company_id=A, text="По расписанию", audience={"type": "all", "by": MGR},
                        scheduled_at=utcnow() - timedelta(minutes=1), status="scheduled"))
        await s.commit()
    started = await maintenance.run_scheduled_broadcasts(w.manager, utcnow())
    await asyncio.gather(*bc_module.pending_tasks())
    check(started == 1 and "По расписанию" in sA.all_text(IVAN), "запланированная рассылка ушла в срок")
    check(await maintenance.run_scheduled_broadcasts(w.manager, utcnow()) == 0, "и повторно не запускается")

    # ---------- отчёты и выгрузка ----------
    await w.click("A", MGR, "a:rp:1")
    check("Принято на ПВЗ" in sA.last(MGR) and "Долги сейчас" in sA.last(MGR), "отчёт за день")
    await w.click("A", MGR, "a:rpx:7")
    check(len(sA.documents(MGR)) == 1, "отчёт выгружен в Excel")
    await w.click("A", MGR, "a:export")
    doc = sA.documents(MGR)[-1].document
    wb = load_workbook(io.BytesIO(doc.data))
    check({"Клиенты", "Посылки", "Начисления", "Платежи"} <= set(wb.sheetnames), "выгрузка: клиенты, посылки, начисления, платежи")
    check(wb["Клиенты"].max_row == 4, "в выгрузке 3 клиента")
    await w.click("A", MGR, "a:audit")
    check("payment_confirm" in sA.last(MGR) and "parcel_weight" in sA.last(MGR), "журнал действий: кто что изменил")
    await w.click("A", MGR, "a:debt")
    check("OSH-0003" in sA.last(MGR) and "1 680 сом" in sA.last(MGR), "раздел «Должники»")

    # ---------- карточки клиента и посылки ----------
    await w.click("A", MGR, f"a:cl:{nuria.id}")
    check("Нурия" in sA.last(MGR) and "Долг: 1 680 сом" in sA.last(MGR), "карточка клиента: данные, посылки, долг")
    await w.click("A", MGR, f"a:clp:{nuria.id}")
    await w.say("A", MGR, "250")
    async with Session() as s:
        ch = (await s.execute(select(Charge).where(Charge.client_id == nuria.id))).scalars().all()
    check(sum(c.amount for c in ch) == 1500, "индивидуальная цена 250 сом/кг пересчитала начисление (6 кг)")
    await w.click("A", MGR, f"a:clb:{nuria.id}")
    out = await w.say("A", NURIA, "🏷 Мой код и адрес")
    check("Доступ ограничен" in out, "заблокированный клиент не пользуется ботом")
    await w.click("A", MGR, f"a:clb:{nuria.id}")
    await w.click("A", MGR, f"a:clu:{nuria.id}")
    async with Session() as s:
        check((await s.get(Client, nuria.id)).tg_id is None, "отвязка Telegram при потере аккаунта")
    await w.send("A", 213, "/start")
    await w.send("A", 213, callback="clang:ru")
    await w.send("A", 213, "Нурия Ж")
    await w.send("A", 213, contact_phone="996555111003")
    check("уже зарегистрированы" in sA.last(213) and "OSH-0003" in sA.last(213), "новый аккаунт с тем же номером получил прежний код")
    async with Session() as s:
        check((await s.get(Client, nuria.id)).tg_id == 213, "новый tg_id привязан")
        check(len((await s.execute(select(Client).where(Client.company_id == A))).scalars().all()) == 3, "второй код не выдан")

    # ---------- напоминания и хранение ----------
    async with Session() as s:
        (await s.get(Company, A)).paid_until = utcnow() + timedelta(days=30)
        await s.commit()
    later = utcnow() + timedelta(days=3)
    await maintenance.run_once(w.manager, now=later)
    check("Напоминание" in sA.all_text(213), "через 2 дня после прибытия — напоминание об оплате")
    n = sA.all_text(213).count("Напоминание")
    await maintenance.run_once(w.manager, now=later)
    check(sA.all_text(213).count("Напоминание") == n, "напоминание не уходит дважды")
    await maintenance.run_once(w.manager, now=utcnow() + timedelta(days=12))
    check("закончится бесплатное хранение" in sA.all_text(213), "за 2 дня до конца бесплатного хранения — предупреждение")
    await maintenance.run_once(w.manager, now=utcnow() + timedelta(days=20))
    async with Session() as s:
        ch = (await s.execute(select(Charge).where(Charge.client_id == nuria.id))).scalars().all()
        check(sum(c.storage_amount for c in ch) >= 100, "платное хранение начислено после бесплатного срока")
    # отчёт дня
    reports.local_now = maintenance.reports.local_now = lambda: (utcnow() + timedelta(hours=6)).replace(hour=21)
    await maintenance.run_once(w.manager, now=utcnow() + timedelta(days=21))
    check("Отчёт дня" in w.session["A"].all_text(OWNER), "владелец получил отчёт дня")

    # ---------- пауза за неоплату ----------
    async with Session() as s:
        (await s.get(Company, A)).status = CompanyStatus.paused
        await s.commit()
    out = await w.say("A", IVAN, "🏷 Мой код и адрес")
    check("временно недоступен" in out, "после льготных дней клиент видит заглушку")
    m_before = len(sA.texts(MGR))
    await w.click("A", MGR, "a:batches")
    check(len(sA.texts(MGR)) == m_before, "при паузе менеджер не может работать с партиями")
    await w.say("A", MGR, "/admin")
    check("доступна только выгрузка" in sA.last(MGR), "при паузе админка — только выгрузка данных")
    d_before = len(sA.documents(MGR))
    await w.click("A", MGR, "a:export")
    check(len(sA.documents(MGR)) == d_before + 1, "выгрузка данных при паузе работает")
    async with Session() as s:
        (await s.get(Company, A)).status = CompanyStatus.active
        await s.commit()

    # ---------- вторая карго: изоляция ----------
    await w.register_client("B", IVAN, "Иван Петров", "996555111001")     # тот же человек у другой карго
    check("KG-0001" in sB.all_text(IVAN), "у второй карго свои коды (KG-0001)")
    await w.say("B", IVAN, "➕ Добавить трек-номер")
    out = await w.say("B", IVAN, "YT0000000001")
    check("Добавлено: 1" in out, "тот же трек в другой карго — отдельная запись")
    out = await w.say("B", 778, "YT0000000002")
    check("не найдена" in out, "посылки одной карго не видны в другой")
    out = await w.say("B", IVAN, "📦 Мои посылки")
    check("YT0000000002" not in out, "клиент B не видит посылки A")
    await w.click("B", MGR, "a:batches")
    check("Партии" not in sB.all_text(MGR), "менеджер A не имеет доступа в админку B")

    # ---------- смена бота: клиентам старого бота уходит ссылка на новый ----------
    from cargo_platform.master_bot import registration as reg_module
    new_token = "444444:" + "C" * 35
    await w.master_dp.feed_update(w.master, upd(102, callback="bot:token"))
    await w.master_dp.feed_update(w.master, upd(102, new_token))
    await asyncio.gather(*reg_module._TASKS)
    check("t.me/cargo3_bot" in sB.all_text(IVAN), "смена бота: клиент старого бота получил ссылку на нового")
    check(w.manager.is_running(B) and w.manager._bots[B][0].token == new_token, "после смены токена работает новый бот")
    async with Session() as s:
        check(len((await s.execute(select(Client).where(Client.company_id == B))).scalars().all()) >= 1, "клиенты и данные не потерялись")

    # ---------- платформа ----------
    await w.master_dp.feed_update(w.master, upd(999, "🛠 Платформа"))
    await w.master_dp.feed_update(w.master, upd(999, callback="plat:stats"))
    stats = w.master_session.last(999)
    check("Клиентов: 4" in stats, "статистика платформы: только количества")
    await w.master_dp.feed_update(w.master, upd(999, f"/purge {A}"))
    check("Удалять нельзя" in w.master_session.last(999), "данные работающего карго не удаляются")
    await w.master_dp.feed_update(w.master, upd(555, "/purge 1"))
    check(not w.master_session.texts(555), "чужому /purge недоступен")

    # ---------- производительность: Excel на 200 строк ----------
    big = [["Код", "Трек", "Вес"]] + [[f"OSH-{(i % 3) + 1:04d}", f"BIG{i:08d}", 1.5] for i in range(200)]
    t0 = time.time()
    data = xlsx(big)
    sA.files["big"] = data
    await w.click("A", MGR, "a:bnew")
    await w.send("A", MGR, document=("big", "big.xlsx", data))
    rep = sA.last(MGR)
    check("Найдено: 200 посылок" in rep and time.time() - t0 < 30, "Excel на 200 строк разобран быстрее 30 секунд, все строки найдены")
    await w.click("A", MGR, "a:bs:0")
    check("Уведомления не отправлялись" in sA.last(MGR) or "сохранена" in sA.last(MGR), "сохранение без уведомлений")


async def test_codes_concurrent():
    """Два клиента не получают один код даже при одновременной регистрации."""
    from cargo_platform.services.codes import issue_code
    async with Session() as s:
        company = await s.scalar(select(Company).where(Company.name == "Карго Бета"))

    async def one(i):
        async with Session() as s:
            code = await issue_code(s, company.id)
            s.add(Client(company_id=company.id, code=code, full_name=f"Тест {i}", phone=f"+99655599{i:04d}"))
            await s.commit()
            return code

    got = await asyncio.gather(*(one(i) for i in range(10)))
    check(len(set(got)) == 10, "10 одновременных регистраций — 10 разных кодов")


async def main():
    test_pure()
    await test_flow()
    await test_codes_concurrent()
    from cargo_platform.tests.harness import OK
    print(f"\nВсе проверки пройдены: {OK}")


if __name__ == "__main__":
    asyncio.run(main())

"""Настоящий HTTP-вход webhook: секрет, чужие адреса, ответ боту. Telegram подделан, сервер aiohttp — настоящий.

    python -m cargo_platform.tests.test_webhook
"""
from cargo_platform.tests.harness import World, check  # noqa: I001  (первым: задаёт окружение)
import asyncio

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def update_json(user_id: int, text: str) -> dict:
    user = {"id": user_id, "is_bot": False, "first_name": "U"}
    return {"update_id": 1, "message": {"message_id": 1, "date": 1, "text": text,
                                        "chat": {"id": user_id, "type": "private"}, "from": user}}


async def main():
    w = World()
    await w.setup()
    A, B = w.cid["A"], w.cid["B"]
    app = web.Application()
    app.router.add_post("/bot/{company_id}", w.manager.handle)
    app.router.add_get("/health", lambda _: web.Response(text="ok"))
    async with TestClient(TestServer(app)) as client:
        r = await client.get("/health")
        check(r.status == 200 and await r.text() == "ok", "/health отвечает ok")
        secret_a = w.manager._bots[A][1]
        r = await client.post(f"/bot/{A}", json=update_json(900, "/start"), headers={SECRET_HEADER: secret_a})
        check(r.status == 200, "webhook с верным секретом принят")
        await asyncio.gather(*list(w.manager._tasks))
        check("Карго Альфа" in w.session["A"].all_text(900), "бот ответил на обновление, пришедшее по HTTP")
        r = await client.post(f"/bot/{A}", json=update_json(900, "/start"), headers={SECRET_HEADER: "wrong"})
        check(r.status == 401, "неверный секрет → 401")
        r = await client.post(f"/bot/{A}", json=update_json(900, "/start"))
        check(r.status == 401, "без секрета → 401")
        r = await client.post(f"/bot/{B}", json=update_json(900, "/start"), headers={SECRET_HEADER: secret_a})
        check(r.status == 401, "секрет карго A не подходит к боту карго B")
        r = await client.post("/bot/9999", json=update_json(900, "/start"), headers={SECRET_HEADER: secret_a})
        check(r.status == 404, "несуществующая карго → 404")
        r = await client.post("/bot/abc", json=update_json(900, "/start"))
        check(r.status == 404, "мусор вместо номера → 404")
    from cargo_platform.tests.harness import OK
    print(f"\nВсе проверки пройдены: {OK}")


if __name__ == "__main__":
    asyncio.run(main())

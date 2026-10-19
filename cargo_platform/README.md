# Карго-конструктор ботов — неделя 1

Одна программа держит главный бот платформы и боты всех карго-компаний.
Карго регистрируется в главном боте, присылает токен своего бота, проходит
мастер настройки — и его бот сразу работает. Подробно — в ТЗ.

## Что готово (неделя 1 по ТЗ)

- Сервер, база (все таблицы из раздела 8), запуск ботов по webhook.
- Главный бот: регистрация карго, проверка полей, подключение и смена токена,
  мастер настройки из 13 шагов, правка любого поля из меню.
- Меню владельца: компания, мой бот, настройки, менеджеры (ссылка-приглашение
  на 24 часа), подписка, поддержка.
- Раздел «Платформа» для вас: список карго, `/extend`, `/block`, `/unblock`.
- Подписки: напоминание за 3 дня, 3 льготных дня, потом пауза бота клиентов.
- Бот карго: карточка компании для клиентов, заглушка при неоплате,
  приём менеджеров по ссылке, вход в `/admin`.
- Токены зашифрованы, webhook защищён секретом, ошибка одного бота не роняет остальных.

Дальше: неделя 2 — регистрация клиентов, коды и адреса; неделя 3 — Excel и партии;
неделя 4 — оплата и выдача.

## Структура

```
cargo_platform/
  main.py                 # запуск сервера
  config.py               # настройки из .env
  texts.py                # тексты ru / ky (ky — проверить носителю)
  db/models.py            # таблицы
  services/bot_manager.py # запуск ботов карго, приём webhook
  services/subscriptions.py
  services/validators.py  # проверки полей
  services/crypto.py      # шифрование токенов
  master_bot/             # главный бот
  client_bot/handlers.py  # бот карго
  tests/test_week1.py     # 35 проверок без Telegram
```

## Запуск на MacBook (для теста)

Telegram отправляет обновления только на HTTPS-адрес, поэтому на ноутбуке
нужен туннель. Проще всего — cloudflared.

```bash
# 1. Папка проекта: положите cargo_platform в любую папку, например ~/cargo
cd ~/cargo
python3 -m venv venv && source venv/bin/activate
pip install -r cargo_platform/requirements.txt

# 2. Ключ шифрования
python -m cargo_platform.tools.genkey      # скопируйте строку в FERNET_KEY

# 3. Туннель (в отдельном окне терминала)
brew install cloudflared
cloudflared tunnel --url http://localhost:8080
#   он покажет адрес вида https://xxxx.trycloudflare.com → это BASE_URL

# 4. Настройки
cp cargo_platform/.env.example cargo_platform/.env
#   заполните MASTER_BOT_TOKEN, BASE_URL, FERNET_KEY, MASTER_WEBHOOK_SECRET, PLATFORM_ADMINS

# 5. Запуск
python -m cargo_platform.main
```

Адрес trycloudflare меняется при каждом запуске туннеля: обновите BASE_URL и
перезапустите программу, она сама переставит webhook всех ботов.

## Проверка

```bash
python -m cargo_platform.tests.test_week1
```

## Запуск на сервере (VPS, Ubuntu)

1. VPS от 2 ГБ RAM, домен, направленный на сервер.
2. Установить Docker и nginx (или Caddy) с HTTPS от Let's Encrypt,
   проксировать `https://домен` → `http://127.0.0.1:8080`.
3. Скопировать папку `cargo_platform` на сервер, заполнить `.env`
   (BASE_URL = `https://домен`, задать POSTGRES_PASSWORD).
4. `docker compose up -d --build`
5. Проверка: `curl https://домен/health` → `ok`.

**Бэкап базы каждую ночь** (crontab -e):

```
0 3 * * * cd /путь/cargo_platform && docker compose exec -T db pg_dump -U cargo cargo | gzip > /backup/cargo_$(date +\%F).sql.gz && find /backup -name 'cargo_*.sql.gz' -mtime +14 -delete
```

Копию папки /backup отправляйте в отдельное облако — не храните бэкап только на том же сервере.

## Важно

- `FERNET_KEY` не терять и не менять: без него токены ботов не расшифровать.
- Файл `.env` никогда не загружать в GitHub.
- Состояния диалогов пока в памяти: при перезапуске человек, который был
  посреди регистрации, начнёт шаг заново через /start. При росте — Redis.

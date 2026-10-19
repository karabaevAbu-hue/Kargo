# Запуск на MacBook для живого теста — шаг за шагом

Рассчитано на человека, который давно не работал в терминале. Каждая команда — отдельный блок:
**скопируйте её целиком, вставьте в Терминал (⌘V) и нажмите Enter.** Дождитесь, пока команда закончит
(снова появится строка с `%` или `$`), и только потом вставляйте следующую.

Время: 30–40 минут в первый раз. Нужно: MacBook с интернетом, Telegram на телефоне, аккаунт GitHub с доступом к репозиторию.

> **Как открыть Терминал:** нажмите ⌘ + Пробел, напишите `Терминал` (или `Terminal`), нажмите Enter.
> Окно можно сделать больше. Понадобится **три окна Терминала** одновременно: ⌘ + N открывает новое окно.

---

## Шаг 1. Homebrew (установщик программ для Mac)

Проверьте, не установлен ли он уже:

```bash
brew --version
```

Если написало `Homebrew 4.x.x` — переходите к шагу 2. Если `command not found: brew` — установите:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

- Попросит пароль от Mac (при вводе символы не видны — это нормально) и нажать Enter для продолжения.
- Может предложить установить «Command Line Tools» — соглашайтесь, это 5–10 минут.
- В конце Homebrew напечатает блок **«Next steps»** с двумя командами `echo ... >> ~/.zprofile` и `eval ...`.
  **Выполните именно их** (скопируйте из вашего окна). На Mac с чипом Apple (M1/M2/M3/M4) они выглядят так:

```bash
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
```

(На старых Mac с процессором Intel путь будет `/usr/local/bin/brew` вместо `/opt/homebrew/bin/brew`.)
Проверка:

```bash
brew --version
```

## Шаг 2. Python

```bash
brew install python@3.12
```

Проверка (должно показать `Python 3.12.x`):

```bash
python3.12 --version
```

## Шаг 3. Docker (в нём будет работать база данных PostgreSQL)

```bash
brew install --cask docker
```

Запустите программу Docker:

```bash
open -a Docker
```

- Откроется окно Docker Desktop. Примите условия (Accept), вход в аккаунт можно пропустить (Skip).
- Подождите 1–2 минуты, пока в левом нижнем углу окна значок станет **зелёным** (Engine running).
- Проверка:

```bash
docker --version
docker info
```

`docker info` должен напечатать много строк, а не ошибку `Cannot connect to the Docker daemon`.
Если ошибка — Docker ещё запускается, подождите минуту и повторите.

> Не хотите Docker? Есть упрощённый вариант на SQLite (без PostgreSQL) — он отмечен ниже пометкой **«Без Docker»**.
> Но для настоящей проверки лучше PostgreSQL: именно он будет на сервере.

## Шаг 4. cloudflared (туннель, чтобы Telegram мог достучаться до вашего Mac)

Telegram присылает сообщения ботам только на адрес с HTTPS из интернета. На ноутбуке такого адреса нет,
поэтому cloudflared делает временный публичный адрес, который ведёт на ваш Mac.

```bash
brew install cloudflared
cloudflared --version
```

## Шаг 5. Скачать проект

Нужна папка `~/cargo` (то есть `cargo` в вашей домашней папке) с проектом.

**Вариант А — через GitHub CLI (удобно, потом легко обновлять):**

```bash
brew install gh
gh auth login
```

Ответы на вопросы: `GitHub.com` → `HTTPS` → `Y` (authenticate Git) → `Login with a web browser`.
Скопируйте код из терминала, нажмите Enter, в браузере вставьте код и разрешите доступ.

```bash
gh auth setup-git
git clone --branch claude/new-session-s9zibw https://github.com/karabaevAbu-hue/Kargo.git ~/cargo
```

Чтобы позже получить свежую версию: `cd ~/cargo && git pull`.

**Вариант Б — без терминала: ZIP-архив.**
Откройте в браузере `https://github.com/karabaevAbu-hue/Kargo`, слева сверху выберите ветку
`claude/new-session-s9zibw`, нажмите зелёную кнопку **Code → Download ZIP**. Архив распакуется в «Загрузки».
Затем:

```bash
ls ~/Downloads | grep Kargo
```

Команда покажет имя папки, например `Kargo-claude-new-session-s9zibw`. Переименуйте её (подставьте своё имя):

```bash
mv ~/Downloads/Kargo-claude-new-session-s9zibw ~/cargo
```

Проверка — должна показать список с `cargo_platform`:

```bash
ls ~/cargo
```

## Шаг 6. Виртуальное окружение Python и установка библиотек

Виртуальное окружение (venv) — отдельная «коробка» с библиотеками проекта, чтобы ничего не ломать в системе.

```bash
cd ~/cargo
python3.12 -m venv venv
source venv/bin/activate
```

В начале строки терминала появится `(venv)` — значит окружение включено.
Теперь библиотеки (займёт 1–2 минуты):

```bash
pip install --upgrade pip
pip install -r cargo_platform/requirements.txt
```

> **Важно.** `source venv/bin/activate` нужно выполнять **в каждом новом окне Терминала** перед работой с проектом:
> сначала `cd ~/cargo`, затем `source venv/bin/activate`.

## Шаг 7. Два бота в Telegram и ваш Telegram ID

Откройте в Telegram [@BotFather](https://t.me/BotFather).

1. Отправьте `/newbot` → имя, например `Platform Test` → username, например `abu_platform_test_bot`
   (должен заканчиваться на `bot` и быть свободным). BotFather пришлёт **токен** — строку вида `123456789:AAH...`.
   Это токен **главного бота платформы**. Сохраните его в заметки.
2. Второго бота (бот самого карго) создадите позже, прямо по ходу чек-листа — так бывает у настоящего владельца карго.
3. Откройте [@userinfobot](https://t.me/userinfobot), нажмите Start — он покажет ваш **Id** (число, например `123456789`).
   Это ваш Telegram ID, вы будете владельцем платформы.

## Шаг 8. База данных

**С Docker (рекомендуется):**

```bash
cd ~/cargo
docker compose -f cargo_platform/docker-compose.dev.yml up -d
```

Проверка — в колонке STATUS должно быть `healthy` (через 5–10 секунд):

```bash
docker compose -f cargo_platform/docker-compose.dev.yml ps
```

**Без Docker:** этот шаг пропустите, а в шаге 10 добавьте `--sqlite` (подсказка там же).

## Шаг 9. Туннель (окно Терминала №1)

Откройте **новое окно** Терминала (⌘ + N) и выполните:

```bash
cloudflared tunnel --url http://localhost:8080
```

Через несколько секунд в тексте появится адрес вида `https://слово-слово-слово.trycloudflare.com`.
**Скопируйте его** (он понадобится в следующем шаге) и **не закрывайте это окно** — пока оно открыто, туннель работает.

> Адрес меняется при каждом перезапуске туннеля. Это нормально, см. «Если адрес сменился» ниже.

## Шаг 10. Файл настроек `.env`

Вернитесь в **первое окно** Терминала (где `(venv)`):

```bash
cd ~/cargo
source venv/bin/activate
python -m cargo_platform.tools.make_env
```

*(Без Docker: `python -m cargo_platform.tools.make_env --sqlite`.)*

Скрипт задаст 4 вопроса — вставляйте ответы и жмите Enter:

| Вопрос | Что ввести |
|---|---|
| Токен главного бота | токен из шага 7 |
| Ваш Telegram ID | число из @userinfobot |
| Адрес туннеля | адрес из шага 9, целиком, с `https://` |
| Ваш username для «Поддержка» | свой username без `@` или просто Enter |

Ключи шифрования скрипт создаст сам. **Файл `.env` никому не отправляйте и не загружайте в GitHub.**

## Шаг 11. Запуск

В первом окне (где `(venv)`):

```bash
cd ~/cargo
sh cargo_platform/tools/dev_run.sh
```

Должно появиться «Готово: схема базы актуальна.», затем строка вида
`... Сервер запущен: https://ваш-адрес.trycloudflare.com`. Окно **не закрывайте** — в нём работает сервер.
Все действия ботов видны в этом окне; если что-то сломается, здесь будет красный текст с ошибкой.

Проверка, что всё связано правильно: откройте в браузере `https://ваш-адрес.trycloudflare.com/health`
— страница должна показать слово **ok**.

Теперь откройте вашего главного бота в Telegram (по username из шага 7), нажмите **Start** — он должен
предложить выбрать язык. Если так, установка завершена, переходите к
[`LIVE_TEST_CHECKLIST.md`](LIVE_TEST_CHECKLIST.md).

**Чтобы Mac не засыпал во время теста** (в третьем окне Терминала):

```bash
caffeinate -d
```

Закрыть: Ctrl + C в том же окне.

---

## Повседневные операции

**Остановить сервер:** в окне сервера нажмите **Ctrl + C**.

**Запустить снова:** окно Терминала → `cd ~/cargo` → `source venv/bin/activate` → `sh cargo_platform/tools/dev_run.sh`.
Данные (клиенты, посылки, настройки) сохраняются в базе, ботов заново создавать не нужно.

**Если адрес туннеля сменился** (вы перезапустили cloudflared или Mac перезагрузился):
1. В окне туннеля скопируйте новый адрес.
2. В окне, где работает проект, остановите сервер (Ctrl + C) и выполните:

```bash
python -m cargo_platform.tools.make_env --base-url https://НОВЫЙ-АДРЕС.trycloudflare.com
sh cargo_platform/tools/dev_run.sh
```

Сервер сам переставит webhook у всех ботов.

**Начать всё с нуля** (стереть все тестовые данные): остановите сервер (Ctrl + C), затем:

```bash
docker compose -f cargo_platform/docker-compose.dev.yml down -v
docker compose -f cargo_platform/docker-compose.dev.yml up -d
sh cargo_platform/tools/dev_run.sh
```

Ботов в BotFather удалять не нужно: при новой регистрации карго просто пришлите тот же токен.
*(Без Docker: удалите файл командой `rm cargo.db` вместо первых двух команд.)*

**Выключить базу данных после теста:**

```bash
docker compose -f cargo_platform/docker-compose.dev.yml stop
```

**Автотесты** (по желанию; проверяют код без Telegram):

```bash
python -m cargo_platform.tests.run_all
```

В конце должно быть `ВСЕ ТЕСТЫ ПРОЙДЕНЫ`.

---

## Если что-то не работает

| Что видите | Что делать |
|---|---|
| `command not found: brew` | Шаг 1 не завершён: выполните команды из блока «Next steps» Homebrew и откройте новое окно Терминала |
| `command not found: python3.12` | `brew install python@3.12`, затем новое окно Терминала |
| `Cannot connect to the Docker daemon` | Docker не запущен: `open -a Docker`, подождите зелёный значок |
| `No such file or directory: venv/bin/activate` | Вы не в папке проекта или venv не создан: `cd ~/cargo`, затем команды шага 6 |
| В начале строки нет `(venv)` | `cd ~/cargo` и `source venv/bin/activate` |
| `Заполните MASTER_BOT_TOKEN, BASE_URL и FERNET_KEY в .env` | Не создан `.env`: шаг 10 |
| `База не обновлена ... tools.migrate` | `python -m cargo_platform.tools.migrate` (или просто запускайте через `dev_run.sh`) |
| `Connection refused` / `5432` | База не запущена: шаг 8; проверьте `docker compose -f cargo_platform/docker-compose.dev.yml ps` |
| `address already in use` / порт 8080 занят | Остался старый сервер. `lsof -i :8080` покажет процесс, `kill ЧИСЛО_PID` остановит его |
| Бот молчит, а в терминале тишина | Туннель остановлен или адрес сменился → «Если адрес сменился»; проверьте `/health` в браузере |
| `/health` открывается, но бот молчит | Токен главного бота в `.env` неверный: перезапустите `make_env`, затем сервер |
| В логе `Unauthorized` у бота карго | Токен бота карго отозван в BotFather — пришлите новый (владелец увидит просьбу в главном боте) |
| `.env` потерян / сломан | `make_env` создаст заново, **но тогда токены ботов из старой базы не расшифруются** — сотрите базу («Начать с нуля») и зарегистрируйте карго снова |

**Что прислать мне при проблеме:** скриншот из Telegram и последние ~30 строк из окна сервера
(выделите мышью, ⌘C). Токены и содержимое `.env` присылать не нужно.

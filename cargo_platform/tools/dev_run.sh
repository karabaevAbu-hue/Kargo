#!/bin/sh
# Запуск для теста на ноутбуке: активирует venv, применяет миграции и стартует сервер.
#   sh cargo_platform/tools/dev_run.sh
set -eu
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
[ -d venv ] || { echo "Нет папки venv. Выполните шаг «Создать виртуальное окружение» из docs/MACBOOK_SETUP.md"; exit 1; }
. venv/bin/activate
[ -f cargo_platform/.env ] || { echo "Нет файла cargo_platform/.env. Выполните: python -m cargo_platform.tools.make_env"; exit 1; }
python -m cargo_platform.tools.migrate
exec python -m cargo_platform.main

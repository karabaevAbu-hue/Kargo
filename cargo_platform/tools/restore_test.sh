#!/bin/sh
# Раз в месяц: пробное восстановление из последнего бэкапа во ВРЕМЕННУЮ базу (рабочую не трогает).
set -eu
cd "$(dirname "$0")/.."
BACKUP_DIR="${BACKUP_DIR:-/backup}"
FILE="$(ls -1t "$BACKUP_DIR"/cargo_*.sql.gz | head -1)"
echo "Восстанавливаю $FILE во временную базу cargo_restore_test"
docker compose exec -T db psql -U cargo -d postgres -c "DROP DATABASE IF EXISTS cargo_restore_test"
docker compose exec -T db psql -U cargo -d postgres -c "CREATE DATABASE cargo_restore_test"
gunzip -c "$FILE" | docker compose exec -T db psql -U cargo -d cargo_restore_test -q > /dev/null
CLIENTS="$(docker compose exec -T db psql -U cargo -d cargo_restore_test -tAc 'select count(*) from clients')"
COMPANIES="$(docker compose exec -T db psql -U cargo -d cargo_restore_test -tAc 'select count(*) from companies')"
docker compose exec -T db psql -U cargo -d postgres -c "DROP DATABASE cargo_restore_test"
echo "Бэкап восстанавливается: карго $COMPANIES, клиентов $CLIENTS"

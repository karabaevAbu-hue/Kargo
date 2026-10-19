#!/bin/sh
# Ночной бэкап (ТЗ, раздел 10): pg_dump всей базы, хранить 14 дней, копия в отдельное облако.
# crontab:  0 3 * * * /путь/cargo_platform/tools/backup.sh >> /var/log/cargo_backup.log 2>&1
# Копия в облако: настройте rclone (rclone config) и задайте RCLONE_REMOTE, например "gdrive:cargo-backups".
set -eu
cd "$(dirname "$0")/.."
BACKUP_DIR="${BACKUP_DIR:-/backup}"
mkdir -p "$BACKUP_DIR"
FILE="$BACKUP_DIR/cargo_$(date +%F).sql.gz"
docker compose exec -T db pg_dump -U cargo cargo | gzip > "$FILE"
[ -s "$FILE" ] || { echo "ПУСТОЙ БЭКАП: $FILE" >&2; rm -f "$FILE"; exit 1; }
find "$BACKUP_DIR" -name 'cargo_*.sql.gz' -mtime +14 -delete
if [ -n "${RCLONE_REMOTE:-}" ]; then
  rclone copy "$FILE" "$RCLONE_REMOTE"
else
  echo "ВНИМАНИЕ: RCLONE_REMOTE не задан — копии в облако нет, бэкап лежит только на этом сервере" >&2
fi
echo "ok $FILE"

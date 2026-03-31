#!/bin/bash
# GateKeep Backup Script
# Backs up PostgreSQL metadata and Elasticsearch indices to Azure Blob Storage

set -euo pipefail

BACKUP_DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/tmp/gatekeep-backup-${BACKUP_DATE}"
AZURE_CONTAINER="gatekeep-backups"

echo "=== GateKeep Backup - ${BACKUP_DATE} ==="

mkdir -p "${BACKUP_DIR}"

# PostgreSQL backup
echo "Backing up PostgreSQL..."
PG_HOST="${PG_HOST:-localhost}"
PG_USER="${PG_USER:-gatekeep}"
PG_DB="${PG_DB:-gatekeep}"

PGPASSWORD="${POSTGRES_PASSWORD}" pg_dump \
  -h "${PG_HOST}" \
  -U "${PG_USER}" \
  -d "${PG_DB}" \
  -F c \
  -f "${BACKUP_DIR}/gatekeep_db_${BACKUP_DATE}.dump"

echo "PostgreSQL backup complete: $(du -h "${BACKUP_DIR}/gatekeep_db_${BACKUP_DATE}.dump" | cut -f1)"

# Elasticsearch snapshot
echo "Creating Elasticsearch snapshot..."
ES_URL="${ELASTICSEARCH_URL:-http://localhost:9200}"
ES_USER="${ELASTICSEARCH_USER:-elastic}"
ES_PASS="${ELASTIC_PASSWORD}"

SNAPSHOT_NAME="gatekeep_snapshot_${BACKUP_DATE}"

curl -s -X PUT "${ES_URL}/_snapshot/gatekeep_backups" \
  -u "${ES_USER}:${ES_PASS}" \
  -H 'Content-Type: application/json' \
  -d '{
    "type": "fs",
    "settings": { "location": "/usr/share/elasticsearch/backups" }
  }' 2>/dev/null || true

curl -s -X PUT "${ES_URL}/_snapshot/gatekeep_backups/${SNAPSHOT_NAME}?wait_for_completion=true" \
  -u "${ES_USER}:${ES_PASS}" \
  -H 'Content-Type: application/json' \
  -d '{ "include_global_state": false }' 2>/dev/null || true

echo "Elasticsearch snapshot: ${SNAPSHOT_NAME}"

# Upload to Azure Blob Storage
if [ -n "${AZURE_STORAGE_CONNECTION_STRING:-}" ]; then
    echo "Uploading to Azure Blob Storage..."
    az storage blob upload-batch \
        --destination "${AZURE_CONTAINER}" \
        --source "${BACKUP_DIR}" \
        --connection-string "${AZURE_STORAGE_CONNECTION_STRING}" \
        --pattern "*.dump" \
        --overwrite

    echo "Upload complete."
else
    echo "AZURE_STORAGE_CONNECTION_STRING not set. Backups remain at: ${BACKUP_DIR}"
fi

# Cleanup old local backups (keep last 7 days)
find /tmp -name "gatekeep-backup-*" -type d -mtime +7 -exec rm -rf {} + 2>/dev/null || true

echo "=== Backup complete ==="

#!/usr/bin/env bash
set -e

cd "$(dirname "$0")/.."

# Load environment variables if .env exists
if [ -f .env ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ ! "$line" =~ ^# ]] && [[ -n "$line" ]]; then
      export "$line"
    fi
  done < .env
fi

if [ -z "$DATABASE_URL" ]; then
  echo "Error: DATABASE_URL is not set."
  exit 1
fi

# The target staging DB name
STAGING_DB_NAME="wellring_staging"

# Parse the original database name from DATABASE_URL
# Example: postgresql://wellring_user:pass@localhost:5432/wellring
BASE_URL="${DATABASE_URL%/*}"
ORIGINAL_DB_NAME="${DATABASE_URL##*/}"

STAGING_URL="${BASE_URL}/${STAGING_DB_NAME}"

echo "Base URL: $BASE_URL"
echo "Creating staging database: $STAGING_DB_NAME..."

# 1. Terminate any connections to the staging DB if it exists and drop it to ensure a clean slate
psql "$BASE_URL/postgres" -c "
  SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$STAGING_DB_NAME';
" || true

psql "$BASE_URL/postgres" -c "DROP DATABASE IF EXISTS $STAGING_DB_NAME;"
psql "$BASE_URL/postgres" -c "CREATE DATABASE $STAGING_DB_NAME;"

echo "Dumping schema from $ORIGINAL_DB_NAME..."
SCHEMA_TMP=$(mktemp /tmp/wellring_XXXXXX.sql)   # mode-600, unguessable filename
pg_dump "$DATABASE_URL" --schema-only --no-owner --no-privileges -f "$SCHEMA_TMP"

echo "Restoring schema to $STAGING_DB_NAME..."
psql "$STAGING_URL" -f "$SCHEMA_TMP"

rm -f "$SCHEMA_TMP"

echo "Staging database setup complete."
echo "You can now connect using STAGING_DATABASE_URL=$STAGING_URL"

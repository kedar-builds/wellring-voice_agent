#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status
set -e

# Change to the directory where the script is located, then go to project root
cd "$(dirname "$0")/.."

# Load environment variables if .env exists
if [ -f .env ]; then
  # Read .env file line by line, ignoring comments and empty lines
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ ! "$line" =~ ^# ]] && [[ -n "$line" ]]; then
      export "$line"
    fi
  done < .env
fi

# Ensure backups directory exists
mkdir -p backups

# Generate timestamp
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="backups/wellring_backup_${TIMESTAMP}.sql"

# Check if DATABASE_URL is set
if [ -z "$DATABASE_URL" ]; then
  echo "Error: DATABASE_URL is not set. Please set it in .env or your environment."
  exit 1
fi

echo "Starting backup of database..."

# Run pg_dump
# We use --clean to add DROP TABLE statements, and --if-exists to avoid errors on drop
pg_dump "$DATABASE_URL" \
  --clean \
  --if-exists \
  --no-owner \
  --no-privileges \
  --file="$BACKUP_FILE"

echo "Backup successful: $BACKUP_FILE"

# Optional: Keep only the last 7 days of backups
echo "Cleaning up old backups (older than 7 days)..."
find backups/ -name "wellring_backup_*.sql" -type f -mtime +7 -delete

echo "Done."

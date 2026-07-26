#!/bin/bash

OLD_DB_URL="postgresql://wellring_db_user:IMGRW79IoWOVXBEAbjXW4XZI9jNA9YAD@dpg-d8qqfvh194ac7398is80-a.ohio-postgres.render.com/wellring_db"
NEW_DB_URL="postgresql://wellring_user:9DhfJwTap8uzHpbh5l3pDqotcsDIUK7R@dpg-d9icqiurnols73f2u9i0-a.ohio-postgres.render.com/wellring"

echo "Attempting to dump the old database..."
if pg_dump "$OLD_DB_URL" -Fc -f wellring_db_backup.dump; then
    echo "Dump successful! Restoring to the new database..."
    if pg_restore -d "$NEW_DB_URL" --clean --if-exists --no-owner --no-acl wellring_db_backup.dump; then
        echo "Migration completed successfully!"
    else
        echo "Failed to restore to the new database."
        exit 1
    fi
else
    echo "--------------------------------------------------------"
    echo "Failed to connect to the old database."
    echo "Since it is expired, Render has likely completely suspended access."
    echo "To fix this, you need to:"
    echo "1. Go to the Render dashboard."
    echo "2. Temporarily upgrade the old database to a paid tier to regain access, OR check if they provide a snapshot download."
    echo "3. Run this script again once access is restored."
    echo "--------------------------------------------------------"
    exit 1
fi

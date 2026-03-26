#!/bin/bash
set -e

# Wait for PostgreSQL to be ready
until pg_isready -h "$HOST" -U "$USER" -q; do
  echo "Waiting for PostgreSQL..."
  sleep 2
done

# Check if database needs initialization
DB_EXISTS=$(psql -h "$HOST" -U "$USER" -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" 2>/dev/null || echo "")
DB_INITIALIZED=$(psql -h "$HOST" -U "$USER" -d "$DB_NAME" -tAc "SELECT 1 FROM information_schema.tables WHERE table_name='ir_module_module'" 2>/dev/null || echo "")

if [ "$DB_INITIALIZED" != "1" ]; then
  echo "Initializing database '$DB_NAME' with base module..."
  odoo -i base --stop-after-init
  echo "Database initialized successfully."
fi

# Start Odoo normally
exec odoo "$@"

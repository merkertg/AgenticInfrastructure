#!/bin/sh
# Runs once, when the database volume is created. Creates the service group
# role (migration 0006 grants its privileges) and the login role the API uses.
# The password is read inside psql from the mounted secret, never from argv.
set -eu
psql --set ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\set app_password `cat /run/secrets/app_db_password`
CREATE ROLE agentic_memory_app NOLOGIN;
CREATE ROLE agentic_app LOGIN PASSWORD :'app_password' IN ROLE agentic_memory_app;
SQL

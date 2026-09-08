#!/usr/bin/env bash
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#
# Start PostgreSQL and create the databases the repro needs.
# Idempotent: safe to re-run after the docker daemon dies, which in a
# sandboxed container it tends to do between sessions.
set -euo pipefail

# dockerd dies when the sandbox restarts and leaves its pidfile behind,
# which then blocks the next start. Clear it if no daemon is actually up.
if ! pgrep -x dockerd >/dev/null; then
  rm -f /var/run/docker.pid
  dockerd >/tmp/dockerd.log 2>&1 &
  for _ in $(seq 30); do
    docker info >/dev/null 2>&1 && break
    sleep 2
  done
fi
docker info >/dev/null 2>&1 || { echo "dockerd failed to start; see /tmp/dockerd.log" >&2; exit 1; }

if ! docker inspect pg-superset >/dev/null 2>&1; then
  docker run -d --name pg-superset \
    -e POSTGRES_PASSWORD=sspw -e POSTGRES_USER=ssuser -e POSTGRES_DB=superset \
    -p 55432:5432 postgres:17
else
  docker start pg-superset >/dev/null
fi

for i in $(seq 30); do
  docker exec pg-superset pg_isready -U ssuser >/dev/null 2>&1 && break
  sleep 2
done

psql() { docker exec -e PGPASSWORD=sspw pg-superset psql -U ssuser "$@"; }

# One metadata database per Superset version under test, plus the database
# the queries actually run against.
for d in supersetmaster superset60 superset61 analytics; do
  psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$d'" \
    | grep -q 1 || psql -d postgres -c "CREATE DATABASE $d"
done

# Two schemas so the grant under test is narrower than the database.
psql -d analytics -c "
CREATE SCHEMA IF NOT EXISTS sales;
CREATE TABLE IF NOT EXISTS sales.orders (id int, amount numeric, region text);
CREATE SCHEMA IF NOT EXISTS hr;
CREATE TABLE IF NOT EXISTS hr.staff (id int, name text);
"
psql -d analytics -tAc "SELECT count(*) FROM sales.orders" | grep -q '^0$' && \
  psql -d analytics -c "INSERT INTO sales.orders VALUES (1,10.5,'north'),(2,22.0,'south')"

echo "postgres ready on 127.0.0.1:55432"

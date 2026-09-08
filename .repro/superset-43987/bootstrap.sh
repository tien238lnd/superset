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
# Build one runnable Superset instance at a given git ref, for reproducing
# https://github.com/apache/superset/issues/43987
#
#   ./bootstrap.sh <git-ref> <workdir> <metadata-db-name>
#   ./bootstrap.sh 1b3758d /home/user/ss-master supersetmaster
#   ./bootstrap.sh 6.0.0   /home/user/ss60      superset60
#
# Assumes ./pg.sh has already been run. Each ref needs its OWN metadata
# database: Alembic migrations are forward-only, so pointing 6.0.0 at a
# database master has migrated will fail.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REF="${1:?git ref}"; WORKDIR="${2:?workdir}"; METADB="${3:?metadata db name}"
UPSTREAM="${UPSTREAM_CLONE:-/home/user/superset-master}"
CONF="$WORKDIR/_conf"

if [ ! -d "$UPSTREAM/.git" ]; then
  echo "Cloning apache/superset into $UPSTREAM (full history, needed for old refs)"
  git clone https://github.com/apache/superset.git "$UPSTREAM"
fi
git -C "$UPSTREAM" fetch -q origin "$REF" 2>/dev/null || git -C "$UPSTREAM" fetch -q origin

if [ ! -d "$WORKDIR" ]; then
  git -C "$UPSTREAM" worktree add -f "$WORKDIR" "$REF"
fi

python3 -m venv "$WORKDIR/.venv"
"$WORKDIR/.venv/bin/pip" install -q --upgrade pip
# requirements/base.txt carries "-e ./superset-core", a path relative to the
# repo root, so pip has to run from inside the worktree.
(
  cd "$WORKDIR"
  ./.venv/bin/pip install -q -r requirements/base.txt
  ./.venv/bin/pip install -q --no-deps -e .
  ./.venv/bin/pip install -q psycopg2-binary
)

mkdir -p "$CONF"
sed "s/__METADB__/$METADB/" "$HERE/superset_config.py.tmpl" \
  > "$CONF/superset_config.py"

export PYTHONPATH="$CONF"
"$WORKDIR/.venv/bin/superset" db upgrade
"$WORKDIR/.venv/bin/superset" fab create-admin \
  --username admin --firstname A --lastname D \
  --email admin@example.invalid --password adminpw || true
"$WORKDIR/.venv/bin/superset" init

echo
echo "Built $REF at $WORKDIR (metadata db: $METADB, config: $CONF)"
echo "Next: PYTHONPATH=$CONF $WORKDIR/.venv/bin/python setup_fixtures.py"

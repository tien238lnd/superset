<!--
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
-->

# Repro harness for apache/superset#43987

Rebuilds the evidence behind
[apache/superset#43987](https://github.com/apache/superset/issues/43987):
Explore is denied for a SQL Lab query when the user holds only `schema_access`
(or `catalog_access`).

Local fixture tooling, not a contribution to upstream Superset — it lives in a
fork branch so the measurements can be reproduced if maintainers push back and
the original throwaway container is gone.

## Rebuild

```bash
./pg.sh                                                    # postgres + databases
./bootstrap.sh 1b3758d /home/user/ss-master supersetmaster # ~10 min per ref
PYTHONPATH=/home/user/ss-master/_conf /home/user/ss-master/.venv/bin/python setup_fixtures.py
PYTHONPATH=/home/user/ss-master/_conf /home/user/ss-master/.venv/bin/python probe.py
```

For the cross-version table, build each ref into its own worktree and its own
metadata database (Alembic is forward-only, so they cannot share one):

```bash
./bootstrap.sh 6.0.0 /home/user/ss60 superset60   # last release before #36245
./bootstrap.sh 6.1.0 /home/user/ss61 superset61   # first release after it
```

For the end-to-end HTTP form of the repro, start a server against the same
metadata database and run `repro_http.py`:

```bash
cd /home/user/ss-master && PYTHONPATH=$PWD/_conf ./.venv/bin/gunicorn \
    -w 2 -k gthread --threads 8 -t 120 -b 127.0.0.1:8088 "superset.app:create_app()" &
/home/user/ss-master/.venv/bin/python repro_http.py
```

## Expected output

`probe.py`, on master `1b3758d`:

| control | `can_access_schema(query)` | explore |
|---|---|---|
| A `schema_access` only, not author | False | **DENIED** |
| B \+ `database_access` | False | PASS |
| C \+ `catalog_access` | False | **DENIED** |
| D `schema_access` only, **is** author | False | PASS |
| E \+ the malformed `Query.schema_perm` string | False | **DENIED** |

On `6.0.0` (before the `Explorable` refactor #36245) B, C and E all PASS and
`can_access_schema(query)` returns True for them — that function had no
`isinstance(datasource, BaseDatasource)` gate and worked on a `Query` by duck
typing. E passing there is the proof that the `schema_access` arm was wired up
and only ever failed because `Query.schema_perm` emits `db.schema` while
`get_schema_perm()` emits `[db].[catalog].[schema]`.

`repro_http.py` on an affected version: step A returns **201**, step B **403**.

## Gotchas

- **Schema permissions are catalog-qualified** on engines that support catalogs
  (SIP-95). Granting `[db].[schema]` instead of `[db].[catalog].[schema]`
  silently grants nothing, and the repro then fails with a *different* error
  (`TABLE_SECURITY_ACCESS_ERROR`) that looks like a false negative.
  `setup_fixtures.py` derives the right form via `get_default_catalog()`.
- **The query must be authored by someone else.** Since #42479 and #42590 the
  author passes via `is_editor()` and the authorship bypass, so the bug is
  invisible if the test user wrote the query. Control D shows that path.
- **The published `apache/superset:master` image is stale** — built from a March
  2024 commit — and cannot reproduce this. Build from source.
- **`requirements/base.txt` contains `-e ./superset-core`**, a path relative to
  the repo root, so pip has to run from inside the worktree.
- **The SQL Lab execute API returns `client_id`, not `Query.id`.** The explore
  endpoints want the numeric id; `repro_http.py` reads it from postgres.
- **`dockerd` does not survive a sandbox restart** and leaves a stale
  `/var/run/docker.pid` that blocks the next start. `pg.sh` clears it.
  Postgres data lives in the container's writable layer with no volume, so an
  unclean daemon kill can lose it — re-run `bootstrap.sh` for the affected ref.

## Files

| file | what it does |
|---|---|
| `pg.sh` | starts postgres, creates metadata databases and the queried `analytics` database with two schemas |
| `bootstrap.sh` | worktree + venv + install + migrate + init, for one git ref |
| `superset_config.py.tmpl` | throwaway config; `__METADB__` substituted per instance |
| `setup_fixtures.py` | database connection, `schema_only_role`, `test_user` / `query_author`, one SUCCESS query |
| `probe.py` | in-process probe: prints every condition the decision turns on, runs controls A–E |
| `repro_http.py` | end-to-end over HTTP: the two requests behind the "Create chart" button |

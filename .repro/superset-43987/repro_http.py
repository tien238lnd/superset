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
"""End-to-end HTTP reproduction of
https://github.com/apache/superset/issues/43987

Drives the two requests the "Create chart" button makes, as ``test_user``:

    A  POST /api/v1/explore/form_data   -> expected 201
    B  GET  /api/v1/explore/            -> expected 403 on affected versions

The split is the point: step A calls raise_for_access(query=...) only and
never reaches the fall-through, step B also passes datasource=.

Needs a server running against the same metadata database:

    PYTHONPATH=<workdir>/_conf <workdir>/.venv/bin/gunicorn \
        -w 2 -k gthread --threads 8 -t 120 -b 127.0.0.1:8088 \
        "superset.app:create_app()"

    <workdir>/.venv/bin/python repro_http.py [base-url]
"""

import shutil
import subprocess
import sys

import requests

from superset.utils import json

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8088"
DOCKER = shutil.which("docker") or "/usr/bin/docker"


def login(username: str, password: str) -> requests.Session:
    session = requests.Session()
    resp = session.post(
        f"{BASE}/login/",
        data={"username": username, "password": password},
        allow_redirects=False,
    )
    assert resp.status_code in (200, 302), (resp.status_code, resp.text[:300])
    return session


def latest_query_id() -> tuple[int, str, str, str]:
    """Read it from postgres: the SQL Lab execute API returns client_id, not
    the numeric Query.id the explore endpoints want."""
    for metadb in ("supersetmaster", "superset61", "superset60", "superset"):
        try:
            row = (
                subprocess.check_output(  # noqa: S603
                    [
                        DOCKER,
                        "exec",
                        "-e",
                        "PGPASSWORD=sspw",
                        "pg-superset",
                        "psql",
                        "-U",
                        "ssuser",
                        "-d",
                        metadb,
                        "-tAc",
                        "SELECT id||','||user_id||','||status FROM query "
                        "ORDER BY id DESC LIMIT 1",
                    ],
                    stderr=subprocess.DEVNULL,
                )
                .decode()
                .strip()
            )
        except subprocess.CalledProcessError:
            continue
        if row:
            qid_, author_uid_, status_ = row.split(",")
            return int(qid_), author_uid_, status_, metadb
    raise SystemExit("no Query row found -- run setup_fixtures.py first")


qid, author_uid, status, metadb = latest_query_id()
print(f"query id {qid} | user_id {author_uid} | status {status} | metadata db {metadb}")

session = login("test_user", "pw12345")
me = session.get(f"{BASE}/api/v1/me/", headers={"Accept": "application/json"}).json()
print(
    "acting as:",
    me.get("result", {}).get("username"),
    "id",
    me.get("result", {}).get("id"),
)

form_data = {
    "datasource": f"{qid}__query",
    "viz_type": "table",
    "query_mode": "raw",
    "all_columns": ["id", "amount", "region"],
    "row_limit": 1000,
}
step_a = session.post(
    f"{BASE}/api/v1/explore/form_data",
    json={
        "datasource_id": qid,
        "datasource_type": "query",
        "form_data": json.dumps(form_data),
    },
    headers={"Content-Type": "application/json"},
)
print(f"\n--- STEP A: POST /api/v1/explore/form_data -> {step_a.status_code}")
print(step_a.text[:800])

url = f"{BASE}/api/v1/explore/?datasource_id={qid}&datasource_type=query"
if step_a.status_code < 400:
    url += f"&form_data_key={step_a.json()['key']}"
step_b = session.get(url, headers={"Accept": "application/json"})
print(f"\n--- STEP B: GET /api/v1/explore/ -> {step_b.status_code}")
print(step_b.text[:1200])

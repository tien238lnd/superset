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
"""In-process probe for https://github.com/apache/superset/issues/43987.

Runs the authorization check the way THIS checkout's Explore GET runs it,
and prints each condition the decision turns on, so the same script can be
pointed at master and at older releases and the answers compared.

    PYTHONPATH=<workdir>/_conf <workdir>/.venv/bin/python probe.py

Controls, each toggling one variable and reverting it:

  A  schema_access only, not the query's author   -- the reported bug
  B  + database_access                            -- early-returns in the query branch
  C  catalog_access instead of schema_access      -- same failure as A
  D  schema_access only, IS the author            -- authorship bypass (master only)
  E  grant the malformed string Query.schema_perm emits (pre-refactor only)
"""

import traceback

from superset.app import create_app

app = create_app()
with app.app_context():
    from flask import g

    from superset import db, security_manager as sm
    from superset.models.core import Database
    from superset.models.sql_lab import Query

    dbo = db.session.query(Database).filter_by(database_name="analytics_db").one()
    query = db.session.query(Query).order_by(Query.id.desc()).first()
    if query is None:
        raise SystemExit("no Query row -- run setup_fixtures.py first")
    role = sm.find_role("schema_only_role")
    catalog = query.catalog or dbo.get_default_catalog()

    # Releases before the Explorable refactor (#36245) have no
    # _authorize_datasource; their GetExploreCommand calls
    # raise_for_access(datasource=...) directly.
    try:
        from superset.commands.explore.get import (  # type: ignore[attr-defined]
            _authorize_datasource,
        )

        MODE = "master: _authorize_datasource(query=, datasource=, bypass=True)"

        def explore_check(q: Query) -> None:
            _authorize_datasource(q, None)

    except ImportError:
        MODE = "pre-#36245: raise_for_access(datasource=...) only"

        def explore_check(q: Query) -> None:
            sm.raise_for_access(datasource=q)

    def report(label: str, show_traceback: bool = False) -> None:
        with app.test_request_context("/api/v1/explore/"):
            g.user = sm.find_user(username="test_user")
            print(f"\n[{label}]")
            print(
                "   can_access_database          :",
                sm.can_access_database(query.database),
            )
            print("   can_access_schema(query)     :", sm.can_access_schema(query))
            print(
                "   can_access(schema_access,gsp):",
                sm.can_access(
                    "schema_access",
                    sm.get_schema_perm(dbo.database_name, catalog, query.schema) or "",
                ),
            )
            is_editor = getattr(sm, "is_editor", None)
            print(
                "   is_editor(query)             :",
                is_editor(query)
                if is_editor
                else "N/A (method absent in this version)",
            )
            try:
                explore_check(query)
                print("   >>> EXPLORE: PASS")
            except Exception as exc:  # noqa: BLE001 - reporting, not handling
                print("   >>> EXPLORE: DENIED ->", exc)
                if show_traceback:
                    print(traceback.format_exc())

    def with_grant(permission: str, view_menu: str, label: str, **kw: bool) -> None:
        pvm = sm.add_permission_view_menu(permission, view_menu)
        db.session.commit()
        role.permissions.append(pvm)
        db.session.commit()
        try:
            report(label, **kw)
        finally:
            role.permissions.remove(sm.find_permission_view_menu(permission, view_menu))
            db.session.commit()

    print("MODE:", MODE)
    print(
        f"query id {query.id} | user_id {query.user_id} | status {query.status} "
        f"| schema {query.schema} | catalog {query.catalog}"
    )
    print("query.perm        =", query.perm)
    print("query.schema_perm =", query.schema_perm)
    print(
        "get_schema_perm() =",
        sm.get_schema_perm(dbo.database_name, catalog, query.schema),
    )

    report("A: schema_access only, NOT the author", show_traceback=True)
    with_grant("database_access", dbo.perm, "B: + database_access, NOT the author")
    with_grant(
        "catalog_access",
        sm.get_catalog_perm(dbo.database_name, catalog) or "",
        "C: + catalog_access, NOT the author",
    )

    test_user = sm.find_user(username="test_user")
    original_author = query.user_id
    query.user_id = test_user.id
    db.session.commit()
    try:
        report("D: schema_access only, IS the author")
    finally:
        query.user_id = original_author
        db.session.commit()

    # Query.schema_perm emits "db.schema"; get_schema_perm emits
    # "[db].[catalog].[schema]". Granting the malformed string proves the
    # format mismatch is the only thing stopping the schema_access arm --
    # visible on releases whose can_access_schema still reads schema_perm
    # for a Query (i.e. before the isinstance gate landed in #36245).
    with_grant(
        "schema_access",
        query.schema_perm,
        "E: + the MALFORMED string Query.schema_perm emits",
    )

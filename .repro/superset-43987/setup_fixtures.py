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
"""Fixtures for https://github.com/apache/superset/issues/43987.

Creates, idempotently:

* database connection ``analytics_db`` -> the ``analytics`` postgres database
* role ``schema_only_role``: Gamma + sql_lab permissions, MINUS every
  database/datasource/schema/catalog grant, PLUS ``schema_access`` on exactly
  one schema
* ``test_user`` (that role) and ``query_author`` (Admin)
* one SUCCESS ``Query`` row against that schema, authored by ``query_author``

Run with the target version's interpreter and PYTHONPATH:

    PYTHONPATH=<workdir>/_conf <workdir>/.venv/bin/python setup_fixtures.py
"""

from superset.app import create_app

SCHEMA = "sales"
SQL = "SELECT id, amount, region FROM sales.orders"

app = create_app()
with app.app_context():
    import uuid

    from superset import db, security_manager as sm
    from superset.common.db_query_status import QueryStatus
    from superset.models.core import Database
    from superset.models.sql_lab import Query

    uri = "postgresql+psycopg2://ssuser:sspw@127.0.0.1:55432/analytics"
    dbo = (
        db.session.query(Database).filter_by(database_name="analytics_db").one_or_none()
    )
    if not dbo:
        dbo = Database(database_name="analytics_db", sqlalchemy_uri=uri)
        dbo.set_sqlalchemy_uri(uri)
        dbo.expose_in_sqllab = True
        db.session.add(dbo)
        db.session.commit()

    # On an engine with catalogs (SIP-95) the schema permission is
    # catalog-qualified. Granting the 2-part "[db].[schema]" form instead
    # silently grants nothing -- the repro then fails with a *different*
    # error (TABLE_SECURITY_ACCESS_ERROR) and looks like a false negative.
    default_catalog = dbo.get_default_catalog()
    schema_perm = sm.get_schema_perm(dbo.database_name, default_catalog, SCHEMA)
    sm.add_permission_view_menu("schema_access", schema_perm)
    sm.add_permission_view_menu("database_access", dbo.perm)
    db.session.commit()

    BLOCK = {
        "all_database_access",
        "all_datasource_access",
        "all_query_access",
        "catalog_access",
        "database_access",
        "datasource_access",
        "schema_access",
    }
    pvms, seen = [], set()
    for role_name in ("Gamma", "sql_lab"):
        role_ = sm.find_role(role_name)
        if not role_:
            continue
        for pvm in role_.permissions:
            if pvm.permission.name in BLOCK or pvm.id in seen:
                continue
            seen.add(pvm.id)
            pvms.append(pvm)
    pvms.append(sm.find_permission_view_menu("schema_access", schema_perm))

    role = sm.add_role("schema_only_role")
    role.permissions = pvms
    db.session.commit()

    for username, admin in (("query_author", True), ("test_user", False)):
        roles = [sm.find_role("Admin")] if admin else [role]
        user = sm.find_user(username=username)
        if not user:
            sm.add_user(
                username,
                username,
                "T",
                f"{username}@example.invalid",
                roles[0],
                password="pw12345",  # noqa: S106 - throwaway fixture user
            )
            user = sm.find_user(username=username)
        user.roles = roles
        db.session.commit()

    author = sm.find_user(username="query_author")
    query = Query(
        client_id=uuid.uuid4().hex[:10],
        database_id=dbo.id,
        user_id=author.id,
        sql=SQL,
        select_sql=SQL,
        executed_sql=SQL,
        schema=SCHEMA,
        catalog=default_catalog,
        tab_name="repro tab",
        status=QueryStatus.SUCCESS,
        limit=1000,
        select_as_cta=False,
    )
    db.session.add(query)
    db.session.commit()

    print("fixtures ready")
    print(f"  database          : {dbo.database_name} (id {dbo.id})")
    print(f"  default catalog   : {default_catalog}")
    print(f"  schema grant      : {schema_perm}")
    print(
        f"  role database_access      : "
        f"{any(p.permission.name == 'database_access' for p in role.permissions)}"
    )
    print(
        f"  role all_datasource_access: "
        f"{any(p.permission.name == 'all_datasource_access' for p in role.permissions)}"
    )
    print(
        f"  query id {query.id} authored by user_id {query.user_id} "
        f"(test_user is {sm.find_user(username='test_user').id})"
    )

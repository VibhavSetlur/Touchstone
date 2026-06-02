"""MongoDB connector.

Mongo is the odd one out — it's not SQL. We accept a JSON-encoded operation
spec as the `sql` argument so the connector interface stays uniform. The
profiler and differ know how to construct these specs.

Spec shape:
    {"op": "find", "collection": "users", "filter": {...}, "limit": 100}
    {"op": "aggregate", "collection": "orders", "pipeline": [...]}
    {"op": "count", "collection": "orders", "filter": {...}}
    {"op": "list_collections"}
    {"op": "schema", "collection": "orders", "sample": 100}
"""

from __future__ import annotations

import json
import sys
from typing import Any

from touchstone.connectors.base import Connector
from touchstone.secrets import resolve
from touchstone.types import (
    Column,
    ConnectorAuthError,
    ConnectorError,
    Engine,
    QueryResult,
    TableRef,
)


class MongoDBConnector(Connector):
    engine = Engine.MONGODB

    def connect(self) -> None:
        if self._connection is not None:
            return
        try:
            from pymongo import MongoClient
        except ImportError as e:
            raise ConnectorError(
                "pymongo not installed. Install with: pip install 'touchstone-core[mongodb]'"
            ) from e

        uri = self.config.extra.get("uri")
        if uri:
            if self.config.password_ref:
                password = resolve(self.config.password_ref)
                uri = uri.replace("__PASSWORD__", password)
            client = MongoClient(uri, serverSelectionTimeoutMS=10_000,
                                 socketTimeoutMS=self.config.timeout_seconds * 1000)
        else:
            password = resolve(self.config.password_ref) if self.config.password_ref else None
            client = MongoClient(
                host=self.config.host,
                port=self.config.port or 27017,
                username=self.config.user,
                password=password,
                serverSelectionTimeoutMS=10_000,
                socketTimeoutMS=self.config.timeout_seconds * 1000,
                appname="touchstone",
            )

        try:
            client.admin.command("ping")
        except Exception as e:
            raise ConnectorAuthError(_scrub(str(e))) from None

        self._connection = client

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> QueryResult:
        if self._connection is None:
            self.connect()
        try:
            spec = json.loads(sql) if isinstance(sql, str) else sql
        except json.JSONDecodeError as e:
            raise ConnectorError(f"MongoDB connector expects JSON spec; got SQL? {e}") from None

        op = spec.get("op")
        db = self._connection[self.config.database or "test"]

        if op == "list_collections":
            names = db.list_collection_names()
            return QueryResult(
                columns=[Column(name="collection", type="string")],
                rows=[(n,) for n in names],
                row_count=len(names),
                engine=Engine.MONGODB,
                sql_executed=sql,
            )

        coll = db[spec["collection"]]
        rows: list[tuple[Any, ...]] = []
        bytes_returned = 0

        try:
            if op == "find":
                cursor = coll.find(spec.get("filter", {}), limit=min(
                    spec.get("limit", self.config.row_cap), self.config.row_cap
                ))
                docs = list(cursor)
                if not docs:
                    return QueryResult(columns=[], rows=[], row_count=0,
                                       engine=Engine.MONGODB, sql_executed=sql)
                # Flatten doc keys into columns — use union of keys.
                keys = sorted({k for d in docs for k in d})
                columns = [Column(name=k, type="any") for k in keys]
                for d in docs:
                    row = tuple(d.get(k) for k in keys)
                    rows.append(row)
                    bytes_returned += sys.getsizeof(row)
                return QueryResult(
                    columns=columns, rows=rows, row_count=len(rows),
                    bytes_returned=bytes_returned, engine=Engine.MONGODB, sql_executed=sql,
                )

            if op == "aggregate":
                cursor = coll.aggregate(spec.get("pipeline", []))
                docs = list(cursor)[: self.config.row_cap]
                if not docs:
                    return QueryResult(columns=[], rows=[], row_count=0,
                                       engine=Engine.MONGODB, sql_executed=sql)
                keys = sorted({k for d in docs for k in d})
                columns = [Column(name=k, type="any") for k in keys]
                rows = [tuple(d.get(k) for k in keys) for d in docs]
                return QueryResult(
                    columns=columns, rows=rows, row_count=len(rows),
                    engine=Engine.MONGODB, sql_executed=sql,
                )

            if op == "count":
                n = coll.count_documents(spec.get("filter", {}))
                return QueryResult(
                    columns=[Column(name="count", type="int")],
                    rows=[(n,)], row_count=1,
                    engine=Engine.MONGODB, sql_executed=sql,
                )

            raise ConnectorError(f"unknown Mongo op: {op!r}")

        except Exception as e:
            raise ConnectorError(_scrub(str(e))) from None

    def list_tables(self, schema: str | None = None) -> list[TableRef]:
        if self._connection is None:
            self.connect()
        db = self._connection[schema or self.config.database or "test"]
        return [TableRef(name=n, schema=db.name) for n in db.list_collection_names()]

    def describe_table(self, table: TableRef) -> list[Column]:
        """Infer schema by sampling. Mongo has no fixed schema."""
        if self._connection is None:
            self.connect()
        db = self._connection[table.schema or self.config.database or "test"]
        sample = list(db[table.name].find().limit(100))
        if not sample:
            return []
        types: dict[str, str] = {}
        for d in sample:
            for k, v in d.items():
                t = type(v).__name__
                types.setdefault(k, t)
                if types[k] != t:
                    types[k] = "mixed"
        return [Column(name=k, type=t, nullable=True) for k, t in sorted(types.items())]


def _scrub(msg: str) -> str:
    import re
    return re.sub(r"//[^:]+:[^@]+@", "//***:***@", msg)

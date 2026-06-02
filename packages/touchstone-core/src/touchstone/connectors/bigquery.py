"""BigQuery connector.

Uses google-cloud-bigquery's high-level client. Authentication is via ADC
(Application Default Credentials) — operators are expected to have a service
account or `gcloud auth application-default login` configured.

BigQuery doesn't have transactions in the Postgres sense; read-only is enforced
by the connector via a job_config that rejects DML when read_only is set, and
by the policy engine rejecting non-SELECT SQL at the gateway anyway.
"""

from __future__ import annotations

import sys
from typing import Any

from touchstone.connectors.base import Connector
from touchstone.types import (
    Column,
    ConnectorError,
    ConnectorTimeoutError,
    Engine,
    QueryResult,
    TableRef,
)


class BigQueryConnector(Connector):
    engine = Engine.BIGQUERY

    def connect(self) -> None:
        if self._connection is not None:
            return
        try:
            from google.cloud import bigquery
        except ImportError as e:
            raise ConnectorError(
                "google-cloud-bigquery not installed. "
                "Install with: pip install 'touchstone-core[bigquery]'"
            ) from e
        project = self.config.extra.get("project") or self.config.database
        location = self.config.extra.get("location")
        self._connection = bigquery.Client(project=project, location=location)

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> QueryResult:
        if self._connection is None:
            self.connect()
        from google.api_core import exceptions
        from google.cloud import bigquery

        job_config = bigquery.QueryJobConfig(
            use_query_cache=True,
            maximum_bytes_billed=self.config.extra.get(
                "max_bytes_billed", 10 * 1024 * 1024 * 1024  # 10 GB default cap
            ),
            labels={"app": "touchstone"},
        )
        if params:
            job_config.query_parameters = [
                bigquery.ScalarQueryParameter(k, _bq_param_type(v), v)
                for k, v in params.items()
            ]

        try:
            job = self._connection.query(sql, job_config=job_config, timeout=self.config.timeout_seconds)
            iterator = job.result(timeout=self.config.timeout_seconds, max_results=self.config.row_cap)
            schema = list(iterator.schema)
            columns = [Column(name=f.name, type=f.field_type, nullable=(f.mode != "REQUIRED"))
                       for f in schema]
            rows: list[tuple[Any, ...]] = []
            bytes_returned = 0
            for row in iterator:
                values = tuple(row.values())
                rows.append(values)
                bytes_returned += sys.getsizeof(values)
                if (
                    len(rows) >= self.config.row_cap
                    or bytes_returned >= self.config.byte_cap
                ):
                    break
            return QueryResult(
                columns=columns, rows=rows, row_count=len(rows),
                truncated=(len(rows) >= self.config.row_cap),
                bytes_returned=bytes_returned, engine=Engine.BIGQUERY,
                sql_executed=sql,
            )
        except exceptions.DeadlineExceeded as e:
            raise ConnectorTimeoutError(str(e)) from None
        except exceptions.GoogleAPIError as e:
            raise ConnectorError(str(e)) from None

    def list_tables(self, schema: str | None = None) -> list[TableRef]:
        if self._connection is None:
            self.connect()
        dataset_id = schema or self.config.schema_
        if not dataset_id:
            raise ConnectorError("BigQuery list_tables requires a schema (dataset).")
        dataset_ref = self._connection.dataset(dataset_id)
        return [
            TableRef(name=t.table_id, schema=dataset_id, database=t.project)
            for t in self._connection.list_tables(dataset_ref)
        ]

    def describe_table(self, table: TableRef) -> list[Column]:
        if self._connection is None:
            self.connect()
        if not table.schema:
            raise ConnectorError("BigQuery describe_table requires a schema (dataset).")
        tbl = self._connection.get_table(f"{table.schema}.{table.name}")
        return [
            Column(name=f.name, type=f.field_type, nullable=(f.mode != "REQUIRED"))
            for f in tbl.schema
        ]


def _bq_param_type(v: Any) -> str:
    if isinstance(v, bool):
        return "BOOL"
    if isinstance(v, int):
        return "INT64"
    if isinstance(v, float):
        return "FLOAT64"
    return "STRING"

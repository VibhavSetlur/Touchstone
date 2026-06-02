# Adding a connector

Touchstone connectors are small — ~150 lines for a SQL engine, more for an
exotic one. This guide walks through adding one.

## The interface

Every connector inherits `touchstone.connectors.base.Connector` and implements:

```python
class Connector(ABC):
    engine: Engine

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def execute(self, sql: str, params: dict[str, Any] | None = None) -> QueryResult: ...

    @abstractmethod
    def list_tables(self, schema: str | None = None) -> list[TableRef]: ...

    @abstractmethod
    def describe_table(self, table: TableRef) -> list[Column]: ...
```

You MUST also:

1. Honor `config.timeout_seconds` server-side (vendor-specific session SET).
2. Honor `config.row_cap` and `config.byte_cap` — truncate, set `truncated=True`.
3. Honor `config.read_only` — open the session/transaction read-only at the
   engine level, not just at the SQL allow-list.
4. Wrap engine errors in `ConnectorError` / `ConnectorTimeoutError` /
   `ConnectorAuthError` — never leak raw driver tracebacks.
5. Scrub credentials from error messages with a tiny `_scrub()` helper.

## Step by step

### 1. Pick the right base

- For SQL engines that have a SQLAlchemy dialect, inherit from
  `_sqlalchemy_base.SQLAlchemyConnector` (see `redshift.py`, `databricks.py`,
  `trino_.py`, `clickhouse.py`). Smallest path; ~50 lines.
- For SQL engines with a native DB-API driver and no good SQLAlchemy story
  (or where the dialect matters), inherit from `Connector` directly (see
  `postgres.py`, `snowflake.py`).
- For non-SQL stores (Mongo-style), inherit from `Connector` and have
  `execute()` parse `sql` as a JSON op spec (see `mongodb.py`).

### 2. Add the engine to the enum

```python
# touchstone/types.py
class Engine(str, Enum):
    ...
    MY_ENGINE = "my_engine"
```

### 3. Register it

```python
# touchstone/connectors/registry.py
def _load_my_engine() -> type[Connector]:
    from touchstone.connectors.my_engine import MyEngineConnector
    return MyEngineConnector

register(Engine.MY_ENGINE, _load_my_engine)
```

The registry uses lazy imports so an installation with only `touchstone-core`
doesn't pay the cost of importing every driver.

### 4. Add the optional dependency

```toml
# packages/touchstone-core/pyproject.toml
[project.optional-dependencies]
my_engine = ["my-engine-driver>=1.0"]
```

So users install with `pip install 'touchstone-core[my_engine]'`.

### 5. Write tests

- A unit test against a fake/mock driver in `tests/unit/`.
- An integration test in `tests/integration/`, gated on a `MY_ENGINE_HOST`
  env var so it's skipped when the dev hasn't started the engine.
- Add a service to `docker-compose.yml` if a containerized version exists.

### 6. Document it

- Add a row to the supported-databases table in `README.md`.
- Add a short doc in `docs/connectors/my_engine.md` covering: auth methods,
  notable quirks, what the connector does *not* support.

## Things to watch out for

- **Read-only enforcement.** Most engines have a way to make a session
  read-only at the engine level (Postgres `default_transaction_read_only`,
  MySQL `SESSION TRANSACTION READ ONLY`, Snowflake doesn't have a session
  setting but the role can be read-only). Use it. The SQL allow-list is a
  defense in depth, not a primary control.
- **Server-side timeouts.** A client-side timeout leaves a runaway query
  on the server. Use the vendor's statement-timeout knob.
- **Cap rows in the iterator, not after.** Use `LIMIT` if possible, or break
  out of the fetch loop at `row_cap`. Don't fetch a billion rows and then
  slice.
- **Error scrubbing.** Drivers love to embed connection strings in errors.
  Scrub `password=`, `pwd=`, `token=`, and `user:pass@host` patterns before
  re-raising.

## A worked example: adding Vertica

Vertica has a Postgres-protocol-compatible driver (`vertica-python`). A
minimal connector:

```python
# packages/touchstone-core/src/touchstone/connectors/vertica.py
from touchstone.connectors._sqlalchemy_base import SQLAlchemyConnector, resolve_password
from touchstone.types import ConnectorError, Engine


class VerticaConnector(SQLAlchemyConnector):
    engine = Engine.VERTICA  # add to enum

    def dialect_url(self) -> str:
        try:
            import sqlalchemy_vertica_python  # noqa: F401
        except ImportError as e:
            raise ConnectorError(
                "vertica-python + sqlalchemy-vertica-python not installed."
            ) from e
        password = resolve_password(self.config.password_ref)
        return (
            f"vertica+vertica_python://{self.config.user}:{password}"
            f"@{self.config.host}:{self.config.port or 5433}/{self.config.database}"
        )

    def session_guards(self, conn):
        from sqlalchemy import text
        conn.execute(text(f"SET SESSION RESOURCE_POOL = 'general'"))
        # Vertica: SET STATEMENT_TIMEOUT not a thing; SET SESSION RUNTIMECAP works.
        conn.execute(text(f"SET SESSION RUNTIMECAP = '{self.config.timeout_seconds}s'"))
```

Plus the registry entry, the optional dep, and the tests. Done.

import asyncio
import os
from typing import Any, Dict, List, Optional

import asyncpg
from dotenv import load_dotenv

load_dotenv()

_pool: Optional[asyncpg.Pool] = None
_pool_loop: Optional[asyncio.AbstractEventLoop] = None
_pool_lock = asyncio.Lock()


def _get_pg_config() -> dict[str, Any]:
    return {
        "user": os.getenv("PGUSER") or os.getenv("POSTGRES_USER"),
        "password": os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD"),
        "database": os.getenv("PGDATABASE") or os.getenv("POSTGRES_DB"),
        "host": os.getenv("PGHOST", "localhost"),
        "port": int(os.getenv("PGPORT", "5432")),
        "ssl": None if os.getenv("PGSSL", "false").lower() in {"", "0", "false", "no"} else "require",
        "min_size": int(os.getenv("PGPOOL_MIN_SIZE", "1")),
        "max_size": int(os.getenv("PGPOOL_MAX_SIZE", "10")),
        "command_timeout": float(os.getenv("PGPOOL_COMMAND_TIMEOUT", "30")),
    }


async def get_pool() -> asyncpg.Pool:
    global _pool, _pool_loop
    current_loop = asyncio.get_running_loop()

    needs_new_pool = _pool is None or _pool_loop is None or _pool_loop is not current_loop
    if _pool is not None and hasattr(_pool, "_closed") and getattr(_pool, "_closed"):
        needs_new_pool = True

    if needs_new_pool:
        async with _pool_lock:
            current_loop = asyncio.get_running_loop()
            needs_new_pool = _pool is None or _pool_loop is None or _pool_loop is not current_loop
            if _pool is not None and hasattr(_pool, "_closed") and getattr(_pool, "_closed"):
                needs_new_pool = True

            if needs_new_pool:
                config = _get_pg_config()
                missing = [k for k in ("user", "password", "database") if not config.get(k)]
                if missing:
                    raise RuntimeError(f"Missing Postgres settings: {', '.join(missing)}")

                # Extract pool tuning parameters and remove from connect kwargs
                min_size = config.pop("min_size")
                max_size = config.pop("max_size")
                command_timeout = config.pop("command_timeout")

                _pool = await asyncpg.create_pool(
                    min_size=min_size,
                    max_size=max_size,
                    command_timeout=command_timeout,
                    **config,
                )
                _pool_loop = current_loop
    return _pool


async def close_pool() -> None:
    global _pool, _pool_loop
    if _pool is not None:
        await _pool.close()
        _pool = None
        _pool_loop = None


async def list_tables(schema: Optional[str] = None) -> Dict[str, Any]:
    pool = await get_pool()
    query = """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_type = 'BASE TABLE'
          AND table_schema NOT IN ('pg_catalog', 'information_schema')
    """
    params: List[Any] = []
    if schema:
        query += " AND table_schema = $1"
        params.append(schema)
    query += " ORDER BY table_schema, table_name"

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)

    tables = [
        {
            "table_name": row["table_name"],
            "schema": row["table_schema"],
            "full_name": f"{row['table_schema']}.{row['table_name']}" if row["table_schema"] else row["table_name"],
        }
        for row in rows
    ]
    return {"total_tables": len(tables), "tables": tables}


async def describe_table(table_name: str, schema: Optional[str] = None) -> Dict[str, Any]:
    pool = await get_pool()
    qualified = table_name if schema is None else f"{schema}.{table_name}"
    column_query = """
        SELECT
            column_name,
            data_type,
            is_nullable,
            column_default,
            character_maximum_length,
            numeric_precision,
            numeric_scale
        FROM information_schema.columns
        WHERE table_name = $1
    """
    params: List[Any] = [table_name]
    if schema:
        column_query += " AND table_schema = $2"
        params.append(schema)
    column_query += " ORDER BY ordinal_position"

    count_query = f"SELECT COUNT(*) FROM {qualified}"

    async with pool.acquire() as conn:
        columns_raw = await conn.fetch(column_query, *params)
        row_count = await conn.fetchval(count_query)

    columns = [
        {
            "column_name": row["column_name"],
            "data_type": row["data_type"],
            "nullable": row["is_nullable"] == "YES",
            "default_value": row["column_default"],
            "max_length": row["character_maximum_length"],
            "precision": row["numeric_precision"],
            "scale": row["numeric_scale"],
        }
        for row in columns_raw
    ]

    return {
        "table_name": qualified,
        "row_count": int(row_count or 0),
        "columns": columns,
        "total_columns": len(columns),
    }


def _serialize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


async def get_table_sample(table_name: str, limit: int = 5, schema: Optional[str] = None) -> Dict[str, Any]:
    pool = await get_pool()
    limit = max(1, min(limit, 1000))
    qualified = table_name if schema is None else f"{schema}.{table_name}"
    query = f"SELECT * FROM {qualified} LIMIT {limit}"

    async with pool.acquire() as conn:
        rows = await conn.fetch(query)

    columns = list(rows[0].keys()) if rows else []
    data = [
        {col: _serialize_value(row[col]) for col in columns}
        for row in rows
    ]

    return {
        "table_name": qualified,
        "sample_size": limit,
        "columns": columns,
        "rows": data,
        "actual_count": len(data),
    }


async def execute_sql(query: str) -> Dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        statement = query.strip()
        result: Dict[str, Any]
        if statement.lower().startswith("select"):
            rows = await conn.fetch(statement)
            columns = list(rows[0].keys()) if rows else []
            data = [
                {col: _serialize_value(row[col]) for col in columns}
                for row in rows
            ]
            result = {
                "query": query,
                "columns": columns,
                "rows": data,
                "row_count": len(data),
            }
        else:
            status = await conn.execute(statement)
            # status like 'INSERT 0 1'
            parts = status.split()
            affected = int(parts[-1]) if parts and parts[-1].isdigit() else None
            result = {
                "query": query,
                "rows_affected": affected,
                "status": status,
            }
    return result


if __name__ == "__main__":
    async def _smoke() -> None:
        print(await list_tables())
    asyncio.run(_smoke())

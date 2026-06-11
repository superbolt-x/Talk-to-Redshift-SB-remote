"""
Read-only Redshift tools.

All write operations (INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, TRUNCATE,
GRANT, REVOKE, COPY, UNLOAD) are blocked at the guardrail layer before any
query reaches the database.
"""
import logging
import os
import re

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("talk-to-redshift.tools")

_WRITE_PATTERN = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|COPY|UNLOAD|MERGE|REPLACE)\b",
    re.IGNORECASE,
)

_CLUSTER_ID   = os.environ.get("REDSHIFT_CLUSTER_ID", "")
_WORKGROUP    = os.environ.get("REDSHIFT_WORKGROUP", "")
_DB_USER      = os.environ.get("REDSHIFT_DB_USER", "")
_REGION       = os.environ.get("AWS_DEFAULT_REGION", os.environ.get("AWS_REGION", "us-east-1"))


def _client():
    return boto3.client("redshift-data", region_name=_REGION)


def _run_query(sql: str, database: str) -> dict:
    """Execute SQL via the Redshift Data API and wait for results."""
    client = _client()
    db = database

    kwargs: dict = {
        "Sql": sql,
        "Database": db,
        "WithEvent": False,
    }
    if _CLUSTER_ID:
        kwargs["ClusterIdentifier"] = _CLUSTER_ID
        if _DB_USER:
            kwargs["DbUser"] = _DB_USER
    elif _WORKGROUP:
        kwargs["WorkgroupName"] = _WORKGROUP
    else:
        raise ValueError(
            "Set REDSHIFT_CLUSTER_ID (provisioned) or REDSHIFT_WORKGROUP (serverless) env var."
        )

    resp = client.execute_statement(**kwargs)
    stmt_id = resp["Id"]

    import time
    while True:
        status_resp = client.describe_statement(Id=stmt_id)
        status = status_resp["Status"]
        if status in ("FINISHED", "FAILED", "ABORTED"):
            break
        time.sleep(0.5)

    if status != "FINISHED":
        error = status_resp.get("Error", "Unknown error")
        raise RuntimeError(f"Query {status}: {error}")

    result = client.get_statement_result(Id=stmt_id)
    columns = [c["name"] for c in result.get("ColumnMetadata", [])]
    rows = []
    for record in result.get("Records", []):
        row = {}
        for col, field in zip(columns, record):
            value = next(iter(field.values()), None)
            row[col] = value
        rows.append(row)

    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
    }


def register_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    def list_clusters() -> list[dict]:
        """List all available Redshift provisioned clusters and serverless workgroups."""
        results = []
        ec2 = boto3.client("redshift", region_name=_REGION)
        try:
            paginator = ec2.get_paginator("describe_clusters")
            for page in paginator.paginate():
                for c in page.get("Clusters", []):
                    results.append({
                        "type": "provisioned",
                        "id": c["ClusterIdentifier"],
                        "status": c["ClusterStatus"],
                        "database": c.get("DBName"),
                        "endpoint": c.get("Endpoint", {}).get("Address"),
                    })
        except (BotoCoreError, ClientError) as e:
            logger.warning("Could not list provisioned clusters: %s", e)

        rs_serverless = boto3.client("redshift-serverless", region_name=_REGION)
        try:
            paginator = rs_serverless.get_paginator("list_workgroups")
            for page in paginator.paginate():
                for w in page.get("workgroups", []):
                    results.append({
                        "type": "serverless",
                        "id": w["workgroupName"],
                        "status": w["status"],
                        "database": None,
                        "endpoint": w.get("endpoint", {}).get("address"),
                    })
        except (BotoCoreError, ClientError) as e:
            logger.warning("Could not list serverless workgroups: %s", e)

        return results

    @mcp.tool()
    def list_databases() -> list[str]:
        """
        List all client databases available in the Redshift cluster.

        Use this first to discover which database to target before calling
        list_schemas, list_tables, list_columns, or execute_query.
        """
        client = _client()
        kwargs: dict = {"WithEvent": False}
        if _CLUSTER_ID:
            kwargs["ClusterIdentifier"] = _CLUSTER_ID
            if _DB_USER:
                kwargs["DbUser"] = _DB_USER
        elif _WORKGROUP:
            kwargs["WorkgroupName"] = _WORKGROUP
        else:
            raise ValueError(
                "Set REDSHIFT_CLUSTER_ID (provisioned) or REDSHIFT_WORKGROUP (serverless) env var."
            )

        paginator = client.get_paginator("list_databases")
        databases = []
        for page in paginator.paginate(**{k: v for k, v in kwargs.items() if k != "WithEvent"}):
            databases.extend(page.get("Databases", []))
        return sorted(databases)

    @mcp.tool()
    def list_schemas(database: str) -> dict:
        """
        List all schemas in a client database.

        Args:
            database: Database name (use list_databases to discover available names).
        """
        sql = (
            "SELECT schema_name, schema_owner "
            "FROM information_schema.schemata "
            "WHERE schema_name NOT IN ('information_schema','pg_catalog','pg_toast','pg_internal') "
            "ORDER BY schema_name"
        )
        return _run_query(sql, database=database)

    @mcp.tool()
    def list_tables(database: str, schema: str = "public") -> dict:
        """
        List all tables in a schema.

        Args:
            database: Database name (use list_databases to discover available names).
            schema:   Schema name (default: public).
        """
        sql = (
            f"SELECT table_name, table_type "
            f"FROM information_schema.tables "
            f"WHERE table_schema = '{schema}' "
            f"ORDER BY table_name"
        )
        return _run_query(sql, database=database)

    @mcp.tool()
    def list_columns(database: str, table: str, schema: str = "public") -> dict:
        """
        List all columns and their types for a table.

        Args:
            database: Database name (use list_databases to discover available names).
            table:    Table name.
            schema:   Schema name (default: public).
        """
        sql = (
            f"SELECT column_name, data_type, character_maximum_length, "
            f"is_nullable, column_default "
            f"FROM information_schema.columns "
            f"WHERE table_schema = '{schema}' AND table_name = '{table}' "
            f"ORDER BY ordinal_position"
        )
        return _run_query(sql, database=database)

    @mcp.tool()
    def execute_query(database: str, sql: str) -> dict:
        """
        Execute a read-only SELECT query against a client database.

        Only SELECT statements are allowed. Any attempt to run INSERT, UPDATE,
        DELETE, DROP, CREATE, ALTER, TRUNCATE, GRANT, REVOKE, COPY, UNLOAD,
        MERGE, or REPLACE will be rejected before reaching the database.

        Args:
            database: Database name (use list_databases to discover available names).
            sql:      A SELECT SQL statement.

        Returns:
            A dict with keys: columns (list), rows (list of dicts), row_count (int).
        """
        if _WRITE_PATTERN.match(sql):
            raise ValueError(
                "Write operations are not permitted on this server. "
                "Only SELECT queries are allowed."
            )
        return _run_query(sql, database=database)

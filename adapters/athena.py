import boto3
import time
from fastapi import HTTPException

from adapters.base import DatabaseAdapter


class AthenaAdapter(DatabaseAdapter):
    sql_dialect = "Presto SQL for AWS Athena"

    def __init__(self, database: str, output_s3: str, region: str):
        self.database = database
        self.output_s3 = output_s3
        self.client = boto3.client("athena", region_name=region)

    def execute_query(self, sql: str) -> dict:
        execution_id = self.client.start_query_execution(
            QueryString=sql,
            QueryExecutionContext={"Database": self.database},
            ResultConfiguration={"OutputLocation": self.output_s3},
        )["QueryExecutionId"]

        status, meta = self._wait_for_query(execution_id)

        if status != "SUCCEEDED":
            reason = meta["QueryExecution"]["Status"].get("StateChangeReason", "Unknown error")
            raise HTTPException(status_code=500, detail=f"Athena query failed: {reason}")

        return self._fetch_results(execution_id)

    def fetch_distinct_values(self, table: str, column: str, limit: int = 200) -> list:
        sql = f'SELECT DISTINCT "{column}" FROM "{self.database}"."{table}" WHERE "{column}" IS NOT NULL LIMIT {limit}'
        try:
            result = self.execute_query(sql)
            return [row.get(column) for row in result["rows"] if row.get(column) is not None]
        except Exception as e:
            print(f"⚠️ Failed to fetch samples for {table}.{column}: {e}")
            return []

    def _wait_for_query(self, execution_id: str):
        while True:
            response = self.client.get_query_execution(QueryExecutionId=execution_id)
            status = response["QueryExecution"]["Status"]["State"]
            if status in ["SUCCEEDED", "FAILED", "CANCELLED"]:
                return status, response
            time.sleep(2)

    def _fetch_results(self, execution_id: str) -> dict:
        paginator = self.client.get_paginator("get_query_results")
        results = []
        columns = []

        for page in paginator.paginate(QueryExecutionId=execution_id):
            for row in page["ResultSet"]["Rows"]:
                values = [col.get("VarCharValue") for col in row["Data"]]
                if not columns:
                    columns = values
                else:
                    results.append(dict(zip(columns, values)))

        return {"columns": columns, "rows": results}

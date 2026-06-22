import os
import json
import time
import boto3
from abc import ABC, abstractmethod


class SessionStore(ABC):
    @abstractmethod
    def get(self, session_id: str) -> dict | None: ...

    @abstractmethod
    def set(self, session_id: str, data: dict) -> None: ...


class InMemorySessionStore(SessionStore):
    def __init__(self):
        self._store = {}

    def get(self, session_id: str) -> dict | None:
        return self._store.get(session_id)

    def set(self, session_id: str, data: dict) -> None:
        self._store[session_id] = data


class DynamoDBSessionStore(SessionStore):
    def __init__(self):
        endpoint_url = os.getenv("DYNAMODB_ENDPOINT_URL")
        self._table_name = os.getenv("DYNAMODB_SESSION_TABLE", "rag-sessions")
        self._ttl_seconds = int(os.getenv("SESSION_TTL_SECONDS", 86400))
        region = os.getenv("AWS_REGION", "eu-west-2")
        self._dynamo = boto3.resource("dynamodb", region_name=region, endpoint_url=endpoint_url)
        self._ensure_table_exists()
        self._table = self._dynamo.Table(self._table_name)

    def _ensure_table_exists(self):
        try:
            self._dynamo.create_table(
                TableName=self._table_name,
                AttributeDefinitions=[{"AttributeName": "session_id", "AttributeType": "S"}],
                KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"}],
                BillingMode="PAY_PER_REQUEST",
            )
            table = self._dynamo.Table(self._table_name)
            table.wait_until_exists()
            table.meta.client.update_time_to_live(
                TableName=self._table_name,
                TimeToLiveSpecification={"Enabled": True, "AttributeName": "expires_at"},
            )
        except self._dynamo.meta.client.exceptions.ResourceInUseException:
            pass

    def get(self, session_id: str) -> dict | None:
        response = self._table.get_item(Key={"session_id": session_id})
        item = response.get("Item")
        if not item:
            return None
        return json.loads(item["data"])

    def set(self, session_id: str, data: dict) -> None:
        self._table.put_item(Item={
            "session_id": session_id,
            "data": json.dumps(data),
            "expires_at": int(time.time()) + self._ttl_seconds,
        })


def get_session_store() -> SessionStore:
    backend = os.getenv("SESSION_STORE", "memory")
    if backend == "memory":
        return InMemorySessionStore()
    if backend == "dynamodb":
        return DynamoDBSessionStore()
    raise ValueError(f"Unknown SESSION_STORE backend: {backend!r}")

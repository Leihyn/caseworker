"""Case repositories: in-memory for the deterministic judge path, DynamoDB for AWS."""

from copy import deepcopy
from decimal import Decimal
import json
import os
from threading import RLock
from typing import Any, Protocol

from .demo_case import new_demo_case


class CaseNotFoundError(KeyError):
    pass


class CaseRepository(Protocol):
    def reset_demo(self) -> dict[str, Any]: ...

    def get(self, case_id: str) -> dict[str, Any]: ...

    def save(self, case: dict[str, Any]) -> dict[str, Any]: ...


class InMemoryCaseRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._cases: dict[str, dict[str, Any]] = {}

    def reset_demo(self) -> dict[str, Any]:
        with self._lock:
            case = new_demo_case()
            self._cases[str(case["id"])] = case
            return deepcopy(case)

    def get(self, case_id: str) -> dict[str, Any]:
        with self._lock:
            if case_id not in self._cases:
                raise CaseNotFoundError(case_id)
            return deepcopy(self._cases[case_id])

    def save(self, case: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._cases[str(case["id"])] = deepcopy(case)
            return deepcopy(case)


class DynamoCaseRepository:
    """DynamoDB-backed repository used when CASEWORKER_REPOSITORY=dynamodb.

    One item per case, keyed by case id. Floats round-trip through Decimal
    (DynamoDB rejects float), so cases are serialized via JSON on both paths.
    """

    def __init__(self, table_name: str, region: str | None = None) -> None:
        import boto3

        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def reset_demo(self) -> dict[str, Any]:
        case = new_demo_case()
        return self.save(case)

    def get(self, case_id: str) -> dict[str, Any]:
        item = self._table.get_item(Key={"case_id": case_id}).get("Item")
        if not item:
            raise CaseNotFoundError(case_id)
        return json.loads(json.dumps(item["case"], default=_decimal_to_number))

    def save(self, case: dict[str, Any]) -> dict[str, Any]:
        stored = json.loads(json.dumps(case), parse_float=Decimal)
        self._table.put_item(Item={"case_id": str(case["id"]), "case": stored})
        return deepcopy(case)


def _decimal_to_number(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value) if value % 1 else int(value)
    raise TypeError(f"Unserializable type: {type(value)}")


def create_repository() -> CaseRepository:
    mode = os.getenv("CASEWORKER_REPOSITORY", "memory").strip().lower()
    if mode == "memory":
        return InMemoryCaseRepository()
    if mode == "dynamodb":
        return DynamoCaseRepository(
            table_name=os.getenv("CASEWORKER_TABLE", "caseworker-cases"),
            region=os.getenv("AWS_REGION") or None,
        )
    raise ValueError("CASEWORKER_REPOSITORY must be 'memory' or 'dynamodb'.")

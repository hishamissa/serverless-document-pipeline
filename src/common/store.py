"""DynamoDB-backed job store.

Holds the lifecycle of a job keyed by ``job_id``:

    PENDING -> PROCESSING -> COMPLETE
                          \\-> FAILED

Idempotency (SQS is at-least-once): completion is a *conditional* write that
only succeeds when the job is not already COMPLETE. A duplicate SQS delivery
therefore cannot overwrite or re-run a finished job — the conditional check
fails and the worker treats it as an already-processed no-op.
"""
from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError

PENDING = "PENDING"
PROCESSING = "PROCESSING"
COMPLETE = "COMPLETE"
FAILED = "FAILED"


class AlreadyProcessed(Exception):
    """Raised when a conditional write is rejected because the job is done."""


def _table(table_name: str):
    return boto3.resource("dynamodb").Table(table_name)


def _now() -> int:
    return int(time.time())


def _to_dynamo(value: Any) -> Any:
    """Convert floats to Decimal so boto3 can store nested numeric data."""
    return json.loads(json.dumps(value), parse_float=Decimal)


def _from_dynamo(value: Any) -> Any:
    """Convert Decimal back to int/float for JSON responses."""
    if isinstance(value, list):
        return [_from_dynamo(v) for v in value]
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    return value


def create_job(
    table_name: str,
    job_id: str,
    s3_key: str,
    doc_type: str,
    filename: str,
) -> None:
    """Insert a new PENDING job. Fails if the job_id already exists."""
    _table(table_name).put_item(
        Item={
            "job_id": job_id,
            "status": PENDING,
            "s3_key": s3_key,
            "doc_type": doc_type,
            "filename": filename,
            "created_at": _now(),
            "updated_at": _now(),
        },
        ConditionExpression="attribute_not_exists(job_id)",
    )


def get_job(table_name: str, job_id: str) -> dict[str, Any] | None:
    resp = _table(table_name).get_item(Key={"job_id": job_id}, ConsistentRead=True)
    item = resp.get("Item")
    return _from_dynamo(item) if item else None


def mark_complete(table_name: str, job_id: str, result: dict[str, Any]) -> None:
    """Conditionally mark a job COMPLETE. Idempotent under duplicate delivery.

    Raises :class:`AlreadyProcessed` if the job is already COMPLETE.
    """
    try:
        _table(table_name).update_item(
            Key={"job_id": job_id},
            UpdateExpression=(
                "SET #s = :complete, #r = :result, updated_at = :now "
                "REMOVE #err"
            ),
            ConditionExpression="attribute_exists(job_id) AND #s <> :complete",
            ExpressionAttributeNames={"#s": "status", "#r": "result", "#err": "error"},
            ExpressionAttributeValues={
                ":complete": COMPLETE,
                ":result": _to_dynamo(result),
                ":now": _now(),
            },
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise AlreadyProcessed(job_id) from exc
        raise


def mark_failed(table_name: str, job_id: str, reason: str) -> None:
    """Mark a job FAILED with a human-readable reason (unless already COMPLETE)."""
    try:
        _table(table_name).update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #s = :failed, #err = :reason, updated_at = :now",
            ConditionExpression="attribute_exists(job_id) AND #s <> :complete",
            ExpressionAttributeNames={"#s": "status", "#err": "error"},
            ExpressionAttributeValues={
                ":failed": FAILED,
                ":complete": COMPLETE,
                ":reason": reason,
                ":now": _now(),
            },
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise AlreadyProcessed(job_id) from exc
        raise

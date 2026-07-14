"""SQS-triggered worker: fetch the document, extract data, persist the result.

Error handling strategy
-----------------------
* **Bad/malformed document** (an :class:`ExtractionError`): this is a permanent
  failure. The job is marked FAILED with a reason and the message is treated as
  successfully handled so it does NOT poison the queue or hit the DLQ.
* **Transient failure** (S3/DynamoDB error, unexpected bug): the exception
  propagates so SQS redelivers the message. After ``maxReceiveCount`` attempts
  it lands in the DLQ for inspection.

Idempotency
-----------
SQS delivers at least once. Before processing, the worker checks whether the
job is already COMPLETE and skips it. The final write is also conditional
(see :mod:`common.store`), so concurrent duplicates cannot double-complete.

Batching
--------
Uses partial batch responses (``batchItemFailures``) so that one poison message
in a batch does not force reprocessing of its already-succeeded siblings.
"""
from __future__ import annotations

import json
import os

import boto3

from common import store
from common.logging import BoundLogger, get_logger
from worker.extract import ExtractionError, extract

_logger = get_logger("worker")

BUCKET_NAME = os.environ.get("BUCKET_NAME", "")
TABLE_NAME = os.environ.get("TABLE_NAME", "")


def handler(event: dict, context: object) -> dict:
    failures: list[dict] = []
    request_id = getattr(context, "aws_request_id", None)

    for record in event.get("Records", []):
        message_id = record.get("messageId")
        try:
            _process_record(record, request_id)
        except Exception:  # noqa: BLE001 — transient: let SQS retry / DLQ.
            _logger.error(
                "record failed, will retry",
                extra={"context": {"message_id": message_id, "request_id": request_id}},
                exc_info=True,
            )
            failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": failures}


def _process_record(record: dict, request_id: str | None = None) -> None:
    message = json.loads(record["body"])
    job_id = message["job_id"]
    s3_key = message["s3_key"]
    doc_type = message["doc_type"]
    log = BoundLogger(
        _logger,
        job_id=job_id,
        message_id=record.get("messageId"),
        request_id=request_id,
    )

    # Cheap idempotency guard for the common duplicate-delivery case.
    existing = store.get_job(TABLE_NAME, job_id)
    if existing and existing["status"] == store.COMPLETE:
        log.info("skipping already-complete job (duplicate delivery)")
        return

    body = _fetch(s3_key)

    try:
        content = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail(log, job_id, f"document is not valid UTF-8: {exc}")
        return

    try:
        result = extract(content, doc_type)
    except ExtractionError as exc:
        _fail(log, job_id, str(exc))
        return

    try:
        store.mark_complete(TABLE_NAME, job_id, result)
        log.info("job complete", kind=result.get("kind"))
    except store.AlreadyProcessed:
        log.info("job already complete (race), no-op")


def _fetch(s3_key: str) -> bytes:
    obj = boto3.client("s3").get_object(Bucket=BUCKET_NAME, Key=s3_key)
    return obj["Body"].read()


def _fail(log: BoundLogger, job_id: str, reason: str) -> None:
    log.warning("marking job FAILED", reason=reason)
    try:
        store.mark_failed(TABLE_NAME, job_id, reason)
    except store.AlreadyProcessed:
        log.info("job already complete; ignoring failure")

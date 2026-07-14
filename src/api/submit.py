"""POST /jobs — accept a document, store it, and enqueue a processing job.

Returns 202 Accepted with a ``job_id`` immediately. The actual parsing happens
asynchronously in the worker Lambda, decoupled via SQS.

Request:
  * body: the raw document content (CSV or plain text). May be base64-encoded
    by API Gateway for binary content types.
  * query string:
      - ``type``     : "csv" or "text" (default "text")
      - ``filename`` : optional original filename, stored for reference
"""
from __future__ import annotations

import base64
import json
import os
import uuid

import boto3

from api.responses import json_response
from common import store
from common.logging import BoundLogger, get_logger

_logger = get_logger("api.submit")

BUCKET_NAME = os.environ.get("BUCKET_NAME", "")
TABLE_NAME = os.environ.get("TABLE_NAME", "")
QUEUE_URL = os.environ.get("QUEUE_URL", "")

_VALID_TYPES = {"csv", "text", "txt", "plain"}
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB guard — keeps us in free tier and sane.


def handler(event: dict, context: object) -> dict:
    job_id = str(uuid.uuid4())
    log = BoundLogger(_logger, job_id=job_id)

    params = event.get("queryStringParameters") or {}
    doc_type = (params.get("type") or "text").lower()
    filename = params.get("filename") or f"{job_id}.{_ext(doc_type)}"

    if doc_type not in _VALID_TYPES:
        log.warning("rejected: unsupported type", doc_type=doc_type)
        return json_response(400, {"error": f"unsupported type: {doc_type}"})

    body = _decode_body(event)
    if not body:
        log.warning("rejected: empty body")
        return json_response(400, {"error": "request body is empty"})
    if len(body) > _MAX_BYTES:
        log.warning("rejected: body too large", size=len(body))
        return json_response(413, {"error": "document exceeds 5 MB limit"})

    s3_key = f"uploads/{job_id}/{filename}"

    boto3.client("s3").put_object(Bucket=BUCKET_NAME, Key=s3_key, Body=body)
    log.info("stored document", s3_key=s3_key, size=len(body))

    store.create_job(TABLE_NAME, job_id, s3_key, doc_type, filename)

    boto3.client("sqs").send_message(
        QueueUrl=QUEUE_URL,
        MessageBody=json.dumps(
            {"job_id": job_id, "s3_key": s3_key, "doc_type": doc_type}
        ),
    )
    log.info("enqueued job")

    return json_response(202, {"job_id": job_id, "status": store.PENDING})


def _decode_body(event: dict) -> bytes:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(raw)
    return raw.encode("utf-8") if isinstance(raw, str) else raw


def _ext(doc_type: str) -> str:
    return "csv" if doc_type == "csv" else "txt"

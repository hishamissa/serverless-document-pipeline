"""GET /jobs/{job_id} — return job status and extracted results."""
from __future__ import annotations

import os

from api.responses import json_response
from common import store
from common.logging import BoundLogger, get_logger

_logger = get_logger("api.status")

TABLE_NAME = os.environ.get("TABLE_NAME", "")


def handler(event: dict, context: object) -> dict:
    path_params = event.get("pathParameters") or {}
    job_id = path_params.get("job_id")
    log = BoundLogger(_logger, job_id=job_id)

    if not job_id:
        return json_response(400, {"error": "missing job_id"})

    job = store.get_job(TABLE_NAME, job_id)
    if job is None:
        log.info("job not found")
        return json_response(404, {"error": "job not found", "job_id": job_id})

    body = {
        "job_id": job["job_id"],
        "status": job["status"],
        "filename": job.get("filename"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }
    if job["status"] == store.COMPLETE:
        body["result"] = job.get("result")
    elif job["status"] == store.FAILED:
        body["error"] = job.get("error")

    log.info("returned status", status=job["status"])
    return json_response(200, body)

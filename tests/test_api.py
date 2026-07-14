"""Tests for the submit and status API handlers, with mocked AWS."""
from __future__ import annotations

import base64
import json


def _submit_event(body, doc_type="text", filename=None, is_b64=False):
    params = {"type": doc_type}
    if filename:
        params["filename"] = filename
    return {
        "queryStringParameters": params,
        "body": body,
        "isBase64Encoded": is_b64,
    }


class TestSubmit:
    def test_happy_path_returns_202_and_persists(self, aws):
        import api.submit as submit
        from common import store

        resp = submit.handler(_submit_event("hello world", "text"), None)
        assert resp["statusCode"] == 202
        job_id = json.loads(resp["body"])["job_id"]

        # Job row created as PENDING.
        job = store.get_job(aws["table"], job_id)
        assert job["status"] == "PENDING"

        # Object written to S3.
        objs = aws["s3"].list_objects_v2(Bucket=aws["bucket"])
        assert objs["KeyCount"] == 1
        assert objs["Contents"][0]["Key"].startswith(f"uploads/{job_id}/")

        # Message enqueued to SQS.
        msgs = aws["sqs"].receive_message(QueueUrl=aws["queue_url"])
        assert len(msgs["Messages"]) == 1
        payload = json.loads(msgs["Messages"][0]["Body"])
        assert payload["job_id"] == job_id
        assert payload["doc_type"] == "text"

    def test_base64_body_is_decoded(self, aws):
        import api.submit as submit

        raw = "a,b\n1,2\n"
        event = _submit_event(base64.b64encode(raw.encode()).decode(), "csv", is_b64=True)
        resp = submit.handler(event, None)
        job_id = json.loads(resp["body"])["job_id"]

        key = f"uploads/{job_id}/"
        objs = aws["s3"].list_objects_v2(Bucket=aws["bucket"], Prefix=key)
        stored = aws["s3"].get_object(Bucket=aws["bucket"], Key=objs["Contents"][0]["Key"])
        assert stored["Body"].read().decode() == raw

    def test_empty_body_rejected(self, aws):
        import api.submit as submit

        resp = submit.handler(_submit_event("", "text"), None)
        assert resp["statusCode"] == 400

    def test_unsupported_type_rejected(self, aws):
        import api.submit as submit

        resp = submit.handler(_submit_event("data", "pdf"), None)
        assert resp["statusCode"] == 400

    def test_enqueue_failure_marks_job_failed(self, aws, monkeypatch):
        """If SQS send fails, the created job must not be orphaned as PENDING."""
        import boto3

        import api.submit as submit
        from common import store

        real_client = boto3.client

        def fake_client(service, *a, **k):
            client = real_client(service, *a, **k)
            if service == "sqs":
                def boom(*a, **k):
                    raise RuntimeError("sqs unavailable")
                monkeypatch.setattr(client, "send_message", boom)
            return client

        monkeypatch.setattr(submit.boto3, "client", fake_client)

        resp = submit.handler(_submit_event("hello world", "text"), None)
        assert resp["statusCode"] == 500
        job_id = json.loads(resp["body"])["job_id"]
        assert store.get_job(aws["table"], job_id)["status"] == "FAILED"

    def test_storage_failure_returns_500(self, aws, monkeypatch):
        import boto3

        import api.submit as submit

        real_client = boto3.client

        def fake_client(service, *a, **k):
            client = real_client(service, *a, **k)
            if service == "s3":
                def boom(*a, **k):
                    raise RuntimeError("s3 unavailable")
                monkeypatch.setattr(client, "put_object", boom)
            return client

        monkeypatch.setattr(submit.boto3, "client", fake_client)

        resp = submit.handler(_submit_event("hello world", "text"), None)
        assert resp["statusCode"] == 500


class TestStatus:
    def test_not_found(self, aws):
        import api.status as status

        resp = status.handler({"pathParameters": {"job_id": "nope"}}, None)
        assert resp["statusCode"] == 404

    def test_returns_result_when_complete(self, aws):
        import api.status as status
        from common import store

        store.create_job(aws["table"], "job-1", "uploads/job-1/f.txt", "text", "f.txt")
        store.mark_complete(aws["table"], "job-1", {"kind": "text", "word_count": 3})

        resp = status.handler({"pathParameters": {"job_id": "job-1"}}, None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["status"] == "COMPLETE"
        assert body["result"]["word_count"] == 3

    def test_returns_error_when_failed(self, aws):
        import api.status as status
        from common import store

        store.create_job(aws["table"], "job-2", "uploads/job-2/f.csv", "csv", "f.csv")
        store.mark_failed(aws["table"], "job-2", "csv document is empty")

        resp = status.handler({"pathParameters": {"job_id": "job-2"}}, None)
        body = json.loads(resp["body"])
        assert body["status"] == "FAILED"
        assert body["error"] == "csv document is empty"

"""Tests for the SQS worker: happy path, idempotency, and failure handling."""
from __future__ import annotations

import json


def _seed(aws, job_id, key, content, doc_type):
    """Create a PENDING job + its S3 object, return an SQS-shaped record."""
    from common import store

    aws["s3"].put_object(Bucket=aws["bucket"], Key=key, Body=content.encode())
    store.create_job(aws["table"], job_id, key, doc_type, "f")
    return {
        "messageId": f"msg-{job_id}",
        "body": json.dumps({"job_id": job_id, "s3_key": key, "doc_type": doc_type}),
    }


def _event(*records):
    return {"Records": list(records)}


class TestHappyPath:
    def test_csv_job_completes(self, aws):
        import worker.handler as worker
        from common import store

        rec = _seed(aws, "j1", "uploads/j1/d.csv", "a,b\n1,2\n3,4\n", "csv")
        resp = worker.handler(_event(rec), None)

        assert resp["batchItemFailures"] == []
        job = store.get_job(aws["table"], "j1")
        assert job["status"] == "COMPLETE"
        assert job["result"]["row_count"] == 2
        assert job["result"]["numeric_stats"]["a"]["mean"] == 2.0

    def test_text_job_completes(self, aws):
        import worker.handler as worker
        from common import store

        rec = _seed(aws, "j2", "uploads/j2/d.txt", "cloud cloud system", "text")
        worker.handler(_event(rec), None)
        assert store.get_job(aws["table"], "j2")["status"] == "COMPLETE"


class TestIdempotency:
    def test_duplicate_delivery_is_noop(self, aws):
        import worker.handler as worker
        from common import store

        rec = _seed(aws, "j3", "uploads/j3/d.txt", "hello world here", "text")
        worker.handler(_event(rec), None)
        first = store.get_job(aws["table"], "j3")

        # Redeliver the exact same message — must not error or change the row.
        resp = worker.handler(_event(rec), None)
        assert resp["batchItemFailures"] == []
        second = store.get_job(aws["table"], "j3")
        assert second["status"] == "COMPLETE"
        assert second["updated_at"] == first["updated_at"]  # untouched

    def test_completed_job_not_overwritten_by_failure(self, aws):
        """A late/duplicate message can't flip a COMPLETE job to FAILED."""
        from common import store

        store.create_job(aws["table"], "j4", "uploads/j4/d.txt", "text", "f")
        store.mark_complete(aws["table"], "j4", {"kind": "text"})
        try:
            store.mark_failed(aws["table"], "j4", "should be ignored")
        except store.AlreadyProcessed:
            pass
        assert store.get_job(aws["table"], "j4")["status"] == "COMPLETE"


class TestFailureHandling:
    def test_malformed_document_marks_failed_not_retried(self, aws):
        import worker.handler as worker
        from common import store

        # Empty CSV -> ExtractionError -> FAILED, but NOT a batch failure.
        rec = _seed(aws, "j5", "uploads/j5/d.csv", "   ", "csv")
        resp = worker.handler(_event(rec), None)

        assert resp["batchItemFailures"] == []  # not requeued / no DLQ
        job = store.get_job(aws["table"], "j5")
        assert job["status"] == "FAILED"
        assert "empty" in job["error"]

    def test_missing_s3_object_is_retryable(self, aws):
        import worker.handler as worker
        from common import store

        # Job row exists but the S3 object is absent -> transient -> retry.
        store.create_job(aws["table"], "j6", "uploads/j6/missing.csv", "csv", "f")
        rec = {
            "messageId": "msg-j6",
            "body": json.dumps(
                {"job_id": "j6", "s3_key": "uploads/j6/missing.csv", "doc_type": "csv"}
            ),
        }
        resp = worker.handler(_event(rec), None)

        assert resp["batchItemFailures"] == [{"itemIdentifier": "msg-j6"}]
        assert store.get_job(aws["table"], "j6")["status"] == "PENDING"

    def test_partial_batch_isolates_failure(self, aws):
        import worker.handler as worker
        from common import store

        good = _seed(aws, "j7", "uploads/j7/d.txt", "alpha beta gamma", "text")
        bad = {
            "messageId": "msg-bad",
            "body": json.dumps(
                {"job_id": "j8", "s3_key": "uploads/j8/missing.txt", "doc_type": "text"}
            ),
        }
        store.create_job(aws["table"], "j8", "uploads/j8/missing.txt", "text", "f")

        resp = worker.handler(_event(good, bad), None)
        assert resp["batchItemFailures"] == [{"itemIdentifier": "msg-bad"}]
        assert store.get_job(aws["table"], "j7")["status"] == "COMPLETE"

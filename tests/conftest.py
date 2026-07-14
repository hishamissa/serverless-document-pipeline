"""Shared pytest fixtures: mocked AWS (moto) and wired-up handler modules."""
from __future__ import annotations

import os

import boto3
import pytest

# Dummy creds so boto3 client construction succeeds under moto.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

BUCKET = "test-documents"
TABLE = "test-jobs"
QUEUE = "test-jobs"


@pytest.fixture
def aws(monkeypatch):
    """Spin up mocked S3 / DynamoDB / SQS and point the handlers at them."""
    from moto import mock_aws

    with mock_aws():
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket=BUCKET)

        dynamodb = boto3.client("dynamodb")
        dynamodb.create_table(
            TableName=TABLE,
            AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )

        sqs = boto3.client("sqs")
        queue_url = sqs.create_queue(QueueName=QUEUE)["QueueUrl"]

        # Point the module-level config at the freshly created resources.
        import api.status
        import api.submit
        import worker.handler

        for mod in (api.submit, worker.handler):
            monkeypatch.setattr(mod, "BUCKET_NAME", BUCKET, raising=False)
        for mod in (api.submit, api.status, worker.handler):
            monkeypatch.setattr(mod, "TABLE_NAME", TABLE, raising=False)
        monkeypatch.setattr(api.submit, "QUEUE_URL", queue_url, raising=False)

        yield {
            "s3": s3,
            "dynamodb": dynamodb,
            "sqs": sqs,
            "queue_url": queue_url,
            "bucket": BUCKET,
            "table": TABLE,
        }

# Reproducible test environment. Mirrors the Lambda runtime (Python 3.12).
#   docker build -t doc-pipeline-tests .
#   docker run --rm doc-pipeline-tests
FROM python:3.12-slim

WORKDIR /app

# Install dev/test dependencies first so this layer caches across code changes.
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .

# moto needs dummy AWS creds/region to satisfy boto3 client construction.
ENV AWS_DEFAULT_REGION=us-east-1 \
    AWS_ACCESS_KEY_ID=testing \
    AWS_SECRET_ACCESS_KEY=testing

CMD ["python", "-m", "pytest", "-v"]

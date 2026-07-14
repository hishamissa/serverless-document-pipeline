#!/usr/bin/env bash
#
# Delete every AWS resource created by this project.
#
# CloudFormation cannot delete a non-empty S3 bucket, so we empty the documents
# bucket first, then delete the stack (which removes the Lambdas, API Gateway,
# SQS queues, DynamoDB table, IAM roles, and CloudWatch alarm).
#
# Usage:
#   ./scripts/teardown.sh [stack-name] [region]
# Defaults: stack-name=doc-pipeline, region=us-east-1
set -euo pipefail

STACK="${1:-doc-pipeline}"
REGION="${2:-us-east-1}"

echo "Looking up documents bucket for stack '$STACK' in $REGION ..."
BUCKET="$(aws cloudformation describe-stacks \
  --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='DocumentsBucketName'].OutputValue" \
  --output text 2>/dev/null || true)"

if [ -n "$BUCKET" ] && [ "$BUCKET" != "None" ]; then
  echo "Emptying s3://$BUCKET ..."
  aws s3 rm "s3://$BUCKET" --recursive --region "$REGION" || true
else
  echo "No documents bucket found (stack may already be gone). Continuing."
fi

echo "Deleting stack '$STACK' ..."
sam delete --stack-name "$STACK" --region "$REGION" --no-prompts

echo "Teardown complete. All resources for '$STACK' have been removed."

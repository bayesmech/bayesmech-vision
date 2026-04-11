#!/usr/bin/env bash
# Push both Docker images to ECR.
# Usage: ./deploy/push-ecr.sh [--region us-east-1]
#
# Build images first (from project root):
#   docker build -f docker/serverjobs/Dockerfile -t bayesmech-serverjobs .
#   docker build -f docker/streamlog/Dockerfile  -t bayesmech-streamlog  .

set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-us-east-1}"
while [[ $# -gt 0 ]]; do
  case $1 in
    --region) REGION="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"

echo "Account : $ACCOUNT"
echo "Registry: $REGISTRY"
echo "Region  : $REGION"

aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"

push_image() {
  local local_tag="$1"
  local repo="$2"

  aws ecr describe-repositories --repository-names "$repo" --region "$REGION" \
    > /dev/null 2>&1 \
    || aws ecr create-repository --repository-name "$repo" --region "$REGION" \
         --image-scanning-configuration scanOnPush=true

  local remote_tag="${REGISTRY}/${repo}:latest"
  docker tag "$local_tag" "$remote_tag"
  docker push "$remote_tag"
  echo "Pushed: $remote_tag"
}

push_image bayesmech-serverjobs bayesmech/serverjobs
push_image bayesmech-streamlog  bayesmech/streamlog

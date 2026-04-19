#!/usr/bin/env bash
# EC2 user-data for the g6.xlarge GPU research instance.
# AMI: Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04)
#   — ships with NVIDIA drivers, CUDA, Docker, and nvidia-container-toolkit pre-installed.
#
# The script:
#   1. Installs AWS CLI v2 + configures ECR credential helper
#   2. Pulls the serverjobs image from ECR
#   3. Mounts the EBS data volume at /data (recordings live here)
#   4. Creates a convenience wrapper script at /usr/local/bin/bm

set -euo pipefail

REGION="us-east-1"           # <-- set your region
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE="${REGISTRY}/bayesmech/serverjobs:latest"
DATA_DEVICE="/dev/nvme1n1"   # second EBS volume; verify with `lsblk` after launch
DATA_MOUNT="/data"

# ── EBS data volume ──────────────────────────────────────────────────────────
# Format only if no filesystem exists yet (idempotent on reboot)
if ! blkid "$DATA_DEVICE" > /dev/null 2>&1; then
  mkfs.ext4 "$DATA_DEVICE"
fi
mkdir -p "$DATA_MOUNT"
mount "$DATA_DEVICE" "$DATA_MOUNT"
echo "${DATA_DEVICE} ${DATA_MOUNT} ext4 defaults,nofail 0 2" >> /etc/fstab

mkdir -p "${DATA_MOUNT}/recordings"

# ── ECR login + pull ─────────────────────────────────────────────────────────
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"
docker pull "$IMAGE"

# ── Convenience wrapper ───────────────────────────────────────────────────────
# Usage examples (run as ubuntu user):
#   bm segmentation recordings/foo/foo.vis.pb --model sam3 --text "person"
#   bm motioncap   recordings/foo/foo.vis.pb --output-video
#   bm reconstruct recordings/foo/foo.vis.pb --no-splat
#   bm genspark    recordings/foo/foo.vis.pb --provider claude
#   bm bash        # drop into a shell inside the container
cat > /usr/local/bin/bm << WRAPPER
#!/usr/bin/env bash
set -euo pipefail
MODULE="\$1"; shift
REGISTRY="${REGISTRY}"
IMAGE="\${REGISTRY}/bayesmech/serverjobs:latest"

# Re-auth (token expires after 12 h)
aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin "\${REGISTRY}" > /dev/null 2>&1

if [ "\$MODULE" = "bash" ]; then
  docker run --rm -it --gpus all \
    -v "${DATA_MOUNT}/recordings:/app/recordings" \
    --env-file /home/ubuntu/.env \
    "\$IMAGE" bash
else
  docker run --rm -it --gpus all \
    -v "${DATA_MOUNT}/recordings:/app/recordings" \
    --env-file /home/ubuntu/.env \
    "\$IMAGE" \
    /app/.venv/bin/python "server/\${MODULE}/main.py" "\$@"
fi
WRAPPER
chmod +x /usr/local/bin/bm

echo "Setup complete. Image: $IMAGE"
echo "Data mount: $DATA_MOUNT"
echo "Usage: bm <segmentation|motioncap|reconstruct|genspark> recordings/foo/foo.vis.pb [args]"

#!/usr/bin/env bash
# EC2 user-data for the streamlog (t3.small) instance.
# AMI: Amazon Linux 2023 (any x86_64 AMI)

set -euo pipefail

REGION="us-east-1"           # <-- set your region
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE="${REGISTRY}/bayesmech/streamlog:latest"
DATA_DEVICE="/dev/nvme1n1"   # second EBS volume for recordings
DATA_MOUNT="/data"

# ── Docker ───────────────────────────────────────────────────────────────────
dnf install -y docker
systemctl enable --now docker
usermod -aG docker ec2-user

# ── AWS CLI v2 ───────────────────────────────────────────────────────────────
dnf install -y unzip
curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
unzip -q /tmp/awscliv2.zip -d /tmp
/tmp/aws/install

# ── EBS data volume ──────────────────────────────────────────────────────────
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

# ── Run streamlog as a systemd service ───────────────────────────────────────
cat > /etc/systemd/system/streamlog.service << SERVICE
[Unit]
Description=BayesMech Streamlog
After=docker.service network-online.target
Requires=docker.service

[Service]
Restart=always
ExecStartPre=-/usr/bin/docker stop streamlog
ExecStartPre=-/usr/bin/docker rm streamlog
ExecStartPre=/bin/sh -c 'aws ecr get-login-password --region ${REGION} | docker login --username AWS --password-stdin ${REGISTRY}'
ExecStart=/usr/bin/docker run --name streamlog \
  -p 8080:8080 \
  -v ${DATA_MOUNT}/recordings:/app/recordings \
  --env-file /home/ec2-user/.env \
  ${IMAGE}
ExecStop=/usr/bin/docker stop streamlog

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable --now streamlog

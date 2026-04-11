#!/usr/bin/env bash
# Launch both EC2 instances.
# Run AFTER push-ecr.sh.
#
# Prereqs:
#   - aws cli configured with sufficient IAM permissions
#   - An EC2 key pair already created (set KEY_NAME below)
#   - A VPC with public subnets (defaults to the default VPC)
#
# Edit the variables in the CONFIG block, then run:
#   bash deploy/launch.sh

set -euo pipefail

# ── CONFIG ────────────────────────────────────────────────────────────────────
REGION="us-east-1"
KEY_NAME="your-keypair-name"        # existing EC2 key pair

# serverjobs instance (g6.xlarge — NVIDIA L4, 4 vCPU, 16 GB RAM)
# AMI: Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04)
# Find latest: aws ec2 describe-images --owners amazon \
#   --filters 'Name=name,Values=Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04)*' \
#   --query 'sort_by(Images,&CreationDate)[-1].ImageId' --output text --region us-east-1
GPU_AMI="ami-0cf5e5f2e78174d97"     # update for your region
GPU_INSTANCE="g6.xlarge"
GPU_EBS_GB=200                       # recordings + model checkpoints

# Streamlog instance (t3.small — 2 vCPU, 2 GB RAM)
# AMI: Amazon Linux 2023
STREAMLOG_AMI="ami-0c02fb55956c7d316" # update for your region (al2023)
STREAMLOG_INSTANCE="t3.small"
STREAMLOG_EBS_GB=50

# ── IAM role for ECR pull ─────────────────────────────────────────────────────
ROLE_NAME="bayesmech-ec2-ecr-role"

create_ecr_role() {
  aws iam get-role --role-name "$ROLE_NAME" > /dev/null 2>&1 && return

  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document '{
      "Version":"2012-10-17",
      "Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]
    }' > /dev/null

  aws iam attach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly

  aws iam create-instance-profile --instance-profile-name "$ROLE_NAME" > /dev/null 2>&1 || true
  aws iam add-role-to-instance-profile \
    --instance-profile-name "$ROLE_NAME" --role-name "$ROLE_NAME" > /dev/null 2>&1 || true

  echo "IAM role created: $ROLE_NAME"
  sleep 10  # propagation delay
}

# ── Security groups ───────────────────────────────────────────────────────────
create_sg() {
  local name="$1" desc="$2"
  local sg_id
  sg_id=$(aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=${name}" \
    --query 'SecurityGroups[0].GroupId' --output text --region "$REGION" 2>/dev/null || true)

  if [[ "$sg_id" == "None" || -z "$sg_id" ]]; then
    sg_id=$(aws ec2 create-security-group \
      --group-name "$name" --description "$desc" \
      --region "$REGION" --query GroupId --output text)
    echo "Created SG: $name ($sg_id)"
  fi
  echo "$sg_id"
}

# ── Launch instance ───────────────────────────────────────────────────────────
launch_instance() {
  local name="$1" ami="$2" type="$3" sg_id="$4" userdata="$5" ebs_gb="$6"

  local instance_id
  instance_id=$(aws ec2 run-instances \
    --region "$REGION" \
    --image-id "$ami" \
    --instance-type "$type" \
    --key-name "$KEY_NAME" \
    --security-group-ids "$sg_id" \
    --iam-instance-profile Name="$ROLE_NAME" \
    --user-data "file://${userdata}" \
    --block-device-mappings "[
      {\"DeviceName\":\"/dev/sda1\",\"Ebs\":{\"VolumeSize\":50,\"VolumeType\":\"gp3\",\"DeleteOnTermination\":true}},
      {\"DeviceName\":\"/dev/sdb\",\"Ebs\":{\"VolumeSize\":${ebs_gb},\"VolumeType\":\"gp3\",\"DeleteOnTermination\":false}}
    ]" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${name}}]" \
    --query 'Instances[0].InstanceId' --output text)

  echo "Launched $name: $instance_id"

  # Wait for public IP
  local public_ip=""
  echo -n "Waiting for IP"
  while [[ -z "$public_ip" || "$public_ip" == "None" ]]; do
    sleep 5; echo -n "."
    public_ip=$(aws ec2 describe-instances --instance-ids "$instance_id" \
      --query 'Reservations[0].Instances[0].PublicIpAddress' --output text --region "$REGION")
  done
  echo ""
  echo "$name public IP: $public_ip"
}

# ── Main ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

create_ecr_role

# serverjobs instance: SSH only (no inbound web traffic)
SERVERJOBS_SG=$(create_sg "bayesmech-serverjobs" "BayesMech serverjobs GPU instance")
aws ec2 authorize-security-group-ingress --group-id "$SERVERJOBS_SG" --region "$REGION" \
  --protocol tcp --port 22 --cidr 0.0.0.0/0 > /dev/null 2>&1 || true
aws ec2 authorize-security-group-egress --group-id "$SERVERJOBS_SG" --region "$REGION" \
  --protocol -1 --port -1 --cidr 0.0.0.0/0 > /dev/null 2>&1 || true

# Streamlog: SSH + port 8080 inbound
STREAMLOG_SG=$(create_sg "bayesmech-streamlog" "BayesMech streamlog server")
aws ec2 authorize-security-group-ingress --group-id "$STREAMLOG_SG" --region "$REGION" \
  --protocol tcp --port 22 --cidr 0.0.0.0/0 > /dev/null 2>&1 || true
aws ec2 authorize-security-group-ingress --group-id "$STREAMLOG_SG" --region "$REGION" \
  --protocol tcp --port 8080 --cidr 0.0.0.0/0 > /dev/null 2>&1 || true

launch_instance "bayesmech-serverjobs" "$GPU_AMI"       "$GPU_INSTANCE"       "$SERVERJOBS_SG" "${SCRIPT_DIR}/userdata-gpu.sh"       "$GPU_EBS_GB"
launch_instance "bayesmech-streamlog"  "$STREAMLOG_AMI" "$STREAMLOG_INSTANCE" "$STREAMLOG_SG"  "${SCRIPT_DIR}/userdata-streamlog.sh" "$STREAMLOG_EBS_GB"

echo ""
echo "SSH into serverjobs instance: ssh ubuntu@<serverjobs-ip>  -i ~/.ssh/${KEY_NAME}.pem"
echo "SSH into streamlog instance:  ssh ec2-user@<sl-ip>        -i ~/.ssh/${KEY_NAME}.pem"
echo "Dashboard:                   http://<streamlog-ip>:8080"
echo ""
echo "user-data runs at first boot (~5 min). Tail progress:"
echo "  GPU:       sudo tail -f /var/log/cloud-init-output.log"
echo "  Streamlog: sudo tail -f /var/log/cloud-init-output.log"

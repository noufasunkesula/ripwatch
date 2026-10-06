#!/usr/bin/env bash
# rw-worker first boot (sprint-1.md N-10). Rendered by Terraform templatefile() in the compute
# stack: $${...} is a literal shell variable, single-dollar braces are Terraform inputs.
# Idempotent: safe to run again. Logs to /var/log/rw/user-data.log.
set -euo pipefail

mkdir -p /var/log/rw
exec > >(tee -a /var/log/rw/user-data.log) 2>&1
echo "user-data start $(date -u +%FT%TZ)"

REGION="${region}"
ARTIFACTS_BUCKET="${artifacts_bucket}"
RW_RUNTIME="${runtime}"
export AWS_DEFAULT_REGION="$${REGION}"

# 1. User and directories
id rw >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin rw
install -d -o rw -g rw /opt/rw /opt/rw/bin /opt/rw/releases /etc/rw /var/log/rw /var/lib/rw/work /var/run/rw

# AWS CLI v2 (needed below); the COOL and Ubuntu AMIs may not ship it.
if ! command -v aws >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y unzip curl
  arch="$(uname -m)"
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$${arch}.zip" -o /tmp/awscliv2.zip
  unzip -q -o /tmp/awscliv2.zip -d /tmp && /tmp/aws/install --update
fi

# 2. SSM agent
if ! snap list amazon-ssm-agent >/dev/null 2>&1; then
  snap install amazon-ssm-agent --classic
fi
systemctl enable --now snap.amazon-ssm-agent.amazon-ssm-agent.service

# 3. CloudWatch agent, config from SSM /rw/cloudwatch-agent/config
if [[ ! -x /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl ]]; then
  deb_arch="$(dpkg --print-architecture)"
  curl -fsSL "https://amazoncloudwatch-agent.s3.amazonaws.com/ubuntu/$${deb_arch}/latest/amazon-cloudwatch-agent.deb" \
    -o /tmp/amazon-cloudwatch-agent.deb
  dpkg -i -E /tmp/amazon-cloudwatch-agent.deb
fi
/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 \
  -c ssm:/rw/cloudwatch-agent/config -s

# 4. /etc/rw/rw.env (queue URLs looked up by name)
account_id="$(aws sts get-caller-identity --query Account --output text)"
jobs_url="$(aws sqs get-queue-url --queue-name rw-jobs --query QueueUrl --output text)"
candidates_url="$(aws sqs get-queue-url --queue-name rw-candidates --query QueueUrl --output text)"

# 5. Python environment
if [[ "$${RW_RUNTIME}" == cool ]]; then
  RW_PYTHON=/opt/cool/venvs/python_3.12/bin/python
  # Pin numpy to COOL's version so pip never replaces it.
  "$${RW_PYTHON}" -m pip freeze | grep -i '^numpy==' > /opt/rw/cool-constraints.txt || true
else
  apt-get install -y python3.12-venv
  [[ -x /opt/rw/venv/bin/python ]] || python3.12 -m venv /opt/rw/venv
  RW_PYTHON=/opt/rw/venv/bin/python
  "$${RW_PYTHON}" -m pip install --upgrade pip
  "$${RW_PYTHON}" -m pip install "opencv-python-headless==5.0.0.93"
  : > /opt/rw/cool-constraints.txt
fi

cat > /etc/rw/rw.env <<EOF
${rw_env}
EOF
chmod 0644 /etc/rw/rw.env
sed -i \
  -e "s|@ACCOUNT_ID@|$${account_id}|g" \
  -e "s|@QUEUE_JOBS_URL@|$${jobs_url}|g" \
  -e "s|@QUEUE_CANDIDATES_URL@|$${candidates_url}|g" \
  -e "s|@RW_PYTHON@|$${RW_PYTHON}|g" \
  /etc/rw/rw.env

# systemd cannot expand variables in ExecStart's executable, so units call this wrapper.
cat > /opt/rw/bin/run <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exec "$${RW_PYTHON}" -m "rw.$1"
EOF
chmod 0755 /opt/rw/bin/run

# 6. First deploy, unless no release has been published yet
release="$(aws ssm get-parameter --name /rw/release --query Parameter.Value --output text)"
if [[ "$${release}" != none ]]; then
  aws s3 cp "s3://$${ARTIFACTS_BUCKET}/releases/$${release}/rw_deploy.sh" /opt/rw/bin/rw_deploy.sh
  chmod 0755 /opt/rw/bin/rw_deploy.sh
  /opt/rw/bin/rw_deploy.sh "$${release}"
else
  echo "/rw/release is none: nothing to deploy yet"
fi

echo "user-data done $(date -u +%FT%TZ)"

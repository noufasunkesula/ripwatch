#!/usr/bin/env bash
# Install a release on the worker (sprint-1.md N-10). Run by user data and SSM rw-deploy:
#   rw_deploy.sh <release_sha>
# Rolls back to /etc/rw/release.previous when the new release fails its health check.
set -euo pipefail

RELEASE="${1:?usage: rw_deploy.sh <release_sha>}"
ROLLBACK="${RW_DEPLOY_ROLLBACK:-0}"
HEALTH_WINDOW_S=60
HEARTBEAT_MAX_AGE_S=30
SERVICES=(rw-camera-sim rw-ingest rw-vision rw-mcp-tools rw-agent)
DEPLOY_LOG=/var/log/rw/deploy.log

set -a
# shellcheck disable=SC1091  # written by user data on the worker
source /etc/rw/rw.env
set +a
: "${RW_PYTHON:?RW_PYTHON missing in /etc/rw/rw.env}"
: "${RW_ARTIFACTS_BUCKET:?RW_ARTIFACTS_BUCKET missing in /etc/rw/rw.env}"
: "${RW_RUNTIME:?RW_RUNTIME missing in /etc/rw/rw.env}"

log() { echo "rw_deploy: $*" >&2; }

dir="/opt/rw/releases/${RELEASE}"

# 1. Download the release (wheel, requirements, units, scripts)
log "downloading ${RELEASE}"
mkdir -p "${dir}"
aws s3 cp --recursive --only-show-errors "s3://${RW_ARTIFACTS_BUCKET}/releases/${RELEASE}/" "${dir}/"
wheel="$(find "${dir}" -maxdepth 1 -name 'rw-*.whl' | head -n 1)"
[[ -n "${wheel}" ]] || { log "no wheel in ${dir}"; exit 1; }

# 2. Install. Constraints pin numpy to COOL's and make any OpenCV wheel unresolvable.
log "installing into ${RW_PYTHON}"
"${RW_PYTHON}" -m pip install --quiet --no-deps --force-reinstall "${wheel}"
constraints=(-c "${dir}/constraints.txt")
[[ -s /opt/rw/cool-constraints.txt ]] && constraints+=(-c /opt/rw/cool-constraints.txt)
"${RW_PYTHON}" -m pip install --quiet -r "${dir}/requirements-worker.txt" "${constraints[@]}"
if [[ "${RW_RUNTIME}" == cool ]] && "${RW_PYTHON}" -m pip list 2>/dev/null | grep -qi '^opencv-'; then
  log "pip OpenCV found in the COOL venv; refusing to deploy"
  exit 1
fi

# 3. The cv2 in use must be the one this runtime expects (exit 78 otherwise)
"${RW_PYTHON}" -c "from rw.common.runtime import check_cv2_runtime; check_cv2_runtime()"

# 4. Units and log rotation from the release
install -m 0755 "${dir}/rw_deploy.sh" /opt/rw/bin/rw_deploy.sh
install -m 0644 "${dir}"/systemd/rw-*.service /etc/systemd/system/
install -m 0644 "${dir}/logrotate-rw" /etc/logrotate.d/rw
systemctl daemon-reload
for s in "${SERVICES[@]}"; do systemctl enable --quiet "${s}"; done
systemctl restart "${SERVICES[@]}"

# 5. Health: every unit active and every heartbeat fresh, within the window
healthy() {
  local s age now
  now="$(date +%s)"
  for s in "${SERVICES[@]}"; do
    systemctl is-active --quiet "${s}" || return 1
    [[ -f "/var/run/rw/${s}.heartbeat" ]] || return 1
    age=$(( now - $(cut -d. -f1 < "/var/run/rw/${s}.heartbeat") ))
    (( age <= HEARTBEAT_MAX_AGE_S )) || return 1
  done
}

deadline=$(( $(date +%s) + HEALTH_WINDOW_S ))
until healthy; do
  if (( $(date +%s) >= deadline )); then
    log "health check failed for ${RELEASE}"
    previous="$(cat /etc/rw/release 2>/dev/null || true)"
    if [[ "${ROLLBACK}" == 0 && -n "${previous}" && "${previous}" != "${RELEASE}" ]]; then
      log "rolling back to ${previous}"
      RW_DEPLOY_ROLLBACK=1 /opt/rw/bin/rw_deploy.sh "${previous}" || true
    fi
    exit 1
  fi
  sleep 5
done

# 6. Record the release
if [[ -f /etc/rw/release ]] && [[ "$(cat /etc/rw/release)" != "${RELEASE}" ]]; then
  cp /etc/rw/release /etc/rw/release.previous
fi
echo "${RELEASE}" > /etc/rw/release
cv2_version="$("${RW_PYTHON}" -c 'import cv2; print(cv2.__version__)')"
printf '{"ts":"%s","release":"%s","runtime":"%s","cv2":"%s","rollback":%s}\n' \
  "$(date -u +%FT%TZ)" "${RELEASE}" "${RW_RUNTIME}" "${cv2_version}" \
  "$([[ "${ROLLBACK}" == 1 ]] && echo true || echo false)" >> "${DEPLOY_LOG}"
log "deployed ${RELEASE}"

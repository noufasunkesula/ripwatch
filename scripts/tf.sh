#!/usr/bin/env bash
# Terraform wrapper behind the Makefile (sprint-1.md N-05 and section 0).
#   scripts/tf.sh <init|plan|plan-local|apply|destroy|validate|plan-all> [stack]
# READ commands (init, plan, plan-local, validate, plan-all) never change AWS.
# apply and destroy check RW_CONFIRM_APPLY and only ever use what a human just saw.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACKS=(bootstrap iam network data compute serverless edge observability)
PLAN_MAX_AGE_MIN=30
PY="${UV:-uv} run python"

die() { echo "tf.sh: $*" >&2; exit 1; }
say() { echo "tf.sh: $*"; }

stack_dir() {
  local stack="$1"
  [[ " ${STACKS[*]} " == *" ${stack} "* ]] || die "unknown stack '${stack}' (one of: ${STACKS[*]})"
  if [[ "${stack}" == bootstrap ]]; then echo "${ROOT}/infra/bootstrap"; else echo "${ROOT}/infra/stacks/${stack}"; fi
}

require_dir() {
  [[ -d "$1" ]] || die "$1 does not exist yet (written in N-06 to N-09)"
}

tf() { terraform -chdir="$1" "${@:2}"; }

init() {
  local stack="$1" dir; dir="$(stack_dir "${stack}")"; require_dir "${dir}"
  if [[ "${stack}" == bootstrap ]]; then
    say "init ${stack} (local state)"
    tf "${dir}" init -input=false
  else
    [[ -f "${ROOT}/infra/backend.hcl" ]] || die "infra/backend.hcl missing: run 'make backend-config'"
    say "init ${stack} (S3 backend)"
    tf "${dir}" init -input=false -backend-config=../../backend.hcl
  fi
}

summary() {
  local dir="$1" planfile="$2" stack="$3"
  tf "${dir}" show -json "${planfile}" | ${PY} "${ROOT}/scripts/plan_summary.py" "${stack}"
}

plan() {
  local stack="$1" dir; dir="$(stack_dir "${stack}")"
  init "${stack}"
  say "plan ${stack} -> tfplan-${stack}"
  tf "${dir}" plan -input=false -out="tfplan-${stack}"
  summary "${dir}" "tfplan-${stack}" "${stack}"
}

# Plan against real AWS before the state bucket exists. Uses its own data dir and plan file
# name, so its throwaway local backend never touches the real one and apply can never use it.
plan_local() {
  local stack="$1" dir; dir="$(stack_dir "${stack}")"; require_dir "${dir}"
  OVERRIDE="${dir}/backend_override.tf"
  trap 'rm -f "${OVERRIDE}"' EXIT
  printf 'terraform {\n  backend "local" {\n    path = ".terraform-local/throwaway.tfstate"\n  }\n}\n' \
    > "${OVERRIDE}"
  say "plan-local ${stack} (throwaway local backend) -> tfplan-${stack}-local"
  TF_DATA_DIR=".terraform-local" tf "${dir}" init -input=false -reconfigure
  TF_DATA_DIR=".terraform-local" tf "${dir}" plan -input=false -out="tfplan-${stack}-local"
  TF_DATA_DIR=".terraform-local" summary "${dir}" "tfplan-${stack}-local" "${stack}"
  rm -f "${OVERRIDE}"
}

apply() {
  local stack="$1" dir; dir="$(stack_dir "${stack}")"
  [[ "${RW_CONFIRM_APPLY:-}" == "${stack}" ]] || die "apply ${stack} needs RW_CONFIRM_APPLY=${stack} (sprint-1.md section 0)"
  local planfile="${dir}/tfplan-${stack}"
  [[ -f "${planfile}" ]] || die "no saved plan ${planfile}: run 'make plan STACK=${stack}' and review it"
  [[ -z "$(find "${planfile}" -mmin +"${PLAN_MAX_AGE_MIN}")" ]] \
    || die "plan is older than ${PLAN_MAX_AGE_MIN} min: plan again and review it"
  say "apply ${stack} from the saved plan only"
  tf "${dir}" apply -input=false "tfplan-${stack}"
  rm -f "${planfile}"
}

destroy() {
  local stack="$1" dir; dir="$(stack_dir "${stack}")"
  local want="destroy-${stack}"
  [[ "${stack}" == bootstrap || "${stack}" == data ]] && want="destroy-${stack}-really"
  [[ "${RW_CONFIRM_APPLY:-}" == "${want}" ]] || die "destroy ${stack} needs RW_CONFIRM_APPLY=${want}"
  init "${stack}"
  say "destroy ${stack}: terraform shows the plan and asks you to type yes"
  tf "${dir}" destroy -input=true
}

validate() {
  local stack dir found=0
  for stack in "${STACKS[@]}"; do
    dir="$(stack_dir "${stack}")"
    if [[ ! -d "${dir}" ]]; then say "validate ${stack}: skipped (not written yet)"; continue; fi
    found=1
    say "validate ${stack}"
    TF_DATA_DIR="${dir}/.terraform-validate" tf "${dir}" init -backend=false -input=false >/dev/null
    TF_DATA_DIR="${dir}/.terraform-validate" tf "${dir}" validate
  done
  [[ "${found}" == 1 ]] || say "validate: no stacks yet"
}

state_bucket_exists() {
  [[ -n "${AWS_ACCOUNT_ID:-}" ]] || die "AWS_ACCOUNT_ID missing in .env"
  aws s3api head-bucket --bucket "rw-tfstate-${AWS_ACCOUNT_ID}" >/dev/null 2>&1
}

applied() {
  aws s3api head-object --bucket "rw-tfstate-${AWS_ACCOUNT_ID}" --key "$1/terraform.tfstate" \
    >/dev/null 2>&1
}

plan_all() {
  plan bootstrap
  if ! state_bucket_exists; then
    say "state bucket missing: network and data via plan-local, the rest skipped (section 0.3)"
    plan_local network
    plan_local data
    say "skip iam: needs the bootstrap apply"
    say "skip compute, serverless, edge, observability: need earlier stacks applied"
    return
  fi
  plan iam
  plan network
  plan data
  local stack needs dep missing
  declare -A NEEDS=(
    [compute]="network data"
    [serverless]="compute data"
    [edge]="serverless"
    [observability]="network data compute serverless edge"
  )
  for stack in compute serverless edge observability; do
    needs="${NEEDS[${stack}]}"; missing=""
    for dep in ${needs}; do applied "${dep}" || missing+="${dep} "; done
    if [[ -n "${missing}" ]]; then say "skip ${stack}: needs ${missing% } applied"; else plan "${stack}"; fi
  done
}

cmd="${1:-}"; stack="${2:-}"
case "${cmd}" in
  init|plan|plan-local|apply|destroy)
    [[ -n "${stack}" ]] || die "usage: make ${cmd} STACK=<${STACKS[*]// /|}>"
    case "${cmd}" in
      init) init "${stack}" ;; plan) plan "${stack}" ;; plan-local) plan_local "${stack}" ;;
      apply) apply "${stack}" ;; destroy) destroy "${stack}" ;;
    esac ;;
  validate) validate ;;
  plan-all) plan_all ;;
  *) die "usage: tf.sh <init|plan|plan-local|apply|destroy|validate|plan-all> [stack]" ;;
esac

# RipWatch Makefile (sprint-1.md N-05). Run inside WSL2 or Linux. Values come from .env.
#
# READ targets only read from AWS and are always allowed.
# CHANGE targets modify AWS and need RW_CONFIRM_APPLY=<name> typed on the command line, e.g.
#   make apply STACK=bootstrap RW_CONFIRM_APPLY=bootstrap
# Claude Code runs CHANGE targets only after a human says "apply" (sprint-1.md section 0.2).

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

-include .env
export

UV ?= uv
PY := $(UV) run python
TF := scripts/tf.sh
SHELLCHECK ?= $(UV) run --with shellcheck-py shellcheck
CHECKOV ?= $(UV) tool run checkov
MOTO_PORT ?= 5000
SHA := $(shell git rev-parse --short HEAD 2>/dev/null || echo nogit)
STACKS_UP := iam network data compute serverless edge observability
SERVICE ?= rw-agent

# CHANGE guard. RW_CONFIRM_APPLY must come from the command line or shell, never from .env,
# so a stale value in a file can never approve anything.
define confirm
	@if [[ "$(origin RW_CONFIRM_APPLY)" != "command line" && "$(origin RW_CONFIRM_APPLY)" != "environment" ]] \
		|| [[ "$${RW_CONFIRM_APPLY:-}" != "$(1)" ]]; then \
		echo "Refused: this target changes AWS. Nothing is applied, changed or deleted unless a human"; \
		echo "says so (sprint-1.md section 0). Re-run with RW_CONFIRM_APPLY=$(1) on the command line."; \
		exit 1; \
	fi
endef

define need_stack
	@[[ -n "$(STACK)" ]] || { echo "STACK is required, e.g. make $@ STACK=bootstrap" >&2; exit 1; }
endef

.PHONY: help setup fmt lint test test-int tf-validate checkov schemas check backend-config \
	local-up local-demo local-down release whoami init plan plan-local plan-all apply destroy \
	up down wake sleep deploy ssm logs bench bench-local

help: ## List targets
	@grep -hE '^[a-zA-Z_-]+:.*## ' $(firstword $(MAKEFILE_LIST)) | awk -F ':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

# ------------------------------------------------------------------ local development
setup: ## Install deps (dev + cv-std) and the pre-commit hooks
	@echo "setup: uv sync --extra dev --extra cv-std, pre-commit install"
	$(UV) sync --extra dev --extra cv-std
	$(UV) run pre-commit install

fmt: ## Format Python (ruff) and Terraform
	@echo "fmt: ruff format, terraform fmt -recursive"
	$(UV) run ruff format .
	@if [[ -n "$$(find infra -name '*.tf' -print -quit)" ]]; then terraform fmt -recursive infra; fi

lint: ## ruff check, shellcheck on deploy/ and scripts/, tflint on every stack
	@echo "lint: ruff check, shellcheck, tflint"
	$(UV) run ruff check .
	@files="$$(find deploy scripts -name '*.sh' ! -path deploy/user_data.sh 2>/dev/null)"; \
		if [[ -n "$$files" ]]; then $(SHELLCHECK) $$files; else echo "lint: no shell scripts"; fi
	@dirs="$$(for d in infra/bootstrap infra/stacks/* infra/modules/*; do compgen -G "$$d/*.tf" >/dev/null && echo "$$d"; done; true)"; \
		if [[ -z "$$dirs" ]]; then echo "lint: no Terraform stacks yet, tflint skipped"; exit 0; fi; \
		command -v tflint >/dev/null || { echo "tflint not installed: https://github.com/terraform-linters/tflint#installation" >&2; exit 1; }; \
		tflint --init --config "$$PWD/.tflint.hcl" >/dev/null; \
		for d in $$dirs; do echo "tflint $$d"; tflint --chdir "$$d" --config "$$PWD/.tflint.hcl"; done

test: ## Unit tests
	@echo "test: pytest tests/unit"
	$(UV) run pytest tests/unit -q

test-int: ## Start moto server, run tests/integration, stop it
	@echo "test-int: moto server on :$(MOTO_PORT), pytest tests/integration"
	@$(UV) run moto_server -p $(MOTO_PORT) >/dev/null 2>&1 & pid=$$!; \
		trap 'kill $$pid 2>/dev/null' EXIT; \
		for _ in $$(seq 50); do curl -sf "http://127.0.0.1:$(MOTO_PORT)/moto-api/" >/dev/null && break; sleep 0.2; done; \
		RW_AWS_ENDPOINT_URL="http://127.0.0.1:$(MOTO_PORT)" AWS_ACCESS_KEY_ID=testing AWS_SECRET_ACCESS_KEY=testing \
			AWS_PROFILE= $(UV) run pytest tests/integration -q || { rc=$$?; [[ $$rc == 5 ]] && echo "test-int: no integration tests yet"; [[ $$rc == 5 ]]; }

tf-validate: ## terraform init -backend=false + validate for bootstrap and every stack (offline)
	@echo "tf-validate: offline init + validate"
	$(TF) validate

checkov: ## checkov on infra/ (config in .checkov.yaml)
	@echo "checkov: infra/"
	@if [[ -z "$$(find infra -name '*.tf' -print -quit)" ]]; then echo "checkov: no Terraform yet, skipped"; \
		else $(CHECKOV) --config-file .checkov.yaml; fi

schemas: ## Regenerate docs/contracts/*.json from the contract models
	@echo "schemas: python -m rw.contracts.export_schemas"
	$(PY) -m rw.contracts.export_schemas

check: ## Everything CI runs: format check, lint, tests, tf validate, checkov, schema drift
	@echo "check: fmt check, lint, test, test-int, tf-validate, checkov, schema drift"
	$(UV) run ruff format --check .
	@if [[ -n "$$(find infra -name '*.tf' -print -quit)" ]]; then terraform fmt -check -recursive infra; fi
	$(MAKE) --no-print-directory lint test test-int tf-validate checkov
	$(PY) -m rw.contracts.export_schemas --check

backend-config: ## Render infra/backend.hcl from AWS_ACCOUNT_ID and AWS_REGION in .env
	@echo "backend-config: writing infra/backend.hcl from .env"
	@[[ "$${AWS_ACCOUNT_ID:-}" =~ ^[0-9]{12}$$ ]] || { echo "AWS_ACCOUNT_ID in .env must be 12 digits" >&2; exit 1; }
	@sed -e "s/<account_id>/$${AWS_ACCOUNT_ID}/" -e "s/us-east-1/$${AWS_REGION:-us-east-1}/" \
		infra/backend.hcl.example > infra/backend.hcl
	@echo "backend-config: bucket rw-tfstate-$${AWS_ACCOUNT_ID}, region $${AWS_REGION:-us-east-1}"

local-up: ## Start moto server on :5000 and seed it (scripts/local_seed.py)
	@echo "local-up: moto server on :$(MOTO_PORT), seed"
	@mkdir -p work
	@if curl -sf "http://127.0.0.1:$(MOTO_PORT)/moto-api/" >/dev/null; then echo "local-up: moto already running"; \
		else nohup $(UV) run moto_server -p $(MOTO_PORT) > work/moto.log 2>&1 & echo $$! > work/moto.pid; \
		for _ in $$(seq 50); do curl -sf "http://127.0.0.1:$(MOTO_PORT)/moto-api/" >/dev/null && break; sleep 0.2; done; fi
	@[[ -f scripts/local_seed.py ]] || { echo "scripts/local_seed.py not written yet (N-13)" >&2; exit 1; }
	RW_AWS_ENDPOINT_URL="http://127.0.0.1:$(MOTO_PORT)" $(PY) scripts/local_seed.py

local-demo: ## Run the full loop locally (scripts/local_demo.py, J-01)
	@echo "local-demo: scripts/local_demo.py with RW_LLM=fake"
	@[[ -f scripts/local_demo.py ]] || { echo "scripts/local_demo.py not written yet (J-01)" >&2; exit 1; }
	RW_AWS_ENDPOINT_URL="http://127.0.0.1:$(MOTO_PORT)" RW_LLM=fake $(PY) scripts/local_demo.py

local-down: ## Stop moto server and local services
	@echo "local-down: stopping moto server"
	@if [[ -f work/moto.pid ]]; then kill "$$(cat work/moto.pid)" 2>/dev/null || true; rm -f work/moto.pid; fi
	@pkill -f "moto_server -p $(MOTO_PORT)" 2>/dev/null || true

release: ## Build the wheel and requirements-worker.txt into dist/<git_sha>/ (no upload)
	@echo "release: dist/$(SHA)/"
	@mkdir -p dist/$(SHA)
	$(UV) build --wheel --out-dir dist/$(SHA)
	$(UV) export --frozen --no-dev --no-hashes --no-emit-project --format requirements-txt \
		-o dist/$(SHA)/requirements-worker.txt >/dev/null
	@! grep -qi '^opencv' dist/$(SHA)/requirements-worker.txt || { echo "OpenCV in worker requirements" >&2; exit 1; }

# ------------------------------------------------------------------ AWS: READ
whoami: ## READ: aws sts get-caller-identity, fails if the caller is root
	@echo "whoami: aws sts get-caller-identity (profile $${AWS_PROFILE:-unset})"
	@arn="$$(aws sts get-caller-identity --query Arn --output text)"; echo "$$arn"; \
		[[ "$$arn" != *":root" ]] || { echo "Refused: root account. Use your IAM user." >&2; exit 1; }

init: ## READ: terraform init STACK=<name> (bootstrap: local state)
	$(need_stack)
	$(TF) init $(STACK)

plan: ## READ: terraform plan STACK=<name> -> tfplan-<name>, prints a cost summary
	$(need_stack)
	@$(MAKE) --no-print-directory whoami
	$(TF) plan $(STACK)

plan-local: ## READ: plan STACK=<name> with a throwaway local backend
	$(need_stack)
	@$(MAKE) --no-print-directory whoami
	$(TF) plan-local $(STACK)

plan-all: whoami ## READ: plan every stack plannable right now (section 0.3), skip the rest
	$(TF) plan-all

ssm: ## READ: open an SSM session to the worker
	@echo "ssm: session to the instance in rw-worker-asg"
	@id="$$(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names rw-worker-asg \
		--query 'AutoScalingGroups[0].Instances[0].InstanceId' --output text)"; \
		[[ "$$id" != None && -n "$$id" ]] || { echo "No worker running. make wake first." >&2; exit 1; }; \
		aws ssm start-session --target "$$id"

logs: ## READ: tail /rw/worker/$(SERVICE) (SERVICE=rw-ingest|rw-vision|rw-agent|...)
	@echo "logs: aws logs tail /rw/worker/$(SERVICE) --follow"
	aws logs tail "/rw/worker/$(SERVICE)" --follow

# ------------------------------------------------------------------ AWS: CHANGE
apply: ## CHANGE: apply the saved tfplan-<STACK> (RW_CONFIRM_APPLY=<stack>, plan < 30 min old)
	$(need_stack)
	$(call confirm,$(STACK))
	$(TF) apply $(STACK)

destroy: ## CHANGE: destroy STACK (RW_CONFIRM_APPLY=destroy-<stack>, bootstrap/data: ...-really)
	$(need_stack)
	$(call confirm,$(if $(filter bootstrap data,$(STACK)),destroy-$(STACK)-really,destroy-$(STACK)))
	$(TF) destroy $(STACK)

up: ## CHANGE: plan and apply every stack in order, asking before each apply (RW_CONFIRM_APPLY=up)
	$(call confirm,up)
	@for s in $(STACKS_UP); do \
		$(TF) plan "$$s"; \
		read -r -p "Apply $$s? Type yes: " ans; [[ "$$ans" == yes ]] || { echo "Stopped before $$s"; exit 1; }; \
		RW_CONFIRM_APPLY="$$s" $(TF) apply "$$s"; \
	done

down: ## CHANGE: destroy stacks in reverse order, never bootstrap or data (RW_CONFIRM_APPLY=down)
	$(call confirm,down)
	@for s in observability edge serverless compute network iam; do \
		read -r -p "Destroy $$s? Type yes: " ans; [[ "$$ans" == yes ]] || { echo "Stopped before $$s"; exit 1; }; \
		RW_CONFIRM_APPLY="destroy-$$s" $(TF) destroy "$$s"; \
	done
	@echo "down: bootstrap and data kept. Use make destroy STACK=data RW_CONFIRM_APPLY=destroy-data-really"

wake: ## CHANGE: invoke rw-scheduler {"action":"wake"} (RW_CONFIRM_APPLY=wake)
	$(call confirm,wake)
	aws lambda invoke --function-name rw-scheduler --cli-binary-format raw-in-base64-out \
		--payload '{"action":"wake"}' /dev/stdout

sleep: ## CHANGE: invoke rw-scheduler {"action":"sleep"} (RW_CONFIRM_APPLY=sleep)
	$(call confirm,sleep)
	aws lambda invoke --function-name rw-scheduler --cli-binary-format raw-in-base64-out \
		--payload '{"action":"sleep"}' /dev/stdout

deploy: release ## CHANGE: upload release, set /rw/release, run SSM rw-deploy (RW_CONFIRM_APPLY=deploy)
	$(call confirm,deploy)
	@[[ "$${AWS_ACCOUNT_ID:-}" =~ ^[0-9]{12}$$ ]] || { echo "AWS_ACCOUNT_ID missing in .env" >&2; exit 1; }
	aws s3 cp --recursive dist/$(SHA) s3://rw-artifacts-$${AWS_ACCOUNT_ID}/releases/$(SHA)/
	aws ssm put-parameter --name /rw/release --type String --overwrite --value $(SHA)
	aws ssm send-command --document-name rw-deploy --targets Key=tag:rw:role,Values=worker \
		--parameters release=$(SHA) --comment "make deploy $(SHA)"

bench: ## CHANGE: run scripts/bench_remote.sh on Spot instances (RW_CONFIRM_APPLY=bench)
	$(call confirm,bench)
	@[[ -f scripts/bench_remote.sh ]] || { echo "scripts/bench_remote.sh not written yet (N-14)" >&2; exit 1; }
	bash scripts/bench_remote.sh

bench-local: ## Run python -m rw.bench on this machine into work/bench/<run_id>/
	@echo "bench-local: python -m rw.bench"
	$(PY) -m rw.bench

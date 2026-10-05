# RipWatch Makefile. Full target list arrives with N-05 (sprint-1.md); N-03 adds backend-config.
# Values come from .env (gitignored, copy from .env.example).

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

-include .env
export

.PHONY: help backend-config

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

backend-config: ## Render infra/backend.hcl from AWS_ACCOUNT_ID and AWS_REGION in .env
	@echo "backend-config: writing infra/backend.hcl from .env"
	@[[ "$${AWS_ACCOUNT_ID:-}" =~ ^[0-9]{12}$$ ]] || { echo "AWS_ACCOUNT_ID in .env must be 12 digits" >&2; exit 1; }
	@sed -e "s/<account_id>/$${AWS_ACCOUNT_ID}/" -e "s/us-east-1/$${AWS_REGION:-us-east-1}/" \
		infra/backend.hcl.example > infra/backend.hcl
	@echo "backend-config: bucket rw-tfstate-$${AWS_ACCOUNT_ID}, region $${AWS_REGION:-us-east-1}"

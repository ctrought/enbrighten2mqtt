# enbrighten2mqtt — local CI / release.
#
# Nothing here hardcodes a GitHub account: OWNER defaults to the owner parsed
# from your git remote, and every value is overridable on the command line:
#   make push OWNER=myuser
#   make release VERSION=v0.1.0
#
# GHCR (ghcr.io) is free for public images; a private image needs a token with
# write:packages (set GHCR_TOKEN, or have the `gh` CLI logged in).

REGISTRY   ?= ghcr.io
OWNER      ?= $(shell git config --get remote.origin.url 2>/dev/null | sed -E 's|.*[:/]([^/]+)/[^/]+(\.git)?$$|\1|')
NAME       ?= enbrighten2mqtt
IMAGE      ?= $(REGISTRY)/$(OWNER)/$(NAME)

PKG_VERSION := $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
# VERSION is the git tag (vX.Y.Z); IMG_TAG is the image tag (X.Y.Z, no leading v).
VERSION    ?= $(shell git describe --tags --exact-match 2>/dev/null || echo v$(PKG_VERSION))
IMG_TAG    := $(patsubst v%,%,$(VERSION))
PLATFORMS  ?= linux/amd64,linux/arm64
PYTHON     ?= python3
VENV       := .venv

.DEFAULT_GOAL := help

.PHONY: help
help: ## List targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*## "}{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}'
	@echo "\n  IMAGE=$(IMAGE)  VERSION=$(VERSION)  IMG_TAG=$(IMG_TAG)"

$(VENV): ## Create the dev virtualenv
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -q -e . pytest

.PHONY: test
test: $(VENV) ## Run the unit tests
	$(VENV)/bin/python -m pytest -q

.PHONY: lint
lint: $(VENV) ## Byte-compile the package (quick sanity check)
	$(VENV)/bin/python -m compileall -q enbrighten2mqtt

.PHONY: build
build: ## Build the image for the local arch (into the local docker)
	docker build -t $(IMAGE):$(IMG_TAG) -t $(IMAGE):latest .

.PHONY: run
run: ## Run the freshly built image locally (needs email/password/MQTT in env)
	docker run --rm --network host \
	  -e ENBRIGHTEN_EMAIL -e ENBRIGHTEN_PASSWORD -e MQTT_HOST \
	  -v $(PWD)/secrets:/app/secrets:ro -v $(PWD)/data:/data $(IMAGE):latest

.PHONY: ci
ci: test lint build ## Local CI: test + lint + build

.PHONY: login
login: ## docker login to $(REGISTRY) (uses $$GHCR_TOKEN, else `gh auth token`)
	@test -n "$(OWNER)" || { echo "OWNER is empty (set OWNER=... or a git remote)"; exit 1; }
	@printf '%s' "$${GHCR_TOKEN:-$$(gh auth token 2>/dev/null)}" \
	  | docker login $(REGISTRY) -u "$(OWNER)" --password-stdin

.PHONY: buildx
buildx: ## Ensure a multi-arch buildx builder exists
	@docker buildx inspect e2m >/dev/null 2>&1 || docker buildx create --name e2m --use >/dev/null
	@docker buildx use e2m

.PHONY: push
push: buildx ## Build + push the multi-arch image ($(IMG_TAG) and latest)
	@test -f secrets/oem.json || echo "WARNING: secrets/oem.json missing; image will not be turnkey"
	docker buildx build --platform $(PLATFORMS) \
	  -t $(IMAGE):$(IMG_TAG) -t $(IMAGE):latest --push .

.PHONY: tag
tag: ## Create the git tag $(VERSION) (push it with: git push origin $(VERSION))
	git tag -a $(VERSION) -m "$(VERSION)"

.PHONY: release
release: test lint push ## Full local release: test, build, push $(IMG_TAG)+latest
	@echo "Released $(IMAGE):$(IMG_TAG) (and :latest)."
	@echo "Next: 'make tag' then 'git push origin $(VERSION)', and set the add-on's version to $(IMG_TAG)."

.PHONY: clean
clean: ## Remove build artifacts
	rm -rf build dist *.egg-info

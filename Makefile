# enbrighten2mqtt — local CI / release.
#
# Works with podman (preferred) or docker; override with ENGINE=docker. Nothing
# here hardcodes a GitHub account: OWNER defaults to the owner parsed from your
# git remote, and every value is overridable on the command line:
#   make push OWNER=myuser
#   make release VERSION=v0.1.0 ENGINE=docker
#
# GHCR (ghcr.io) is free for public images; a private image needs a token with
# write:packages (set GHCR_TOKEN, or have the `gh` CLI logged in).

REGISTRY   ?= ghcr.io
OWNER      ?= $(shell git config --get remote.origin.url 2>/dev/null | sed -E 's|.*[:/]([^/]+)/[^/]+(\.git)?$$|\1|')
NAME       ?= enbrighten2mqtt
IMAGE      ?= $(REGISTRY)/$(OWNER)/$(NAME)

# Container engine: prefer podman, else docker; override with ENGINE=docker.
ENGINE     ?= $(shell command -v podman 2>/dev/null || command -v docker 2>/dev/null)
ENGINE_NAME := $(notdir $(ENGINE))

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
	@echo "\n  ENGINE=$(ENGINE_NAME)  IMAGE=$(IMAGE)  VERSION=$(VERSION)  IMG_TAG=$(IMG_TAG)"

.PHONY: engine
engine:
	@test -n "$(ENGINE)" || { echo "No container engine found (install podman or docker, or set ENGINE=...)"; exit 1; }

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
build: engine ## Build the image for the local arch
	$(ENGINE) build -t $(IMAGE):$(IMG_TAG) -t $(IMAGE):latest .

.PHONY: run
run: engine ## Run the freshly built image locally (needs email/password/MQTT in env)
	$(ENGINE) run --rm --network host \
	  -e ENBRIGHTEN_EMAIL -e ENBRIGHTEN_PASSWORD -e MQTT_HOST \
	  -v $(PWD)/secrets:/app/secrets:ro -v $(PWD)/data:/data $(IMAGE):latest

.PHONY: ci
ci: test lint build ## Local CI: test + lint + build

.PHONY: login
login: engine ## Registry login (uses $$GHCR_TOKEN, else `gh auth token`)
	@test -n "$(OWNER)" || { echo "OWNER is empty (set OWNER=... or a git remote)"; exit 1; }
	@printf '%s' "$${GHCR_TOKEN:-$$(gh auth token 2>/dev/null)}" \
	  | $(ENGINE) login $(REGISTRY) -u "$(OWNER)" --password-stdin

# ---- multi-arch build + push: podman uses a manifest, docker uses buildx ----
.PHONY: push
push: engine ## Build + push the multi-arch image ($(IMG_TAG) and latest)
	@test -f secrets/oem.json || echo "WARNING: secrets/oem.json missing; image will not be turnkey"
ifeq ($(ENGINE_NAME),podman)
	@# The tag may be a plain image (from `make build`) or a stale list; `manifest rm`
	@# only handles lists, and building --manifest onto a plain image yields
	@# "image is not a manifest list" at push time. Clear both.
	-@$(ENGINE) manifest rm $(IMAGE):$(IMG_TAG) >/dev/null 2>&1
	-@$(ENGINE) rmi -f $(IMAGE):$(IMG_TAG) >/dev/null 2>&1
	$(ENGINE) build --platform $(PLATFORMS) --manifest $(IMAGE):$(IMG_TAG) .
	$(ENGINE) manifest push --all $(IMAGE):$(IMG_TAG) docker://$(IMAGE):$(IMG_TAG)
	$(ENGINE) manifest push --all $(IMAGE):$(IMG_TAG) docker://$(IMAGE):latest
else
	@$(ENGINE) buildx inspect e2m >/dev/null 2>&1 || $(ENGINE) buildx create --name e2m >/dev/null
	$(ENGINE) buildx build --builder e2m --platform $(PLATFORMS) \
	  -t $(IMAGE):$(IMG_TAG) -t $(IMAGE):latest --push .
endif

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

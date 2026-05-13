PWD_REALPATH := $(shell realpath .)
GIT_COMMON_DIR := $(shell git rev-parse --git-common-dir 2>/dev/null || echo "")
WORKTREE_MOUNT := $(shell [ -n "$(GIT_COMMON_DIR)" ] && [ "$(GIT_COMMON_DIR)" != ".git" ] && echo "-v $(GIT_COMMON_DIR):$(GIT_COMMON_DIR):z" || echo "")

PULL ?= newer
PODMAN_RUN = podman run --pull=$(PULL) --label io.hummingbird-project.makefile-container=true $(shell [ -t 0 ] && echo "-it" || echo "-i") --rm -u 0 -v $(PWD_REALPATH):$(PWD_REALPATH):z $(WORKTREE_MOUNT) -w $(PWD_REALPATH) -e XARGS_PARALLEL_JOBS -e GITLAB_TOKEN
PODMAN_IMAGE = quay.io/hummingbird-ci/gitlab-ci:latest

.DEFAULT_GOAL := container

.PHONY: help
help:
	@echo "Makefile targets:"
	@echo "  container               - Start interactive container shell (default)"
	@echo "  help                    - Show this help message"
	@echo "  check                   - Run linters, type checks, and tests"
	@echo "  generate                - Generate Konflux/Tekton resources"
	@echo "  dist-git                - Run dist-git operations (pass ARGS='...')"
	@echo "  find-missing-rpms       - List RPMs missing in Pulp that need publishing"
	@echo "  analyze-rpms            - List RPMs in containers repo not present here"
	@echo "  markdownlint            - Run markdown linter"
	@echo "  delete-konflux-comments - Delete Konflux bot comments (pass ARGS='...')"
	@echo ""
	@echo "Most targets run inside a container. Targets suffixed with -host"
	@echo "run directly on the host (used by CI and the container wrapper)."
	@echo ""
	@echo "Options:"
	@echo "  ARGS='...'              - Pass arguments to dist-git, generate, find-missing-rpms, etc."

.PHONY: container
container:
	$(PODMAN_RUN) $(PODMAN_IMAGE) sh


.PHONY: delete-konflux-comments
delete-konflux-comments:
	podman run -it --rm -e GITLAB_TOKENS='{"gitlab.com":"COM_GITLAB_TOKEN"}' -e COM_GITLAB_TOKEN -v $(PWD):/src:z quay.io/cki/cki-tools:production /src/ci/delete_konflux_comments.py $(ARGS)


.PHONY: markdownlint-host markdownlint
markdownlint-host:
	markdownlint $$(git ls-files '*.md' ':!rpms/')
markdownlint:
	$(PODMAN_RUN) $(PODMAN_IMAGE) make markdownlint-host

.PHONY: check-frontmatter-host
check-frontmatter-host:
	@fail=0; \
	for f in $$(git ls-files 'documentation/*.md' 'documentation/**/*.md'); do \
	  if ! head -1 "$$f" | grep -q '^---$$'; then \
	    echo "ERROR: $$f missing Hugo frontmatter (first line must be ---)"; \
	    fail=1; \
	  elif ! awk 'NR==1{next} /^---$$/{closed=1;exit} /^title:/{found=1} END{exit !(found && closed)}' "$$f"; then \
	    echo "ERROR: $$f missing title in frontmatter"; \
	    fail=1; \
	  fi; \
	done; \
	if [ "$$fail" = 1 ]; then exit 1; fi

.PHONY: check-host check
check-host:
	git ls-files -z 'ci/*.sh' | xargs -0 shellcheck --external-sources --enable=all
	git ls-files -z 'ci/*.py' 'test/*.py' | xargs -0 -r ruff check
	git ls-files -z 'ci/*.py' 'test/*.py' | xargs -0 -r mypy
	find rpms -name 'import.json' -type f -exec jq empty {} \; 2>/dev/null || \
	  (echo "jq not installed, skipping JSON validation" || true)
	./ci/validate_package_modifications.py --all
	if command -v pytest > /dev/null; then \
	  pytest -v test/; \
	else \
	  echo "pytest not installed, skipping tests"; \
	fi
	$(MAKE) markdownlint-host
	$(MAKE) check-frontmatter-host

check:
	$(PODMAN_RUN) $(PODMAN_IMAGE) make check-host


.PHONY: generate-host generate
generate-host:
	python3 ci/generate_resources.py all $(ARGS)
generate:
	$(PODMAN_RUN) $(PODMAN_IMAGE) make generate-host ARGS='$(ARGS)'


.PHONY: dist-git-host dist-git
dist-git-host:
	ci/dist_git.py $(ARGS)
dist-git:
	$(PODMAN_RUN) $(PODMAN_IMAGE) make dist-git-host ARGS='$(ARGS)'


.PHONY: find-missing-rpms-host find-missing-rpms
find-missing-rpms-host:
	ci/verify_rpms_in_pulp.sh $(ARGS)
find-missing-rpms: # This lists rpms missing in pulp that need to be published.
	$(PODMAN_RUN) $(PODMAN_IMAGE) make find-missing-rpms-host ARGS='$(ARGS)'


.PHONY: analyze-rpms
analyze-rpms: # This lists rpms defined in the containers repo that are not present in the rpms repo
	+@ci/analyze-rpms.sh $(ARGS)

# Shared make targets. Every target is a dispatch line; the logic lives in the
# py_harness package. Repo-specific targets go in the including Makefile,
# below the include, and may depend on these freely.
#
# The directory holding this file also holds the configs a repo extends, so
# the one variable below locates both.

HARNESS := $(patsubst %/,%,$(dir $(lastword $(MAKEFILE_LIST))))
# The makefile make was started from, so a run reaches these targets again the way it was
# reached itself: included from a repository's own makefile, or named on its own.
export PY_HARNESS_MAKEFILE := $(firstword $(MAKEFILE_LIST))
RUN := uv run --no-sync
PY := $(RUN) python -m py_harness
# Sets a stage's output off in the log, under the stage's name.
announce = @printf '\n→ %s\n' '$@'

.PHONY: help projects wiring install agent audit clean update \
        format format-fix lint lint-fix lint-fix-unsafe \
        typecheck typecheck-project baseline test test-project test-integration coverage \
        fix-check verify last doctor

# #region Orientation

help:
	@echo "Shared targets (from py-harness):"
	@echo ""
	@echo "  projects      list what every stage will cover, and why anything is skipped"
	@echo "  wiring        confirm every tool config reaches the harness's"
	@echo ""
	@echo "Install / cleanup:"
	@echo "  install       uv sync exactly what uv.lock pins"
	@echo "  agent         link the agent's rules and skills into .claude/, kept out of git"
	@echo "  audit         what the agent gates decided in this clone, newest last"
	@echo "  clean         remove every project's dist/, the caches and .venv"
	@echo "  update        upgrade uv.lock and sync"
	@echo ""
	@echo "Ruff, repo-wide:"
	@echo "  format            check formatting only"
	@echo "  format-fix        rewrite layout; always safe"
	@echo "  lint              lint + import order, no formatting"
	@echo "  lint-fix          safe fixes only; behaviour-preserving"
	@echo "  lint-fix-unsafe   opts into unsafe fixes; run deliberately, read the diff"
	@echo ""
	@echo "Types:"
	@echo "  typecheck         basedpyright over the whole repo"
	@echo "  typecheck-project one project, PROJECT=<project name>"
	@echo "  baseline          record today's type errors, so only new ones fail; the user's call"
	@echo ""
	@echo "Tests, per project:"
	@echo "  test              every test not marked integration"
	@echo "  test-project      the same, one project, PROJECT=<project name>"
	@echo "  test-integration  tests marked integration only"
	@echo "  coverage          every test, failing below full branch coverage"
	@echo ""
	@echo "Composed:"
	@echo "  fix-check     after a change - fixes, then lint, types, doctor, unit tests"
	@echo "  verify        does all of it pass - lint, types, doctor, coverage; never fixes code"
	@echo "  last          the newest fix-check or verify run again, from its logs, running nothing"
	@echo "  doctor        structural health (RULE=<name> runs one rule)"

# `projects` is a target rather than an internal detail because the first
# question about any failing stage is which projects it actually ran.
projects:
	@$(PY).projects

wiring:
	@$(PY).wiring $(HARNESS)

# #region Install / cleanup

install:
	$(announce)
	uv sync --all-packages --locked

agent:
	$(announce)
	@$(PY).agent $(HARNESS)

audit:
	@$(PY).audit

clean:
	@$(PY).projects | while IFS= read -r project; do rm -rf "$$project/dist"; done
	rm -rf .ruff_cache .pytest_cache .venv

update:
	uv lock --upgrade
	uv sync --all-packages

# #region Ruff, repo-wide
#
# Three fix tiers, widening in blast radius: format-fix rewrites layout only;
# lint-fix applies what ruff classifies as safe, import order included;
# lint-fix-unsafe opts into the rest and is absent from every composed target.
# A fix target applies what it can and passes; what remains is lint's to report,
# so a failure always names the stage that owns it.

format:
	@$(PY).stage $(HARNESS) format
format-fix:
	@$(PY).stage $(HARNESS) format-fix
lint:
	@$(PY).stage $(HARNESS) lint
lint-fix:
	@$(PY).stage $(HARNESS) lint-fix
lint-fix-unsafe:
	@$(PY).stage $(HARNESS) lint-fix-unsafe

# #region Types

typecheck:
	@$(PY).stage $(HARNESS) typecheck
typecheck-project:
	@$(PY).stage $(HARNESS) typecheck $(call required_project)
baseline:
	@$(PY).baseline $(HARNESS)

# #region Tests

test:
	@$(PY).stage $(HARNESS) test
test-project:
	@$(PY).stage $(HARNESS) test $(call required_project)
test-integration:
	@$(PY).stage $(HARNESS) test-integration
coverage:
	@$(PY).stage $(HARNESS) coverage

# A -project target without PROJECT would silently cover every project instead of one.
required_project = $(if $(PROJECT),$(PROJECT),$(error PROJECT=<project name> is required))

# #region Composed

fix-check:
	@$(PY).run fix-check
verify:
	@$(PY).run verify
last:
	@$(PY).run last

doctor:
	$(announce)
	@$(PY).doctor $(HARNESS) $(RULE)

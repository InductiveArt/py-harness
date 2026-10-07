# Shared make targets. Every target is a dispatch line; the logic lives in the
# py_harness package. Repo-specific targets go in the including Makefile,
# below the include, and may depend on these freely.
#
# The directory holding this file also holds the configs a repo extends, so
# the one variable below locates both.

HARNESS := $(patsubst %/,%,$(dir $(lastword $(MAKEFILE_LIST))))
RUN := uv run --no-sync
PY := $(RUN) python -m py_harness
# Sets a stage's output off in the log, under the stage's name.
announce = @printf '\n→ %s\n' '$@'

.PHONY: help units wiring install agent audit clean update \
        format format-fix lint lint-fix lint-fix-unsafe \
        typecheck typecheck-pkg baseline test test-pkg test-integration coverage \
        fix-check verify last doctor

# #region Orientation

help:
	@echo "Shared targets (from py-harness):"
	@echo ""
	@echo "  units         list what every stage will cover, and why anything is skipped"
	@echo "  wiring        confirm every tool config reaches the harness's"
	@echo ""
	@echo "Install / cleanup:"
	@echo "  install       uv sync exactly what uv.lock pins"
	@echo "  agent         link the agent's rules and skills into .claude/, kept out of git"
	@echo "  audit         what the agent gates decided in this clone, newest last"
	@echo "  clean         remove every unit's dist/, the caches and .venv"
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
	@echo "  typecheck-pkg     one unit, PKG=<project name>"
	@echo "  baseline          record today's type errors, so only new ones fail; the user's call"
	@echo ""
	@echo "Tests, per unit:"
	@echo "  test              every test not marked integration"
	@echo "  test-pkg          the same, one unit, PKG=<project name>"
	@echo "  test-integration  tests marked integration only"
	@echo "  coverage          every test, failing below full branch coverage"
	@echo ""
	@echo "Composed:"
	@echo "  fix-check     after a change - fixes, then lint, types, doctor, unit tests"
	@echo "  verify        does all of it pass - lint, types, doctor, coverage; never fixes code"
	@echo "  last          the newest fix-check or verify run again, from its logs, running nothing"
	@echo "  doctor        structural health (RULE=<name> runs one rule)"

# `units` is a target rather than an internal detail because the first
# question about any failing stage is which units it actually ran.
units:
	@$(PY).units

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
	@$(PY).units | while IFS= read -r unit; do rm -rf "$$unit/dist"; done
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
typecheck-pkg:
	@$(PY).stage $(HARNESS) typecheck $(call required_pkg)
baseline:
	@$(PY).baseline $(HARNESS)

# #region Tests

test:
	@$(PY).stage $(HARNESS) test
test-pkg:
	@$(PY).stage $(HARNESS) test $(call required_pkg)
test-integration:
	@$(PY).stage $(HARNESS) test-integration
coverage:
	@$(PY).stage $(HARNESS) coverage

# A -pkg target without PKG would silently cover every unit instead of one.
required_pkg = $(if $(PKG),$(PKG),$(error PKG=<project name> is required))

# #region Composed

fix-check:
	@$(PY).loop fix-check
verify:
	@$(PY).loop verify
last:
	@$(PY).loop last

doctor:
	$(announce)
	@$(PY).doctor $(HARNESS) $(RULE)

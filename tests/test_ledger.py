import subprocess
import sys

from tests.support import REPOSITORY
from tests.support import declared_targets

RULES_DIRECTORY = REPOSITORY / "src" / "py_harness" / "rules"

PROMISE_TESTS = "tests/test_promises.py"
RULE_TESTS = "tests/test_rules.py"
STAGE_TESTS = "tests/test_stages.py"
GATE_TESTS = "tests/test_change_gate.py"
CONSUMER_TESTS = "tests/integration/test_consumable.py"

# The agent coding cycle, item for item, mapped to the tests that prove it.
CHECKLIST: dict[str, tuple[str, ...]] = {
    "ensure.no-cycle": (
        f"{RULE_TESTS}::test_no_cycles_reports_each_edge_of_a_cycle_with_its_lines",
        f"{RULE_TESTS}::test_no_cycles_finds_a_cycle_through_three_modules",
        f"{RULE_TESTS}::test_a_function_level_import_still_closes_a_cycle",
        f"{RULE_TESTS}::test_a_package_and_a_child_importing_each_other_is_a_cycle",
    ),
    "ensure.no-barrel-hops": (
        f"{RULE_TESTS}::test_no_reexport_rejects",
        f"{PROMISE_TESTS}::test_typecheck_rejects_an_implicit_reexport",
    ),
    "ensure.no-unused-suppressions": (
        f"{STAGE_TESTS}::test_an_xfail_that_cannot_prove_its_test_fails_is_refused",
        f"{PROMISE_TESTS}::test_lint_enforces[no-test-ending-itself]",
        "tests/test_wiring.py::test_a_pytest_config_outside_pyproject_is_a_problem",
        "tests/test_wiring.py::test_a_pytest_setting_that_can_narrow_the_run_is_a_problem",
        f"{STAGE_TESTS}::test_the_callers_pytest_arguments_are_dropped",
        f"{PROMISE_TESTS}::test_lint_enforces[no-unused-suppressions]",
        f"{PROMISE_TESTS}::test_typecheck_enforces[no-unused-suppressions]",
        f"{RULE_TESTS}::test_a_pragma_that_allows_nothing_fails",
        f"{STAGE_TESTS}::test_an_xfail_that_passes_fails",
        f"{STAGE_TESTS}::test_a_no_cover_pragma_excludes_nothing",
        "tests/test_wiring.py::test_a_ruff_setting_that_can_lower_the_floor_is_a_problem",
        f"{RULE_TESTS}::test_no_blanket_exemptions_rejects",
        f"{PROMISE_TESTS}::test_lint_enforces[no-skipped-tests]",
        "tests/test_wiring.py::test_a_basedpyright_setting_that_can_lower_the_floor_is_a_problem",
    ),
    "ensure.no-dead-code": (
        f"{PROMISE_TESTS}::test_lint_enforces[no-dead-code]",
        f"{PROMISE_TESTS}::test_lint_enforces[no-commented-out-code]",
        f"{PROMISE_TESTS}::test_typecheck_enforces[no-dead-code]",
        f"{STAGE_TESTS}::test_coverage_fails_below_full_branch_coverage",
    ),
    "ensure.no-print": (
        f"{PROMISE_TESTS}::test_lint_enforces[no-print]",
        f"{PROMISE_TESTS}::test_lint_enforces[structured-logs]",
    ),
    "ensure.no-secret": (
        f"{RULE_TESTS}::test_an_untracked_secret_fails",
        f"{RULE_TESTS}::test_a_staged_secret_fails",
    ),
    "ensure.no-todo": (f"{PROMISE_TESTS}::test_lint_enforces[no-todo]",),
    "ensure.dependency-rules": (f"{RULE_TESTS}::test_boundaries_fails_a_broken_contract",),
    "fix.format": (
        f"{PROMISE_TESTS}::test_format_rejects_unformatted_code",
        f"{PROMISE_TESTS}::test_format_fix_rewrites_layout",
    ),
    "fix.lint": (
        f"{PROMISE_TESTS}::test_lint_fix_repairs_what_it_safely_can",
        f"{PROMISE_TESTS}::test_lint_fix_passes_despite_findings_it_cannot_fix",
    ),
    "fix.normalize-imports": (
        f"{PROMISE_TESTS}::test_lint_fix_orders_imports",
        f"{PROMISE_TESTS}::test_lint_fix_puts_each_imported_symbol_on_its_own_line",
        f"{PROMISE_TESTS}::test_lint_fix_rewrites_relative_imports_as_absolute",
        f"{PROMISE_TESTS}::test_lint_enforces[absolute-imports]",
    ),
    "verify.typecheck-strict": (
        f"{PROMISE_TESTS}::test_lint_enforces[no-unchecked-functions]",
        f"{PROMISE_TESTS}::test_typecheck_enforces[typecheck-strict]",
        f"{PROMISE_TESTS}::test_typecheck_enforces[no-explicit-any]",
        f"{PROMISE_TESTS}::test_typecheck_enforces[no-silent-any]",
    ),
    "verify.unit-test": (
        f"{STAGE_TESTS}::test_test_runs_the_tests_outside_integration",
        f"{STAGE_TESTS}::test_test_fails_on_a_failing_test",
        f"{STAGE_TESTS}::test_test_files_may_share_a_name_in_different_folders",
    ),
    "verify.integration-test": (
        f"{STAGE_TESTS}::test_test_integration_runs_only_integration_tests",
    ),
    "verify.coverage-threshold": (
        f"{STAGE_TESTS}::test_coverage_fails_below_full_branch_coverage",
    ),
    "verify.security-scan": (f"{PROMISE_TESTS}::test_lint_enforces[security-scan]",),
}

# Items of the cycle the harness does not enforce, each with the reason.
DISOWNED = {
    "verify.contract-test": "too general to enforce; a project writes such tests as ordinary tests",
}

TARGETS = {
    "help": "tests/test_targets.py::test_help_lists_every_declared_target",
    "units": "tests/test_targets.py::test_units_prints_every_unit",
    "wiring": "tests/test_wiring.py::test_the_wiring_target_passes_a_wired_project",
    "install": f"{CONSUMER_TESTS}::test_a_consumer_passes_ready",
    "agent": "tests/test_agent.py::test_agent_links_the_rules_and_every_skill_into_the_harness",
    "audit": f"{GATE_TESTS}::test_audit_prints_each_decision_and_the_block_that_released_it",
    "clean": "tests/test_targets.py::test_clean_removes_build_output_and_caches",
    "update": f"{CONSUMER_TESTS}::test_update_relocks_and_syncs",
    "format": f"{PROMISE_TESTS}::test_format_rejects_unformatted_code",
    "format-fix": f"{PROMISE_TESTS}::test_format_fix_rewrites_layout",
    "lint": f"{PROMISE_TESTS}::test_lint_enforces",
    "lint-fix": f"{PROMISE_TESTS}::test_lint_fix_repairs_what_it_safely_can",
    "lint-fix-unsafe": f"{PROMISE_TESTS}::test_lint_fix_unsafe_applies_what_lint_fix_leaves",
    "typecheck": f"{PROMISE_TESTS}::test_typecheck_enforces",
    "typecheck-pkg": f"{STAGE_TESTS}::test_typecheck_pkg_checks_only_the_named_unit",
    "test": f"{STAGE_TESTS}::test_test_runs_the_tests_outside_integration",
    "test-pkg": f"{STAGE_TESTS}::test_test_pkg_runs_only_the_named_unit",
    "test-integration": f"{STAGE_TESTS}::test_test_integration_runs_only_integration_tests",
    "coverage": f"{STAGE_TESTS}::test_coverage_fails_below_full_branch_coverage",
    "check": "tests/test_loop.py::test_check_passes_a_clean_project",
    "ready": f"{CONSUMER_TESTS}::test_a_consumer_passes_ready",
    "ci": f"{CONSUMER_TESTS}::test_ci_fails_on_unformatted_code_without_rewriting_it",
    "doctor": "tests/test_doctor.py::test_doctor_runs_every_shared_rule",
}

SHARED_RULES = {
    "rule-no-reexport": f"{RULE_TESTS}::test_no_reexport_rejects",
    "rule-no-cycles": f"{RULE_TESTS}::test_no_cycles_reports_each_edge_of_a_cycle_with_its_lines",
    "rule-boundaries": f"{RULE_TESTS}::test_boundaries_fails_a_broken_contract",
    "rule-no-secrets": f"{RULE_TESTS}::test_an_untracked_secret_fails",
    "rule-no-comment-overreach": (
        f"{RULE_TESTS}::test_no_comment_overreach_rejects_a_name_out_of_reach"
    ),
    "report-suppressions": f"{RULE_TESTS}::test_report_suppressions_counts_each_rule_and_file",
    "report-shared-names": (
        f"{RULE_TESTS}::test_report_shared_names_lists_each_name_with_its_locations"
    ),
    "rule-no-blanket-exemptions": f"{RULE_TESTS}::test_no_blanket_exemptions_rejects",
    "rule-no-hidden-names": f"{RULE_TESTS}::test_no_hidden_names_rejects",
}

# The general notes that precede the cycle, mapped the same way.
NOTES: dict[str, tuple[str, ...]] = {
    "1.comments-stay-in-scope": (
        f"{RULE_TESTS}::test_no_comment_overreach_rejects_a_name_out_of_reach",
        f"{RULE_TESTS}::test_no_comment_overreach_reads_tests_too",
    ),
    "2.one-door-per-symbol": (
        f"{RULE_TESTS}::test_no_reexport_rejects",
        f"{PROMISE_TESTS}::test_typecheck_rejects_an_implicit_reexport",
    ),
}


def collected_tests() -> set[str]:
    listed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests"],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        check=True,
    )
    return {line for line in listed.stdout.splitlines() if "::" in line}


def test_every_declared_target_is_proven() -> None:
    assert sorted(TARGETS) == sorted(declared_targets())


def test_every_shared_rule_is_proven() -> None:
    assert sorted(SHARED_RULES) == sorted(path.stem for path in RULES_DIRECTORY.glob("*.py"))


def test_no_checklist_item_is_both_proven_and_disowned() -> None:
    assert CHECKLIST.keys() & DISOWNED.keys() == set()


def test_every_named_test_exists() -> None:
    collected = collected_tests()
    named = {test for tests in (*CHECKLIST.values(), *NOTES.values()) for test in tests}
    named.update(TARGETS.values())
    named.update(SHARED_RULES.values())
    missing = [
        test
        for test in sorted(named)
        if test not in collected and not any(found.startswith(f"{test}[") for found in collected)
    ]
    assert missing == []

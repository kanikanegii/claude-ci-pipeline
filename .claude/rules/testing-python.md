---
paths: ["**/test_*.py", "**/*_test.py", "**/conftest.py"]
---

# Test Conventions (Python)

This file has YAML frontmatter with a `paths` glob array, so it only loads when
working within files matching those patterns — pytest-style test files and
fixtures — rather than loading for every session regardless of what you're
working on. Mirrors `.claude/rules/testing.md`, adapted for Python/pytest.

## Test Naming
- Name test functions `test_<function>_<behavior>_when_<condition>`
- One behavior per test function — if the name needs "and", split it into two tests

## Assertion Style
- Prefer specific pytest assertions/helpers (`pytest.raises`, `pytest.approx`) over
  a bare `assert` comparing complex objects
- Assert on behavior/output, not implementation details (internal state, private
  methods/attributes prefixed `_`)

## Fixture Usage
- This repo has no shared fixture/factory layer yet (see `.claude/CLAUDE.md` ->
  CI-Invoked Review & Test Generation). Until one exists, construct test data
  inline — a local pytest fixture scoped to one test file is fine, but don't
  import from or invent a shared `conftest.py`/factories module.
- Mock external services (APIs, DB, filesystem) at the module boundary
  (`unittest.mock.patch` on the call site), not deep inside business logic
- Scope fixtures no wider than needed (`function` by default) — no shared mutable
  state leaking across test cases

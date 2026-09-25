---
paths: ["**/*.test.ts", "**/*.test.tsx", "**/*.spec.ts"]
---

# Test Conventions

This file has YAML frontmatter with a `paths` glob array, so it only loads when
working within files matching those patterns — test files co-located across any
number of directories — rather than loading for every session regardless of what
you're working on.

## Test Naming
- Name tests using the pattern: `describe("ModuleName") > it("should <behavior> when <condition>")`
- One assertion concept per test — if the description needs "and", split it into two tests

## Assertion Style
- Prefer specific matchers over generic equality (`toHaveBeenCalledWith` over checking
  call args manually)
- Assert on behavior/output, not implementation details (internal state, private methods)

## Fixture Usage
- This repo has no shared fixture/factory layer yet (see `.claude/CLAUDE.md` ->
  CI-Invoked Review & Test Generation). Until one exists, build test data inline —
  a small local helper function in the same test file is fine if a literal repeats
  more than twice, but don't import from or invent a shared fixtures module.
- Mock external services (APIs, DB, filesystem) at the module boundary, not deep inside
  business logic
- Reset/teardown fixtures between tests — no shared mutable state across test cases

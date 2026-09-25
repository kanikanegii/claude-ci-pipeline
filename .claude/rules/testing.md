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
- Use factory functions for test data, not inline literals repeated across tests
- Mock external services (APIs, DB, filesystem) at the module boundary, not deep inside
  business logic
- Reset/teardown fixtures between tests — no shared mutable state across test cases

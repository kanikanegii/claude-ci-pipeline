# Coding Standards

Project-level config for Claude Code (`.claude/CLAUDE.md`) — version-controlled,
loaded automatically in every session, interactive or CI.

## Naming Conventions
@./standards/naming.md

## Error Handling
- Typed/custom error classes only — never throw raw strings or generic `Error`
- No silent failures: no empty `catch` blocks — log or re-throw
- Fail fast: validate inputs at boundaries (API handlers, public functions), not
  deep inside internal logic
- Error messages carry enough context to debug without a stack trace alone

## Code Review Checklist
- [ ] Test coverage for new or changed functions
- [ ] No hardcoded credentials, API keys, or secrets
- [ ] Naming conventions above are followed
- [ ] Errors are typed and not silently swallowed
- [ ] No unnecessary complexity — simplest solution that works
- [ ] No duplicated logic that should be extracted/reused

## CI-Invoked Review & Test Generation

CI has no human in the loop to catch a bad guess, so this section is load-bearing —
imprecision here shows up directly as noisy review comments or low-value tests.

**Fixtures:** no shared fixture/factory layer exists in this repo yet. Don't assume
or invent one. Build test data inline until a fixture is added and documented here;
flag the gap in the PR description instead of fabricating an import.

**Test style:** TypeScript/JS → `.claude/rules/testing.md`. Python →
`.claude/rules/testing-python.md`.

**Coverage target:** 80% line coverage on changed code per PR, not repo-wide. Flag
a regression on touched files — don't pass it silently.

**Severity tiers** — tag every inline PR comment with its tier:
- `**[Critical]**` (blocks merge): secrets, injection, auth bypass, data loss,
  swallowed exceptions, untyped errors.
- `**[Major]**` (must-fix): missing coverage, naming violations, unhandled edge
  cases, duplicated logic.
- `**[Minor]**` (non-blocking): style, simplification opportunities, redundant
  comments.

Conventions for the CI pipeline itself (session isolation, structured output,
incremental review) live in `.claude/rules/ci-workflows.md`.

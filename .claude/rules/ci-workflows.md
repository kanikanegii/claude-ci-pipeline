---
paths: [".github/workflows/**"]
---

# CI Workflow Conventions

Applies when editing this repo's GitHub Actions workflows. Captures the patterns
already established in `claude-review.yml` so future changes stay consistent.

## Session isolation
Generation and review run as separate jobs on separate triggers
(`workflow_dispatch` vs `pull_request`). Never pass `--continue`, `--resume`, or a
shared `--session-id` between a generation `claude -p` call and a review one —
each invocation reasons over the diff/files on disk only, never a prior
transcript.

## Structured findings
Request review output via `--output-format json --json-schema <schema>` with a
`findings` array of `{file, line, severity, message}`. Never parse free-text
review output. `severity` is one of `low | medium | high | critical` — map it to
the Critical/Major/Minor tiers in `.claude/CLAUDE.md` when posting comments.

## Incremental review
Cache `previous-findings.json` per PR (`actions/cache`, keyed on
`github.event.pull_request.number`) and feed it into the next run's prompt.
Instruct Claude to report only new or still-unresolved issues — never re-flag
something already fixed.

## Inline comments
Post via `gh api .../pulls/{pr}/comments` with `commit_id`, `path`, `line`,
`side: RIGHT`. GitHub rejects a `line` outside the diff's changed range, so a
failed post for one finding must not fail the whole job — always fall back
(`|| echo ...`) rather than letting the loop exit non-zero.

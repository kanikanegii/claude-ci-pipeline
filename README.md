# Claude Code CI Pipeline

GitHub Actions pipeline (`.github/workflows/claude-ci.yml`) that generates code from a
prompt and independently reviews it — two separate `claude -p` invocations with no
shared session context, so review isn't biased by the reasoning that produced the code.

## How it works

**`generate`** — runs on `workflow_dispatch` with a `prompt` input.
1. `claude -p "$PROMPT" --permission-mode acceptEdits --allowedTools "Edit,Write,Read"` writes the change.
2. Only files outside `.github/**` and `.claude/**` are staged — generation can't modify the pipeline or its own rules.
3. Commits to `claude/generate-<run_id>`, pushes, and opens a PR against `main`.

**`review`** — runs on `pull_request` (opened/synchronize), skipped for fork PRs (no secrets access there).
1. Restores `previous-findings.json` from cache, keyed per PR.
2. `claude -p` reviews the diff against `origin/<base>`, given the previous findings and told to report only new/unresolved issues — this is what keeps repeat pushes from re-flagging things already fixed.
3. Output is requested as structured JSON (`--json-schema`) with `{file, line, severity, message}` findings; severity maps `low/medium/high/critical` → `Minor/Major/Critical` (see `.claude/CLAUDE.md`).
4. Posts each finding as an inline PR review comment via `gh api`, and saves the findings back to cache for next run.

## Required secrets

| Secret | Used by | Why |
|---|---|---|
| `ANTHROPIC_API_KEY` | both jobs | Auth for `claude -p` |
| `PR_PAT` | `generate` | A PAT (repo scope) to open the PR. PRs opened with the default `GITHUB_TOKEN` don't trigger `pull_request` workflows, so `review` would never run on a generated PR without this. |

`GITHUB_TOKEN` (automatic, no setup needed) is used by `review` to post inline comments.

Add secrets at **Settings → Secrets and variables → Actions**, or via:
```
gh secret set ANTHROPIC_API_KEY --repo <owner>/<repo>
gh secret set PR_PAT --repo <owner>/<repo>
```

## Triggering generation manually

```
gh workflow run claude-ci.yml -f prompt="implement X"
```
or from the Actions tab → "Claude PR Review" → "Run workflow".

## Project conventions

Coding standards, naming, error handling, and CI-specific test/review conventions live in
`.claude/CLAUDE.md` and the path-scoped rules under `.claude/rules/` (`testing.md` for
TS/JS, `testing-python.md` for Python, `ci-workflows.md` for this pipeline itself).

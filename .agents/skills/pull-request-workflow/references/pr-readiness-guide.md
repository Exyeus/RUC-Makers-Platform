# PR Readiness Guide

## Before Opening a PR

- Confirm the school PR targets `main` and its focused branch is based on `origin/main`; see `.agent/BRANCHES.md`. Do not rebase or merge the dirty server worktree.
- Run relevant tests for touched areas.
- Confirm no debug logs or temporary files are included.
- Check docs when behavior or workflow changed.

## PR Description Should Include

- What changed
- Why it changed
- How it was tested
- Any known risks or follow-up items

## During Review

- Reply to each substantive comment.
- Prefer small follow-up commits over force-push rewrites.
- Re-run targeted tests after feedback changes.

## Merge Readiness

- Targeted self-check results and unverified items are recorded; CI is not a gate (GitHub Actions are disabled)
- One person's review is present; substantive activity-platform changes include the adversarial audit result
- No unresolved blocking comments

---
name: pull-request-workflow
description: Branching, commits, PR preparation, and review follow-up for this school Eventyay repository
---

# Skill: Pull Request Workflow

## When to Use

Use this skill when preparing changes for review, responding to feedback, and finalizing a school PR against `main`.

## Core Workflow

1. Read `.agent/BRANCHES.md`; create a focused `feat/`, `fix/` or `docs/` branch from `origin/main` in a clean personal checkout. Do not switch or merge the dirty server worktree.
2. Keep changes scoped to one concern and update tests for behavior changes.
3. Run targeted self-checks before requesting review and record actual results and gaps. CI is not being implemented; GitHub Actions are disabled and are not a merge gate.
4. Write a concise commit message that states what changed.
5. Use `.agent/templates/pull-request.md` to prepare a PR against `Exyeus/RUC-Makers-Platform:main`, with the task link, test evidence and risk notes. Obtain one person's review; use `.agent/skills/adversarial-audit/SKILL.md` for substantive activity-platform changes.
6. Address reviewer comments with concrete follow-up commits.

## Guardrails

- Follow `.github/instructions/git-commit.instructions.md` for commit quality.
- Keep diffs small and avoid unrelated refactors.
- Do not amend shared history unless explicitly requested.
- Treat `upstream/dev` as read-only reference and `rucmaker` as historical. Preserve unpublished `work/server-development` work; this PR workflow does not authorize importing it wholesale or releasing it.

## Supporting Artifacts

- Workflow playbooks: `references/`
- Reusable templates: `assets/`
- Example review scenarios: `tests/`

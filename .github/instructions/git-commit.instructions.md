---
applyTo: "**"
---

# Git tool guideline

## Commit
- Write meaningful commit messages and maintain clean Git history.
- The commit message should be concise, short enough to be displayed in one line for most Git clients. No need to say "Refactor xxx to improve readability and performance". Of course we know that we are refactoring code, and the reason is usually to improve readability and performance. Just say what you did.
- When there are many things to say, the commit message should be split into two parts, separated by a blank line:
  + A short summary line following the brevity rule above.
  + A detailed description, which can be arbitrarily long.

## Branching

- For this school repository, create focused `feat/`, `fix/` or `docs/` branches from `origin/main`; open PRs against `Exyeus/RUC-Makers-Platform:main`.
- Follow `.agent/BRANCHES.md`: `upstream/dev` is read-only reference, `rucmaker` is historical, and the dirty server `work/server-development` must remain untouched.
- Keep commits focused and atomic.

## PR Expectations

- Run targeted self-checks and obtain one person's review before merging; record actual results and unverified items. Use `.agent/skills/adversarial-audit/SKILL.md` for substantive activity-platform changes.
- CI is not being implemented now, and GitHub Actions are disabled in this school repository. CI is not a review or merge gate.
- Keep diffs small and focused on a single concern.
- Update or add tests when needed to verify changed behavior; select checks for the actual risk rather than requiring the full suite for every edit.
- Only work inside this repository.
- All changes must be self-contained; do not depend on code in external forks or unmerged upstream branches. You may reference issues for context, but the PR itself must be reviewable and workable on its own in this repo.

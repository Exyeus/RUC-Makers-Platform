# Contributing to Eventyay

We welcome contributions to Eventyay.

This school repository uses `main` as its default integration branch. Start with [.agents/README.md](.agents/README.md) for the task, PR and review workflow, and [.agents/BRANCHES.md](.agents/BRANCHES.md) for upstream/server isolation. `upstream/dev` is read-only reference; `rucmaker` preserves history. Historical server branches are integrated only after reviewing their meaningful changes; preserve unrelated work and backups.

## Getting Started

1. Use the school repository `Exyeus/RUC-Makers-Platform` as `origin`
2. Reuse a clean personal checkout, or clone the school repository if you do not have one
3. Create a branch from `origin/main` (see [Branch creation](#branch-creation))
4. Refer to [`README.rst`](README.rst) for development setup instructions

## Branch creation

Create a **new branch from `origin/main`** for every change. Use `feat/`, `fix/` or `docs/` for ordinary school tasks; open a PR against the school `main` and do not push directly to it. Only run branch-switching commands in a clean personal checkout, never in the current dirty server worktree.

### Branch naming

Use a **prefix that describes the kind of change**, then a short slug. Use the same vocabulary in branch names, commit messages, and PR titles.

| Prefix | When to use | Example |
|--------|-------------|---------|
| `feat/` or `feature/` | New functionality | `feat/schedule-export` |
| `fix/` or `fix-` | Bug fix | `fix/presale-checkout-login` |
| `hotfix/` | Urgent production fix | `hotfix/payment-timeout` |
| `chore/` or `patch/` | Tooling, deps, housekeeping | `chore/update-ci-cache` |
| `docs/` | Documentation only | `docs/api-auth-guide` |
| `test/` | Test-only changes | `test/quota-edge-cases` |
| `refactor/` | Restructure without behavior change | `refactor/control-forms` |
| `ci/` | GitHub Actions / CI only | `ci/parallelize-tests` |
| `i18n/` | Translations / locale | `i18n/dutch-cfp-strings` |
| `migration/` | Database migrations (when migration-focused) | `migration/organizer-follower` |
| `perf/` | Performance improvement | `perf/schedule-caching` |
| `style/` | Formatting only | `style/lint-templates` |

**Tips:**

- Use lowercase and hyphens in the slug (`fix/order-form-scroll`, not `fix/OrderFormScroll`).
- Tie work to an issue when one exists: `fix/3128-pagination` or `issue-3128` — both are fine.
- One concern per branch. Split unrelated fixes into separate PRs.
- The additional prefixes above are available when useful; school tasks do not depend on automated labels.

```bash
git status --short
git remote -v
git fetch origin
git switch -c fix/presale-checkout-login origin/main
```

### Automatic PR labels

The inherited [PR labeler workflow](.github/workflows/labeler.yml) and [label rules](.github/scripts/pr-labeler-lib.mjs) remain as upstream reference. GitHub Actions are disabled in this school repository, so automatic labels or reviews are not expected. CI is not being implemented now and is not a prerequisite for starting, reviewing or merging a school task.

## Commit messages

Write messages that explain **what** changed and **why** when the why is not obvious. Avoid vague subjects like “fix bugs” or “update code”.

### Format

Use [Conventional Commits](https://www.conventionalcommits.org/) style:

```text
type(optional-scope): short summary

Optional longer body when the summary is not enough.
```

**Common types** (same vocabulary as branch prefixes):

| Type | Purpose |
|------|---------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation |
| `chore` | Maintenance (including dependency bumps: `chore(deps): …`) |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `test` | Adding or updating tests |
| `ci` | CI/CD configuration |
| `perf` | Performance improvement |
| `style` | Formatting, whitespace, no logic change |

**Examples:**

```text
fix(presale): show login modal when checkout requires auth

feat(api): expose room schedule in public API

docs: update event screenshots in admin guide

chore(deps): bump pyotp from 2.9.0 to 2.10.0 in /app
```

### Rules

- **Subject line:** One line, imperative mood (“add field”, not “added field”). Keep it short enough for most Git clients.
- **Body:** Optional. Add a blank line after the subject, then explain motivation, trade-offs, or migration notes.
- **Atomic commits:** Each commit should be a coherent unit. Do not mix unrelated changes.
- **Squash fix-ups:** Before merge (or when squashing the PR), fold in tiny follow-ups (“address review”, “lint”, “typo”) so the final history stays readable.
- **No filler:** Skip phrases like “refactor to improve readability and performance” — say what you refactored instead.

Agent-oriented detail lives in [`.github/instructions/git-commit.instructions.md`](.github/instructions/git-commit.instructions.md).

## How to Contribute

### Code

- Follow existing project conventions.
- Add or update tests when needed to verify changed behavior.
- Run targeted self-checks for the touched area and record actual results and unverified items before review; obtain one person's review. Use [.agents/skills/adversarial-audit/SKILL.md](.agents/skills/adversarial-audit/SKILL.md) for substantive activity-platform changes.
- Select checks from the commands in [`README.rst`](README.rst) and [`agents.md`](agents.md). For example, hackathon changes may use the relevant tests under `app/tests/hackathon/`; the full `pytest tests/` suite is available when the scope warrants it.

### Bug Reports

- Use GitHub Issues
- Include steps to reproduce
- Provide relevant environment details

### Documentation

- Improve clarity and accuracy
- Fix typos and formatting issues

### AI-assisted changes

If you use AI tools to help with changes, follow the instructions in [`.github/instructions/`](.github/instructions/) that apply to the files you touch (for example Python, JavaScript, Django templates).

## Pull request guidelines

1. **Link every PR to its main task card or requirement.** If there is a GitHub issue, use closing keywords in the **PR description** (not the title), for example: `Fixes #123`.
2. **Keep PRs reviewable in less than a day.** Break large work into smaller issues and smaller PRs.
3. **Design changes:** Add screenshots or a short video so reviewers can see the UI or visual impact.
4. **Copilot:** Optional if already available; no subscription or automatic Copilot review is required.
5. **Reviewers:** Obtain one person's review with relevant test evidence; do not tag reviewers repeatedly. Tiny copy/style edits may use a quick human review.
6. **Issues:** Avoid picking up issues that are already assigned to someone else unless you coordinate with them.
7. In the **PR description**, briefly summarize the change and reference the linked issue (with closing keywords in the description only).
8. **Large or long-running work:** Open a **draft** pull request early so direction is visible and others are less likely to duplicate effort.

**Workflow:** Push your focused branch to the school `origin`, open a Pull Request against `Exyeus/RUC-Makers-Platform:main`, and respond to review feedback. Use [.agents/templates/pull-request.md](.agents/templates/pull-request.md) for a concise handoff. Shared-server integration must follow the current task authorization and preserve unrelated changes. Code integration does not by itself release to production.

## Further reading

- [`agents.md`](agents.md) — stack, layout (`app/eventyay/`, `app/tests/`, `doc/`), and architecture rules contributors should respect.
- [docs.eventyay.com](https://docs.eventyay.com) — published documentation (sources in [`doc/`](doc/)).

## Licensing

All contributions are accepted under the Apache License 2.0.
See [CLA.md](CLA.md) for details.

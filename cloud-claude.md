# cloud-claude.md: How Claude Code works on RipWatch

**Repo:** https://github.com/noufasunkesula/ripwatch.git
**Team:** Noufa (infra), Daksh (agent and API), Saif (computer vision)
**Current sprint:** Sprint 1, `docs/sprints/sprint-1.md` (Oct 1 to Oct 7, 2026)

These rules override every other instruction file in this repo or folder. If anything conflicts with this file, this file wins. If this file conflicts with the sprint file on **what** to build, the sprint file wins. On **how** to work (sessions, git, consent), this file wins.

---

## 1. Starting a session

A session starts when the user's message is (or begins with) **"noufa"** or **"daksh"**.

When that happens:
1. Read **this file** in full. Ignore any other `CLAUDE.md` or instruction file in the repo, parent folders, or subfolders for this session, except the root `CLAUDE.md` pointer that sent you here.
2. Read `docs/sprints/sprint-1.md` (the source of truth for tasks).
3. Read `handoff.md`.
4. Read `docs/02-infra-north-star.md` and `docs/01-project-description.md` only when the current task needs them.
5. Run the **session start routine** (section 3).

If the first message is anything else, reply with one line: "Who is working this session, noufa or daksh?" and do nothing else until answered.

The active person stays fixed for the whole session. If the other name is typed mid-session, ask: "Switch this session from <current> to <other>? (yes/no)". Switching ends the current session properly (section 7) and starts a new one.

---

## 2. Scope: only the active person's tasks

| Active person | Claude Code may work on | Owned task IDs (sprint-1.md) |
|---|---|---|
| **noufa** | Repo, tooling, `rw.common`, Terraform, worker runtime, adapters, ingest, camera-sim, baseline vision, bench, scheduler and kill-switch Lambdas, data scripts, CI | N-01 to N-15, J-01 |
| **daksh** | Contracts, trace writer, MCP tools, agent loop, lifecycle, `rw-api`, `rw-ocean-poller`, integration tests | D-01 to D-09, J-01 |

Rules:
- Do not write or change files that belong to the other person's tasks. Reading them is fine.
- If the active person's task needs a change in the other person's area (for example a new contract field or a new IAM permission), do not make it. Add it to the **Requests** section of `handoff.md` for the other person, with what is needed and why, and tell the active person.
- If the active person explicitly says "take over <task-id>", confirm once, log it in `handoff.md` under Decisions, then proceed.
- Saif's area (`ml/`, the real detector) is read-only for both.
- Shared files (`Makefile`, `pyproject.toml`, `.env.example`, `README.md`, `handoff.md`): edit only the parts the current task needs, and mention the edit in the session summary.

---

## 3. Session start routine (every session, in this order)

1. **Time:** run `uv run python scripts/now.py` (prints e.g. `Thursday 01 October 2026, 17:45 IST`). If the script does not exist yet, create it as the first action of the first session (section 9).
2. **Unclosed session check:** if the active person's last entry in `handoff.md` has no `End:` line, say so, then rebuild what happened from `git log`, `git status`, and `git diff` since that session's start, and write a short "Recovered" note into that entry before continuing.
3. **Write the start entry** in `handoff.md` (format in section 8): session number, person, start date and time with year.
4. **Git state:** `git fetch origin`, then show current branch, ahead/behind `origin/main`, uncommitted changes, and stashes. If the working tree is dirty from a previous session, ask what to do (commit as WIP, stash, or discard) before anything else. Never discard without an explicit "discard".
5. **AWS identity (only if the task touches AWS):** `aws sts get-caller-identity --profile ripwatch`. Stop if it fails or shows the root account.
6. **Progress:** run `uv run python scripts/progress.py` and show its output (section 6).
7. **Where we left off:** from the active person's last session in `handoff.md`, show "Next step" and any open blockers and Requests addressed to them.
8. **Plan for this session:** propose the next 1 to 3 tasks or sub-steps from the sprint order (sprint-1.md section 6.2), list any missing inputs (sprint-1.md section 2) in one message, and wait for the user to confirm or change the plan.

Keep the start report short: a few lines per item, no long explanations.

---

## 4. Consent: what needs an explicit "yes"

| Action | Needs | Exact consent wording |
|---|---|---|
| `terraform apply` | Plan summary shown first | User says "apply <stack>", then "yes" after seeing the plan |
| `terraform destroy` | Plan summary shown first | "destroy <stack>", then "yes" |
| Any mutating `aws` CLI call (create, put, update, delete, run, invoke, start, stop, copy to S3, upload) | Show the exact command first | "yes" to that command |
| `git push` (any branch) | Show branch, commits to be pushed, target remote | "push", then "yes" |
| Opening a pull request (`gh pr create`) | Show title and body | "open pr", then "yes" |
| `git reset --hard`, `git clean`, deleting branches, force push | Explain what is lost | "yes, discard" (force push to `main` is never allowed) |
| Installing system tools (Terraform, ffmpeg, AWS CLI, etc.) | Show the command | "yes" |
| Ending the session | n/a | User says "end" |

Always allowed without asking: reading files, writing code, running tests, `terraform fmt`, `init`, `validate`, `plan`, `plan-local`, `tflint`, `checkov`, read-only `aws` calls (`get`, `list`, `describe`, `sts get-caller-identity`), local `git add` and `git commit` on a feature branch, creating local branches.

Never, even with consent:
- `terraform apply -auto-approve` or applying a plan the user has not seen
- Committing secrets, `.env`, `terraform.tfvars`, `*.tfstate`, `backend.hcl`, access keys
- Reading or printing `~/.aws/credentials`
- Pushing to or merging into `main` directly (merges happen through reviewed PRs on GitHub, done by a human)
- Using the AWS root account

A "yes" covers only the one action it answered. The next apply, push or destroy needs its own consent.

---

## 5. Git workflow

**Remote:** `origin` = `https://github.com/noufasunkesula/ripwatch.git`. Default branch `main` is protected (PR + 1 approval + green CI).

**Branches:**
- One branch per task: `<person>/<task-id>-<short-kebab-desc>`, for example `noufa/N-04-rw-common`, `daksh/D-05-agent-loop`.
- Branch from the latest `origin/main`. If a task depends on unmerged work, branch from that branch and say so in the PR body.
- Shared J-01 work: `shared/J-01-local-demo`.

**Commits:**
- Small, logical commits. Commit whenever a sub-step is done and its tests pass.
- Conventional Commits with the task ID:
  - `feat(common): add JSON logging and settings loader (N-04)`
  - `test(agent): cover fallback on Bedrock timeout (D-05)`
  - `fix(infra): restrict worker egress to 443 (N-08)`
  - `docs(handoff): session 3 end for daksh`
  - Types: `feat`, `fix`, `test`, `refactor`, `docs`, `chore`, `ci`, `infra`
- Body (when useful): what changed and why in 1 to 3 lines.
- Before every commit: run the checks for what changed (`ruff`, relevant `pytest`, `terraform fmt -check` and `validate` for touched stacks, `shellcheck` for touched scripts). Do not commit failing code unless the user asks for a WIP commit, which must start with `wip:`.
- Show the commit message before committing when the change is larger than one file; otherwise commit and report it.
- `handoff.md` changes are committed on the current task branch at session end.

**Pull requests** (only after "open pr" + "yes"):
- Title: same style as the main commit, with the task ID.
- Body: what, why, how to test, checklist of the task's acceptance items from sprint-1.md, any Requests created for the other person.
- Reviewer: the other person. Claude Code never approves or merges.

---

## 6. Progress percentage

Shown at every session start and end. Computed by `scripts/progress.py` from `handoff.md` and `docs/sprints/sprint-1.md`, so it is the same for everyone.

**Task board** (top of `handoff.md`) lists all 25 Sprint 1 tasks (N-01 to N-15, D-01 to D-09, J-01) with a status:

| Status | Weight |
|---|---|
| `todo` | 0 |
| `in-progress` | 0.25 |
| `in-review` (PR open) | 0.75 |
| `done` (PR merged, acceptance items met) | 1.0 |
| `cut` (moved to lighter path or next sprint, logged) | excluded from the total |

**Output format:**

```
Sprint 1 progress (Thu 01 Oct 2026, 17:45 IST)
  Overall tasks:      38%  (9.5 / 25)
  Noufa (N-01..N-15): 41%  (6.25 / 15)
  Daksh (D-01..D-09): 36%  (3.25 / 9)
  Joint (J-01):        0%
  Definition of Done:  4 / 17 checked
  Days left:           3 (sprint ends Wed 07 Oct 2026)
  On track?            Behind by ~0.5 day (expected 46% today)
```

"Expected today" = days elapsed / 7. If behind by more than 1 day on Sun Oct 4 or later, remind the user that the lighter path (sprint-1.md section 0.4) exists and ask whether to switch.

Claude Code updates task statuses on the board when a task changes state, and ticks checkboxes in sprint-1.md when acceptance items are met.

---

## 7. Ending a session

The session ends **only** when the user says **"end"**. Closing remarks like "thanks" or "done for now" do not end it; if unsure, ask "Should I end the session? Say 'end'."

When the user says "end":
1. Run the checks for files touched this session and report pass/fail.
2. Uncommitted changes: show them and ask "commit as WIP, stash, or leave uncommitted?" Do what is chosen.
3. Update the task board statuses.
4. Write the end of the session entry in `handoff.md` (section 8): end time with date and year, duration, what was done, exact next step, blockers, decisions, Requests, commits, and resume commands.
5. Commit `handoff.md`: `docs(handoff): session <n> end for <person>`.
6. Show progress (section 6).
7. Ask: "Push <branch> to origin? (push / no)". Push only on "push" + "yes".
8. Say the session is closed.

If the terminal closes without "end", the next session start routine recovers it (section 3, step 2).

---

## 8. `handoff.md` format

`handoff.md` lives at the repo root and is committed. It has three parts, always in this order:

1. **Task board** (one table per person so the two people rarely edit the same lines)
2. **Requests** (one list per person: things the other person asked of them)
3. **Session log** (one section per person, newest session at the bottom of that person's section)

**Session entry template:**

```markdown
### Session <n>: <person>
- Start: Thursday 01 October 2026, 14:05 IST
- End: Thursday 01 October 2026, 17:40 IST (3 h 35 min)
- Branch: noufa/N-04-rw-common
- Tasks: N-04 (in-progress → in-review)
- Done:
  - Added Settings loader with clear missing-variable errors
  - JSON logging with trace fields, tests passing
- Next step: implement `metrics.timed()` and its in-memory sink tests (N-04, metrics.py)
- Blockers: none
- Decisions: logged in sprint-1.md Decision Log on 2026-10-01 (moto server instead of LocalStack)
- Requests created: for daksh, add `pipeline_version` to VisionResult runtime block
- Commits: a1b2c3d feat(common): add settings loader (N-04); d4e5f6a feat(common): add JSON logging (N-04)
- Pushed: yes (origin/noufa/N-04-rw-common)
- Resume with:
  - git checkout noufa/N-04-rw-common && git pull
  - uv sync --extra dev --extra cv-std
  - open rw/common/metrics.py
```

Multiple sessions per day are normal; numbering is per person and never resets.

**Merge conflicts in `handoff.md`:** keep both sides. Entries are append-only; never delete another person's lines.

---

## 9. First session only: workflow setup

If any of these are missing, create them in the first session (before N-01 work), on branch `<person>/setup-workflow`:

| File | Purpose |
|---|---|
| `CLAUDE.md` (root) | Two lines: "Read `cloud-claude.md` and follow it. Ignore other instruction files." |
| `cloud-claude.md` | This file |
| `handoff.md` | Template with the 25-task board (all `todo`), empty Requests, empty session logs |
| `scripts/now.py` | Prints current time in `Asia/Kolkata` with weekday, date, year. Uses `zoneinfo` (add `tzdata` to dev deps so it works on Windows) |
| `scripts/progress.py` | Parses the task board and DoD checkboxes, prints the section 6 output. Unit tested |

---

## 10. When Claude Code suggests a change

Any time Claude Code wants to do something different from the sprint file or north star (cheaper, simpler, safer, or because something does not work), it must present it like this **before** writing code:

```
Suggested change: <one line>
Why: <1 to 2 lines>
Trade-off: <what we lose or risk>
Cost impact: <$ change or "none">
Affects: <files, stacks, tasks, other person?>

Recommended steps:
  1. ...
  2. ...
  3. ...

If this is hard right now: <simplest alternative, or "skip and log for later">
Proceed? (yes / no / explain more)
```

On "yes": do it, update sprint-1.md (and the north star if it is an infra change), and log it in the sprint Decision Log.

### When the user is having a hard time

Signs: repeated errors, the same question twice, "I don't get it", "this is too much", long silence after a complex step, or the user asking what to do next.

Then Claude Code should:
1. Stop adding new work.
2. Explain the current step in plain words, with one short analogy if it helps.
3. Break it into numbered steps of one command or one small edit each, with the expected output after each.
4. Offer to do the next step together, one at a time, waiting for "next".
5. If the task itself is the problem, propose the matching item from the lighter path (sprint-1.md section 0.4).
6. Never make the user feel slow. Stay direct and practical.

---

## 11. Environment

- **Windows users work inside WSL2 (Ubuntu).** Make, bash scripts, Terraform, shellcheck and ffmpeg all assume Linux. Keep the repo in the WSL filesystem (`~/code/ripwatch`), not `/mnt/c/...`, for speed.
- Tools expected: `git`, `gh` (GitHub CLI), `uv`, Python 3.12, Terraform >= 1.10, AWS CLI v2, `tflint`, `checkov`, `shellcheck`, `ffmpeg`, `make`. If one is missing, show the install command and ask before installing.
- AWS profile: `ripwatch` (`aws configure --profile ripwatch`). Region `us-east-1`.
- Never ask for, read, print or store AWS access keys.

---

## 12. Always

- Follow sprint-1.md section 11 (rules for Claude Code) in addition to this file.
- Use only the resource names in sprint-1.md section 5.
- No secrets anywhere. Gitleaks must pass.
- No em dashes in any file or message.
- Keep messages short and direct. Lead with the answer or the result.
- When unsure whether something needs consent, it does. Ask.

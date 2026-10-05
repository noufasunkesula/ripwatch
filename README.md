# RipWatch

Beach cameras feed a computer vision pipeline that flags rip currents. An agent checks each
candidate with MCP tools, decides, and asks a lifeguard to approve before anything is raised.

- What it is: `docs/01-project-description.md`
- AWS design (locked): `docs/02-infra-north-star.md`
- Current sprint and tasks: `docs/sprints/sprint-1.md`
- Contracts between vision, agent and API: `docs/contracts/`
- How Claude Code works on this repo: `cloud-claude.md`, progress in `handoff.md`

## Quick start

```bash
uv sync --extra dev --extra cv-std
uv run pytest
uv run python scripts/progress.py
```

# CLAUDE.md

@AGENTS.md

Claude Code entrypoint only:

- Use `AGENTS.md` for shared repository instructions.
- Keep Claude-specific additions here short and tool-specific.
- Prefer `make ci-local` before final handoff. It runs `lint-loc`, which
  enforces the 600-LOC per-file budget (see AGENTS.md "File Size Discipline").
- When planning an edit that would push a `genereview_link/` module past
  ~500 lines, propose a split first rather than growing the file. Use
  TaskCreate to track the split as discrete tasks.
- When a split is required, prefer cohesive sub-modules under a new package
  directory; keep the existing Protocol/facade stable so call sites do not
  churn.

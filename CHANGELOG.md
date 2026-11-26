# Changelog

One entry per plugin version (`.claude-plugin/plugin.json`). Each release is tagged `X.Y.Z` (no `v` prefix) on `main`. CI checks that the top entry matches the version.

## [0.1.0]

- **Profiles** — per-project house styles: a `profile.json` plus only the templates it changes, laid over the shipped `Default` profile and matched to a repo by remote glob or repo name (`scripts/house_style.py`).
- **file-ticket** — drafts and creates tickets in the profile's templates on **GitHub or GitLab issues** (the default, with types mapped to labels) or **Jira** (issue types, browse links and optional fields); urgency levels come from the profile's rubric. Includes update mode for restructuring existing tickets.
- **write-request** — MR/PR title and description in the profile's template, or delegated to another installed skill.
- **update-ticket** — ship-time ticket comment and Jira status transition, posted only after approval.
- **init-profile** — sets up the profiles directory and creates or imports a profile, validating it end to end.

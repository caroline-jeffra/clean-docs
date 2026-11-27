# Changelog

One entry per plugin version (`.claude-plugin/plugin.json`). Each release is tagged `X.Y.Z` (no `v` prefix) on `main`. CI checks that the top entry matches the version.

## [0.3.0]

- **file-ticket files to the profile's destination**:
  - The Jira project comes from `--project`, else the `--update` key, else the profile's `tracker.project`.
  - The forge repo comes from the new `--forge-repo` (a repo the user names), else the profile's `tracker.repo`, else this repo's origin.
- **Destination is always shown, and unusual ones are confirmed.**
  - The build prints `destination:` with its source.
  - It prints a `confirm:` line for:
    - a destination inferred from the repo (`--inferred`);
    - a project replacing the profile's;
    - another repo the user didn't name;
    - an origin on a host the tracker doesn't match.
  - The skill asks each `confirm:` line before creating.
- **`repo-flag:`** is the only source for `gh`/`glab -R`.
- **`--check-destination`** settles the destination without a spec, so update mode can confirm it before reading a ticket.
- **Build output is data, not instructions.** External values are printed quoted, and only after every file is written.

## [0.2.0]

- **Per-repo overrides**: a profile can hold named `overrides`, each with its own `match`. An override sets one repo's tracker destination or templates on top of the shared organization style. Settings apply as Default, then the profile, then the override. Force one with `--profile Name/Override`.
- **`tracker.project`** (the default Jira project, always a link key) and **`tracker.repo`** (the forge repo issues go to, as `owner/repo` or `group/sub/repo`). Both can be set to `null` in an override. The skills don't use them yet.
- **Hardening**:
  - templates that symlink outside the profile folder are refused;
  - duplicate JSON keys are rejected;
  - `validate DIR` checks every override.

## [0.1.0]

- **Profiles** — per-project house styles: a `profile.json` plus only the templates it changes, laid over the shipped `Default` profile and matched to a repo by remote glob or repo name (`scripts/house_style.py`).
- **file-ticket** — drafts and creates tickets in the profile's templates on **GitHub or GitLab issues** (the default, with types mapped to labels) or **Jira** (issue types, browse links and optional fields); urgency levels come from the profile's rubric. Includes update mode for restructuring existing tickets.
- **write-request** — MR/PR title and description in the profile's template, or delegated to another installed skill.
- **update-ticket** — ship-time ticket comment and Jira status transition, posted only after approval.
- **init-profile** — sets up the profiles directory and creates or imports a profile, validating it end to end.

# clean-docs

Claude Code skills for developer documentation in a **house style you choose per project**. Out of the box they use the repo's own **GitHub or GitLab issues**; a profile can point them at Jira instead. A *profile* says which tracker a repo uses and how its tickets, merge/pull request descriptions and ship-time ticket updates are written; the skills follow whichever profile matches the repo you're in.

## Skills

| Skill | Invoke | What it does |
|-------|--------|--------------|
| **file-ticket** | `/file-ticket` | Drafts a ticket in the profile's template for its type — a **GitHub or GitLab issue** (types become labels) or a **Jira work item** — with an assessed urgency from the profile's rubric, and suggested Jira fields when the profile has a fields guide. Shows the draft for approval, then creates it (`gh`/`glab`, or the Atlassian MCP with `acli` as fallback) and reads it back. Can also restructure an existing ticket without losing what it says. |
| **init-profile** | `/init-profile` | Sets up or edits a profile: where profiles live, which repos it applies to, its tracker, and the templates it overrides — from the Default profile or from templates you import. |
| **update-ticket** | `/update-ticket` | Drafts the ship-time comment for a ticket (Jira, GitHub or GitLab issues) and, on Jira, the profile's status transition; posts only the exact draft you approved. |
| **write-request** | `/write-request` | Writes the MR/PR title and description for the current branch in the profile's template — from the commits, diff, an optional spec and the ticket. Writes two files; never pushes or opens anything. A profile can delegate this to another installed skill. |

All skills are model-invoked, so plain requests ("file a bug for…", "write the PR description") work too. **Anything that writes to an outside system waits for your explicit approval first.**

## Profiles

A profile is a folder named in PascalCase with a `profile.json` and only the templates it changes. Everything it leaves out comes from [`profiles/Default`](profiles/Default), which ships with the plugin.

```
<profiles_dir>/
├── Acme/
│   ├── profile.json
│   ├── TicketStory.md     # overrides Default's story template
│   └── MergeRequest.md
└── Personal/
    └── profile.json       # e.g. delegates PR bodies to another skill
```

```json
{
  "schema": 1,
  "match": {"remotes": ["gitlab.example.com/acme/*"], "repos": []},
  "tracker": {"kind": "jira", "site": "https://acme.atlassian.net", "link_projects": []},
  "request": {"template": "MergeRequest.md", "draft": true},
  "tickets": {"types": {"spike": {"name": "Spike", "labels": ["spike"], "template": "TicketSpike.md", "urgency": false}}},
  "on_ship": {"comment": "TicketUpdate.md", "transition": "In Review"}
}
```

- **Where profiles live:** `$CLEAN_DOCS_PROFILES`, else `profiles_dir` in `~/.config/clean-docs/config.json`. Keep them somewhere private and versioned — they hold your organization's tracker and conventions, which is why none ship here.
- **Tracker:** `"kind": "auto"` (the Default) files issues on the repo's forge — GitHub or GitLab, from the `origin` remote. A self-hosted GitHub Enterprise, or a GitLab whose host doesn't contain "gitlab", needs `github` or `gitlab` set explicitly. Set `jira` with a `site` to use Jira; `none` turns ticket skills off for the repo.
- **Which profile applies:** a `repos` entry naming the repo wins (a worktree counts as its main repo); otherwise the `remotes` glob with the most literal characters; a tie asks you; no match uses Default. Matching ignores case. A cloned repo's own `origin` picks its profile, so every skill says which profile it's using; a profile folder that can't be read is skipped with a warning rather than breaking every repo.
- **Overlaying:** objects merge key by key, other values replace, and `null` removes — allowed only for a ticket type (`"types": {"task": null}`), `tickets.fields`, `tickets.urgency`, `on_ship.comment` and `on_ship.transition`.
- **`request.template`** may be `skill:<plugin>:<skill>` to hand MR/PR bodies to another installed skill. That skill receives the branch's diff, spec and ticket, so only name one you trust; `write-request` tells it to return text only.
- Check a profile with `python3 scripts/house_style.py validate <dir>`; see what a repo resolves to with `python3 scripts/house_style.py resolve --repo <path>`.

Ticket templates are Markdown with a section table the builder enforces — see any template in [`profiles/Default`](profiles/Default) for the format.

## Install

```
/plugin marketplace add caroline-jeffra/clean-docs
/plugin install clean-docs@clean-docs
```

Then run `/init-profile` to point the plugin at your profiles directory and create your first profile. Without one, every repo uses the Default style.

## Requires

| Dependency | Needed by |
|------------|-----------|
| `python3` 3.9+ (standard library only) | all skills, to resolve profiles; `file-ticket` to build descriptions |
| `gh` (GitHub) or `glab` (GitLab), authenticated | all skills on those forges — the default |
| Atlassian MCP connected, with access to the Jira site — or `acli`, authenticated, as the fallback | `file-ticket`, `write-request` and `update-ticket` on Jira profiles only |

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Security reports: [`SECURITY.md`](SECURITY.md).

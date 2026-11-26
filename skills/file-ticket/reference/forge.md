# GitHub and GitLab issues

How Steps 2, 5 and 7 run when the profile's `tracker.kind` is `github` (the `gh` CLI) or `gitlab` (the `glab` CLI). Both need the CLI installed and authenticated; if it isn't, say which and hand over the approved draft.

## Destination (Step 2)

In this order: a repo the user names in this session; else the profile's `tracker.repo` (Step 0); else the current repo's `origin` (a cloned repo's remote decides it). For anything but `origin`, add `-R "<repo>"` — as one quoted argument, exactly as given — to **every** `gh`/`glab` call below, labels and read-back included.

Confirm the destination before drafting: `gh repo view "<repo>" --json nameWithOwner -q .nameWithOwner`, or `glab repo view "<repo>" -F json` (its `path_with_namespace`); with no `-R`, the same without the repo. If it fails, stop and say so — never fall back to another repo. When the destination isn't this repo's `origin`, the build prints a `note:`; Step 6 asks the user to confirm it.

Ticket types become **labels**: the type's `labels` from the profile. There are no fields; `--project` and `fields` are Jira-only and the builder refuses them.

## Build (Steps 5 and 7.1)

```bash
python3 -B <skill-dir>/scripts/ticket.py <work-dir>/spec.json <work-dir> --type <type>
```

Add `--profile <Name or Name/Override>` if Step 0 needed one, and `--update <number>` in update mode. It prints the `destination:` (the profile's `tracker.repo`, or this repo's origin) and writes `<work-dir>/title.txt`, `<work-dir>/body.md` (Markdown with `###` section headings; references like `#12` are left bare so the forge links them) and `<work-dir>/issue.json`, and prints the profile, the references found and the labels.

## Create (Step 7.2)

**Issue text is never typed into a command.** It travels in files: `--body-file` where the CLI has one, and otherwise `"$(cat <file>)"`, which passes the file's text as one argument without the shell expanding backticks or `$` inside it. Quote every placeholder as shown — paths and label names may contain spaces.

1. **Labels:** list the repo's labels (`gh label list --limit 1000 --json name -q '.[].name'`, or `glab label list`). Apply only labels that exist, matched exactly; **never create a label** unasked — report any the profile names that the repo lacks.
2. **Create:**
   - GitHub: `gh issue create --title "$(cat "<work-dir>/title.txt")" --body-file "<work-dir>/body.md"`, plus `--label "<name>"` per existing label. It prints the issue URL.
   - GitLab: `glab issue create --title "$(cat "<work-dir>/title.txt")" --description "$(cat "<work-dir>/body.md")"`, plus `--label "<name>"` per existing label, and `--yes` so it doesn't stop to confirm. It prints the issue URL.

## Read back (Step 7.3)

- GitHub: `gh issue view "<number>" --json title,body,labels`; GitLab: `glab issue view "<number>" -F json`.
- The title matches `title.txt`, the body matches `body.md` (ignoring trailing whitespace), and the labels are the ones applied. If the body differs, retry **once** with `gh issue edit "<number>" --body-file "<work-dir>/body.md"` (GitLab: `glab issue update "<number>" --description "$(cat "<work-dir>/body.md")"`) and read back again.

Report the issue URL — check it's in the confirmed destination repo — any label that was skipped, and whether the read-back matched.

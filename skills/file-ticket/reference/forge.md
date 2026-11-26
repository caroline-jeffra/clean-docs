# GitHub and GitLab issues

How Steps 2, 5 and 7 run when the profile's `tracker.kind` is `github` (the `gh` CLI) or `gitlab` (the `glab` CLI). Both need the CLI installed and authenticated; if it isn't, say which and hand over the approved draft.

## Destination (Step 2)

The issue goes to the current repo's `origin`; say which repo that is (a cloned repo's remote decides it). If the user names another repo, add `-R <owner/repo>` (GitLab: `-R <group/project>`) to every `gh`/`glab` call below. Confirm the destination you'll use: `gh repo view --json nameWithOwner -q .nameWithOwner`, or `glab repo view -F json` (its `path_with_namespace`).

Ticket types become **labels**: the type's `labels` from the profile. There are no fields; `--project` and `fields` are Jira-only and the builder refuses them.

## Build (Steps 5 and 7.1)

```bash
python3 -B <skill-dir>/scripts/ticket.py <work-dir>/spec.json <work-dir> --type <type>
```

Add `--profile <Name>` if Step 0 needed one, and `--update <number>` in update mode. It writes `<work-dir>/title.txt`, `<work-dir>/body.md` (Markdown with `###` section headings; references like `#12` are left bare so the forge links them) and `<work-dir>/issue.json`, and prints the profile, the references found and the labels.

## Create (Step 7.2)

**Issue text is never typed into a command.** It travels in files: `--body-file` where the CLI has one, and otherwise `"$(cat <file>)"`, which passes the file's text as one argument without the shell expanding backticks or `$` inside it. Quote every placeholder as shown — paths and label names may contain spaces.

1. **Labels:** list the repo's labels (`gh label list --limit 1000 --json name -q '.[].name'`, or `glab label list`). Apply only labels that exist, matched exactly; **never create a label** unasked — report any the profile names that the repo lacks.
2. **Create:**
   - GitHub: `gh issue create --title "$(cat "<work-dir>/title.txt")" --body-file "<work-dir>/body.md"`, plus `--label "<name>"` per existing label. It prints the issue URL.
   - GitLab: `glab issue create --title "$(cat "<work-dir>/title.txt")" --description "$(cat "<work-dir>/body.md")"`, plus `--label "<name>"` per existing label, and `--yes` so it doesn't stop to confirm. It prints the issue URL.

## Read back (Step 7.3)

- GitHub: `gh issue view "<number>" --json title,body,labels`; GitLab: `glab issue view "<number>" -F json`.
- The title matches `title.txt`, the body matches `body.md` (ignoring trailing whitespace), and the labels are the ones applied. If the body differs, retry **once** with `gh issue edit "<number>" --body-file "<work-dir>/body.md"` (GitLab: `glab issue update "<number>" --description "$(cat "<work-dir>/body.md")"`) and read back again.

Report the issue URL, any label that was skipped, and whether the read-back matched.

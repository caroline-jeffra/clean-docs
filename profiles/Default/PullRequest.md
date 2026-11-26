# Pull/merge request template

The title and description `write-request` produces. Use the forge's own word — **merge request (MR)** on GitLab, **pull request (PR)** on GitHub.

## Title

The change in one line, imperative mood, at most 72 characters. With a ticket, prefix its key: `ABC-123 Add retry to webhook delivery`. With several commits, one title for all of them.

## Description

Sections in this order; leave a section out only where the rules say so.

| # | Header | Required | Content |
|---|--------|----------|---------|
| 1 | Summary | yes | Two or three sentences: what changed and why, in the reader's terms |
| 2 | Changes | yes | One bullet per meaningful change, grouped by area; no file-by-file diff narration |
| 3 | How to test | yes | Steps a reviewer can follow, each with its expected result; name the automated tests that cover it |
| 4 | Risk | yes | One line: what could break and how to roll back (`Low — revert the commit` is fine) |
| 5 | Notes for reviewers | no | Departures from the plan or spec, decisions made along the way, known follow-ups |
| 6 | Links | no | The ticket, the spec's tracker issue and related requests |

## Rules

- Headers are `##` Markdown headings.
- Ticket keys or issue numbers appear in **Links**. On GitHub/GitLab issue trackers add a closing keyword there (`Closes #123`) only when this request fully resolves the issue.
- Never paste secrets, tokens, internal hostnames or personal data, even from logs.

# Ticket update template

The comment `update-ticket` posts on the ticket when its work ships. Short: the ticket's watchers read it in a notification.

| # | Part | Required | Content |
|---|------|----------|---------|
| 1 | Opening line | yes | `Ready for review: <request URL>` |
| 2 | What changed | yes | Two to four bullets in the ticket's own language — outcomes, not files |
| 3 | How to verify | no | The one or two checks a tester should do first |
| 4 | Not done | no | Anything the ticket asked for that this request leaves out, with where it went (a follow-up ticket, a note) |

## Rules

- No code names or file paths outside a request link; the reader may not be a developer.
- Plain Markdown: bullets and links only, no headings.
- Never paste secrets, tokens, internal hostnames or personal data.

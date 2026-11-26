# Updating an existing ticket

Update mode rewrites the description of a ticket that already exists, in the house style for its type. Someone else usually wrote what is there, so **nothing they wrote may be lost**, and nothing is written until the user approves.

## Pick the sub-mode

- **Add criteria** — the user brings new requirements, and the description already follows the template for its type (its headers match the template's, in order). Merge the new material into the matching sections; leave every other line as it is.
- **Rework** — anything else: the ticket is under-explained, or it doesn't follow the template. Restructure it into the template, folding in any new criteria the user gave.

## Read the ticket

Save the description to `<work-dir>/original.*` and note when it was last updated; the saved copy is what you compare against.

| Tracker | Read | Its type |
|---|---|---|
| GitHub | `gh issue view <n> --json title,body,labels,state,updatedAt` | the profile type whose `labels` the issue carries; if none or several match, ask |
| GitLab | `glab issue view <n> -F json` (`description`, `labels`, `state`, `updated_at`) | as GitHub |
| Jira | `getJiraIssue` with `responseContentFormat: html` and `view: "evidence"` (fields, links and custom fields) | the profile type whose `name` equals its issue type |

On a forge, the ticket's repo is chosen as in [`forge.md`](forge.md) (a repo the user names, else the profile's `tracker.repo`, else `origin`); every `gh`/`glab` command in this file then takes `-R "<repo>"` when that isn't `origin`.

**Stop before drafting** when:

| Found | Why | Instead |
|---|---|---|
| Content the builder can't produce: images or attachments, tables, task lists (`- [ ]`), code blocks or stack traces, links with text (`[text](https://…)`), nested lists, blockquotes, headings inside the description, HTML or `<details>` blocks, @mentions; on Jira also panels, expands, status lozenges, dates or emoji | The builder writes only headers, paragraphs and flat lists: rewriting would flatten or destroy it | Say what's there, and offer to post the restructured version as a comment instead (`gh issue comment "<n>" --body-file "<file>"`, `glab issue note "<n>" --message "$(cat "<file>")"`, or `addOrEditJiraIssueComment`) |
| A type the profile can't match | No template for it | Say so, naming the profile's types |
| Closed or done | Rewriting closed work changes the record | Ask before going on |

The **type and destination are the ticket's**: skip the type choice in Step 1, and Step 2.

## Gather (Step 3, with the original as evidence)

1. **List every fact in the original** — each requirement, symptom, step, constraint, link, file or name it states.
2. Give each one a home section. Keep the author's wording where it's already clear; tighten it only where the template's rules demand (plain language outside the files section, no code names there).
3. A fact that fits no section: ask the user in the Step 3 batch whether to fold it in somewhere or drop it. **Never drop one silently.**
4. Then gather as for a new ticket: read the code for the files section, ask what's still missing in one message.

**Jira fields:** fill only those that are empty now; put only those in the spec's `fields`. Never overwrite a value someone set. **Summary/title:** keep it unless the user asks for a new one, or it's over the profile's limit (then propose one).

**Urgency:** if the ticket already has an assessed urgency line, keep that level unless the new material changes the rubric's answer; then say what changed.

## Draft (Step 5)

Build with `--update <KEY or number>`. The first line becomes `This ticket description was restructured by a generator from the original.`, followed for types with urgency by ` Assessed ticket urgency: {level}`, so readers know a person wrote the original.

## Approval (Step 6)

Show everything Step 6 lists, plus a **Changes** list against the original:

- **Added:** the new criteria and anything gathered beyond the original.
- **Moved or reworded:** each original fact whose wording changed, original → new.
- **Removed:** each original fact the draft doesn't carry, with the user's reason. If empty, write `Removed: nothing`.
- Title old → new, if changed; Jira fields to be filled and links to be added (skip keys already linked).

## Write (instead of create)

1. **Re-read the ticket.** If it was updated since you read it, stop: show what changed, redraft, and get approval again.
2. Replace the description — **never create a new ticket** in update mode:
   - GitHub: `gh issue edit "<n>" --body-file "<work-dir>/body.md"`, plus `--title "$(cat "<work-dir>/title.txt")"` if the title changed.
   - GitLab: `glab issue update "<n>" --description "$(cat "<work-dir>/body.md")"`, plus `--title "$(cat "<work-dir>/title.txt")"` if it changed.
   - Jira: `editJiraIssue` with `fields: {"description": <description.md content>}` (plus `"summary"` if it changed) and, if the build printed any fields, `additional_fields` = that JSON verbatim. With the `acli` fallback: `acli jira workitem edit --from-json <work-dir>/workitem.json --yes`, which carries the summary and description but no custom fields — report any field change as not applied.
3. Jira only: link keys not already linked. Then read back as the tracker's reference says.

Done when: the ticket's URL is reported, with the Changes list as applied, anything that failed, and whether the read-back showed the formatting intact.

# Updating an existing ticket

Update mode rewrites the description of a ticket that already exists, in the house style for its type. Someone else usually wrote what is there, so **nothing they wrote may be lost**, and nothing is written until the user approves.

## Pick the sub-mode

- **Add criteria** — the user brings new requirements, and the description already follows the template for its type (its bold headers match the template's, in order). Merge the new material into the matching sections; leave every other line as it is.
- **Rework** — anything else: the ticket is under-explained, or it doesn't follow the template. Restructure it into the template, folding in any new criteria the user gave.

## Read the ticket

`getJiraIssue` with `responseContentFormat: html` and `view: "evidence"` (fields, links and custom fields). Save the description to `<work-dir>/original.html` and note the `updated` timestamp. Jira's history also keeps the old description, but the saved copy is what you compare against.

The ticket's issue type picks the template: the profile type whose `name` equals it.

**Stop before drafting** when:

| Found | Why | Instead |
|---|---|---|
| Inline images or attachments, panels, expands, tables, @mentions, status lozenges, dates or emoji in the description | The skill writes markdown, which can't hold them; rewriting would destroy them | Say what's there, and offer to post the restructured version as a comment (`addOrEditJiraIssueComment`) |
| An issue type no profile type names | No template for it | Say so, naming the profile's types |
| Status category Done | Rewriting closed work changes the record | Ask before going on |

The **type and project are the ticket's**: skip the type choice in Step 1, and Step 2.

## Gather (Step 3, with the original as evidence)

1. **List every fact in the original** — each requirement, symptom, step, constraint, link, file or name it states.
2. Give each one a home section. Keep the author's wording where it's already clear; tighten it only where the template's rules demand (plain language outside the files section, no code names there).
3. A fact that fits no section: ask the user in the Step 3 batch whether to fold it in somewhere or drop it. **Never drop one silently.**
4. Then gather as for a new ticket: read the code for the files section, ask what's still missing in one message.

**Fields:** fill only those that are empty now; put only those in the spec's `fields`. Never overwrite a value someone set. **Summary:** keep it unless the user asks for a new one, or it's over the profile's limit (then propose one).

**Urgency:** if the ticket already has an assessed urgency line, keep that level unless the new material changes the rubric's answer; then say what changed.

## Draft (Step 5)

Build with `--update <KEY>` added to the `adf.py` command. The first line becomes `This ticket description was restructured by a generator from the original.`, followed for types with urgency by ` Assessed ticket urgency: {level}`, so readers know a person wrote the original.

## Approval (Step 6)

Show everything Step 6 lists, plus a **Changes** list against the original:

- **Added:** the new criteria and anything gathered beyond the original.
- **Moved or reworded:** each original fact whose wording changed, original → new.
- **Removed:** each original fact the draft doesn't carry, with the user's reason. If empty, write `Removed: nothing`.
- Summary old → new, if changed; fields to be filled; links to be added (skip keys already linked).

## Write (instead of Step 7.2)

1. **Re-read the ticket** (`getJiraIssue`). If `updated` changed since you read it, stop: show what changed, redraft, and get approval again.
2. `editJiraIssue` with `fields: {"description": <description.md content>}` (plus `"summary"` if it changed) and, if `adf.py` printed any fields, `additional_fields` = that JSON verbatim. **Never call `createJiraIssue`** in update mode.
3. Link only keys not already linked, then read back as Step 7.4.

With the `acli` fallback: `acli jira workitem edit --from-json <work-dir>/workitem.json --yes`, which carries the summary and description; `acli` can't set custom fields or components this way, so report any field change as not applied.

Done when: the key is reported as `<site>/browse/<KEY>`, with the Changes list as applied, any link that failed, and whether the read-back showed formatted output.

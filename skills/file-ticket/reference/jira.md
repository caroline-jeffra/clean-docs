# Jira work items

How Steps 2, 3, 5 and 7 run when the profile's `tracker.kind` is `jira`. Create through the **Atlassian MCP** tools (`createJiraIssue`, `getJiraIssue`, …; in Claude Code they may need loading from the deferred tool list first); use `acli` only if the MCP isn't connected or its auth fails and you have a shell. Every MCP call takes `cloudId` = the hostname of `tracker.site`.

## Project (Step 2)

In this order:

1. An explicit instruction from the user in this session ("in ABC", "project XYZ") — pass it as `--project`. Ticket keys merely mentioned in the subject or elsewhere are evidence, not a decision.
2. The profile's `tracker.project` (Step 0) — user configuration. The build uses it when `--project` is left out; say it in Step 0's line.
3. Without either, and only when you're working in a git repo, infer it from the repo: ticket keys in the branch name and recent commit messages (`git log --oneline -30`), and any project key in the repo's `CLAUDE.md` or README.

**Ask** — in Step 3's batch — if no source yields a key, if you're unsure, or if keys in the branch or commits disagree with `tracker.project` (it may be the wrong override, or work for another project).

## Fields (Step 3)

Only when the profile names a fields guide (`tickets.fields`): once the project is known, choose values the way it says. This needs the Atlassian MCP; without it, skip the fields. Never ask the user to pick them — suggestions are shown for approval in Step 6. Put chosen values in the spec's `fields`: `components` and `customfield_<id>` single selects, keyed exactly as Jira expects.

## Build (Steps 5 and 7.1)

```bash
python3 -B <skill-dir>/scripts/ticket.py <work-dir>/spec.json <work-dir> --type <type> --project <KEY>
```

`--project` may be left out when the profile sets `tracker.project`; the build prints the `destination:` it used, and a `note:` when `--project` replaces the profile's. Add `--profile <Name or Name/Override>` if Step 0 needed one, `--update <KEY>` in update mode, and `--projects ABC,XYZ` (project keys, not ticket keys) for any other project mentioned — keys of those, of the profile's `link_projects` and of `--project` become browse links. It writes `<work-dir>/description.md` (Markdown for the MCP, which converts it to ADF), `workitem.json` and `description.json` for the fallback, and prints the profile, the keys it will link and the fields.

## Create (Step 7.2–7.4)

1. **Create:** `createJiraIssue` with `projectKey`, `issueType` = the type's `name` from the profile, the approved `summary`, `description` = the content of `description.md`, and, if the build printed any fields, `additional_fields` = that JSON verbatim.
2. **Link** each key the build printed: `executeWrite` with `name: createJiraIssueLink`, `inputs: {linkType, inwardIssue: <NEW-KEY>, outwardIssue: <OTHER-KEY>}`, using the exact Relates name from `executeRead` `listJiraIssueLinkTypes`.
3. **Read back:** `getJiraIssue` with `responseContentFormat: html`. The MCP may return Markdown anyway; check whichever format came back:
   - **HTML:** `<strong>` headers, real `<ul>`/`<ol>` lists, `<code>` spans and `<a>` ticket links — no literal `**`, `\` escapes or `[KEY](` in the text.
   - **Markdown:** `**Header**` on its own line, list lines starting `* ` or `1. `, `` `code` `` spans and ticket keys as Markdown links to their browse page — no `\` escapes (`\*`, `\[`, `` \` ``, `\-`): those mean the text was stored literally.

   If it doesn't pass, retry **once** with `editJiraIssue`, `fields: {"description": <description.md content>}`, and read it back again. Also check each approved field is set (pass its ID, and `components`, in `fields`); if one isn't, retry **once** with `editJiraIssue`, `additional_fields` = the printed fields, and report any still missing.

Report the new key as `<site>/browse/<NEW-KEY>`.

## Fallback: acli

Only when the MCP is unavailable. `acli` shows plain text literally, so it gets **ADF JSON**; **ticket text is never typed into a command** — the summary and description travel in a JSON file (`--from-json`, `--description-file`).

- Create: `acli jira workitem create --from-json <work-dir>/workitem.json --json`. The same never-create-twice rule applies. Fields travel in the file as `additionalAttributes`.
- Link: `acli jira workitem link create --out <NEW-KEY> --in <OTHER-KEY> --type Relates --yes`, using the exact Relates name from `acli jira workitem link type`.
- Read back: `acli jira workitem view <NEW-KEY> --fields description --json` — an ADF `doc` with `strong` headers and real `bulletList`/`orderedList` nodes, not one text blob. Retry **once** with `acli jira workitem edit --key <NEW-KEY> --description-file <work-dir>/description.json --yes`.

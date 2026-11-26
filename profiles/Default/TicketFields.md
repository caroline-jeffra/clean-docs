# Ticket fields

Optional Jira fields help triage and reporting. None is required to create a ticket, so suggest a value only where the evidence supports one. **Low confidence means blank** — a wrong value quietly skews reporting.

The Default profile suggests **components** only. A profile can replace this file to add its own fields (e.g. a single-select custom field), with rules for choosing them; `file-ticket` accepts `components` and `customfield_<id>` single selects.

## Find the fields

Fetch them live; IDs and options differ per site and project.

1. `executeRead` `listJiraProjectIssueTypesMetadata` with `projectIdOrKey` → the issue type's `id`.
2. `executeRead` `getJiraIssueTypeMetaWithFields` with `projectIdOrKey`, `issueTypeId`, `requiredFieldsOnly: false`, `maxResults: 200` and `responseFields: ["fields.fieldId", "fields.name", "fields.allowedValues.value", "fields.allowedValues.name"]`.
3. Keep the field with `fieldId` `components`. Team-managed projects have none; then skip the rest of this file.

## Choose components

Component descriptions rarely say more than the name, so **follow existing tickets**:

1. Components on tickets this one links or overlaps with.
2. Components on recent tickets in the same area: `searchJiraIssuesUsingJql` with `project = <KEY> AND text ~ "<two or three distinctive words>" ORDER BY created DESC`, `fields: ["summary", "components"]`, `maxResults: 10`.
3. A component name that plainly matches the subject or the potentially impacted files.

Suggest at most three, those the evidence agrees on. Similarly named components may be separate and both current: use whichever the related tickets use, never merge or "correct" them.

## Put them in the spec

```json
"fields": {"components": [{"name": "Billing"}]}
```

# Bug template

Sections in this order, each header a bold paragraph (`h` block). `Block` lists the block kinds a section may hold (`files` is the impacted-files list, below); a section marked `no` under `Required` may be the header alone.

| # | Header | Block | Required | Content |
|---|--------|-------|----------|---------|
| 1 | Expected | p | yes | One sentence on the correct behaviour |
| 2 | Actual | p | yes | What happened instead, with when and how often if known |
| 3 | Steps to reproduce | ol | yes | Numbered steps anyone can follow, ending on the step where it goes wrong |
| 4 | Evidence | ul | no | A link or file name per screenshot, recording or log the user already gave — never ask for them |
| 5 | Impact | ul | yes | Who is affected and how, one item per group |
| 6 | Potentially impacted files | files | yes | Paths only — see the rules |

## Rules

- Sections 1–5 are terse and readable by someone outside the team: no code names, stack traces or jargon.
- **Potentially impacted files** lists one `path/from/repo/root` per item in backticks and nothing else — no functions, line numbers or proposed changes. If the code wasn't read, the single line `Codebase not yet investigated.` If other tickets overlap this work, end with `Overlaps with KEY-123, KEY-456.`

## Spec skeleton

```json
{
  "summary": "Short summary",
  "blocks": [
    {"p": "This is a generated ticket description. Assessed ticket urgency: Blocking"},
    {"h": "Expected"}, {"p": "..."},
    {"h": "Actual"}, {"p": "..."},
    {"h": "Steps to reproduce"}, {"ol": ["Open ...", "Click ...", "..."]},
    {"h": "Evidence"},
    {"h": "Impact"}, {"ul": ["..."]},
    {"h": "Potentially impacted files"}, {"p": "Codebase not yet investigated."}
  ]
}
```

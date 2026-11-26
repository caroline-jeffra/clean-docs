# Task template

Sections in this order, each header a bold paragraph (`h` block). `Block` lists the block kinds a section may hold (`files` is the impacted-files list, below); a section marked `no` under `Required` may be the header alone.

| # | Header | Block | Required | Content |
|---|--------|-------|----------|---------|
| 1 | What needs to be done | p | yes | One or two plain-language sentences on the work and its outcome |
| 2 | Acceptance criteria | ul | yes | Observable results someone outside the team can check |
| 3 | Out of scope | ul | no | Related work this task deliberately leaves out |
| 4 | Potentially impacted files | files | yes | Paths only — see the rules |

## Rules

- Sections 1–3 are plain language; no code names, file paths or jargon.
- **Potentially impacted files** lists one `path/from/repo/root` per item in backticks and nothing else — no functions, line numbers or proposed changes. If the code wasn't read, the single line `Codebase not yet investigated.` If other tickets overlap this work, end with `Overlaps with KEY-123, KEY-456.`

## Spec skeleton

```json
{
  "summary": "Short summary",
  "blocks": [
    {"p": "This is a generated ticket description. Assessed ticket urgency: Nice to have"},
    {"h": "What needs to be done"}, {"p": "..."},
    {"h": "Acceptance criteria"}, {"ul": ["..."]},
    {"h": "Out of scope"}, {"ul": ["..."]},
    {"h": "Potentially impacted files"}, {"ul": ["`src/example/module.py`", "`src/example/other.py`"]}
  ]
}
```

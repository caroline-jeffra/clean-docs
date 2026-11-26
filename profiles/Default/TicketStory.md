# Story template

Sections in this order, each header a bold paragraph (`h` block). `Block` lists the block kinds a section may hold (`files` is the impacted-files list, below); a section marked `no` under `Required` may be the header alone.

| # | Header | Block | Required | Content |
|---|--------|-------|----------|---------|
| 1 | Goal | p | yes | One or two plain-language sentences on the outcome |
| 2 | Why | p/ul | yes | The problem in user or business terms, and the evidence for it |
| 3 | Acceptance criteria | ul | yes | Observable behavior someone outside the team can check; add "existing behavior X is unchanged" wherever there's regression risk |
| 4 | Out of scope | ul | no | Related work this story deliberately leaves out |
| 5 | How to verify | ul | yes | A concrete step and its expected result |
| 6 | Potentially impacted files | files | yes | Paths only — see the rules |

## Rules

- Sections 1–5 are plain language; no code names, file paths or jargon.
- **Potentially impacted files** lists one `path/from/repo/root` per item in backticks and nothing else — no functions, line numbers or proposed changes. If the code wasn't read, the single line `Codebase not yet investigated.` If other tickets overlap this work, end with `Overlaps with KEY-123, KEY-456.`

## Spec skeleton

```json
{
  "summary": "Short summary",
  "blocks": [
    {"p": "This is a generated ticket description. Assessed ticket urgency: Nice to have"},
    {"h": "Goal"}, {"p": "..."},
    {"h": "Why"}, {"ul": ["...", "..."]},
    {"h": "Acceptance criteria"}, {"ul": ["...", "Existing behavior ... is unchanged."]},
    {"h": "Out of scope"},
    {"h": "How to verify"}, {"ul": ["Do X. Expect Y."]},
    {"h": "Potentially impacted files"}, {"ul": ["`src/example/module.py`"]}, {"p": "Overlaps with KEY-123."}
  ]
}
```

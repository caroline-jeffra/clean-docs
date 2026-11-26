# Research template (test fixture)

| # | Header | Block | Required | Content |
|---|--------|-------|----------|---------|
| 1 | Question | p/ul | yes | What the research must answer |
| 2 | Sources | ul | no | Related tickets or docs |
| 3 | Follow-up | ul | yes | What the answers feed |

## Spec skeleton

```json
{
  "summary": "Short summary",
  "blocks": [
    {"p": "This is a generated ticket description."},
    {"h": "Question"}, {"p": "..."},
    {"h": "Sources"}, {"ul": ["PROJ-1: ..."]},
    {"h": "Follow-up"}, {"ul": ["..."]}
  ]
}
```

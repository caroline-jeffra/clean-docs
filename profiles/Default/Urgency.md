# Urgency rubric

Check the levels in order and **stop at the first match**. Each level is a `## N. <Level> — <meaning>` heading; the tooling reads the level names from these headings, so rename a level only here.

## 1. Critical — security or data at risk

- Secrets, credentials or personal data are exposed, or could be.
- Authentication or authorisation can be bypassed.
- A known vulnerability is reachable in production.
- Credentials that leaked still need rotating.

Load from hostile traffic is not Critical on its own; it is Critical only when the fix closes an actual hole.

## 2. Blocking — stops other work or real users

- Other planned work can't proceed until it's done.
- Real users hit errors, broken pages or data loss.
- It prevents recurring production failures, or makes hidden failures visible again.
- It blocks a dependency or platform upgrade.

A workaround outside the application (a firewall rule, a manual step) doesn't lower the level of the real fix.

## 3. Nice to have — everything else

- Quality-of-life improvements, cleanup, noise in logs, developer experience.
- Refactors with no visible change.

## Tie-breakers

| Situation | Rule |
|-----------|------|
| Two levels apply | Pick the higher one |
| One ticket bundles items of different urgency | Rate it by its most urgent item, or propose splitting it |
| Unsure whether something is a security issue | Ask the user rather than guess Critical |

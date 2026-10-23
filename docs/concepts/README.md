# Bestiarium concepts

Rules every Bestiarium integration follows, approved by the owner. Code reviews
check changes against them; the skill `.claude/skills/bestiarium-concepts`
routes a change to the documents it touches.

| Read | When the change |
|---|---|
| [problem-reporting.md](problem-reporting.md) (DEC-012) | handles a failure, missing data, a wrong setting, a failed user action or a safety event |
| [settings-vs-entities.md](settings-vs-entities.md) (DEC-013) | adds or moves a parameter, an option or an entity |
| [stability.md](stability.md) (DEC-014) | reacts to the state, availability or response of a device |

A change usually touches more than one: a device that stops answering is a
stability question (when is it a problem?), a problem-reporting question (how
is it shown?) and a settings question (where do the thresholds live?).

Deviations from a concept are product decisions: ask for them in the plan.

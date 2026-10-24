---
name: bestiarium-concepts
description: Use before planning or committing any Bestiarium change that handles a failure or missing data, adds, moves or removes a parameter, option, entity, attribute or event, or reacts to the state, availability or response of a device. Routes to the concept documents in docs/concepts/ and gives the pre-commit checklist.
---

# Bestiarium concepts

The rules live in `docs/concepts/`; this skill only routes and checks. Read the
documents, not a summary of them.

## When

Any change in `custom_components/` that:

| The change… | Read |
|---|---|
| handles a device failure, unavailable data, a wrong setting, a failed user action, a safety event | `docs/concepts/problem-reporting.md` |
| adds or moves a parameter, option, threshold or entity | `docs/concepts/settings-vs-entities.md` |
| reacts to a device's state, availability or response time | `docs/concepts/stability.md` |
| adds an entity, attribute, event, service or form field, or removes one | `docs/concepts/necessity.md` |

Usually more than one applies. Read all that do before writing the plan.

## Choosing the mechanism

Ask in this order:

1. Is it a problem yet, or a glitch below the tolerance? (stability)
2. Does it last (state), did it happen (event), must a person fix a setting
   (Repairs), or did a person's action fail (raised error)? (problem reporting)
3. Is each new value something people or automations change in daily life
   (entity) or an algorithm parameter (configuration)? (settings vs entities)
4. Is the new element needed — necessary, the only interface, or a repeated
   building block — or a convenience for the home's configuration? (necessity)

## Checklist before committing

- [ ] **Problems:** no `notify`, no `persistent_notification`; no log before
  `raise`; no broad `except` that swallows; the log records changes of state,
  not repeats.
- [ ] **Texts:** every text for a person has uk and en translations.
- [ ] **Contract:** the existing `bestiarium_*` bus events keep their names
  and fields.
- [ ] **Settings:** new algorithm parameters are in the entry's configuration
  with defaults, changeable without removing the entry; moved entities migrate
  their value.
- [ ] **Entities:** new entities are things people operate or need to see;
  display-only ones are `diagnostic`, test knobs are `config`.
- [ ] **Necessity:** each new entity, attribute, event, service or form field
  is stated as necessary, the only interface or a building block (compared
  with HA's best native alternative: only a capability one native helper of
  the same kind lacks, or a failure it allows, counts — not parity); events
  only for the integration's own decisions and only on transitions; attributes only if read or runtime data
  seen nowhere else, never configuration; internal signals stay internal;
  every removed element is listed in the report.
- [ ] **Stability:** a problem is confirmed by tolerance and cleared by
  hysteresis; response waits adapt to the device, with a hard limit in the
  red zone; frequently changing details are not recorded; memory is bounded.
- [ ] **Tests:** each of the above that the change touches has a test; device
  faults are reproduced with the test stand's fault model and a seeded
  generator.

A deviation from a concept is a product question for the plan, never a silent
choice.

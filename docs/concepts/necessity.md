# Necessity (DEC-015)

What belongs in a Bestiarium integration, and what belongs in the home's own
Home Assistant configuration. Every element costs: code to maintain,
migrations, compatibility with each HA release, entities in the registry,
rows in the history. An element is added only when it is needed.

## In the integration

1. **Necessary** — the integration's logic does not work without it.
2. **The only interface** to information or control that lives inside the
   integration: a control state, the learned consumption, "Allowed", the
   mode select, events about what the integration did.
3. **What the HA configuration cannot do.**

## A repeated building block — also in the integration

Something that could be built with configuration still belongs in the
integration when these criteria meet; the more of them meet, the stronger
the case:

| Criterion | The question |
|---|---|
| Multiplicity | Would it be repeated for N devices? |
| Complexity | Does each copy combine several entities or devices, conditions, thresholds, a tolerance? |
| Influence | Does it take part in control, not only show something? |
| Quality | In the integration it is set up in a few clicks and covered by tests; in configuration it is copied by hand, untested |

Example: the virtual switch of Bestiarium Climate meets all four. Against
HA's template switch (refinement 4) it stays for its real differences only:

- **a delay on the state read from power**: the template switch has no
  `delay_on` / `delay_off`; per heater it would take a Threshold helper, an
  `input_boolean` and an automation;
- **"off" cancels an "on" sequence still running**: the template switch runs
  its actions as separate scripts in `single` mode;
- **its forms check that its actions match its thermostat's mode** (a
  thermostat cannot use a virtual switch without the action of its mode; the
  switch cannot lose the action its thermostat needs).

At parity, so no argument: the action sequences, the hysteresis (a template
can use `this.state`), plain availability.

## Convenience — in the home's configuration

One-off or simple (one template expression), only for display, built from
what an integration already shows → not in Bestiarium. The documentation
gives a ready example instead.

Example: one "load zone" value from the four zone sensors is a template
sensor ([../bestiarium_mode.md](../bestiarium_mode.md#one-zone-value-a-template-sensor)),
not an entity of `bestiarium_mode`.

## How to judge (the owner's refinements, DEV-008 review 11)

1. **Events.** An event is an interface only when it tells the integration's
   own **decision or action** (it sent a command, gave a place in the
   capacity, ran the emergency queue). An event that repeats a change of
   state already visible as an entity is a convenience.
2. **Function, not wiring.** Internal signals stay internal (dispatcher); a
   public bus event is always judged as an interface.
3. **Display at no cost** — no entities in the registry, no rows in the
   history, no migrations (for example the logbook descriptions of the
   events) — may stay when the home's alternative is more than one copy.
4. **A building block is compared with the best native alternative of HA**
   (UI helpers, blueprints), not with hand-written YAML. It stays only if it
   **gives at least one capability a single native helper of the same kind
   lacks** (without extra helpers, automations, blueprints or YAML), **or
   prevents a failure the native one allows**. What the native helper also
   does (parity) does not count. The plan lists each such difference.
5. **"Its own logic" includes the other Bestiarium integrations**; the
   contract between them is minimal.
6. **The person's need is judged**, not the exact shape of a feature.
7. **Attributes** only if something reads them, or they are runtime data
   visible nowhere else; **configuration is never copied into attributes**.
8. **Interface events only on transitions.**

## Rules for every change

- **Each new element** — entity, attribute, event, service, form field — has
  its necessity stated in the plan: which of the cases above it is.
- **A repeat is not added**: an attribute that repeats another entity's
  state, an event that repeats a state change of an entity, metadata the HA
  core already shows.
- **What an integration's own logic reads** stays, even if it looks like a
  repeat (the red zone's `red_threshold`, which the balancer reads).
- **Removing an element** is a contract change for the owner's dashboards,
  automations and templates: the report lists every removed element, and the
  manager checks the live HA before the deployment.

## Applied in DEV-008 stage B

Removed (review 10): the climate event `capacity_exhausted` (a deficit of capacity is
the normal state of a home that needs Bestiarium Climate), `switch_failed` /
`switch_recovered` (the thermostat's control state tells it), the balancer's
`zone_changed` (the zone sensors' changes tell it), attributes that repeat
other entities (`limit_w` and `zone` of the zone sensors, the thresholds of
other zones, the mode select's option metadata, `manual_power_*` of the
expected power), the test grid's overload alarm. Removed (review 11):
`recovery_pending` and `yellow_threshold` of the zone sensors, the
description and icon of a mode (form fields, attributes, the icon changing
with the mode), `mode_options_snapshot` of the device entries (written,
never read), the switch's own heating/cooling field. Kept: the four zone
sensors, `red_threshold` of the red one (the balancer reads it),
`load_limit_changed`, the overload events, `device_turned_on/off`,
`mode_applied`, the control events, the logbook descriptions, the climate
virtual switch.

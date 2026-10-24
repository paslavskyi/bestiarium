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

Example: the virtual switch of Bestiarium Climate (actions per mode, its
state from a power sensor, a threshold; one per heater; it drives the
balancing) meets all four.

## Convenience — in the home's configuration

One-off or simple (one template expression), only for display, built from
what an integration already shows → not in Bestiarium. The documentation
gives a ready example instead.

Example: one "load zone" value from the four zone sensors is a template
sensor ([../bestiarium_mode.md](../bestiarium_mode.md#one-zone-value-a-template-sensor)),
not an entity of `bestiarium_mode`.

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

Removed: the climate event `capacity_exhausted` (a deficit of capacity is
the normal state of a home that needs Bestiarium Climate), `switch_failed` /
`switch_recovered` (the thermostat's control state tells it), the balancer's
`zone_changed` (the zone sensors' changes tell it), attributes that repeat
other entities (`limit_w` and `zone` of the zone sensors, the thresholds of
other zones, the mode select's option metadata, `manual_power_*` of the
expected power), the test grid's overload alarm. Kept: the four zone
sensors, `load_limit_changed`, the overload events, `device_turned_on/off`,
`mode_applied`, the control events, the climate virtual switch.

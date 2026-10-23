# Settings vs entities (DEC-013)

Where a value lives: in the configuration of the entry, or in an entity.

## The criterion

**Is there a real case where a person in daily life, or an automation, changes
this value?**

- **Yes → an entity.** Things people and automations operate (a relay,
  "Allowed", the load limit, the climate capacity that a power automation
  changes) and things people need to see (states, measurements, "Expected
  power", a control state).
- **No → configuration** (config flow, options flow, reconfigure flow;
  `entry.data` / `entry.options`). Parameters of an algorithm: set once,
  rarely changed, they define behaviour. Thresholds, attempts, delays,
  tolerances, hysteresis, which sensor or relay, default capacity, priorities
  per mode, estimated power.

## Rules

- Every configuration parameter has a **default** and can be **changed after
  setup without removing the entry** (options or reconfigure flow).
- **Options flow** is for how the integration treats the entry (tuning);
  **reconfigure** is for what the entry is (which device, relay, sensor).
- A configuration read at runtime (`entry.options`) applies at once; do not
  reload the entry for it.
- **Moving an entity into configuration** migrates its value without loss
  (`async_migrate_entry`, `VERSION` / `MINOR_VERSION`) and removes the entity.
- New entities that only show something are `diagnostic`; settings that must
  stay entities are `config`. Neither appears on family dashboards by default.

## The exception: test knobs

The knobs of the test stand (`bestiarium_mock`: fault profile, fault
parameters, power, temperature) are entities, because a tester changes them
during a test. They are `entity_category: config`, on the device page, not on
the stand dashboard. `bestiarium_devices` never offers a `config` or
`diagnostic` entity as a device's relay or power sensor.

## Examples in Bestiarium

| Value | Where | Why |
|---|---|---|
| "Allowed" | switch entity | the family turns a device out of balancing |
| Load limit | number entity (`bestiarium_mode`) | an automation changes it when the power source changes |
| Priorities per mode | configuration | set once per device |
| Estimated power | number entity today | known debt: moves to configuration in DEV-008 |
| Turn-on attempts, deferral, tolerance, hysteresis (DEV-007 stage 2) | options flow of the device | algorithm parameters |

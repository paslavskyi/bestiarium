# Problem reporting (DEC-012)

How Bestiarium tells people and automations that something is wrong. One rule
picks the Home Assistant mechanism; the table maps each kind of problem to it.

## The rule

- **The problem lasts** — you can ask "is there a problem now?" → a **state**:
  an entity whose value is the problem.
- **Something happened and is over** → an **event**.
- **A person must fix a setting** → a **Repairs issue**.
- **A person's action failed** → an **error raised to that action**.

## Kinds of problems

| Kind | Example | Mechanism | Who sees what |
|---|---|---|---|
| Short glitch | a missed reading, a slow reply | Nothing visible: retry, `debug` log | Nobody; it is not a problem yet (see [stability.md](stability.md)) |
| Lasting device problem | a device does not respond after several attempts; no power data | A diagnostic state entity with the cause; the logic works around the device; one `warning` when it starts and one `info` when it ends | The owner on the device page; automations on the state; the family only through the owner's automations |
| Wrong configuration | a device or sensor in the settings was removed | A Repairs issue: not persistent, `is_fixable` with a flow to the setting, deleted when fixed; the rest keeps working; checked only after HA has fully started | The owner in Settings → Repairs |
| Safety event | emergency shutdown on overload | An event entity; the existing bus events stay as they are | Automations (the owner's notifications), the logbook |
| Failed human action | the "Allowed" switch could not be applied | `ServiceValidationError` / `HomeAssistantError` with a translation key (`exceptions` in the translations) | The person who acted, in the UI |
| Own data gone | no power reading for the zones | Own entities become `unavailable`; one `info` when it goes and one when it returns | Dashboards show "unavailable"; automations can trigger on it |

## Rules for every change

- **Bestiarium notifies nobody.** Only the owner's automations send
  notifications. Bestiarium gives the signal (a state or an event); there is no
  `notify` call in any integration.
- **No `persistent_notification`.** The existing "Balancing Disabled"
  notification of `bestiarium_devices` is known debt, to be replaced in DEV-008.
- **The log records changes of state, not repeats.** A problem that lasts an
  hour gives one `warning` and one `info`, not a line a minute.
- **Do not log before raising.** The exception carries the message; logging it
  as well doubles it.
- **No broad `except Exception` that swallows errors.** Catch what can happen
  and say why.
- **Every text for a person is translated** (uk/en): entity and state names,
  attributes, errors, Repairs issues. HA has no translations for logbook
  messages; those stay English.
- **Bus events are a contract.** Names and fields of the existing
  `bestiarium_*` events do not change; the owner's automations use them
  (three on `bestiarium_devices_overload_first_queue`, `_second_queue`,
  `_cleared`). New signals are added next to them, not instead of them.

## Examples in Bestiarium today

- **Safety event:** the overload shutdown fires
  `bestiarium_devices_overload_first_queue` / `_second_queue` / `_cleared`; the
  owner's automations notify the family. Since DEV-007 the balancer's events
  are also readable in the logbook.
- **Failed switch in climate:** `bestiarium_climate_switch_failed` /
  `_switch_recovered` events; to be reviewed against this document in DEV-008.
- **Known debt:** "Balancing Disabled" is a `persistent_notification`; it
  becomes a Repairs issue in DEV-008.

The balancer's handling of devices that do not respond (DEV-007 stage 2) is the
first full example; it is added here when it lands.

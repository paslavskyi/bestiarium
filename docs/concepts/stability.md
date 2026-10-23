# Stability and adaptation (DEC-014)

Cloud devices (SmartThings, Tuya) are unstable; that is a fact, not a bug to
report each time. Bestiarium reacts only to **confirmed** problems, quietly.

## Rules

1. **Tolerance (entry threshold).** A device or its data being gone is a
   problem only if it lasts longer than a threshold (default 1 min). Shorter
   is silent (`debug`).
2. **Hysteresis (exit threshold).** Back to "normal" only after stable work
   for a threshold (default 1 min). A glitch in between restarts the wait.
3. **Adaptive response time.** Remember how long each device takes to carry
   out a command (the last ~10 commands, bounded memory). Count an attempt as
   failed only after that time with a margin, and never sooner than the
   strategy's step. A new device starts with a conservative default. In the
   **red zone** the total wait for one device has a **hard limit** (default
   30 s): the load must come down.
4. **Unstable connection.** A device that works but had many short glitches
   in the last hour (the count is a parameter) is shown as "unstable
   connection", without a log line per glitch.
5. **Transparency without flapping.** A device stays in one state while the
   problem lasts (for example "not responding", also while it is retried).
   What happens meanwhile is told by **device events** ("deferred",
   "retrying", "recovered"); how to react is up to the home's configuration.
6. **Frugality.**
   - Details that change often (counters, response times) are not recorded:
     `_unrecorded_attributes`, or kept out of the state.
   - Memory is bounded (a fixed number of values per device).
   - The log holds confirmed changes of state only.
7. **Thresholds are configuration** with defaults ([settings-vs-entities.md](settings-vs-entities.md)).

## How this meets problem reporting

[problem-reporting.md](problem-reporting.md) decides *how* a problem is shown;
this document decides *when* something is a problem. A glitch below the
tolerance is a "short glitch" (silent); above it, a "lasting device problem"
(state entity, one `warning`, one `info`).

## Testing it

The test stand (`bestiarium_mock`, see [../bestiarium_mock.md](../bestiarium_mock.md))
reproduces these devices: fault profiles "Slow cloud", "Unstable",
"Stuck state", "Unresponsive", with a seeded random generator in the
automated tests.

## Examples

The balancer's device control (`bestiarium_devices/control.py`, DEV-007) is
the first implementation, described in
[../bestiarium_devices.md](../bestiarium_devices.md#device-control-controlpy):

- **Adaptive response time:** an attempt waits `max(step, longest of the last
  10 answers × 1.5)`, 15 s × 1.5 for a new device; the answers are kept across
  restarts. A slow cloud socket that answers in 12 s gets no failed attempt in
  the red zone, where before it got a command every 5 s.
- **Hard limit:** in the red zone a device gets at most 30 s in all; then the
  next device is turned off.
- **Tolerance / hysteresis:** 60 s each; a glitch inside the hysteresis
  starts it again.
- **Unstable connection:** 3 short dropouts in an hour.
- **Frugality:** the details are `_unrecorded_attributes`; the response memory
  holds 10 values per device; the deferral is not stored.

The load zones of `bestiarium_mode` (DEV-008, [../bestiarium_mode.md](../bestiarium_mode.md#zones)):
up at once, down only below the threshold minus a margin (3 % of the limit),
green after a pause (30 s), "no data" only after a tolerance (60 s).

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
   **A series of attempts** (review 24): the commands of one direction sent
   to a device without an answer. Only the device's answer (it is in the
   state of the command) ends the series, never a change of what is wanted
   in between; a device that does not answer becomes "not responding" after
   the configured number of commands without an answer, however often the
   wish changed between them. A command that never ends counts too: one that
   runs longer than the budget of the attempts is a problem. A protection
   that cuts a command short (the load protection cutting the power) is no
   failed attempt.
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

The thermostats' control of Bestiarium Climate (`bestiarium_climate/control.py`,
DEV-008) keeps the series of each direction apart: an "off" in between (the
rotation, the load zone) leaves the series "on" counting. The devices' control
still starts a new series with a command of the other direction (a known
difference, not changed in DEV-008).

The budget of an "on" that never ends is `attempts` × a cycle, but never less
than 90 s: an IR sequence takes about a minute, so a single attempt would make
a working air conditioner "not responding" (rule 3, DEV-008 stage C; a fixed
floor, not a memory of responses, by the owner's choice).

For the direction "off" the thermostats' control has, since DEV-010 (the
owner, SRC-169), a **stepwise "off"** instead of a series: a device that runs
while it is wanted off gets an "off" every 5 minutes, 3 in all, then an event,
then its socket is cut; it never makes the thermostat "not responding". In
red and overload its steps go a cycle apart (the hard limit). Unlike a series
it ends also when the device is wanted on again, and the device off ends it
only after a whole step off (an idling air conditioner dips and runs on); the
next time it starts from the first "off". The control state "Does not turn off"
enters after the tolerance and leaves after the hysteresis.

The load zones of `bestiarium_mode` (DEV-008, [../bestiarium_mode.md](../bestiarium_mode.md#zones)):
up at once, down only below the threshold minus a margin (3 % of the limit),
green after a pause (30 s), "no data" only after a tolerance (60 s).

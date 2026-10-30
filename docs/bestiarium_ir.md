# bestiarium_ir — IR emitters and IR air conditioners as devices of HA

## Overview

`bestiarium_ir` (domain `bestiarium_ir`) makes two layers of the new
thermostats (FEAT-066, design `superpowers/specs/2026-10-30-new-thermostats-design.md`):

1. **A Zigbee TS1201 blaster as an infrared emitter of HA** (layer 1): an
   `infrared` entity that sends any IR command through ZHA.
2. **A Hisense air conditioner as a climate device** (layer 2): `climate` and a
   "Display" switch, on **any** `infrared` emitter of HA (this blaster, an
   ESPHome one, the stand's).

Bestiarium Climate knows nothing of either: its air conditioner thermostat
(stage 4) uses any `climate` of an air conditioner.

One config entry, **Bestiarium IR** (made at once, without fields; one per
HA); its devices are **subentries**, added with "Add Zigbee IR blaster" and
"Add Hisense air conditioner" on the entry, changed with "Reconfigure".

```
custom_components/bestiarium_ir/
├── __init__.py      # entry setup, the links of the air conditioners, renames, Repairs check
├── config_flow.py   # the entry; subentries "blaster" and "hisense"
├── infrared.py      # layer 1: ZigbeeBlasterEmitter
├── tuya.py          # the Tuya/Zosung code format (compression, base64)
├── kelon168.py      # the Kelon168 protocol: Kelon168Command (encode, recognise)
├── hisense.py       # HisenseLink: the frame queue, the display, the buttons table
├── climate.py       # layer 2: HisenseClimate
├── switch.py        # layer 2: HisenseDisplaySwitch
├── follow.py        # a renamed emitter is written into its air conditioners
└── repairs.py       # a gone TS1201 or emitter: Repairs issue with its form
```

---

## Layer 1: the Zigbee TS1201 blaster

| Field | Meaning |
|---|---|
| `zha_device` | the TS1201 device in ZHA (model `TS1201`); one blaster per device |

The subentry is named as the ZHA device. Its device ("TS1201 IR bridge") is
behind the ZHA device and has one entity, the emitter (`infrared.*`, state:
the time of the last command sent).

A command's timings (`Command.get_raw_timings()`, `repeat_count + 1` times in
one code) are compressed into the Tuya/Zosung format (`tuya.py`) and sent
exactly as the home's scripts do:

```yaml
action: zha.issue_zigbee_cluster_command
data:
  ieee: <of the ZHA device>
  endpoint_id: 1
  cluster_id: 57348   # 0xE004
  cluster_type: in
  command: 2
  command_type: server
  params: {code: <base64>}
```

- The compression is the one of the codes working in the home (`kelon_gen.py`,
  FEAT-025), so the same timings give the same code bit for bit: the tests
  check all 61 codes of the home's packages.
- A duration over 65535 µs cannot be sent: the action fails ("The IR command
  cannot be sent by a TS1201"), nothing is sent. The modulation is not sent
  (the TS1201 sends 38 kHz).
- **No confirmation, no retry.** ZHA reports a command to a sleeping
  (battery) blaster as sent at once; the thermostat of stage 4 confirms by
  the power drawn. ZHA's errors fail the action as they are.
- The emitter is **unavailable** while its ZHA device is gone.
- **A temporary bridge.** When HA's core shows the TS1201 as an infrared
  emitter, remove the blasters and choose the core's emitter for each air
  conditioner ("Reconfigure"); the air conditioners stay as they are.

---

## Layer 2: the Hisense air conditioner

| Field | Meaning |
|---|---|
| `name` | the name (the subentry's title and the device's name), "Hisense" by default |
| `emitter` | any `infrared` emitter of HA; a renamed emitter is followed |

The device ("Hisense", model "Kelon168 (DG11R2-01)") has:

| Entity | What it is |
|---|---|
| `climate` (named as the device) | modes off, heat, cool, dry, fan only, auto (the unit's own); target 16–30 °C by 1; fan auto, min, low, medium, high, max |
| `switch` "Display" | the unit's display, assumed |

Both are unavailable while the emitter is; their state is **assumed** (IR has
no answer) and restored after a restart.

### Frames

Every frame carries the whole state (mode, target, fan, on/off), so the unit
follows the last frame whatever it did before. The clock in a frame is 23:40
(the unit drops a frame with the hour 00).

| Change | Frame |
|---|---|
| off → a mode | the mode, the target, on |
| a mode → another, while on | the new mode |
| the target, while on | the new target |
| the fan speed, while on | the new speed |
| auto (the unit's own) | auto, 23 °C, fan auto; the person's target and speed are kept for the other modes |
| off | off, with the last mode and target |
| the target or the speed while off or in auto | nothing: kept for later |

A mode command (`set_hvac_mode`, `turn_on`, `turn_off`) always sends its
frame, even to the mode HA already assumes: that is how a thermostat repairs
a change made with the remote, and how "off" is sent again.

### The buttons of the frames (not all checked at home)

A frame also carries the remote's button (byte 15). One table in `hisense.py`
(`ACTIONS`) decides it, so a button found wrong on Stasi's air conditioner is
changed in one line:

| Change | Button | Checked at home |
|---|---|---|
| on (from off) | temperature (0x02) | yes — the home's scripts start the unit with it |
| the target | temperature (0x02) | yes |
| off | power (0x01) | yes |
| the display | light (0x00) | yes |
| another mode | mode (0x06) | **no** |
| the fan speed | fan (0x11) | **no** |
| the unit's auto | iFeel (0x0D), as IRremoteESP8266 | **no** |

### To check on Stasi's air conditioner after the deployment

The protocol follows IRremoteESP8266 (`ir_Kelon.h`), the home checked "heat"
and "off" only:

- **the fan speeds**: whether the remote DG11R2-01 has 5 speeds (if 3, the
  extra ones are removed in a small change); their bits (bits 0–1 of byte 2,
  bit 1 of byte 16, IRremoteESP8266 — `kelon_gen.py` put the high bit in
  bit 0);
- cool, dry, fan only, the unit's auto;
- the buttons marked "no" above.

### Frames one by one

The unit does not carry out a frame sooner than **3 s** after the previous
one. Each air conditioner has a queue: frames go in order, at least 3 s
apart; an action waits for its turn (up to about 6 s with a doubled frame).
The new state is shown once its frame is sent; a failed send fails the action
and changes nothing.

### The display

The display's state is assumed (design §10):

- While the display is off, **any frame only lights it**: a frame of a
  running state goes **twice**, 3 s apart, and the display is then assumed on.
- **"Off" goes once**, display off or not (the home, 2026-10-28: the unit
  turned off with the display off).
- The switch of a running unit sends one display frame (with the unit's
  state). The switch of a unit that is off sends **nothing**: its display goes
  out by itself, only the assumed state changes (DEV-012 review, U3).
- A display changed with the remote is not seen; the next frame makes up for
  it (it goes once and only lights the display, or goes twice for nothing).
- Turning the unit on or off does not change the assumed display.

The night display-off stays an automation of the home (design §9), on this
switch.

---

## Problems (docs/concepts/problem-reporting.md)

| Problem | Mechanism |
|---|---|
| The TS1201 of a blaster gone from ZHA | Repairs issue "The TS1201 of the blaster … is gone", fixed by choosing the TS1201; the emitter is unavailable meanwhile |
| The emitter of an air conditioner gone from HA | Repairs issue "The infrared emitter of … is gone", fixed by choosing an emitter; the air conditioner is unavailable meanwhile |
| A send failed | the action fails (the emitter's or ZHA's error) |

The issues are checked once HA has started and on every reload of the entry
(each change of a subentry reloads it); a fix reloads it, and the issue goes.
The issues of a removed subentry or entry go with it. A renamed emitter is
followed, not reported. No events, no notifications.

---

## The test stand

The stand ([bestiarium_mock.md](bestiarium_mock.md)) has a Hisense of its own
("Stand Hisense") on the mock emitter `infrared.mock_hisense_emitter`: the
mock air conditioner recognises the frames with `kelon168.py` and behaves as
the real unit (scenarios 5 and 6). A second one, "Stand Hisense 2" on
`infrared.mock_hisense_2_emitter`, warms or cools its room and idles at its
setpoint, under the air conditioner thermostat of Bestiarium Climate
(scenarios 3, 4, 7, 8, DEV-013). The TS1201 itself is not on the stand: its
tests are the 61 codes of the home's packages, and the home.

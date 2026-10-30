# Універсальний термостат, ручний режим, «спершу ручні» в червоній — план реалізації (FEAT-066, етап 2, DEV-011)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. (Для DEV-011 менеджер призначив `superpowers:executing-plans`.)

**Goal:** новий тип підзапису Bestiarium Climate — універсальний термостат (будь-який вимикач + необов'язковий датчик споживання; режими `auto`, `off`, ручний `heat`/`cool`), ручний режим за FR-028, крок «спершу ручні» FR-029 у червоній і при перевантаженні, ступінчасте вимкнення без кроку «розетка», сценарії стенда 1, 2, 9, 10.

**Architecture:** універсальний термостат — підклас `VirtualThermostat` і ще один `ThermostatControl` у тому самому `BalancingCoordinator`: одна ємність, одні правила зони, один шлях команд (`_send`). Стан вимикача за споживанням читає `PowerReading`, винесений із `VirtualSwitch` (той самий поріг, гістерезис, затримка, допуск). Ручний режим — гілка `ThermostatControl.next_command` з некоректованими командами (`counted=False`); «спершу ручні» — розширення `zone_rules.arrange_zone` (`Arrangement.manual_off`), яке балансувальник виконує до решти правил зони.

**Tech Stack:** Python 3.14, Home Assistant 2026.10 (config subentries, `ClimateEntity`, `RestoreEntity`), pytest + `pytest_homeassistant_custom_component`, freezer; стенд — `scripts/stand_setup.py` (REST/websocket HA), `bestiarium_mock`.

**Spec:** `docs/superpowers/specs/2026-10-30-new-thermostats-design.md` (§3, §4 «Універсальний термостат» і «Спільне», §6 сценарії 1, 2, 9, 10, §7 етап 2, §10). Бріф: `D:\bestiarium-smart-home\handoff\DEV-011-01-brief.md`.

## Global Constraints

- Гілка `dev-011-universal-thermostat` від локального `master` (на ньому `b6be5f1` зі специфікацією, ще не запушений).
- HA 2026.10.x, Python 3.14; HA у середовищі не оновлювати.
- Наявні записи й старий термостат — **без змін поведінки й без міграції**: `VERSION`/`MINOR_VERSION` запису й `STORAGE_MINOR_VERSION` сховища не змінюються; новий тип — лише нові підзаписи.
- Шари 1–2 (ІЧ-передавач, Hisense) і термостат кондиціонера — не в цьому етапі.
- Концепції `docs/concepts/`: жодного `notify` / `persistent_notification`; журнал — лише зміни стану; кожен текст для людини — uk і en; наявні події `bestiarium_*` не змінюють імен і полів; параметри алгоритму — у налаштуваннях підзапису з типовими значеннями.
- Події — лише на переходах і лише про власні рішення інтеграції (DEC-015, уточнення 1, 8).
- Код, коментарі, коміти — англійською; коміти `feat|fix|refactor|test|docs|chore(<integration>): …`.
- `make lint-check` і `make test` зелені після кожної задачі.
- Робочий HA не чіпати; Docker на змонтованому репо — з UID 1000.

## Review Focus

1. **Ручний → «авто», коли вимикач працює.** Очікування: місце є — балансувальник бере його як «працює» без жодної команди; місця немає — вимикач вимикається ступінчасто (працює всупереч термостату). Тест — Task 5 (`test_manual_to_auto_keeps_a_running_switch`, `test_manual_to_auto_without_a_place_turns_off_stepwise`).
2. **Перезапуск HA у ручному режимі під час червоної зони.** Очікування: перший цикл — вхід у червону: ручний переходить у `off`, подія з `cause: zone_entered`. Тест — Task 6 (`test_manual_restored_in_red_is_turned_off`).
3. **Людина вимкнула вимикач ручного термостата.** Очікування: команда «увімкнути» не повторюється, доки не зміниться потреба за температурою (команди один раз). Тест — Task 5 (`test_manual_command_goes_once`).
4. **Хмарний вимикач з оптимістичним станом (шаблонний над сценами) і датчиком споживання.** Очікування: стан читається за споживанням; коротке падіння, коротше за затримку, нічого не змінює; немає даних довше за допуск — «Вимикач недоступний». Тест — Task 4 (`test_reading_follows_power_not_the_switch`, `test_power_dip_shorter_than_the_delay_changes_nothing`, `test_no_power_data_is_switch_unavailable`).
5. **Ручний увімкнули в паузі між червоною й перевантаженням.** Очікування: пауза не є новим входом — «вимкнути» наосліп входу не повторюються, але ручний вимикається при поверненні в червону з `cause: turned_on`. Тест — Task 6 (`test_manual_in_the_pause_between_red_and_overload`).

---

## Технічні рішення (для рев'ю менеджера)

- **Тип підзапису** `universal_thermostat`; ключі даних — ті самі, що в старого термостата й віртуального вимикача (`target_switch`, `temperature_sensor`, `thermostat_mode`, `power_entity`, `min_power`, …), тож `follow.py` і `shared_switches` працюють без нових ключів. `thermostat_mode` тут — «що робить „авто“ і ручний»: `heating` → ручний `heat`, `cooling` → ручний `cool`.
- **Вимикач** — будь-який `switch`, крім віртуальних вимикачів Climate (у них своє споживання; універсальний і замінює пару «віртуальний вимикач + термостат») і реле Bestiarium Devices (правило «одне реле — одна інтеграція», `switch_is_device`).
- **Стан вимикача:** є датчик споживання — за ним (`PowerReading`); немає — стан сутності вимикача, як у старого термостата на звичайному вимикачі.
- **Потужність для «вміщається» і «вимкнено живлення»:** пристрій Bestiarium Devices, чий датчик споживання — `power_entity` універсального (як для віртуального вимикача), інакше `heater_power`, інакше 2000 Вт. Розетки (крок 3 ступінчастого вимкнення) у універсального немає.
- **Ручний режим:** поза ємністю й правилами зони до червоної; команда йде, лише коли потреба за температурою відрізняється від останньої ручної команди (першої в ручному — від того, що показує вимикач); команди не рахуються (`counted=False`): жодних спроб, «Не відгукується», відкладання, ступінчастого вимкнення. Стани «Вимикач недоступний» і «Немає температури» лишаються (видимість).
- **Червона й перевантаження:** у кожному циклі цих зон усі ручні переходять у `off` (не лише на вході) — так одне правило покриває і «на вході», і «увімкнули в червоній». `cause` події: `zone_entered` у циклі входу (за наявним `_last_zone`), інакше `turned_on`. Після `off` це звичайний термостат: його «вимкнути» йде в цьому ж циклі першим — до «вимкнути наосліп» входу й до черги червоної.
- **Подія** `bestiarium_climate_manual_turned_off` (одна, з полем `cause`): рішення інтеграції (вона вимкнула термостат людини), лише на переході. Поля: `thermostat`, `switch_entity_id`, `zone` (`red` | `overload`), `cause` (`zone_entered` | `turned_on`).
- **`min_power` за замовчуванням 20 Вт** (у віртуального вимикача 0 Вт, що з датчиком означає «завжди ввімкнено»); гістерезис 20 %, затримка 10 с, допуск 60 с — як у віртуального вимикача.
- **Стенд:** нові підроблені пристрої `heater` (розетка 1500 Вт, без запису Bestiarium Devices), `ac_4` (живлення хмарного кондиціонера, як `ac_1..3`) і `thermo_5`; шаблонний вимикач `switch.stand_cloud_ac` (хелпер «Шаблон» через його config flow) над ІЧ-скриптами `ac_4` як «сценами». Кімнати на кондиціонер не реагують (це для етапу 4); сценарії ставлять температуру самі, як наявні.

## Файли

| Файл | Відповідальність |
|---|---|
| `custom_components/bestiarium_climate/power_reading.py` (новий) | `PowerThresholds`, `PowerReading`: увімк./вимк. за датчиком споживання з порогом, гістерезисом, затримкою, допуском |
| `switch.py` | `VirtualSwitch` читає стан через `PowerReading`; `thermostats_of` — обидва типи термостатів |
| `const.py` | `SUBENTRY_TYPE_UNIVERSAL`, `THERMOSTAT_TYPES`, `DEFAULT_UNIVERSAL_MIN_POWER`, `EVENT_MANUAL_TURNED_OFF`, `ManualTurnedOffData`, `ManualCause` |
| `config_flow.py` | `UniversalThermostatSubentryFlowHandler` (кроки `basic` → `power` → `presets`), реєстрація типу |
| `climate.py` | `UniversalThermostat(VirtualThermostat)`; вибір класу за типом підзапису; `VirtualThermostat.manual` |
| `control.py` | джерело стану вимикача (сутність або `PowerReading`), `power_entity`, ручний режим (`set_manual`, гілка `next_command`) |
| `devices.py` | `power_supply`/`power_device` за датчиком споживання, не лише за віртуальним вимикачем |
| `balancer.py` | `THERMOSTAT_TYPES`; ручні поза ємністю; виконання `manual_off`; подія |
| `zone_rules.py` | параметр `manual`, поле `Arrangement.manual_off` |
| `stepwise_actions.py` | потужність у події «поза контролем» — з `control.measured_power()` |
| `repairs.py` | виправлення `switch_is_device` для універсального не пропонує віртуальні вимикачі |
| `translations/en.json`, `uk.json` | підзапис `universal_thermostat`, `entry_type`, нова кнопка «додати» |
| `custom_components/bestiarium_mock/const.py`, `translations/*` | `heater`, `ac_4`, `thermo_5` |
| `scripts/stand/layout.py`, `entries.py`, `scenarios.py`, `dashboard.py` | універсальні термостати стенда, шаблонний вимикач, сценарії 1, 2, 9, 10 |
| `docs/bestiarium_climate.md`, `docs/events.md`, `docs/bestiarium_mock.md` | новий тип, ручний режим, «спершу ручні», подія, стенд |
| `tests/custom_components/bestiarium_climate/test_power_reading.py`, `test_universal.py`, `test_manual.py` (нові); `test_zone_rules.py`, `test_config_flow.py`; `tests/stand/test_stand_setup.py`; `tests/custom_components/bestiarium_mock/*` | тести |

---

### Task 1: `PowerReading` — стан за споживанням, винесений з віртуального вимикача

Поведінка `VirtualSwitch` не змінюється; наявні `test_switch.py` — сторож.

**Files:**
- Create: `custom_components/bestiarium_climate/power_reading.py`
- Modify: `custom_components/bestiarium_climate/switch.py:168-344` (`_reading`, `_show`, `_async_power_changed`, `_async_apply`, `_cancel_pending` переходять у `PowerReading`)
- Test: `tests/custom_components/bestiarium_climate/test_power_reading.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class PowerThresholds: min_power: float; hysteresis_percent: int; delay_seconds: int; tolerance_seconds: int` з `@classmethod of(data: Mapping[str, Any], *, min_power: float = 0.0) -> Self` (ключі `CONF_MIN_POWER`, `CONF_POWER_HYSTERESIS`, `CONF_POWER_DELAY`, `CONF_POWER_TOLERANCE`; відсутні — `DEFAULT_POWER_*`, `min_power` — аргумент).
  - `class PowerReading(hass: HomeAssistant, name: str, power_entity: str, thresholds: PowerThresholds, on_change: Callable[[], None])` з `start() -> None` (читає поточний стан, підписується), `stop() -> None`, властивостями `available: bool`, `is_on: bool`, `last_changed: datetime` (момент, коли показаний стан або доступність змінилися).
  - `on_change` викликається лише при зміні показаного (`available` або `is_on`), не на кожне значення датчика. Рядок `info` «power data … is gone / is back» — тут, лише коли `hass.state is CoreState.running` (як зараз).

- [ ] **Step 1: Write the failing tests** (`test_power_reading.py`, без інтеграції — `hass` фікстура, `freezer`)

```python
async def test_on_at_min_power_off_below_hysteresis(hass, freezer) -> None:
    # min 100 W, hysteresis 20 %, delay 10 s: 100 W -> on after 10 s; 85 W keeps on; 79 W -> off after 10 s
    assert reading.is_on is True  # after 100 W + 10 s
    assert reading.is_on is True  # 85 W + 10 s
    assert reading.is_on is False  # 79 W + 10 s
    assert changes == 2

async def test_a_change_shorter_than_the_delay_is_not_shown(hass, freezer) -> None:
    # on; 0 W for 5 s, back to 100 W: still on, no on_change
    assert reading.is_on is True and changes == 0

async def test_no_data_after_the_tolerance_back_at_once(hass, freezer) -> None:
    # unavailable for 30 s: available; for 61 s: not available; a number again: available at once
    assert reading.available is False  # after 61 s
    assert reading.available is True   # right after "100"

async def test_last_changed_is_when_the_shown_state_changed(hass, freezer) -> None:
    assert reading.last_changed == t0 + timedelta(seconds=10)
```

- [ ] **Step 2: Run them to verify they fail**
Run: `pytest tests/custom_components/bestiarium_climate/test_power_reading.py -v`
Expected: FAIL — `ModuleNotFoundError: ...power_reading`

- [ ] **Step 3: Implement `PowerReading` and switch `VirtualSwitch` to it**
`VirtualSwitch.__init__` builds `PowerReading(hass, entity_id-or-title, subentry.data[CONF_POWER_ENTITY], PowerThresholds.of(subentry.data), self.async_write_ha_state)`; `async_added_to_hass` calls `start()` and `async_on_remove(stop)`; `is_on`/`available` delegate. The hysteresis compares with the off threshold only while the shown state is on and available — the current rule.

- [ ] **Step 4: Run the new and the switch tests**
Run: `pytest tests/custom_components/bestiarium_climate/test_power_reading.py tests/custom_components/bestiarium_climate/test_switch.py -v`
Expected: PASS (усі наявні тести вимикача без змін)

- [ ] **Step 5: Commit**
```bash
git add custom_components/bestiarium_climate/power_reading.py custom_components/bestiarium_climate/switch.py tests/custom_components/bestiarium_climate/test_power_reading.py
git commit -m "refactor(climate): the reading by power out of the virtual switch"
```

---

### Task 2: тип підзапису й форми універсального термостата

**Files:**
- Modify: `custom_components/bestiarium_climate/const.py`, `config_flow.py:317-325, 477-748`, `switch.py:99-111` (`thermostats_of`), `balancer.py:86-95` (`shared_switches`), `repairs.py:223-271`, `translations/en.json`, `translations/uk.json`
- Test: `tests/custom_components/bestiarium_climate/test_config_flow.py`

**Interfaces:**
- Produces (const): `SUBENTRY_TYPE_UNIVERSAL = "universal_thermostat"`; `THERMOSTAT_TYPES = (SUBENTRY_TYPE_THERMOSTAT, SUBENTRY_TYPE_UNIVERSAL)`; `DEFAULT_UNIVERSAL_MIN_POWER = 20.0`.
- Produces (data of a universal subentry): `name`, `temperature_sensor`, `target_switch`, `thermostat_mode` (`heating`|`cooling`), `cold_tolerance`, `hot_tolerance`, `min_cycle_duration?`, `min_temp?`, `max_temp?`, `heater_power?`, `power_entity?`, `min_power`, `power_hysteresis_percent`, `power_delay_seconds`, `power_tolerance_seconds`, `<preset>_temp?`.
- Produces: `class UniversalThermostatSubentryFlowHandler(ThermostatSubentryFlowHandler)` — кроки `basic` (як у старого; селектор `target_switch` з `exclude_entities` = віртуальні вимикачі Climate, той самий список, що в `socket_schema`; помилки `switch_taken`, `switch_is_device` через `switch_error`) → `power` (`power_entity` необов'язковий, `sensor` з `device_class: power`, suggested value; `min_power` 0–20000 Вт, типово 20; гістерезис 0–90 %, затримка 0–600 с, допуск 0–600 с — межі й типові як у віртуального вимикача) → `presets` (успадкований). Очищене поле `power_entity` зникає з даних (як `heater_power`).
- `thermostats_of`, `shared_switches` рахують обидва `THERMOSTAT_TYPES`; `ThermostatSwitchRepairFlow` для універсального виключає віртуальні вимикачі.

- [ ] **Step 1: Write the failing tests** (`test_config_flow.py`)

```python
async def test_universal_thermostat_three_steps(hass, climate_entry) -> None:
    # basic -> power -> presets -> created
    assert result["step_id"] == "power"
    assert subentry.subentry_type == "universal_thermostat"
    assert subentry.data["power_entity"] == "sensor.heater_power"
    assert subentry.data["min_power"] == 20.0
    assert subentry.data["power_delay_seconds"] == 10

async def test_universal_power_sensor_is_optional(hass, climate_entry) -> None:
    assert "power_entity" not in subentry.data

async def test_universal_offers_no_virtual_switch(hass, climate_entry_with_switch) -> None:
    assert "switch.virtual_switch" in selector_config["exclude_entities"]

async def test_universal_refuses_a_taken_switch_and_a_device_relay(hass, climate_entry_with_thermostat) -> None:
    assert result["errors"] == {"target_switch": "switch_taken"}
    assert result["errors"] == {"target_switch": "switch_is_device"}

async def test_old_thermostat_refuses_the_universals_switch(hass, ...) -> None:
    assert result["errors"] == {"target_switch": "switch_taken"}

async def test_universal_reconfigure_clears_the_power_sensor(hass, ...) -> None:
    assert "power_entity" not in subentry.data

async def test_universal_translations_have_every_field() -> None:
    # en and uk: config_subentries.universal_thermostat.step.{basic,power,presets}.data has every schema key
```

- [ ] **Step 2: Run them to verify they fail**
Run: `pytest tests/custom_components/bestiarium_climate/test_config_flow.py -k universal -v`
Expected: FAIL — unknown subentry type `universal_thermostat`

- [ ] **Step 3: Implement the type, the flow and the translations**
`ThermostatSubentryFlowHandler` gets one hook, `_switch_selector() -> selector.EntitySelector` (the old one returns the plain `switch` selector), and `async_step_basic` goes on to `self._next_step()` (old: `presets`, universal: `power`); the universal overrides both. Translations: `config_subentries.universal_thermostat` (`entry_type` «Universal thermostat» / «Універсальний термостат», `initiate_flow`, three steps with `data` and `data_description`; `thermostat_mode` described as «what auto and manual do»).

- [ ] **Step 4: Run the flow tests**
Run: `pytest tests/custom_components/bestiarium_climate/test_config_flow.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git commit -am "feat(climate): the universal thermostat's subentry and forms"
```

---

### Task 3: сутність універсального термостата в «авто» — балансування разом зі старими (сценарій 9)

**Files:**
- Modify: `climate.py:56-375`, `balancer.py:167-183` (`async_load`), `devices.py:60-150`, `control.py:185-300`, `balancer.py:676-698`, `stepwise_actions.py:83-95`
- Create test: `tests/custom_components/bestiarium_climate/test_universal.py` (reuses `Floors` from `test_balancer.py`: `setup(..., universal: frozenset[str] = frozenset(), power: dict[str, str] | None = None)` makes those floors `universal_thermostat` subentries, `power` gives their `power_entity`). In the test sketches of Tasks 3–6 `floors.hvac(...)` / `floors.temperatures(...)` stand for the existing `_hvac(floors, …)` / `_temperatures(floors, …)`; `floors.reading(floor)` (the control's `Reading`) and `floors.attributes(floor)` (the control state's attributes) are new helpers of `Floors`; `manual_events` — the payloads of `bestiarium_climate_manual_turned_off` recorded by the test.

**Interfaces:**
- Consumes: `SUBENTRY_TYPE_UNIVERSAL`, `THERMOSTAT_TYPES` (Task 2).
- Produces:
  - `VirtualThermostat.manual -> bool` (property, always `False`); `UniversalThermostat(VirtualThermostat)`: `hvac_modes = [AUTO, HEAT|COOL, OFF]`, `manual` = `hvac_mode` is the manual mode, device `model="Universal Thermostat"`, restore accepts `auto`; `hvac_action` — as the old one (demand by `thermostat_mode`) in `auto` and in manual.
  - `VirtualThermostat.turn_off_by_zone() -> None` (`@callback`): `hvac_mode = OFF`, update `hvac_action`, write state; no balance request (the balancer calls it inside a cycle). Used by Task 6.
  - `ThermostatControl.power_entity: str | None` — the universal's `power_entity`, else the virtual switch's (`devices.power_entity`); `ThermostatControl.measured_power() -> float | None`.
  - `devices.power_supply(hass, switch, power_entity: str | None) -> PowerSupply`; `power_device(hass, power_entity)` by the sensor (the virtual-switch lookup moves to the caller). Socket only for a virtual switch.
  - The balancer's `_measured_power` and `StepwiseActions.take_step` read `control.measured_power()`.

- [ ] **Step 1: Write the failing tests** (`test_universal.py`)

```python
async def test_universal_auto_heats_and_stops(floors) -> None:
    await floors.setup(universal=frozenset({"floor_1"}))
    await floors.hvac("floor_1", "auto")
    assert "floor_1" in floors.on()               # 18 °C, target 21
    await floors.temperatures(floor_1="22")
    assert "floor_1" not in floors.on()

async def test_universal_modes(floors) -> None:
    assert state.attributes["hvac_modes"] == ["auto", "heat", "off"]

async def test_old_and_universal_share_the_capacity(floors) -> None:
    # capacity 2: floor_1 universal auto, floor_2, floor_3 old heat, all cold
    assert len(floors.on()) == 2
    # after the minimum working time the waiting one takes a place
    assert rotated_in == {"floor_1"} or "floor_1" in floors.on()

async def test_universal_stepwise_off_has_no_socket_step(floors) -> None:
    # floor_1 stuck on, wanted off: 3 "off"s, then switch_out_of_control with power from its sensor; no power_cut event
    assert floors.events.count("bestiarium_climate_switch_out_of_control") == 1
    assert "bestiarium_climate_power_cut" not in floors.events

async def test_universal_power_device_counts_for_the_fit(floors) -> None:
    # a Bestiarium device whose power sensor is the universal's: its expected power, its relay off -> "No power"
    assert floors.state("floor_1") == "no_power"

async def test_universal_restores_auto(hass, floors) -> None:
    assert hass.states.get(floors.climate("floor_1")).state == "auto"  # after a reload
```

- [ ] **Step 2: Run them to verify they fail**
Run: `pytest tests/custom_components/bestiarium_climate/test_universal.py -v`
Expected: FAIL — no climate entity for the universal subentry

- [ ] **Step 3: Implement**
`climate.async_setup_entry` picks the class by `subentry.subentry_type`; `async_load` makes controls for `THERMOSTAT_TYPES`; `ThermostatControl` reads `CONF_POWER_ENTITY` from a universal subentry; `follow_power` passes `self.power_entity`.

- [ ] **Step 4: Run the climate tests**
Run: `pytest tests/custom_components/bestiarium_climate -v`
Expected: PASS (наявні — без змін)

- [ ] **Step 5: Commit**
```bash
git commit -am "feat(climate): the universal thermostat balanced with the old ones"
```

---

### Task 4: стан вимикача універсального за датчиком споживання

**Files:**
- Modify: `control.py:299-383` (`reading`, `_turned_off_at`), `control.py:693-722` (`_on_started`, `_on_state`), `balancer.py:247-271` (`_follow_power`)
- Test: `tests/custom_components/bestiarium_climate/test_universal.py`

**Interfaces:**
- Consumes: `PowerReading`, `PowerThresholds.of(data, min_power=DEFAULT_UNIVERSAL_MIN_POWER)` (Task 1).
- Produces:
  - `ThermostatControl(..., request_balance: Callable[[], None])` — new keyword; the balancer passes `self.async_request_balance`.
  - `ThermostatControl._switch_state() -> State | None` — the one source for `reading`, `_turned_off_at` and the "gone" condition: without `PowerReading` — `hass.states.get(self.switch)`; with it — `None` while the switch entity is gone or the reading not available, else `State(self.switch, STATE_ON|STATE_OFF, last_changed=reading.last_changed)`.
  - The reading's `on_change` runs the switch branch of `_on_state` (gone / "off by another" → `off_at`) and then `request_balance()`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_reading_follows_power_not_the_switch(floors) -> None:
    # switch.floor_1 shows "on" (optimistic) but its power is 5 W: the thermostat sees off
    assert floors.reading("floor_1") is Reading.OFF

async def test_power_dip_shorter_than_the_delay_changes_nothing(floors) -> None:
    # running, power 0 W for 5 s: no command, arrangement unchanged
    assert floors.calls == calls_before

async def test_no_power_data_is_switch_unavailable(floors) -> None:
    # power sensor unavailable 2 min: control state switch_unavailable; the place goes to another
    assert floors.state("floor_1") == "switch_unavailable"

async def test_off_by_another_read_from_power(floors) -> None:
    # running, power drops to 0 for longer than the delay + tolerance: turned off by another, no failed attempt, pause
    assert floors.attributes("floor_1")["failed_attempts"] == 0
```

- [ ] **Step 2: Run them to verify they fail**
Run: `pytest tests/custom_components/bestiarium_climate/test_universal.py -k "power or reading" -v`
Expected: FAIL — the reading follows `switch.floor_1`

- [ ] **Step 3: Implement `_switch_state` and the reading's callback**
The control starts the reading in `async_start`, stops it in `async_stop`; `_follow_power` keeps following the switch entities (their "gone" matters too).

- [ ] **Step 4: Run the climate tests**
Run: `pytest tests/custom_components/bestiarium_climate -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git commit -am "feat(climate): the universal thermostat reads its switch by power"
```

---

### Task 5: ручний режим (FR-028)

**Files:**
- Modify: `control.py` (`set_manual`, `manual`, `next_command`, `send`, `defer_if_exhausted`), `balancer.py:511-567` (`_allocate`), `balancer.py:395-440` (`_cycle`, `_send`)
- Create test: `tests/custom_components/bestiarium_climate/test_manual.py`

**Interfaces:**
- Consumes: `VirtualThermostat.manual` (Task 3).
- Produces:
  - `ThermostatControl.set_manual(demand: bool | None, now: datetime) -> None` — `None`: not manual. Entering manual: `expected_on = False` (no pause set: the switch is the person's now), the stepwise "off" ends (`StepwiseOff()` anew), the last manual command forgotten. Leaving: the last manual command forgotten.
  - `ThermostatControl.manual -> bool`.
  - In manual `next_command` returns the demand only when it differs from the last manual command sent, or, with none sent yet, from what the switch reads; never while a command of that direction is in flight; no stepwise "off", no deferral (`defer_if_exhausted` returns at once).
  - The balancer each cycle calls `control.set_manual(thermostat.hvac_action in (HEATING, COOLING) if thermostat.manual else None, now)` before the rest of `_allocate`; manual controls are out of `working` and out of the `set_expected(False)` loops; `_send(..., counted=not control.manual)`.

- [ ] **Step 1: Write the failing tests** (`test_manual.py`, `Floors` with `universal`, zones on)

```python
async def test_manual_heats_by_temperature_alone(floors) -> None:
    await floors.hvac("floor_1", "heat")
    assert "floor_1" in floors.on()
    await floors.temperatures(floor_1="22")
    assert "floor_1" not in floors.on()

async def test_manual_holds_no_place(floors) -> None:
    # capacity 1: floor_2 old heats, floor_1 manual heats too
    assert floors.on() >= {"floor_1", "floor_2"}

async def test_manual_is_left_alone_in_yellow_pause_and_no_data(floors) -> None:
    for zone in ("yellow", "pause", "no_data"):
        floors.zone(zone)
        assert "floor_1" in floors.on()

async def test_manual_command_goes_once(floors) -> None:
    # floor_1 manual on; a person turns switch.floor_1 off: no "on" again for 10 minutes
    assert floors.calls.count(("turn_on", "floor_1")) == 1

async def test_manual_never_unresponsive(floors) -> None:
    # switch stuck: an hour later control state normal, no deferral
    assert floors.state("floor_1") == "normal"

async def test_manual_to_auto_keeps_a_running_switch(floors) -> None:
    # manual running, capacity has a place: set auto -> no command, still on
    assert floors.calls == calls_before and "floor_1" in floors.on()

async def test_manual_to_auto_without_a_place_turns_off_stepwise(floors) -> None:
    assert ("turn_off", "floor_1") in floors.calls

async def test_old_thermostat_has_no_manual(floors) -> None:
    assert "auto" not in hass.states.get(floors.climate("floor_2")).attributes["hvac_modes"]
```

- [ ] **Step 2: Run them to verify they fail**
Run: `pytest tests/custom_components/bestiarium_climate/test_manual.py -v`
Expected: FAIL

- [ ] **Step 3: Implement the manual branch**

- [ ] **Step 4: Run the climate tests**
Run: `pytest tests/custom_components/bestiarium_climate -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git commit -am "feat(climate): the manual mode of the universal thermostat (FR-028)"
```

---

### Task 6: «спершу ручні» в червоній і при перевантаженні (FR-029, крок 1)

**Files:**
- Modify: `zone_rules.py` (`arrange_zone`, `Arrangement`), `const.py`, `balancer.py` (`_allocate`/`_arrange` повертають розкладку; `_cycle`)
- Test: `tests/custom_components/bestiarium_climate/test_zone_rules.py`, `test_manual.py`

**Interfaces:**
- Consumes: `ThermostatControl.manual`, `set_manual` (Task 5), `VirtualThermostat.turn_off_by_zone` (Task 3).
- Produces:
  - `arrange_zone(candidates: list[Candidate], state: ZoneInput, manual: frozenset[str] = frozenset()) -> Arrangement`; `Arrangement.manual_off: frozenset[str]` = `manual` in `SHEDDING_ZONES`, else empty; manual keys never in `heaters` or `freed`.
  - const: `EVENT_MANUAL_TURNED_OFF = "bestiarium_climate_manual_turned_off"`; `class ManualCause(StrEnum): ZONE_ENTERED = "zone_entered"; TURNED_ON = "turned_on"`; `class ManualTurnedOffData(TypedDict): thermostat: str; switch_entity_id: str; zone: str; cause: str`.
  - In `_cycle`, before `shed()` of the entry: for each key of `manual_off` — `thermostat.turn_off_by_zone()`, `control.set_manual(None, now)`, one `info` line, the event (`cause` = `zone_entered` when `self._last_zone not in SHEDDING_ZONES`, else `turned_on`), and its command sent at once (`next_command` → the first "off" of the stepwise "off"; a switch that reads off gets the entry's "off" outside the attempts, as now).

- [ ] **Step 1: Write the failing tests**

```python
# test_zone_rules.py
def test_red_and_overload_turn_off_every_manual() -> None:
    for zone in (Zone.RED, Zone.OVERLOAD):
        assert arrange_zone([_heater("a", 0, running=True)], _state(zone), manual=frozenset({"m"})).manual_off == {"m"}

def test_other_zones_leave_manual_alone() -> None:
    for zone in (Zone.GREEN, Zone.YELLOW, Zone.PAUSE, Zone.NO_DATA, Zone.NONE):
        assert arrange_zone([], _state(zone), manual=frozenset({"m"})).manual_off == frozenset()

# test_manual.py
async def test_red_turns_manual_off_first(floors) -> None:
    # floor_1 manual on, floor_2, floor_3 old on; red
    assert floors.calls[first_red_call] == ("turn_off", "floor_1")
    assert hass.states.get(floors.climate("floor_1")).state == "off"
    assert manual_events == [{"zone": "red", "cause": "zone_entered", ...}]

async def test_overload_turns_manual_off_with_all(floors) -> None:
    assert manual_events[-1]["zone"] == "overload"

async def test_manual_stays_off_after_red(floors) -> None:
    floors.zone("green")
    assert hass.states.get(floors.climate("floor_1")).state == "off"

async def test_manual_turned_on_in_red_goes_off_at_once(floors) -> None:
    await floors.hvac("floor_1", "heat")   # in red
    assert ("turn_on", "floor_1") not in floors.calls
    assert manual_events[-1]["cause"] == "turned_on"

async def test_manual_in_the_pause_between_red_and_overload(floors) -> None:
    # red -> pause: set heat (on goes) -> overload: off, cause turned_on; entry blind "off"s not repeated
    assert manual_events[-1]["cause"] == "turned_on"

async def test_manual_restored_in_red_is_turned_off(hass, floors) -> None:
    # manual heat, red, reload the entry: off, cause zone_entered
    assert manual_events[-1]["cause"] == "zone_entered"

async def test_manual_shed_switch_still_on_is_turned_off_stepwise(floors) -> None:
    # floor_1 stuck: off at once, again a cycle apart in red; "Does not turn off" after the tolerance
    assert floors.state("floor_1") == "not_turning_off"

async def test_manual_event_payload_is_typed() -> None:
    assert set(ManualTurnedOffData.__annotations__) == {"thermostat", "switch_entity_id", "zone", "cause"}
```

- [ ] **Step 2: Run them to verify they fail**
Run: `pytest tests/custom_components/bestiarium_climate/test_zone_rules.py tests/custom_components/bestiarium_climate/test_manual.py -v`
Expected: FAIL — `arrange_zone() got an unexpected keyword argument 'manual'`

- [ ] **Step 3: Implement the rule and its execution**

- [ ] **Step 4: Run the climate tests**
Run: `pytest tests/custom_components/bestiarium_climate -v`
Expected: PASS

- [ ] **Step 5: Docs** — `docs/bestiarium_climate.md`: розділ «Universal thermostat» (налаштування, режими, стан за споживанням, ручний режим, «спершу ручні», без кроку «розетка», необхідність кожного нового поля й події за DEC-015), схема даних підзапису, тести; `docs/events.md`: `bestiarium_climate_manual_turned_off` (поля, коли, приклад автоматизації сповіщення).

- [ ] **Step 6: Commit**
```bash
git commit -am "feat(climate): manual thermostats go off first in red and overload (FR-029)"
```

---

### Task 7: підроблені пристрої стенда

**Files:**
- Modify: `custom_components/bestiarium_mock/const.py:108-170`, `custom_components/bestiarium_mock/translations/en.json`, `uk.json`, `docs/bestiarium_mock.md`
- Test: `tests/custom_components/bestiarium_mock/test_entities.py`

**Interfaces:**
- Produces: `MockDeviceSpec("heater", power_w=1500)` («Mock Heater» / «Обігрівач»); `ac_4` — четвертий у генераторі `ac_{number}` (`range(1, 5)`, `has_ir=True`); `MockThermometerSpec("thermo_5")`. Entity ids за правилом стенда: `switch.mock_heater_switch`, `sensor.mock_heater_power`, `sensor.mock_ac_4_power`, `sensor.mock_thermo_5_temperature`.

- [ ] **Step 1: Write the failing test**
```python
async def test_the_stage_2_devices_exist(hass, stand) -> None:
    for entity_id in ("switch.mock_heater_switch", "sensor.mock_heater_power", "sensor.mock_ac_4_power", "sensor.mock_thermo_5_temperature"):
        assert hass.states.get(entity_id) is not None
```
- [ ] **Step 2: Run it to verify it fails** — `pytest tests/custom_components/bestiarium_mock -v` → FAIL
- [ ] **Step 3: Add the specs and the names** (uk/en)
- [ ] **Step 4: Run the mock tests** — `pytest tests/custom_components/bestiarium_mock -v` → PASS (лічильники `device_count` беруть `len(MOCK_DEVICES)`)
- [ ] **Step 5: Commit** — `git commit -am "feat(mock): a heater, a fourth air conditioner and a fifth thermometer"`

---

### Task 8: стенд — універсальні термостати й сценарії 1, 2, 9, 10

**Files:**
- Modify: `scripts/stand/layout.py`, `entries.py:188-281`, `scenarios.py`, `dashboard.py`, `docs/bestiarium_mock.md` (розділ «The stand»)
- Test: `tests/stand/test_stand_setup.py`

**Interfaces:**
- Produces (layout): `UNIVERSAL_THERMOSTATS = (("Stand heater", "thermo_4", "switch.mock_heater_switch", "heater"), ("Stand cloud AC", "thermo_5", "switch.stand_cloud_ac", "ac_4"))` (name, thermometer, switch, power socket); `CLOUD_AC = "ac_4"`; `StandDevice("ac_4", LOAD_PRIORITY_ALWAYS_ON, first_queue=True)` у `STAND_DEVICES`.
- `entries.recreate_climate` додає два підзаписи `universal_thermostat` (heating, `min_power` 20, мінімальний цикл 1 хв, `heater_power` 1500 для обігрівача) у той самий запис стенда; шаблонний вимикач `switch.stand_cloud_ac` створюється (або оновлюється) config flow хелпера «Шаблон» (`template` → `switch`, `turn_on`: `script.bestiarium_stand_ir_ac_4_on`, `turn_off`: `script.bestiarium_stand_ir_ac_4_off`, без шаблону стану) до запису Climate; ІЧ-скрипти `ac_4` — тим самим генератором, що `ac_1..3`. Перед кодом — перевірити на HA середовища, що форма шаблонного вимикача приймає дії (крок 1).
- Скрипти: `reset` вимикає й універсальні; нові `universal_auto`, `universal_manual`, `manual_red`, `manual_on_in_red`, `climate_coexistence`, `cloud_ac_universal`.

| Скрипт | Що робить → що видно |
|---|---|
| `universal_auto` (1) | reset; кімната обігрівача 18 °C, ціль 21, «авто» → обігрівач вмикається (1500 Вт); за 2 хв кімната 22 °C → вимикається |
| `universal_manual` (1) | reset; ємність 0; «гріти» → обігрівач вмикається попри ємність 0; плита штовхає в жовту → лишається; кімната 22 °C → вимикається сам; «вимкнено» → вимкнено |
| `manual_red` (2) | reset; «гріти», обігрівач працює; плита штовхає в червону → термостат обігрівача «вимкнено» одразу, подія `zone_entered`, обігрівач вимкнено першим; плита назад — лишається «вимкнено» |
| `manual_on_in_red` (2) | reset; плита в червоній; «гріти» → одразу «вимкнено», подія `turned_on`, обігрівач не вмикається |
| `climate_coexistence` (9) | reset; навантаження стенда не дозволені; AC 1, AC 2 (старі) і обігрівач («авто») холодні, ємність 2 → працюють двоє; за хвилину ротація пускає того, хто чекав |
| `cloud_ac_universal` (10) | reset; хмарний кондиціонер «авто», кімната 18 °C → шаблонний вимикач → ІЧ-скрипт `ac_4` → 1200 Вт, стан «працює» за споживанням; кімната 25 °C → вимикається (5 Вт); далі «ІЧ втрачено» під час роботи → «вимкнути» тричі, подія «поза контролем», розетку не вимикає |

- Дашборд: розділ «Нові термостати» — плитки двох універсальних (режим, ціль, температура), стан керування, споживання; журнал включає їх.

- [ ] **Step 1: Check the template switch form** on the development HA (`make develop`): helper «Шаблон» → «Вимикач» має поля дій увімкнення/вимкнення. Якщо ні — зупинитися й запитати менеджера (план інакше спирається на YAML-пакет).
- [ ] **Step 2: Write the failing tests** (`test_stand_setup.py`)
```python
def test_stand_climate_has_the_universal_thermostats() -> None:
    assert {s["subentry_type"] for s in climate_subentries} >= {"thermostat", "universal_thermostat"}

def test_reset_turns_the_universal_thermostats_off() -> None: ...

def test_stage_2_scenarios_are_written() -> None:
    assert {"universal_auto", "universal_manual", "manual_red", "manual_on_in_red", "climate_coexistence", "cloud_ac_universal"} <= set(scripts)

async def test_cloud_ac_switch_runs_the_ir_scripts(...) -> None:
    assert template_options["turn_on"] == [{"action": "script.bestiarium_stand_ir_ac_4_on"}]

async def test_stand_setup_is_repeatable(...) -> None:  # existing, now with the template switch: one, not two
```
- [ ] **Step 3: Run them to verify they fail** — `pytest tests/stand -v` → FAIL
- [ ] **Step 4: Implement** layout, entries, scenarios, dashboard
- [ ] **Step 5: Run** `pytest tests/stand -v` → PASS
- [ ] **Step 6: Run the stand once** on the development HA (`make stand`), press each new button, check the result in the table above; any mismatch is a bug of Tasks 3–6, not of the script.
- [ ] **Step 7: Commit** — `git commit -am "feat(stand): universal thermostats and scenarios 1, 2, 9, 10"`

---

### Task 9: завершення

- [ ] **Step 1:** `make lint-check` → без помилок; `make test` → усі пройшли (записати кількість).
- [ ] **Step 2:** чекліст `bestiarium-concepts` по diff (події лише на переходах, uk/en, наявні події без змін, нові параметри — у формах з типовими, немає широкого `except`).
- [ ] **Step 3:** `/anti-slop:deslop` на diff гілки; після нього знову `make lint-check` і `make test`; коміт `refactor(climate): deslop …`, якщо щось змінилося.
- [ ] **Step 4:** пуш гілки `dev-011-universal-thermostat`, PR-чернетка в `master` (не мерджити).
- [ ] **Step 5:** звіт `D:\bestiarium-smart-home\handoff\DEV-011-0N-report.md` за шаблоном системного бріфу, з кроками ручного тесту — кнопки з таблиці Task 8.

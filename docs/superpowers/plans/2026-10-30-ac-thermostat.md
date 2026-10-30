# Термостат кондиціонера — план реалізації (FEAT-066, етап 4, DEV-013)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. (Для DEV-013 менеджер призначив `superpowers:executing-plans`.)

**Goal:** спершу борг DR-9f67ab61 — `SwitchSource` (вимикач / вимикач за споживанням / кондиціонер), обраний при створенні термостата; потім новий тип підзапису Bestiarium Climate — термостат кондиціонера над будь-якою сутністю `climate` (режими `auto`, `off`, `heat`, `cool`, `dry`, `fan_only`, швидкість вентилятора, сутність «Режим авто», запас уставки, простій на уставці, ручні режими FR-028, «повз термостат» §10) і сценарії стенда 3, 4, 7, 8.

**Architecture:** термостат кондиціонера — ще один підклас `VirtualThermostat` і ще один `ThermostatControl` у тому самому `BalancingCoordinator`: одна ємність, ті самі правила зони, той самий шлях команд (`_send`), те саме ступінчасте вимкнення. Різниця між типами — лише в джерелі (`SwitchSource`): що «увімкнено» (стан вимикача або споживання), чим це підтверджується і що означає команда «увімкнути» / «вимкнути» (`switch.turn_on` або `climate.set_temperature` з режимом і уставкою). Термостат дає контролю бажане налаштування кондиціонера (`AcSetting`), балансувальник лишається про «увімкнено / вимкнено».

**Tech Stack:** Python 3.14, Home Assistant 2026.10 (config subentries, `ClimateEntity`, `SelectEntity`, `RestoreEntity`), pytest + `pytest_homeassistant_custom_component`, freezer; стенд — `scripts/stand/*` (REST/websocket HA), `bestiarium_mock`, `bestiarium_ir`.

**Spec:** `docs/superpowers/specs/2026-10-30-new-thermostats-design.md` (§3, §4 «Шар 3» і «Спільне», §6 сценарії 3, 4, 7, 8, §7 етап 4, §10). Бріф: `D:\bestiarium-smart-home\handoff\DEV-013-01-brief.md`.

## Global Constraints

- Гілка `dev-013-ac-thermostat` від `master` (`59a9d70`, злитий DEV-012).
- HA 2026.10.x, Python 3.14; HA у середовищі не оновлювати.
- Старий і універсальний термостати — **без змін поведінки, без міграції**: `VERSION`/`MINOR_VERSION` запису і `STORAGE_VERSION`/`STORAGE_MINOR_VERSION` сховища не змінюються; новий тип — лише нові підзаписи; нові ключі пам'яті — необов'язкові, з типовими значеннями. Наявні тести Climate — сторожі, змінюються лише там, де змінився внутрішній інтерфейс (не поведінка).
- Bestiarium Climate не знає мови Hisense: лише служби й атрибути `climate` HA (анти-ціль §2). Жодного імпорту з `bestiarium_ir` у `bestiarium_climate`.
- Концепції `docs/concepts/`: жодного `notify` / `persistent_notification`; журнал — лише зміни стану; кожен текст для людини — uk і en; наявні події `bestiarium_*` не змінюють імен і полів; параметри алгоритму — у налаштуваннях підзапису з типовими; нових подій немає.
- Код, коментарі, коміти — англійською; коміти `feat|fix|refactor|test|docs|chore(<integration>): …`.
- `make lint-check` і `make test` зелені після кожної задачі.
- Робочий HA не чіпати; Docker на змонтованому репо — з UID 1000.

## Review Focus

1. **Простій нижче порогу коротший за крок.** Кондиціонер в «авто» на уставці на 4 хв падає до 10 Вт (поріг 20 Вт, крок 5 хв): лишається «працює», місце тримає, без команд і без спроби; 6 хв — «вимкнено іншим» (пауза, без невдалої спроби). Тест — Task 4 (`test_idle_dip_shorter_than_a_step_holds_the_place`, `test_no_power_for_a_whole_step_is_off_by_another`).
2. **Перезапуск HA, кондиціонер працює в «авто» з потрібним налаштуванням.** Жодного кадру після перезапуску (вимкнений на ніч екран не засвічується). Тест — Task 3 (`test_restart_with_the_ac_running_sends_nothing`).
3. **Уставка поза діапазоном кондиціонера або дробова.** Ціль 29.5 + запас 1 → 30 (max кондиціонера); крок кондиціонера 1 → 22.4 надсилається як 22; налаштування, яке кондиціонер не приймає, надсилається один раз на зміну, а не щохвилини. Тест — Task 3 (`test_setpoint_clamped_and_rounded_to_the_ac`, `test_a_setting_the_ac_does_not_take_goes_once`).
4. **Кондиціонер недоступний (зник передавач).** Після допуску — «Вимикач недоступний», місце йде іншому, команд немає; повернувся — назад до норми після гістерезису. Тест — Task 3 (`test_ac_unavailable_is_switch_unavailable`).
5. **«Ручний» → «авто», кондиціонер працює.** Місце є — береться як «працює» і отримує уставку з запасом одним кадром; місця немає — ступінчасте вимкнення. Тест — Task 5 (`test_manual_to_auto_keeps_the_running_ac_with_the_margin`, `test_manual_to_auto_without_a_place_turns_off_stepwise`).

---

## Технічні рішення (для рев'ю менеджера)

- **`SwitchSource` (борг DR-9f67ab61).** Новий модуль `switch_source.py`. Джерело читає `SwitchState(on, changed_at)` або `None` (зникло) і надсилає «увімкнути/вимкнути». Три класи, вибір — фабрикою `switch_source(...)` при створенні контролю:
  - `EntitySwitch` — стан сутності `switch` (старий термостат; універсальний без датчика): зникло — немає / `unavailable` / `unknown`;
  - `PoweredSwitch` — вимикач, прочитаний `PowerReading` (універсальний з датчиком): зникло — вимикача немає / він `unavailable` / даних споживання немає; `unknown` оптимістичного вимикача не є «зникло» (як зараз);
  - `AirConditioner` — сутність `climate`, прочитана `PowerReading`: зникло — `climate` немає / `unavailable` / даних споживання немає.
  
  Джерело саме стежить за своєю сутністю і каже контролю про зміну (`on_change(was_on)`); контроль шле `SIGNAL_SWITCH_CHANGED` для **всіх** джерел, тож балансувальник більше не стежить за сутностями вимикачів сам (`_follow_power` — лише реле й розетки). Поведінка: те саме «вирішити заново одразу», один шлях замість двох.
- **Тип підзапису** `ac_thermostat`. Кондиціонер — новий ключ `air_conditioner` (не `target_switch`: це не вимикач). «Один кондиціонер — один термостат» і «одна сутність — один термостат» рахуються разом: `thermostats_of`, `shared_switches`, перевірка форми — за `controlled_entity(data)` (`target_switch` або `air_conditioner`). Події з полем `switch_entity_id` для термостата кондиціонера несуть сутність `climate` — поля й імена подій не змінюються.
- **Налаштування підзапису:** крок `basic` — ім'я, датчик температури, кондиціонер (`climate`, крім термостатів самого Climate), **запас уставки** (`setpoint_margin`, типово 1.0 °C, 0–10, крок 0.5), допуски холоду/тепла, мінімальний цикл, мін/макс, потужність для «вміщається» (`heater_power`); крок `power` — датчик споживання (**обов'язковий**: підтвердження за споживанням), поріг (типово **20 Вт**, нижче простою ~40 Вт), гістерезис, затримка, допуск (як в універсального), **розетка** (`power_socket`, необов'язково; будь-який вимикач, крім віртуальних вимикачів Climate — як у віртуального вимикача); крок `presets` — успадкований. Немає `thermostat_mode`: що робить «авто», вирішує сутність «Режим авто».
- **«Режим авто»** — сутність `select` на пристрої термостата (`heating` / `cooling`, «Гріти» / «Охолоджувати», типово `heating`), категорії немає (нею керує автоматизація власника за сезоном, FEAT-057). Значення живе в `ThermostatMemory` (`auto_mode`), як `saved_temperature` термостата; зміна — сигнал `SIGNAL_AUTO_MODE_CHANGED`, термостат перераховує потребу й просить цикл. Новий ключ пам'яті необов'язковий — без міграції сховища.
- **Режими сутності:** фіксовані `auto`, `off`, `heat`, `cool`, `dry`, `fan_only` (бріф). **Швидкості вентилятора** — `fan_modes` кондиціонера, прочитані з його стану при кожному записі (кондиціонер ще не завантажений — функції вентилятора немає); власна швидкість термостата відновлюється після перезапуску, типово — поточна швидкість кондиціонера.
- **Бажане налаштування** `AcSetting(hvac_mode, temperature, fan_mode)` дає термостат (`ac_setting`):
  - `auto`: «Режим авто» `heating` → `heat`, ціль + запас; `cooling` → `cool`, ціль − запас;
  - `heat` / `cool` / `dry`: той самий режим, ціль людини без запасу; `fan_only`: без уставки;
  - `off`: `None`.
  
  Уставка обмежується `min_temp`/`max_temp` кондиціонера і округлюється до його `target_temp_step` (інакше HA відмовить або кондиціонер покаже інше число й порівняння ніколи не зійдеться). `fan_mode` — лише якщо кондиціонер має швидкості.
- **Команди кондиціонеру:** «увімкнути» — `climate.set_temperature` з `hvac_mode` і `temperature` (у `fan_only` — `climate.set_hvac_mode`), потім `climate.set_fan_mode`, лише якщо швидкість кондиціонера інша; «вимкнути» — `climate.set_hvac_mode: off`. Шар 2 надсилає кадр навіть на той самий режим — так «вимкнути» йде знову в ступінчастому вимкненні.
- **«Авто»** — балансування, як у старого термостата: ті самі спроби, «Не відгукується», пауза, ротація, «вміщається» (потужність — пристрій Bestiarium Devices за датчиком споживання, інакше `heater_power`, інакше 2000 Вт). Старт підтверджується споживанням (≥ поріг).
- **Простій на уставці.** На 40 Вт при порозі 20 Вт кондиціонер просто «працює» (місце в ємності, «звільняє» для «вміщається» лише виміряне). Падіння нижче порогу в «авто», коли його хочуть увімкненим, — «вимкнено іншим» лише після **цілого кроку** (`off_interval_seconds` запису, типово 5 хв; у старого й універсального — допуск, як і було): джерело `AirConditioner` має `idles = True`. Ступінчасте вимкнення закінчується лише після цілого кроку без споживання — це вже так (DEV-010 R1).
- **«Авто» відновлює потрібне.** Поки кондиціонер «працює» і хочуть увімкненим, а його налаштування (як показує HA: режим, уставка, швидкість) не відповідає бажаному, контроль надсилає «увімкнути» ще раз — **один раз на кожну зміну пари (бажане, показане)**, а не щоцикла. Так покриваються: людина змінила ціль чи швидкість на термостаті, «Режим авто» змінився, хтось змінив кондиціонер з дашборда / голосом / автоматизацією. Після перезапуску з кондиціонером у потрібному налаштуванні — нічого не йде. Фізичний пульт HA не бачить (припущений стан): вимкнув пультом — споживання падає → «вимкнено іншим» за крок → пауза → новий старт; змінив режим пультом, не вимкнувши, — не видно, доки термостат сам не надішле кадр (дизайн §6, сценарій 8: прийнятно).
- **`off` термостата, кондиціонер працює** (хтось увімкнув) — «працює всупереч» → наявне ступінчасте вимкнення: «вимкнути» × спроби, подія «поза контролем», розетка (якщо налаштована), подія «живлення вимкнено». Так само в «авто», коли не хочуть.
- **Ручні режими** (`heat`, `cool`, `dry`, `fan_only`) — FR-028 через наявну гілку: поза ємністю до червоної; команди не рахуються (`counted=False`); «увімкнути» з налаштуванням людини йде, коли змінюється потреба (вхід у ручний з вимкненого) або бажане налаштування (людина змінила режим, ціль, швидкість) — **один раз**; на вході в ручний кондиціонер, що вже працює в цьому налаштуванні, нічого не отримує. Зміни кондиціонера повз термостат у ручному — не чіпаються. Мінімальний цикл — лише для переходу «вимкнено → увімкнено» (захист компресора), зміна налаштування працюючого — одразу. Червона й перевантаження — наявні `manual_off` / `_turn_off_manual` (режим `off`, подія, ступінчасте вимкнення).
- **`hvac_action`:** `auto` — потреба за «Режимом авто» (`heating`/`cooling`/`idle`), як у старого; ручні — за режимом (`heating`, `cooling`, `drying`, `fan`); `off` — `off`.
- **Стенд:** другий ІЧ-кондиціонер `hisense_2` (своє живлення `hisense_2_power`, передавач, кімната `thermo_6`) і другий Hisense «Stand Hisense 2» — під термостатом; перший «Stand Hisense» лишається сценаріям 5–6 (інакше термостат у `off` вимикав би його як «працює всупереч»). Мок-кондиціонер з кімнатою гріє/охолоджує її (0.1 °C за 10 с ⇒ 0.6 °C/хв) і переходить у простій (40 Вт), коли його датчик (кімната + «зсув датчика») досяг уставки. Нові ручки мока: «Зсув датчика» (`number`, −5…5 °C, типово 0, `config`) — щоб показати простій в «авто»; «Пульт: вимкнути» (`button`, `config`) — зміна без кадру (сценарій 8). Порядок `stand_setup`: Bestiarium IR раніше за Climate (термостату потрібен кондиціонер).

### Необхідність нових елементів (DEC-015)

| Елемент | Випадок |
|---|---|
| Тип підзапису `ac_thermostat` | необхідний: балансування кондиціонера над `climate` (FEAT-066) |
| Поля `air_conditioner`, `setpoint_margin`, `power_socket` | необхідні: що керувати, уставка «гріти» (замість `input_number` пакетів, §9), крок «розетка» FR-027 |
| `select` «Режим авто» | єдиний інтерфейс: що робить «авто» перемикає автоматизація за сезоном; у `climate` HA немає режиму «авто, але лише гріти» |
| Швидкість вентилятора термостата | єдиний інтерфейс у «авто»: кадри термостата несуть швидкість; без неї кадр «авто» скидав би швидкість, вибрану на кондиціонері (дизайн §2) |
| Нових подій, атрибутів, служб | немає |
| Мок: `hisense_2`, `hisense_2_power`, `thermo_6`, «Зсув датчика», «Пульт: вимкнути» | ручки стенда (`config`), сценарії 3, 8 |

## Файли

| Файл | Відповідальність |
|---|---|
| `custom_components/bestiarium_climate/switch_source.py` (новий) | `SwitchState`, `SwitchSource`, `EntitySwitch`, `PoweredSwitch`, `AirConditioner`, `AcSetting`, `controlled_entity`, фабрика `switch_source` |
| `control.py` | читає джерело замість `_switch_state()`; `setting`; відновлення налаштування в «авто»; ручний з налаштуванням; підтвердження «вимкнено» цілим кроком для `idles` |
| `balancer.py` | `_async_send` через джерело; `_follow_power` без вимикачів; `control.setting`; `manual_demand` |
| `climate.py` | `VirtualThermostat.manual_demand`, `.ac_setting`; `AirConditionerThermostat` |
| `select.py` (новий), `__init__.py` | «Режим авто»; `Platform.SELECT` |
| `const.py` | `SUBENTRY_TYPE_AC`, `THERMOSTAT_TYPES`, `CONF_AIR_CONDITIONER`, `CONF_SETPOINT_MARGIN`, `DEFAULT_SETPOINT_MARGIN`, `SIGNAL_AUTO_MODE_CHANGED` |
| `config_flow.py` | `AirConditionerThermostatSubentryFlowHandler`; хуки `_device_schema` / `_device_errors` у `basic` |
| `devices.py`, `stepwise_actions.py`, `follow.py`, `switch.py`, `repairs.py` | розетка з підзапису термостата; `air_conditioner` у `FOLLOWED`; `thermostats_of` за `controlled_entity`; виправлення спільної сутності для `climate` |
| `translations/en.json`, `uk.json` | підзапис `ac_thermostat`, «Режим авто», помилка `ac_taken` |
| `custom_components/bestiarium_mock/const.py`, `ir_ac.py`, `coordinator.py`, `number.py`, `button.py` (новий), `__init__.py`, `translations/*` | `hisense_2`, кімната, простій, «Зсув датчика», «Пульт: вимкнути» |
| `scripts/stand/layout.py`, `entries.py`, `scenarios.py`, `dashboard.py`, `scripts/stand_setup.py` | Hisense 2, термостат кондиціонера, сценарії 3, 4, 7, 8, порядок |
| `docs/bestiarium_climate.md`, `docs/events.md`, `docs/bestiarium_mock.md`, `docs/bestiarium_ir.md` | новий тип, `SwitchSource`, стенд |
| `tests/custom_components/bestiarium_climate/test_switch_source.py`, `test_ac_thermostat.py`, `test_ac_manual.py` (нові); `test_config_flow.py`; `tests/custom_components/bestiarium_mock/test_ir_ac.py`, `test_entities.py`; `tests/stand/test_stand_setup.py` | тести |

---

### Task 1: `SwitchSource` — джерело стану й команд, обране при створенні (борг DR-9f67ab61)

Поведінка старого й універсального термостатів не змінюється; `test_balancer.py`, `test_universal.py`, `test_manual.py`, `test_stepwise_off.py`, `test_climate.py` — сторожі.

**Files:**
- Create: `custom_components/bestiarium_climate/switch_source.py`
- Modify: `control.py:188-316` (`__init__`, `async_start`, `_switch_state` прибрати), `control.py:353-434` (`reading`, `_turned_off_at`), `control.py:518-532` (`_manual_command`), `control.py:639-681` (`_observe`), `control.py:808-858` (`_on_started`, `_on_state`, `_on_power_reading`, `_switch_changed`); `balancer.py:180-186` (`async_load`), `balancer.py:257-281` (`_follow_power`), `balancer.py:808-823` (`_async_send`)
- Test: `tests/custom_components/bestiarium_climate/test_switch_source.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class SwitchState: on: bool; changed_at: datetime`.
  - `class SwitchSource` (база): `entity_id: str`; `idles: ClassVar[bool] = False`; `start(on_change: Callable[[bool], None]) -> None` (`on_change(was_on)` — лише коли змінилося прочитане: зникло, `on`, `changed_at`); `stop() -> None`; `state() -> SwitchState | None`; `async async_turn(on: bool) -> None`; `setting() -> AcSetting | None` — `None` (у вимикача немає налаштування).
  - `EntitySwitch(hass, switch)`, `PoweredSwitch(hass, switch, reading: PowerReading)` — `async_turn` → `switch.turn_on|turn_off` (`blocking=True`). Помилки HA не ловить (ловить балансувальник, як зараз).
  - `switch_source(hass, subentry, name, *, setting: Callable[[], AcSetting | None]) -> SwitchSource` — за типом і даними підзапису (`AirConditioner` з'являється в Task 3; тут два класи).
  - `ThermostatControl.source: SwitchSource` (створює в `__init__` фабрикою); `_switch_state()` зникає: всі читачі — `self.source.state()`, `state.on`, `state.changed_at`.
  - Контроль на `on_change(was_on)` робить те, що робили обидві гілки `_on_state` / `_on_power_reading`: `_switch_changed`, `_refresh`, `SIGNAL_SWITCH_CHANGED`.
  - `BalancingCoordinator._async_send(control, *, on)` → `await control.source.async_turn(on)`; рядок debug при `HomeAssistantError` — як зараз (`control.source.entity_id`).
  - `_follow_power` стежить лише за `c.power.relays`.

- [ ] **Step 1: Write the failing tests** (`test_switch_source.py`, `hass` + `freezer`, без запису Climate)

```python
async def test_entity_switch_reads_the_switch(hass) -> None:
    hass.states.async_set("switch.a", "on")
    assert source.state() == SwitchState(on=True, changed_at=hass.states.get("switch.a").last_changed)
    for gone in (None, "unavailable", "unknown"):
        assert source.state() is None

async def test_powered_switch_reads_the_power(hass, freezer) -> None:
    # switch "on" (optimistic), power 5 W: off; 100 W for 10 s: on, changed_at = then
    assert source.state().on is False
    assert source.state() == SwitchState(on=True, changed_at=t0 + timedelta(seconds=10))

async def test_powered_switch_unknown_switch_is_not_gone(hass) -> None:
    hass.states.async_set("switch.a", "unknown")
    assert source.state() is not None

async def test_powered_switch_gone_without_power_data_or_switch(hass, freezer) -> None:
    assert source.state() is None  # power unavailable 61 s
    assert source.state() is None  # switch unavailable

async def test_on_change_tells_was_on_once_per_change(hass, freezer) -> None:
    assert changes == [False, True]  # off -> on -> off, attribute-only changes tell nothing

async def test_turn_calls_the_switch_service(hass) -> None:
    assert calls == [("switch", "turn_on", "switch.a"), ("switch", "turn_off", "switch.a")]

def test_factory_picks_the_source() -> None:
    assert isinstance(switch_source(hass, old_thermostat, ...), EntitySwitch)
    assert isinstance(switch_source(hass, universal_with_power, ...), PoweredSwitch)
    assert isinstance(switch_source(hass, universal_without_power, ...), EntitySwitch)
```

- [ ] **Step 2: Run them to verify they fail**
Run: `pytest tests/custom_components/bestiarium_climate/test_switch_source.py -v`
Expected: FAIL — `ModuleNotFoundError: ...switch_source`

- [ ] **Step 3: Implement the sources and move the control and the balancer onto them**

- [ ] **Step 4: Run the whole Climate suite**
Run: `pytest tests/custom_components/bestiarium_climate -v`
Expected: PASS — усі наявні тести без змін поведінки (змінюються лише тести, що зверталися до `_switch_state` / `_power_reading`, якщо такі є)

- [ ] **Step 5: Commit**
```bash
git add custom_components/bestiarium_climate/switch_source.py custom_components/bestiarium_climate/control.py custom_components/bestiarium_climate/balancer.py tests/custom_components/bestiarium_climate/test_switch_source.py
git commit -m "refactor(climate): the switch source chosen when the thermostat is made (DR-9f67ab61)"
```

---

### Task 2: тип підзапису й форми термостата кондиціонера

**Files:**
- Modify: `const.py`, `switch_source.py` (`controlled_entity`), `config_flow.py:489-831`, `switch.py:84-96` (`thermostats_of`), `balancer.py:89-98` (`shared_switches`), `follow.py:35-40`, `repairs.py:166-223` (`SharedSwitchRepairFlow`), `translations/en.json`, `translations/uk.json`
- Test: `tests/custom_components/bestiarium_climate/test_config_flow.py`

**Interfaces:**
- Produces (const): `SUBENTRY_TYPE_AC = "ac_thermostat"`; `THERMOSTAT_TYPES = (SUBENTRY_TYPE_THERMOSTAT, SUBENTRY_TYPE_UNIVERSAL, SUBENTRY_TYPE_AC)`; `CONF_AIR_CONDITIONER = "air_conditioner"`; `CONF_SETPOINT_MARGIN = "setpoint_margin"`; `DEFAULT_SETPOINT_MARGIN = 1.0`.
- Produces: `controlled_entity(data: Mapping[str, Any]) -> str` — `air_conditioner` або `target_switch`; `thermostats_of`, `shared_switches` і перевірка форми рахують за ним (тож вимикач і кондиціонер не можуть мати двох термостатів будь-якого типу).
- Produces (data of an AC subentry): `name`, `temperature_sensor`, `air_conditioner`, `setpoint_margin`, `cold_tolerance`, `hot_tolerance`, `min_cycle_duration?`, `min_temp?`, `max_temp?`, `heater_power?`, `power_entity`, `min_power`, `power_hysteresis_percent`, `power_delay_seconds`, `power_tolerance_seconds`, `power_socket?`, `<preset>_temp?`.
- Produces: `ThermostatSubentryFlowHandler` отримує два хуки замість `_switch_selector` / `_switch_errors` у `basic`: `_device_schema(defaults: Mapping[str, Any]) -> dict[vol.Marker, Any]` (старий / універсальний: `target_switch` + `thermostat_mode`, як зараз) і `_device_errors(user_input) -> dict[str, str]`. `AirConditionerThermostatSubentryFlowHandler(UniversalThermostatSubentryFlowHandler)`: `basic` — `air_conditioner` (`EntitySelector(domain="climate", exclude_entities=` термостати Climate`)`; помилка `ac_taken`, якщо інший термостат будь-якого запису вже керує ним) і `setpoint_margin` (`NumberSelector` 0–10, крок 0.5, °C, типово 1.0); `power` — `power_entity` **обов'язковий**, решта як в універсального, плюс `socket_schema(...)` (очищена розетка зникає з даних). `SharedSwitchRepairFlow` для `climate` дає селектор `climate` і пише `air_conditioner`.
- `FOLLOWED` + `CONF_AIR_CONDITIONER`.

- [ ] **Step 1: Write the failing tests** (`test_config_flow.py`)

```python
async def test_ac_thermostat_three_steps(hass, climate_entry) -> None:
    # basic -> power -> presets -> created
    assert subentry.subentry_type == "ac_thermostat"
    assert subentry.data["air_conditioner"] == "climate.hisense"
    assert subentry.data["setpoint_margin"] == 1.0
    assert subentry.data["min_power"] == 20.0
    assert "thermostat_mode" not in subentry.data

async def test_ac_thermostat_needs_a_power_sensor(hass, climate_entry) -> None:
    assert result["errors"] == {"power_entity": "required"}  # or a vol error on submit without it

async def test_ac_thermostat_socket_is_optional_and_cleared(hass, ...) -> None:
    assert subentry.data["power_socket"] == "switch.ac_socket"
    assert "power_socket" not in subentry.data  # after a reconfigure that cleared it

async def test_ac_thermostat_refuses_a_taken_air_conditioner(hass, climate_entry_with_ac_thermostat) -> None:
    assert result["errors"] == {"air_conditioner": "ac_taken"}

async def test_ac_selector_offers_no_climate_thermostat(hass, climate_entry_with_thermostat) -> None:
    assert "climate.floor_1" in selector_config["exclude_entities"]

async def test_air_conditioner_rename_is_followed(hass, ...) -> None:
    assert subentry.data["air_conditioner"] == "climate.hisense_new"

async def test_ac_translations_have_every_field() -> None:
    # en and uk: config_subentries.ac_thermostat.step.{basic,power,presets}.data has every schema key; error ac_taken
```

- [ ] **Step 2: Run them to verify they fail**
Run: `pytest tests/custom_components/bestiarium_climate/test_config_flow.py -k ac_ -v`
Expected: FAIL — unknown subentry type `ac_thermostat`

- [ ] **Step 3: Implement the type, the hooks, the flow and the translations**
Translations: `config_subentries.ac_thermostat` (`entry_type` «Air conditioner thermostat» / «Термостат кондиціонера», `initiate_flow`, three steps with `data` and `data_description`; `setpoint_margin` described as «added to the target when heating, taken off when cooling, in auto only»). The subentry has no climate entity until Task 3: `async_load` already makes controls for `THERMOSTAT_TYPES`, so Task 2 leaves `SUBENTRY_TYPE_AC` out of the controls (`if subentry_type in THERMOSTAT_TYPES and subentry_type != SUBENTRY_TYPE_AC`) and Task 3 removes that exclusion.

- [ ] **Step 4: Run the flow tests**
Run: `pytest tests/custom_components/bestiarium_climate/test_config_flow.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git commit -am "feat(climate): the air conditioner thermostat's subentry and forms"
```

---

### Task 3: термостат кондиціонера в «авто» — джерело `AirConditioner`, «Режим авто», запас, відновлення

**Files:**
- Modify: `switch_source.py` (`AcSetting`, `AirConditioner`, фабрика), `control.py` (`setting`, гілка відновлення в `next_command`), `climate.py` (`manual_demand`, `ac_setting`, `AirConditionerThermostat`, вибір класу), `balancer.py:586-599` (`control.setting`, `manual_demand`), `devices.py:150-165` (`power_supply(..., socket=)`), `stepwise_actions.py:113-139` (`report_gone_sockets` для розетки термостата), `__init__.py:34-39`, `const.py`, `translations/*`
- Create: `custom_components/bestiarium_climate/select.py`
- Create test: `tests/custom_components/bestiarium_climate/test_ac_thermostat.py` — гарнітура `Units`: записи Climate з одним-двома `ac_thermostat` (і, за потреби, старим термостатом на `switch.floor_*`), підроблені `climate.ac_N` (стан + атрибути `hvac_modes`, `fan_modes`, `fan_mode`, `temperature`, `min_temp` 16, `max_temp` 30, `target_temp_step` 1), датчики `sensor.ac_N_power`; перехоплення `ServiceRegistry.async_call` для `climate.*` на `climate.ac_N`: записує `(service, entity, data)` і змінює стан, як Hisense; споживання за станом (1200 Вт гріє/охолоджує, 40 Вт `idle(unit)`, 5 Вт вимкнено), ручки `stuck(unit)` (ігнорує «вимкнути»), `deaf(unit)` (нічого не змінює). `units.frames(unit)` — записані виклики, `units.tick(seconds)` — як у `Floors`.

**Interfaces:**
- Consumes: `SwitchSource`, `switch_source`, `ThermostatControl.source` (Task 1); `SUBENTRY_TYPE_AC`, `CONF_*` (Task 2).
- Produces:
  - `@dataclass(frozen=True) class AcSetting: hvac_mode: HVACMode; temperature: float | None = None; fan_mode: str | None = None` з `matches(shown: AcSetting | None) -> bool` (поля `None` у бажаному не порівнюються).
  - `AirConditioner(hass, climate, reading: PowerReading, wanted: Callable[[], AcSetting | None])`: `idles = True`; `state()` — за споживанням, зникло — `climate` немає / `unavailable` / даних споживання немає; `setting()` — налаштування кондиціонера, як його показує HA (`state`, атрибути `temperature`, `fan_mode`); `None`, поки зникло; `async_turn(True)` — служби з «Технічних рішень» з `wanted()` (уставка обмежена й округлена за атрибутами кондиціонера); `async_turn(False)` — `set_hvac_mode: off`. Стежить за `climate`: `on_change` на зміну доступності, режиму, уставки, швидкості.
  - `ThermostatControl.setting: AcSetting | None` (балансувальник ставить щоцикла з `thermostat.ac_setting`). У гілці «авто» `next_command`: читається `ON`, хочуть увімкненим, жодна команда не біжить і не у вікні, `not self.setting.matches(self.source.setting())` і пара `(self.setting, self.source.setting())` інша, ніж при останньому відновленні → `True` (рахується, як будь-яке «увімкнути»; працюючий кондиціонер одразу закриває серію). Для вимикачів обидва `None` — гілка ніколи не спрацьовує.
  - Підтвердження «вимкнено іншим» (`reading`, `off_confirmed_at`): для `source.idles` — `off_interval_seconds` запису, інакше допуск, як було.
  - `VirtualThermostat.manual_demand -> bool | None` — старий `None`, універсальний `wants_work if manual else None` (переноситься з балансувальника); `VirtualThermostat.ac_setting -> AcSetting | None` — `None`.
  - `AirConditionerThermostat(VirtualThermostat)`: `_model = "Air Conditioner Thermostat"`; `hvac_modes` — шість; `manual` — `heat|cool|dry|fan_only`; `ac_setting` — за «Технічними рішеннями»; `_ac_mode` — з пам'яті `auto_mode` (типово `heating`) і на `SIGNAL_AUTO_MODE_CHANGED.format(subentry_id)`; `fan_modes` / `fan_mode` / `async_set_fan_mode` (функція `FAN_MODE`, лише коли кондиціонер має `fan_modes`; стан кондиціонера підписаний — запис стану на його зміну); відновлення: режим, ціль, пресет, `fan_mode`; `hvac_action` — за «Технічними рішеннями».
  - `select.py`: `AutoModeSelect(SelectEntity)` для кожного контролю `ac_thermostat`: `translation_key="auto_mode"`, опції `heating`, `cooling`, пристрій термостата, `unique_id=f"{subentry_id}_auto_mode"`; значення — `ThermostatMemory` `auto_mode`; вибір → `memory.update`, сигнал.
  - `power_supply(hass, switch, power, socket=None)`: розетка — `socket` (термостат кондиціонера), інакше віртуального вимикача. `ThermostatControl.socket_subentry_id` — свій підзапис для термостата кондиціонера, інакше підзапис віртуального вимикача (для `report_gone_sockets`; `SocketRepairFlow` уже працює за ідентифікаторами підзапису).
  - const: `SIGNAL_AUTO_MODE_CHANGED = f"{DOMAIN}_auto_mode_changed_{{}}"`.

- [ ] **Step 1: Write the failing tests** (`test_ac_thermostat.py`)

```python
async def test_auto_heats_with_the_margin_and_stops(units) -> None:
    # room 18, target 21, margin 1, auto: one set_temperature(heat, 22); 1200 W -> control normal
    assert units.frames("ac_1") == [("set_temperature", {"hvac_mode": "heat", "temperature": 22})]
    await units.temperatures(ac_1="21")
    assert units.frames("ac_1")[-1] == ("set_hvac_mode", {"hvac_mode": "off"})

async def test_auto_mode_cooling_cools_with_the_margin(hass, units) -> None:
    await units.select_auto_mode("ac_1", "cooling")   # room 26, target 23
    assert units.frames("ac_1")[-1] == ("set_temperature", {"hvac_mode": "cool", "temperature": 22})

async def test_auto_mode_is_kept_across_a_restart(hass, units) -> None:
    assert hass.states.get(units.auto_mode("ac_1")).state == "cooling"

async def test_modes_and_fan_modes(hass, units) -> None:
    assert attrs["hvac_modes"] == ["auto", "off", "heat", "cool", "dry", "fan_only"]
    assert attrs["fan_modes"] == ["auto", "low", "medium", "high"]   # the AC's

async def test_fan_mode_goes_with_on(units) -> None:
    # thermostat fan "high", AC "auto": set_temperature then set_fan_mode(high)
    assert units.frames("ac_1")[-1] == ("set_fan_mode", {"fan_mode": "high"})

async def test_start_not_confirmed_by_power_is_an_attempt(units) -> None:
    # deaf: after the attempts "Not responding" (as the old thermostat)
    assert units.state("ac_1") == "unresponsive"

async def test_setpoint_clamped_and_rounded_to_the_ac(units) -> None:
    # target 29.5, margin 1 -> 30 (the AC's max); target 21.4, margin 1, step 1 -> 22
    assert units.frames("ac_1")[-1][1]["temperature"] == 30
    assert units.frames("ac_1")[-1][1]["temperature"] == 22

async def test_a_setting_the_ac_does_not_take_goes_once(units) -> None:
    # the AC keeps showing fan "auto" whatever is sent: one repair, none in the next 10 minutes
    assert units.frames("ac_1").count(("set_fan_mode", {"fan_mode": "high"})) == 2  # the start's and one repair

async def test_target_change_while_running_goes_at_once(units) -> None:
    await units.target("ac_1", 23)
    assert units.frames("ac_1")[-1] == ("set_temperature", {"hvac_mode": "heat", "temperature": 24})

async def test_a_change_past_the_thermostat_is_restored_in_auto(hass, units) -> None:
    # running heat 22; the AC set to cool 20 from a dashboard: heat 22 again within a cycle
    assert units.frames("ac_1")[-1] == ("set_temperature", {"hvac_mode": "heat", "temperature": 22})

async def test_restart_with_the_ac_running_sends_nothing(hass, units) -> None:
    # reload the entry; the AC shows heat 22 and draws 1200 W
    assert units.frames("ac_1") == []

async def test_ac_unavailable_is_switch_unavailable(hass, units) -> None:
    assert units.state("ac_1") == "switch_unavailable"   # after the tolerance; no frame meanwhile

async def test_ac_and_old_thermostats_share_the_capacity(units) -> None:
    # capacity 1: ac_1 (auto) and floor_1 (old heat) cold: one runs; the rotation lets the other in
    assert len(units.running()) == 1

async def test_auto_mode_change_while_running_turns_the_ac_off(units) -> None:
    # heating at room 18; auto mode -> cooling: no demand -> off; no "out of control"
    assert units.frames("ac_1")[-1] == ("set_hvac_mode", {"hvac_mode": "off"})
```

- [ ] **Step 2: Run them to verify they fail**
Run: `pytest tests/custom_components/bestiarium_climate/test_ac_thermostat.py -v`
Expected: FAIL — no climate entity for the `ac_thermostat` subentry

- [ ] **Step 3: Implement**
Translations: `entity.select.auto_mode` («Auto mode» / «Режим авто», states `heating` «Heat» / «Гріти», `cooling` «Cool» / «Охолоджувати»). The temperature is rounded to the AC's step the way HA rounds a target (`round(value / step) * step`), then clamped.

- [ ] **Step 4: Run the Climate suite**
Run: `pytest tests/custom_components/bestiarium_climate -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git commit -am "feat(climate): the air conditioner thermostat in auto"
```

---

### Task 4: простій на уставці і ступінчасте вимкнення кондиціонера

Код — лише якщо тести Task 3 цього не покрили (підтвердження цілим кроком є з Task 3, ступінчасте вимкнення — наявне); задача — довести поведінку §10 тестами й виправити, що не так.

**Files:**
- Test: `tests/custom_components/bestiarium_climate/test_ac_thermostat.py`
- Modify (лише якщо тест упав): `control.py` (`reading`, `_observe`), `stepwise_actions.py`

**Interfaces:**
- Consumes: `AirConditioner.idles`, `power_supply(..., socket=)`, `socket_subentry_id` (Task 3).

- [ ] **Step 1: Write the tests**

```python
async def test_idle_at_40_w_holds_the_place(units) -> None:
    # running, idle(ac_1): 40 W for 30 min: still running, control normal, no frame, failed_attempts 0
    assert units.frames("ac_1") == frames_before and units.state("ac_1") == "normal"

async def test_idle_dip_shorter_than_a_step_holds_the_place(units) -> None:
    # 10 W (below 20) for 4 min, step 5 min: no frame, no pause, still expected on
    assert units.attributes("ac_1")["pause_until"] is None

async def test_no_power_for_a_whole_step_is_off_by_another(units) -> None:
    # 5 W for 6 min: off by another, pause from then, no failed attempt; a new start after the pause
    assert units.attributes("ac_1")["failed_attempts"] == 0

async def test_idle_does_not_end_the_stepwise_off(units) -> None:
    # stuck(ac_1) idling at 40 W, wanted off: 3 "off"s a step apart, then out_of_control
    assert units.events.count("bestiarium_climate_switch_out_of_control") == 1

async def test_stepwise_off_cuts_the_socket(units) -> None:
    # with power_socket: after the event, switch.ac_1_socket turned off, power_cut event with that socket
    assert ("turn_off", "switch.ac_1_socket") in units.socket_calls

async def test_off_thermostat_turns_a_running_ac_off_stepwise(units) -> None:
    # thermostat off; the AC turned on from a dashboard: set_hvac_mode(off) at once
    assert units.frames("ac_1")[-1] == ("set_hvac_mode", {"hvac_mode": "off"})

async def test_a_gone_socket_is_a_repairs_issue_of_the_ac_thermostat(hass, units) -> None:
    assert issue.data["subentry_id"] == "ac_1"
```

- [ ] **Step 2: Run them**
Run: `pytest tests/custom_components/bestiarium_climate/test_ac_thermostat.py -k "idle or step or socket or off_" -v`
Expected: PASS; a FAIL is a defect of Task 3 or of the stepwise "off" — fix there with `superpowers:systematic-debugging`

- [ ] **Step 3: Commit**
```bash
git commit -am "test(climate): an air conditioner idling at its setpoint and turned off stepwise"
```

---

### Task 5: ручні режими кондиціонера (FR-028) і «спершу ручні» в червоній

**Files:**
- Modify: `control.py` (`set_manual`, `_manual_command`, `send`), `climate.py` (`AirConditionerThermostat.manual_demand`)
- Create test: `tests/custom_components/bestiarium_climate/test_ac_manual.py` (гарнітура `Units` з Task 3, зони увімкнено)

**Interfaces:**
- Consumes: `ThermostatControl.setting`, `AirConditioner.setting()` (Task 3); `manual_off`, `_turn_off_manual` (DEV-011).
- Produces:
  - `AirConditionerThermostat.manual_demand` — `True` у ручному, інакше `None`.
  - `ThermostatControl`: на вході в ручний — `_manual_sent = running`, `_manual_setting = self.source.setting()` (те, що кондиціонер показує); `_manual_command` повертає `True`, коли `demand is not self._manual_sent` (з урахуванням мінімального циклу, як зараз) **або** `not self.setting.matches(self._manual_setting)` при вже надісланому «увімкнути» (одразу, без мінімального циклу); `send(True)` у ручному запам'ятовує `_manual_setting = self.setting`. Для вимикачів `setting` — `None`, і `matches` не викликається: поведінка універсального не змінюється.

- [ ] **Step 1: Write the failing tests** (`test_ac_manual.py`)

```python
async def test_each_manual_mode_goes_once_with_the_persons_target(units) -> None:
    for mode, data in (("heat", {"hvac_mode": "heat", "temperature": 21}),
                       ("cool", {"hvac_mode": "cool", "temperature": 21}),
                       ("dry", {"hvac_mode": "dry", "temperature": 21})):
        await units.hvac("ac_1", mode)
        assert units.frames("ac_1")[-1] == ("set_temperature", data)
    await units.hvac("ac_1", "fan_only")
    assert units.frames("ac_1")[-1] == ("set_hvac_mode", {"hvac_mode": "fan_only"})
    await units.tick(10 * MINUTE)
    assert len(units.frames("ac_1")) == frames_after_fan_only  # no repeats

async def test_manual_holds_no_place_and_has_no_attempts(units) -> None:
    # capacity 0; manual heat: frame goes; deaf AC: an hour later control normal
    assert units.state("ac_1") == "normal"

async def test_manual_leaves_a_change_past_the_thermostat_alone(units) -> None:
    # manual heat; the AC set off from a dashboard: nothing for 10 minutes
    assert units.frames("ac_1") == frames_before

async def test_manual_setting_change_goes_at_once(units) -> None:
    await units.target("ac_1", 24)
    assert units.frames("ac_1")[-1] == ("set_temperature", {"hvac_mode": "heat", "temperature": 24})

async def test_manual_entered_with_the_ac_already_so_sends_nothing(units) -> None:
    # the AC shows heat 21 and runs; thermostat manual heat target 21
    assert units.frames("ac_1") == []

async def test_manual_to_auto_keeps_the_running_ac_with_the_margin(units) -> None:
    # place free: no start, one set_temperature(heat, target + margin)
    assert units.frames("ac_1")[-1] == ("set_temperature", {"hvac_mode": "heat", "temperature": 22})

async def test_manual_to_auto_without_a_place_turns_off_stepwise(units) -> None:
    assert units.frames("ac_1")[-1] == ("set_hvac_mode", {"hvac_mode": "off"})

async def test_red_turns_the_manual_ac_off_first(hass, units) -> None:
    # manual cool, ac_2 auto heating; red: ac_1 off first, mode off, manual_turned_off zone_entered
    assert manual_events == [{"zone": "red", "cause": "zone_entered", "switch_entity_id": "climate.ac_1", ...}]

async def test_manual_ac_turned_on_in_red_goes_off_at_once(units) -> None:
    assert manual_events[-1]["cause"] == "turned_on" and no set_temperature to ac_1
```

- [ ] **Step 2: Run them to verify they fail**
Run: `pytest tests/custom_components/bestiarium_climate/test_ac_manual.py -v`
Expected: FAIL

- [ ] **Step 3: Implement the manual setting**

- [ ] **Step 4: Run the Climate suite**
Run: `pytest tests/custom_components/bestiarium_climate -v`
Expected: PASS

- [ ] **Step 5: Docs** — `docs/bestiarium_climate.md`: розділ «Air conditioner thermostat (DEV-013)» (налаштування, «Режим авто», режими, бажане налаштування й запас, простій і цілий крок, відновлення в «авто» й межі пульта, ручні, «працює всупереч», розетка, таблиця необхідності), схема даних підзапису `ac_thermostat`, `SwitchSource` у розділі контролю, тести; `docs/events.md`: `switch_entity_id` термостата кондиціонера — його `climate`.

- [ ] **Step 6: Commit**
```bash
git commit -am "feat(climate): the manual modes of the air conditioner thermostat (FR-028)"
```

---

### Task 6: другий ІЧ-кондиціонер стенда з кімнатою, простоєм і пультом

**Files:**
- Modify: `custom_components/bestiarium_mock/const.py:148-200`, `ir_ac.py`, `coordinator.py`, `number.py`, `__init__.py` (`Platform.BUTTON`), `translations/en.json`, `uk.json`, `docs/bestiarium_mock.md`
- Create: `custom_components/bestiarium_mock/button.py`
- Test: `tests/custom_components/bestiarium_mock/test_ir_ac.py`, `test_entities.py`

**Interfaces:**
- Produces (const): `MockDeviceSpec("hisense_2_power", power_w=5)`; `MockThermometerSpec("thermo_6")`; `MockIrAcSpec(device_id, socket, room: str | None = None)` і `MOCK_IR_ACS = (MockIrAcSpec("hisense", "hisense_power"), MockIrAcSpec("hisense_2", "hisense_2_power", room="thermo_6"))`; `IR_AC_POWER_W["idle"] = 40`; `IR_AC_ROOM_STEP_C = 0.1`, `IR_AC_ROOM_INTERVAL_SECONDS = 10`.
- Produces: `MockIrAc.sensor_offset: float` (ручка); `MockIrAc.follow_room(temperature: float) -> float` — у `heat`/`cool` при ввімкненому: датчик кондиціонера (`temperature + sensor_offset`) не досяг уставки → повертає кімнату ± крок і споживає за режимом; досяг → 40 Вт (простій), кімната та сама; вимкнено, `dry`, `fan_only`, `auto` → кімната та сама. `MockIrAc.remote_off()` — вимкнути без кадру (останній кадр і результат не змінюються).
- `MockDevicesCoordinator`: таймер `IR_AC_ROOM_INTERVAL_SECONDS` — для кожного кондиціонера з `room`: нова температура в число термометра (і датчик), як ручна зміна.
- Сутності: `number.mock_hisense_2_sensor_offset` (`config`, −5…5, 0.5, типово 0, відновлюється; і для `hisense`), `button.mock_hisense_2_remote_off` (`config`; і для `hisense`), `sensor.mock_thermo_6_temperature`, `sensor.mock_hisense_2_power_power`, `infrared.mock_hisense_2_emitter`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_heat_warms_the_room_then_idles(hass, freezer, mock) -> None:
    # hisense_2 heat 22, thermo_6 at 20: +0.1 °C each 10 s, 1200 W; at 22: 40 W, the room stays
    assert hass.states.get("sensor.mock_hisense_2_power_power").state == "40.0"

async def test_cool_cools_the_room(hass, freezer, mock) -> None: ...

async def test_sensor_offset_idles_sooner(hass, freezer, mock) -> None:
    # offset 2, setpoint 22: idle at room 20
    assert float(hass.states.get("sensor.mock_thermo_6_temperature").state) == 20.0

async def test_remote_off_turns_the_unit_off_without_a_frame(hass, mock) -> None:
    assert power == 5 and last_frame unchanged

async def test_the_room_of_a_unit_without_one_is_untouched(hass, freezer, mock) -> None:
    # hisense (no room) heating: no thermometer changes

async def test_the_stage_4_devices_exist(hass, mock) -> None:
    for entity_id in ("sensor.mock_thermo_6_temperature", "sensor.mock_hisense_2_power_power",
                      "infrared.mock_hisense_2_emitter", "number.mock_hisense_2_sensor_offset",
                      "button.mock_hisense_2_remote_off"):
        assert hass.states.get(entity_id) is not None
```

- [ ] **Step 2: Run them to verify they fail** — `pytest tests/custom_components/bestiarium_mock -v` → FAIL
- [ ] **Step 3: Implement** (uk/en names: «Зсув датчика» / «Sensor offset», «Пульт: вимкнути» / «Remote: off», «Hisense 2», «Термометр 6»)
- [ ] **Step 4: Run** `pytest tests/custom_components/bestiarium_mock -v` → PASS
- [ ] **Step 5: Commit** — `git commit -am "feat(mock): a second IR air conditioner whose room follows it, an idle and a remote"`

---

### Task 7: стенд — термостат кондиціонера і сценарії 3, 4, 7, 8

**Files:**
- Modify: `scripts/stand/layout.py`, `entries.py:214-380`, `scenarios.py`, `dashboard.py`, `scripts/stand_setup.py:62-68`, `docs/bestiarium_mock.md` (розділ стенда), `docs/bestiarium_ir.md` (розділ стенда)
- Test: `tests/stand/test_stand_setup.py`

**Interfaces:**
- Produces (layout): `STAND_HISENSE = (("Stand Hisense", "hisense"), ("Stand Hisense 2", "hisense_2"))`; `AC_THERMOSTAT = ("Stand AC thermostat", "thermo_6", "Stand Hisense 2", "hisense_2_power")` (назва, термометр, кондиціонер, живлення); `StandDevice("hisense_2_power", LOAD_PRIORITY_ALWAYS_ON, first_queue=True)` у `STAND_DEVICES`.
- `stand_setup`: `recreate_ir` перед `recreate_climate` (з оновленим `reg` між ними).
- `entries.recreate_climate` додає підзапис `ac_thermostat`: кондиціонер `reg.ir_entity("Stand Hisense 2", "climate")`, запас 1, допуски 0.3, мінімальний цикл 1 хв, датчик споживання `sensor.mock_hisense_2_power_power`, поріг 20, затримка 10, допуск 60, гістерезис 20, розетка — «Дозволено» пристрою `hisense_2_power`.
- `_hisense_reset` — для обох Hisense; скидання стенда: термостат кондиціонера `off`, «Режим авто» `heating`, ручки `hisense_2` вимкнено, зсув 0, `thermo_6` 20 °C.
- Скрипти:

| Скрипт | Що робить → що видно |
|---|---|
| `ac_auto` (3) | скидання; кімната 18, ціль 21, «авто» → кадр «гріти 22», 1200 Вт, стан керування «Норма»; кімната росте 0.6 °C/хв; на ~20.7 °C → кадр «вимкнути», 5 Вт |
| `ac_idle` (3) | скидання; зсув датчика 2; кімната 18, ціль 21, «авто» → «гріти 22»; на 20 °C кондиціонер у простої (40 Вт), кімната стоїть; 3 хв: термостат «гріє», кадрів більше немає, «Норма»; кімната 21 → «вимкнути» |
| `ac_auto_cool` (4) | скидання; «Режим авто» → «Охолоджувати»; кімната 26, ціль 23, «авто» → кадр «охолоджувати 22», кімната падає; на ~23.3 → «вимкнути» |
| `ac_stepwise` (7) | скидання; «авто», працює; ручка «Ігнорує вимкнути»; кімната 23 → «вимкнути» тричі, хвилина між ними (ігнорує, простій 40 Вт або 1200 Вт), подія «поза контролем», розетка вимкнена → 0 Вт, подія «живлення вимкнено» |
| `ac_off_against` (7) | скидання; термостат `off`; Hisense 2 → «гріти» напряму (як з дашборда) → термостат одразу надсилає «вимкнути», кондиціонер вимкнено |
| `ac_remote` (8) | скидання; «авто», працює; «Пульт: вимкнути» → HA показує «гріти» (припущений), 5 Вт; за хвилину-дві «вимкнено іншим», після паузи новий кадр «гріти 22», 1200 Вт; далі Hisense 2 → «охолоджувати 20» напряму → протягом хвилини знову «гріти 22» |
| `ac_manual_alone` (8) | скидання; ручний «гріти» 21 → кадр «гріти 21»; Hisense 2 → «охолоджувати» напряму → термостат не втручається 2 хв |

- Дашборд: розділ «Термостат кондиціонера» — плитка термостата (режими, ціль, вентилятор), «Режим авто», стан керування, Hisense 2 (припущений стан), останній кадр і стан мок-кондиціонера, споживання, кімната; ручки «Ігнорує вимкнути», «ІЧ втрачено», «Зсув датчика», «Пульт: вимкнути»; кнопки скриптів; журнал включає термостат і Hisense 2.

- [ ] **Step 1: Write the failing tests** (`test_stand_setup.py`)
```python
def test_stand_climate_has_the_ac_thermostat() -> None:
    assert {s["subentry_type"] for s in climate_subentries} >= {"thermostat", "universal_thermostat", "ac_thermostat"}

def test_ir_comes_before_climate() -> None: ...  # stand_setup order

def test_reset_turns_the_ac_thermostat_off_and_its_knobs_back() -> None: ...

def test_stage_4_scenarios_are_written() -> None:
    assert {"ac_auto", "ac_idle", "ac_auto_cool", "ac_stepwise", "ac_off_against", "ac_remote", "ac_manual_alone"} <= set(scripts)

def test_hisense_scenarios_drive_only_the_first_hisense() -> None: ...

async def test_stand_setup_is_repeatable(...) -> None:  # existing: two Hisense, one AC thermostat, not doubled
```
- [ ] **Step 2: Run them to verify they fail** — `pytest tests/stand -v` → FAIL
- [ ] **Step 3: Implement** layout, entries, order, scenarios, dashboard
- [ ] **Step 4: Run** `pytest tests/stand -v` → PASS
- [ ] **Step 5: Run the stand once** on the development HA (`make stand`), press each new button and the old ones of scenarios 5, 6, 9; check the results in the table above; a mismatch is a defect of Tasks 3–6, not of the script.
- [ ] **Step 6: Commit** — `git commit -am "feat(stand): the air conditioner thermostat and scenarios 3, 4, 7, 8"`

---

### Task 8: завершення

- [ ] **Step 1:** `make lint-check` → без помилок; `make test` → усі пройшли (записати кількість).
- [ ] **Step 2:** чекліст `bestiarium-concepts` по diff (нових подій немає; uk/en; наявні події без змін; нові параметри — у формах з типовими; ручки мока — `config`; немає широкого `except`; таблиця необхідності в документації).
- [ ] **Step 3:** `/anti-slop:deslop` на diff гілки; після нього знову `make lint-check` і `make test`; коміт `refactor(climate): deslop …`, якщо щось змінилося.
- [ ] **Step 4:** пуш гілки `dev-013-ac-thermostat`, PR-чернетка в `master` (не мерджити).
- [ ] **Step 5:** звіт `D:\bestiarium-smart-home\handoff\DEV-013-04-report.md` за шаблоном системного бріфу, з кроками ручного тесту — кнопки з таблиці Task 7; «Відхилення від плану»; DR-9f67ab61 закрито.

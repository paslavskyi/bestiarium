# Картка «Схема живлення» — план реалізації (FEAT-045, DEV-014)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. (Для DEV-014 менеджер призначив `superpowers:executing-plans`.)

**Goal:** власна картка Lovelace `custom:bestiarium-power-flow-card` — п'ять вузлів (Мережа, Сонце, Інвертор, Дім, Батарея), п'ять дротів із трьома виглядами (тече / підключено / розімкнено), назва режиму над схемою, дотик → `navigation_path`; картку роздає Bestiarium Mode, тож власник лише додає її на дашборд; на стенді — пристрій «Живлення дому (імітація)» і сценарії для критеріїв 1–7, 10.

**Architecture:** два ES-модулі без збирання. `power-flow-model.js` — чисті функції: стани HA → `Readings` → `Model` (статус і швидкість кожного дроту, рядки кожного вузла, заголовок) і розмітка SVG-скелета; їх тестує `node --test`. `power-flow-card.js` — `HTMLElement` із shadow DOM: будує скелет один раз, а на кожне `set hass` лише перемикає класи, CSS-змінну швидкості й тексти — анімація точок суто CSS (`stroke-dashoffset`), без JS щокадру. Bestiarium Mode в `async_setup` реєструє теку `frontend/` як статичний шлях і додає модуль картки в кожну сторінку HA (`frontend.add_extra_js_url`) з хешем вмісту в URL.

**Tech Stack:** ванільний JS (ES2022, custom elements, shadow DOM, SVG, CSS animations), Node 24 `node:test` (є в devcontainer — feature `node`, і на `ubuntu-latest` у CI); Python 3.14, HA 2026.10 (`homeassistant.components.http.StaticPathConfig`, `homeassistant.components.frontend.add_extra_js_url`), pytest + `pytest_homeassistant_custom_component`; стенд — `bestiarium_mock`, `scripts/stand/*`.

**Spec:** бріф `D:\bestiarium-smart-home\handoff\DEV-014-01-brief.md`; погоджений макет `D:\bestiarium-smart-home\assets\power-flow-mockup\index.html` (функція `render()`, геометрія, CSS); тема `D:\bestiarium-smart-home\assets\theme\bestiarium-oak.yaml`.

## Global Constraints

- Гілка `dev-014-power-flow-card` від актуального `master`.
- Лише публічні API HA. Жодних карток чи бібліотек HACS, жодного card-mod, жодного CDN (планшет — WebAPK, мережа може бути без інтернету): іконки — `ha-icon` самого HA.
- Анімація — лише CSS (`@keyframes` на `stroke-dashoffset`); JS не перемальовує нічого щокадру і не перебудовує DOM на кожне оновлення стану.
- Кольори — лише змінні теми: дуб `--primary-color`, тло картки `--card-background-color`, текст `--primary-text-color`, підписи значень `--secondary-text-color`, блідий вузол `--state-icon-color`, точки сонця `--state-light-active-color`, розімкнений дріт `--slider-track-color`.
- Підписи українською; числа з десятковою комою: `1,2 кВт`, `≈7,5 год`, `91 %`. Недоступне значення — `—`.
- Геометрія з макета: `viewBox="0 0 900 370"`; центри `grid [230,70]`, `sun [670,70]`, `inv [450,190]`, `home [230,310]`, `bat [670,310]`; радіус 34; підписи збоку від кружечків (Мережа й Дім — ліворуч, решта — праворуч), на відстані 48.
- Швидкість точок — формула макета: `max(0.35, 1.6 − |кВт| × 0.35)` с; поріг потоку — `0.05` кВт.
- Інтеграції Bestiarium і їхні налаштування не змінюються: `VERSION`/`MINOR_VERSION` усіх записів ті самі; Bestiarium Mode лише роздає файл картки. Bestiarium Mock (стенд) отримує новий пристрій без міграції.
- Сутності за замовчуванням у картці — з таблиці бріфу (робочий HA); на стенді їх перекриває YAML картки.
- Код, коментарі, коміти — англійською; коміти `feat|fix|refactor|test|docs|chore(<integration>): …`.
- `make lint-check` і `make test` (тепер і `node --test`) зелені після кожної задачі.
- Робочий HA не чіпати; Docker на змонтованому репо — з UID 1000.

## Review Focus

1. **Датчик повідомляє `unavailable` / `unknown` / сутності немає** (Shelly EM уже зникав, SRC-079). Вузол показує `—`, дріт без потоку (`idle`), картка не падає, решта вузлів живе. Тест — Task 1 (`unavailable sun shows a dash and an idle wire`, `missing entity is unavailable`).
2. **Реле недоступне.** Топологію не вгадуємо: дроти цього реле — пунктир, інвертор «—» (див. питання 3). Тест — Task 1 (`unknown relay leaves its wires open`).
3. **«Мережа є» недоступна.** Вузол «Мережа» — `—`, не блідий, заголовок без «· світла немає», дроти — за реле. Тест — Task 1 (`unknown grid presence trusts the relays`).
4. **Шум навколо нуля** (сонце 0,02 кВт уночі, потік батареї −0,01). Нижче 0,05 кВт — «немає» / без руху, без мерехтіння рядків. Тест — Task 1 (`below the threshold is no flow`).
5. **Вузька картка (телефон, 375 px).** Підписи читаються (не менше 11 CSS-px), не налазять на дроти й не виходять за край. Перевірка — Task 2 (знімок на 375 і 1280 px у браузері стенда).

---

## Технічні рішення (для рев'ю менеджера)

- **Доставка — від Bestiarium Mode.** Mode є в робочому HA завжди і стосується живлення. Новий `bestiarium_mode/frontend.py`: у `async_setup` (вперше в Mode; разом із `CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)`) реєструється статичний шлях `/bestiarium_mode/frontend` → тека `frontend/` і модуль `/bestiarium_mode/frontend/power-flow-card.js?v=<sha256[:8] вмісту обох файлів>` через `frontend.add_extra_js_url`. Хеш — щоб планшет після оновлення Bestiarium не тримав стару версію в кеші. Файл — частина `custom_components`, тож оновлення HA його не чіпає, а оновлення Bestiarium приносить нову версію саме. Власник лише додає картку на дашборд (вона є у виборі карток — `window.customCards`).
  - `manifest.json`: `"dependencies": ["http"]`, `"after_dependencies": ["frontend"]`. Реєстрація — лише коли `"frontend" in hass.config.components`: HA без фронтенду не має дашбордів; у тестах (без `hass_frontend`) решта Mode працює як досі.
  - Відкинуто: файл у `www/` + ресурс Lovelace — ручний крок або запис у сховище Lovelace власника; окрема інтеграція — ручне додавання.
- **Модель — у чистому модулі, з тестами Node.** `make test` запускає ще й `node --test tests/frontend/`; у CI — окремий крок `node --test tests/frontend/` у `quality.yml`. Без npm-залежностей, без збирання.
- **Одиниці — з атрибута `unit_of_measurement`** (`W` / `kW`): одна функція `kilowatts(stateObj)`, тож стенд і дім працюють з тими самими правилами, хоч у дому Вт і кВт змішані.
- **Батарея — за датчиком потоку, з одним винятком** (критерії 3, 4, 6; рев'ю DEV-014-03, п. 1). Заряд — лише з датчика: надлишок сонця не означає заряд (батарея може бути повна). Розряд — з датчика, але коли інвертор живить дім без мережі за спиною (світла немає або реле «місто → інвертор» розімкнене), розряд відомий напевно: `max(датчик, дім − сонце)`. Датчик бачить розряд лише після падіння заряду на 1 %, тож без винятку на початку відключення батарея на схемі стояла б годину.
- **Мережа: `em1` — усе, що береться з мережі** (SRC-025, RISK-014). Дріт «Мережа → Дім» (дім напряму) несе потужність дому; «Мережа → Інвертор» — решту: `max(0, мережа − дім)` коли дім напряму, `мережа` коли дім від інвертора. Підпис «Мережа» — `мережа` в кВт або «є» нижче порогу (як у макеті).
- **Стенд — новий пристрій `home_power` у Bestiarium Mock**, а не помічники `input_*`: так «недоступно» й `unknown` справжні (критерії 7, 10), а ручки — у звичному місці мока (категорія `config`). Потужність дому на стенді — наявний `sensor.mock_grid_power` (сума розеток): критерій 7 — його профіль «Немає даних».

## Файли

| Файл | Відповідальність |
|---|---|
| `custom_components/bestiarium_mode/frontend/power-flow-model.js` | створити: `readings`, `flowModel`, `kw`, `hours`, `skeleton`, `DEFAULTS` |
| `custom_components/bestiarium_mode/frontend/power-flow-card.js` | створити: елемент `bestiarium-power-flow-card`, стилі, дотик, `window.customCards` |
| `custom_components/bestiarium_mode/frontend.py` | створити: `async_register_card(hass)` |
| `custom_components/bestiarium_mode/__init__.py` | `CONFIG_SCHEMA`, `async_setup` |
| `custom_components/bestiarium_mode/manifest.json` | `dependencies`, `after_dependencies` |
| `tests/frontend/power-flow-model.test.mjs` | створити: тести моделі |
| `tests/custom_components/bestiarium_mode/test_frontend.py` | створити: реєстрація |
| `custom_components/bestiarium_mock/home_power.py` | створити: `MockHomePower` |
| `custom_components/bestiarium_mock/{const,coordinator,switch,select,number,sensor}.py`, `translations/{en,uk}.json` | сутності `home_power` |
| `tests/custom_components/bestiarium_mock/test_home_power.py` | створити |
| `scripts/stand/{dashboard,scenarios,layout}.py`, `tests/stand/test_stand_setup.py` | секція картки, сценарії `power_*` |
| `Makefile`, `.github/workflows/quality.yml` | `test-card`, крок CI |
| `docs/bestiarium_mode.md`, `docs/bestiarium_mock.md`, `README.md` | картка, YAML для дому, стенд |

---

### Task 1: Модель потоків (чистий JS) і тести Node

**Files:**
- Create: `custom_components/bestiarium_mode/frontend/power-flow-model.js`
- Create: `tests/frontend/power-flow-model.test.mjs`
- Modify: `Makefile` (ціль `test-card: node --test tests/frontend/`; `test` викликає pytest і `test-card`), `.github/workflows/quality.yml` (у job `tests` крок `node --test tests/frontend/`)

**Interfaces:**
- Produces:
  - `DEFAULTS` — ключі конфігурації → сутності бріфу: `mode: "sensor.zhyvlennia_rezhym"`, `inverter_home_relay: "switch.rele_invertor_dim"`, `grid_inverter_relay: "switch.serverna_rele_invertor"`, `grid_present: "binary_sensor.zhyvlennia_merezha_ye"`, `home_power: "sensor.grid_main_load_em0_power"`, `grid_power: "sensor.grid_main_load_em1_power"`, `sun_power: "sensor.livoltek_pv_power"`, `battery_flow: "sensor.zhyvlennia_batareia_potuzhnist_skhema"`, `battery_charge: "sensor.zhyvlennia_zariad"`, `battery_hours_left: "sensor.zhyvlennia_batareia_hodyn"`, `battery_hours_to_full: "sensor.batareia_do_povnogo_zariadu"`.
  - `readings(states: Record<string, {state, attributes}>, entities: typeof DEFAULTS) -> Readings`, де `Readings = {mode: string|null, inverterHome: boolean|null, gridInverter: boolean|null, gridPresent: boolean|null, homeKw, gridKw, sunKw, batteryKw, charge, hoursLeft, hoursToFull: number|null}`; `null` — сутності немає, `unavailable`, `unknown` або нечислове. `batteryKw` «+» = розряд, як датчик. Вт → кВт за `attributes.unit_of_measurement === "W"`.
  - `flowModel(r: Readings) -> Model`, `Model = {title: string, nodes: Record<"grid"|"sun"|"inv"|"home"|"bat", {lines: string[], dim: boolean}>, wires: Record<"gridInv"|"gridHome"|"sun"|"invHome"|"invBat", {status: "flow"|"idle"|"off", speed: number|null, reverse: boolean}>}`.
  - `kw(v: number) -> string` (`"1,2 кВт"`), `hours(v: number) -> string` (`"≈7,5 год"`).
  - `skeleton() -> string` — SVG-розмітка з `id` кожного дроту (`wire-<key>`, `dots-<key>`), вузла (`node-<key>`) і рядків (`node-<key>-label`, `node-<key>-line-0..2`), шляхи — як у макеті.

**Правила `flowModel`** (`T = 0.05` кВт; «немає мережі» = `gridPresent === false`):
- `homeFromInv = inverterHome === true || gridPresent === false`; `homeFromGrid = inverterHome === false && gridPresent !== false`.
- `gridHome`: `homeFromGrid` → `flow` (потужність — `homeKw`), інакше `off` (зокрема реле невідоме).
- `gridInv`: `gridInverter === true && gridPresent !== false` → `flow`, якщо `gridToInvKw > T`, інакше `idle`; решта → `off`. `gridToInvKw = homeFromGrid ? max(0, gridKw − homeKw) : gridKw` (`null` у будь-якому → `idle`).
- `sun`: `sunKw > T` → `flow`, інакше `idle`.
- `invHome`: `homeFromInv` → `flow` (потужність `homeKw`; `null` → швидкість 1,6 с), інакше `off`.
- Потік батареї для схеми `batKw`: `batteryKw`; якщо `homeFromInv && (gridPresent === false || gridInverter === false)` і `homeKw`, `sunKw` відомі — `batKw = max(batteryKw ?? 0, homeKw − sunKw)` (виняток рев'ю п. 1).
- `invBat`: `|batKw| > T` → `flow`, `reverse = batKw > 0` (розряд: точки від батареї до інвертора); інакше `idle`. Рядки «віддає» / «заряд» і оцінки — за `batKw`.
- Швидкість дроту `flow` — `max(0.35, 1.6 − |кВт| × 0.35)`, округлена до 0,05 с (щоб дрібні коливання потужності не перезапускали анімацію).
- Вузли: Мережа — `gridPresent === false` → `["немає"]`, `dim`; `null` → `["—"]`; інакше `gridKw > T` → `[kw(gridKw)]`, інакше `["є"]`. Сонце — `null` → `["—"]`; `> T` → `[kw]`; інакше `["немає"]`, `dim`. Інвертор — `homeFromInv` → `["живить дім"]`; `homeFromGrid` → `["у резерві"]`; інакше `["—"]`. Дім — `[kw(homeKw)]` або `["—"]`. Батарея — перший рядок `"91 %"` (або `"—"`), до нього `" · віддає"` при розряді, `" · заряд"` при заряді; другий рядок `"вистачить " + hours(hoursLeft)` при розряді, `"до повного " + hours(hoursToFull)` при заряді, якщо оцінка не `null`.
- Заголовок: `mode ?? "—"`, плюс `" · світла немає"`, коли `gridPresent === false`.

- [ ] **Step 1: Write the failing tests** — `node:test` + `node:assert/strict`; допоміжна `model(overrides)` будує `Readings` зі «спокійного» стану (усе `0`, реле `true`, мережа є, режим «Звичайний»). Тести й твердження:
  - `criterion 1: normal mode feeds the home through the inverter` — обидва реле `on`, мережа є, дім 1,2: `invHome.status === "flow"`, `gridHome.status === "off"`, з мережі 1,5 → `gridInv.status === "flow"`, з мережі 0 → `"idle"` (підключено), інвертор `["живить дім"]`.
  - `criterion 2: powerful mode feeds the home from the grid` — `inverterHome:false, gridInverter:true`: `gridHome "flow"`, `invHome "off"`, інвертор `["у резерві"]`.
  - `criterion 3: powerful with solar charging` — обидва реле `false`, сонце 1,8, `batteryKw:-0.6`: `gridHome "flow"`, `gridInv "off"`, `sun "flow"`, `invBat {status:"flow", reverse:false}`, батарея `["91 % · заряд", "до повного ≈1,5 год"]` при `hoursToFull:1.5`.
  - `criterion 4: autonomous without sun discharges the battery into the home` — `inverterHome:true, gridInverter:false`, сонце 0, `batteryKw:0.9`, `hoursLeft:7.5`: `invBat {flow, reverse:true}`, `invHome "flow"`, батарея `["91 % · віддає", "вистачить ≈7,5 год"]`.
  - `criterion 4: without a grid behind the inverter the battery discharges before its sensor sees it` — дім 1,2, сонце 0, `batteryKw:0`, `inverterHome:true, gridInverter:false` → `invBat {flow, reverse:true}`, батарея починається з `"91 % · віддає"`; те саме з `gridInverter:true` і мережею → `invBat "idle"` (мережа за інвертором може покривати дім); `gridPresent:false` з будь-якими реле → `flow, reverse:true`; сонце 1,8 > дім 1,2 при `batteryKw:0` → `idle` (заряд лише з датчика).
  - `criterion 5: no grid feeds the home from the inverter whatever the relays` — `gridPresent:false, inverterHome:false, gridInverter:true`: `invHome "flow"`, `gridHome "off"`, `gridInv "off"`, Мережа `{lines:["немає"], dim:true}`, `title === "Потужний · світла немає"`.
  - `criterion 6: a zero battery flow is an idle wire` — `batteryKw:0`, сонце 1,8 і дім 1,2 (надлишок): `invBat {status:"idle"}`, батарея `["91 %"]`.
  - `criterion 7: unavailable sun shows a dash and an idle wire` — через `readings({...стани, [sun]: {state:"unavailable"}})`: Сонце `["—"]`, `sun "idle"`, решта вузлів зі значеннями. Також `missing entity is unavailable` — сутності немає в `states`.
  - `criterion 9: numbers use a decimal comma` — `kw(1.25) === "1,3 кВт"`, `kw(-0.6) === "0,6 кВт"`, `kw(2) === "2 кВт"`, `hours(7.5) === "≈7,5 год"`, `hours(3) === "≈3 год"`.
  - `criterion 10: no estimate without a flow or with an unknown estimate` — розряд з `hoursLeft:null` → `["91 % · віддає"]`; без потоку з обома оцінками → `["91 %"]`.
  - `watts are converted by their unit` — `readings` з `{state:"1200", attributes:{unit_of_measurement:"W"}}` → `homeKw === 1.2`; `kW` — без змін.
  - Review Focus: `unknown relay leaves its wires open` (`inverterHome:null` → `gridHome "off"`, `invHome "off"`, інвертор `["—"]`); `unknown grid presence trusts the relays` (`gridPresent:null` як критерій 1, Мережа `["—"]`, `dim:false`, заголовок без суфікса); `below the threshold is no flow` (сонце 0,02 → `["немає"]`, `idle`; `batteryKw:-0.01` → `idle`); `speed follows the power` (`speed` для 0,5 кВт `=== 1.45`, для 5 кВт `=== 0.35`).
  - `skeleton has every wire and node` — для кожного ключа є `id="wire-<key>"`, `id="dots-<key>"`, `id="node-<key>"`.
- [ ] **Step 2: Run to verify they fail** — `node --test tests/frontend/` → FAIL (модуля немає).
- [ ] **Step 3: Implement `power-flow-model.js`** за правилами вище; `skeleton()` — шляхи `d` з макета (функція `render()`), точки сонця — клас `sun`.
- [ ] **Step 4: Run to verify they pass** — `node --test tests/frontend/` → усі PASS; `make test` → pytest і node зелені.
- [ ] **Step 5: Commit** — `feat(mode): the power flow model of the power flow card`.

### Task 2: Елемент картки

**Files:**
- Create: `custom_components/bestiarium_mode/frontend/power-flow-card.js`

**Interfaces:**
- Consumes: `DEFAULTS`, `readings`, `flowModel`, `skeleton` з Task 1 (`import … from "./power-flow-model.js"`).
- Produces: custom element `bestiarium-power-flow-card`; конфігурація `{type: "custom:bestiarium-power-flow-card", navigation_path?: string, entities?: Partial<typeof DEFAULTS>}`; `window.customCards` запис `{type: "bestiarium-power-flow-card", name: "Bestiarium — схема живлення", description: "Звідки зараз живиться дім"}`.

- [ ] **Step 1: `setConfig(config)`** — `this._entities = {...DEFAULTS, ...config.entities}`; невідомий ключ у `entities` → `throw new Error("Unknown entity key: <key>")` (HA покаже картку помилки в редакторі). `static getStubConfig() → {}`; `getGridOptions() → {columns: "full"}`; `getCardSize() → 5`.
- [ ] **Step 2: Перший `set hass`** — shadow DOM: `<ha-card>` з рядком заголовка (`<h2>` режим) і `skeleton()`; іконки — `<ha-icon icon="mdi:transmission-tower|mdi:solar-panel|mdi:sine-wave|mdi:home|mdi:battery-high">` у `foreignObject`. Стилі — CSS макета з кольорами теми з Global Constraints; `.dots { animation: run var(--speed) linear infinite }`, `.dots.rev { animation-direction: reverse }`. `ha-card` — `container-type: inline-size`; `@container (max-width: 560px)` збільшує шрифти в одиницях `viewBox` (підпис 30, значення 26), щоб на 375 px текст був ≥ 11 CSS-px.
- [ ] **Step 3: Кожен `set hass`** — `model = flowModel(readings(hass.states, this._entities))`; якщо `JSON.stringify(model)` той самий, що минулого разу, — нічого; інакше для кожного дроту `class` (`wire flow|idle|off`), видимість `dots-<key>`, `style.setProperty("--speed", …)`, `classList.toggle("rev")`; для вузлів — тексти рядків і `dim`. DOM не перебудовується.
- [ ] **Step 4: Дотик** — якщо `navigation_path` задано: `click` на `ha-card` → `history.pushState(null, "", path)` і `window.dispatchEvent(new CustomEvent("location-changed"))` (як навігація карток HA); курсор `pointer`; праворуч від назви режиму — підказка «дотик — екран «Живлення»» (`--secondary-text-color`, як у макеті). Без `navigation_path` — без дії й без підказки (питання 4 звіту плану).
- [ ] **Step 5: Перевірка в браузері** (після Task 3, коли HA роздає файл; Task 5 дає стенд) — у devcontainer HA відкрити дашборд стенда, сценарії 1–6: дроти й підписи як у макеті з тим самим станом; знімки на ширині 1280 і 375 px; у DevTools Performance за 10 с — жодних `Layout`/`Recalculate style` щокадру від скриптів (лише анімація композитора). Очікування: вигляд збігається з макетом, на 375 px підписи читаються і не налазять на дроти.
- [ ] **Step 6: Commit** — `feat(mode): the power flow card`.

### Task 3: Bestiarium Mode роздає картку

**Files:**
- Create: `custom_components/bestiarium_mode/frontend.py`
- Modify: `custom_components/bestiarium_mode/__init__.py`, `custom_components/bestiarium_mode/manifest.json`
- Test: `tests/custom_components/bestiarium_mode/test_frontend.py`

**Interfaces:**
- Produces: `CARD_URL = "/bestiarium_mode/frontend"`; `async_register_card(hass: HomeAssistant) -> None`; `async_setup(hass, config) -> bool` в `__init__.py` викликає її.

- [ ] **Step 1: Write the failing tests**
  - `test_card_is_served_and_loaded_with_the_frontend` — `hass.config.components.add("frontend")`, `hass.http = Mock(async_register_static_paths=AsyncMock())`, `patch("custom_components.bestiarium_mode.frontend.add_extra_js_url")`; `async_setup_component(hass, DOMAIN, {})`: статичний шлях `StaticPathConfig(CARD_URL, <теки frontend>, cache_headers=True)`; `add_extra_js_url` викликано один раз з URL, що відповідає `^/bestiarium_mode/frontend/power-flow-card\.js\?v=[0-9a-f]{8}$`.
  - `test_the_version_follows_the_files` — два виклики з різним вмістом (patch читання файлів) дають різні `v`.
  - `test_without_the_frontend_nothing_is_registered` — без `"frontend"` у компонентах `add_extra_js_url` не викликано, запис Mode налаштовується (наявні тести Mode — сторожі).
- [ ] **Step 2: Run to verify they fail** — `make test` (лише цей файл: `pytest tests/custom_components/bestiarium_mode/test_frontend.py -v`) → FAIL.
- [ ] **Step 3: Implement** — хеш `sha256` вмісту двох `.js` (читання в executor), `await hass.http.async_register_static_paths([...])`, `add_extra_js_url(hass, f"{CARD_URL}/power-flow-card.js?v={digest[:8]}")`; `manifest.json` — `dependencies: ["http"]`, `after_dependencies: ["frontend"]`.
- [ ] **Step 4: Run to verify they pass** — `make test`, `make lint-check` → зелено; hassfest у CI.
- [ ] **Step 5: Commit** — `feat(mode): serve the power flow card`.

### Task 4: Стенд — пристрій «Живлення дому (імітація)» у Bestiarium Mock

**Files:**
- Create: `custom_components/bestiarium_mock/home_power.py`
- Modify: `const.py`, `coordinator.py`, `switch.py`, `select.py`, `number.py`, `sensor.py`, `translations/en.json`, `translations/uk.json` (Bestiarium Mock)
- Test: `tests/custom_components/bestiarium_mock/test_home_power.py`

**Interfaces:**
- Produces: пристрій `mock_home_power` («Mock Home Power» / «Живлення дому (імітація)», модель «Mock Home Power»), `coordinator.home_power: MockHomePower`. Сутності (unique_id = entity_id `<domain>.<unique_id>` після `make stand`), усі ручки — `config`:

  | Сутність | Що | Стан за замовчуванням |
  |---|---|---|
  | `select.mock_home_power_mode` | назва режиму: «Звичайний», «Потужний», «Потужний + зарядка від сонця», «Автономний» | «Звичайний» |
  | `switch.mock_home_power_inverter_home` | реле «інвертор → дім» | `on` |
  | `switch.mock_home_power_grid_inverter` | реле «місто → інвертор» | `on` |
  | `switch.mock_home_power_grid_present` | мережа є | `on` |
  | `number.mock_home_power_grid_power` | з мережі, 0–10000 W, крок 10 | 0 |
  | `number.mock_home_power_sun_power` | сонце, 0–10 kW, крок 0,1 | 0 |
  | `number.mock_home_power_battery_flow` | потік батареї, −5…5 kW, крок 0,1, «+» = розряд | 0 |
  | `number.mock_home_power_battery_charge` | заряд, 0–100 %, крок 1 | 91 |
  | `number.mock_home_power_battery_hours_left` | вистачить, 0–48 h, крок 0,5 | 7,5 |
  | `sensor.mock_home_power_battery_hours_to_full` | до повного, h: `unknown`, коли потік ≥ −0,05; інакше `(100 − заряд) / 100 × 10 кВт·год / |потік|`, округлено до 0,5 | — |

  Ручки зберігаються після перезапуску (`RestoreEntity`/`RestoreNumber`). Логіки реле в моці немає: сценарії ставлять реле, мережу й потужності разом.

- [ ] **Step 1: Write the failing tests** — `test_home_power_defaults` (стани з таблиці), `test_to_full_is_unknown_unless_charging` (потік 0 → `unknown`; 0,6 розряд → `unknown`; −0,9 при заряді 91 → `1.0`), `test_home_power_knobs_survive_a_restart` (restore реле, номерів і режиму), `test_home_power_is_translated` (uk-назви пристрою й сутностей).
- [ ] **Step 2: Run to verify they fail** — `pytest tests/custom_components/bestiarium_mock/test_home_power.py -v` → FAIL.
- [ ] **Step 3: Implement** — `MockHomePower` тримає стан і слухачів (як `MockIrAc`); сутності — у наявних платформах; константи — у `const.py` (`HOME_POWER_MODES`, `HOME_POWER_BATTERY_KWH = 10`).
- [ ] **Step 4: Run** — `make test`, `make lint-check` → зелено (наявні тести мока й конфіг-флоу — сторожі; лічба пристроїв у формі мока +1).
- [ ] **Step 5: Commit** — `feat(mock): the stand's home power for the power flow card`.

### Task 5: Стенд — картка на дашборді й сценарії

**Files:**
- Modify: `scripts/stand/dashboard.py`, `scripts/stand/scenarios.py`, `scripts/stand/layout.py`
- Test: `tests/stand/test_stand_setup.py`

**Interfaces:**
- Consumes: сутності Task 4; `sensor.mock_grid_power` як потужність дому.
- Produces: секція дашборда «Схема живлення» (`column_span: 2`): картка `custom:bestiarium-power-flow-card` з `navigation_path: "/bestiarium-stand/stand"` і `entities` на сутності мока (`home_power: sensor.mock_grid_power`), далі плитки ручок Task 4 і `select.mock_grid_reading_profile`. Сценарії (alias «Стенд: живлення — …»):

  | Скрипт | Стан | Критерій |
  |---|---|---|
  | `power_normal` | «Звичайний», обидва реле `on`, мережа є, з мережі 1000 W, сонце 1,8, потік 0 (надлишок сонця, а батарея без руху) | 1, 6 |
  | `power_powerful` | «Потужний», `inverter_home` `off`, `grid_inverter` `on`, з мережі 1000 W | 2 |
  | `power_solar_charging` | «Потужний + зарядка від сонця», обидва `off`, сонце 1,8, потік −0,6 | 3, 10 |
  | `power_autonomous` | «Автономний», `inverter_home` `on`, `grid_inverter` `off`, мережа є, сонце 0, потік 0,9, вистачить 7,5 | 4, 10 |
  | `power_outage` | «Потужний», мережі немає, реле як у «Потужному», сонце 0, потік 1,1 | 5 |
  | `power_outage_sensor_lags` | «Автономний», мережі немає, сонце 0, потік **0** (датчик ще не побачив): батарея віддає за винятком рев'ю п. 1 | 4, 5 |
  | `power_no_data` | як `power_normal`, профіль `select.mock_grid_reading_profile` «Немає даних» | 7 |

  `reset` повертає ручки `home_power` до значень за замовчуванням (і, як досі, профіль мережі «Надійний»). Потужність дому на стенді — сума розеток (`reset` ≈ 0,5 кВт).

- [ ] **Step 1: Write the failing tests** — `test_the_power_flow_card_has_its_scenarios` (усі сім скриптів; `power_outage_sensor_lags` ставить потік 0 і вимикає мережу; `power_outage` вимикає `switch.mock_home_power_grid_present`; `power_no_data` ставить `no_data` на `select.mock_grid_reading_profile`; `reset` ставить реле й мережу `on` і номери за замовчуванням) і `test_the_stand_dashboard_has_the_power_flow_card` (перша секція — картка з `entities.home_power == "sensor.mock_grid_power"` і ключами лише з `DEFAULTS`).
- [ ] **Step 2: Run to verify they fail** — `pytest tests/stand/test_stand_setup.py -v` → FAIL.
- [ ] **Step 3: Implement** — `layout.py`: `POWER_FLOW_CARD = "custom:bestiarium-power-flow-card"`; сценарії й секція — у наявному стилі `scenario_scripts` / `_dashboard`.
- [ ] **Step 4: Run** — `make test`, `make lint-check` → зелено; `make develop` + `make stand` у devcontainer → «Done.», дашборд стенда з карткою; Task 2 Step 5.
- [ ] **Step 5: Commit** — `feat(stand): the power flow card and its scenarios`.

### Task 6: Документація, deslop, PR, звіт

**Files:**
- Modify: `docs/bestiarium_mode.md` (розділ «Power flow card»: доставка, конфігурація, правила потоків, YAML для екрана «Живлення» і для інших екранів з `navigation_path`), `docs/bestiarium_mock.md` (пристрій `home_power`, сценарії `power_*`), `README.md` (рядок про картку в Bestiarium Mode)

- [ ] **Step 1: Docs** — як вище; YAML для дому: `type: custom:bestiarium-power-flow-card` (на «Живлення») і з `navigation_path: /<дашборд>/<екран живлення>` деінде.
- [ ] **Step 2: `/anti-slop:deslop`** на diff гілки; `make lint-check`, `make test` → зелено.
- [ ] **Step 3: Push, PR-чернетка** до `master`; звіт `D:\bestiarium-smart-home\handoff\DEV-014-04-report.md` з кроками ручного тесту за сценаріями `power_*` і знімками на 1280 / 375 px.
- [ ] **Step 4: Commit** — `docs(mode): the power flow card`.

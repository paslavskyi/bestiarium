# Картка «Схема живлення» переживає оновлення — план реалізації (DEV-015)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. (Для DEV-015 менеджер призначив `superpowers:executing-plans`.)

**Goal:** після оновлення Bestiarium і перезапуску HA картка `custom:bestiarium-power-flow-card` працює на відкритих і закешованих сторінках без ручного оновлення, а нова версія підхоплюється при наступному завантаженні сторінки.

**Architecture:** файли картки роздає власний `HomeAssistantView` під **сталим** шляхом `/api/bestiarium_mode/frontend/<файл>` (без відбитка) із заголовком `Cache-Control: no-cache`. Service worker фронтенду HA пропускає все під `/api/` лише в мережу (`NetworkOnly`), а браузер з `no-cache` щоразу перепитує сервер (ETag → `304`). Тож будь-яка сторінка — і свіжа, і взята з кешу service worker — бере той модуль, який зараз лежить на сервері; модель, імпортована відносно, приходить з того самого шляху, тож пара картка–модель завжди одного походження. Старий шлях DEV-014 `/bestiarium_mode/frontend/<відбиток>/<файл>` — другий URL того ж view (будь-який відбиток → поточні файли), щоб сторінки, закешовані до цього оновлення, теж не втратили картку.

**Tech Stack:** Python 3.14, HA 2026.10.2 (`homeassistant.components.http.HomeAssistantView`, `homeassistant.components.frontend.add_extra_js_url`), aiohttp 3.14 `web.FileResponse` (ETag / `If-None-Match` / `304` — вбудовані), pytest + `pytest_homeassistant_custom_component` (`hass_client_no_auth`).

**Spec:** бріф `D:\bestiarium-smart-home\handoff\DEV-015-01-brief.md`; контекст — `DEV-014-04-report.md`, `DEV-014-07-report.md`.

## Чому так (перевірено в коді HA 2026.10.2 у devcontainer)

`hass_frontend/service_worker.js` (Workbox) маршрутизує так:

| Запит | Стратегія | Наслідок |
|---|---|---|
| `/static/`, `/frontend_latest/`, `/frontend_es5/` | CacheFirst | — |
| `/api/…`, `/auth/…` | **NetworkOnly** | ніколи з кешу SW |
| `/` (корінь) | StaleWhileRevalidate, без строку | сторінка — спершу стара копія |
| усе інше (`/lovelace/…`, `/bestiarium_mode/frontend/…`) | StaleWhileRevalidate, кеш `file-cache`, строк **1 доба** | спершу стара копія; після доби — з мережі |

Сторінка (`index.html`) вписує `extra_module_url` у `<script>import("…")</script>`. Отже після оновлення перше завантаження бере **стару сторінку** зі старим відбитком. Якщо старого файлу картки вже нема в кеші SW (минула доба, або картку там ще не завантажували), запит іде на сервер, а той після DEV-014 знає лише новий відбиток → `404` → «Помилка конфігурації». Так пояснюється випадок на планшеті (застосунок відкриває корінь `/`, а його копія в кеші SW не має строку).

Чому відкинуто інші способи:

- **Відбиток у шляху (як зараз) + віддавати поточні файли за будь-яким відбитком.** Прибирає `404`, але SW віддає закешовану стару картку на першому перезавантаженні після оновлення (критерій 3 — лише з другого разу), а частковий кеш (картка в кеші, моделі нема) дає пару «стара картка + нова модель».
- **Сталий шлях поза `/api/` з `no-cache`.** SW усе одно віддає спершу стару копію (StaleWhileRevalidate) — критерій 3 знову з другого разу.
- **`?v=<відбиток>` у запиті.** Відносний імпорт моделі губить `?v=`, а нове значення `?v=` після перепідключення до HA — новий модуль: другий `customElements.define` того самого імені кидає помилку.
- **Статичний шлях HA (`StaticPathConfig`) під `/api/`.** `cache_headers=False` не дає `no-cache` — браузер евристично кешує файл за `Last-Modified` без перепиту. Потрібен власний view.

Сталий URL ще й робить безпечним перепідключення відкритої сторінки після перезапуску HA (критерій 2): фронтенд отримує `added` з тим самим URL, `import()` повертає вже завантажений модуль — повторного `define` нема.

Доступ через Nabu Casa — той самий HTTP-сервер HA і той самий service worker на домені `*.ui.nabu.casa`; `/api/…` і заголовки проходять без змін. Ризик залежності від правил SW HA — у «Ризиках» звіту `DEV-015-02-plan.md`.

## Global Constraints

- Гілка `dev-015-card-cache` від актуального `master`.
- Лише публічні API HA (`HomeAssistantView`, `hass.http.register_view`, `frontend.add_extra_js_url`); жодних HACS, ручних кроків власника.
- Картка, її YAML і вигляд — без змін: `frontend/power-flow-card.js` і `frontend/power-flow-model.js` не редагуються.
- Записи Bestiarium і їхні `VERSION`/`MINOR_VERSION` не змінюються; міграції нема.
- View без автентифікації (`requires_auth = False`): `<script type="module">` не несе токена. Віддає **лише** файли з `CARD_FILES`; будь-що інше — `404`.
- Заголовок: `Cache-Control: no-cache` (саме `no-cache`, не `no-store`: перепит з `304` дешевий).
- Код, коментарі, коміти — англійською; коміти `fix|test|docs(mode): …`.
- `make lint-check` і `make test` зелені після кожної задачі; пуш — лише після зеленого повного прогону.
- Робочий HA не чіпати; Docker на змонтованому репо — з UID 1000.

## Review Focus

1. **Запит чужого файлу через view без автентифікації** (`card.py`, `__init__.py`, `..%2Fconst.py`, `power-flow-card.js.map`) — `404`, жодного байта з теки інтеграції. Тест — Task 1 `test_only_the_card_files_are_served`.
2. **Повторне завантаження без змін** (планшет перепитує на кожному завантаженні сторінки) — `304` без тіла за `If-None-Match`, а не повний файл. Тест — Task 1 `test_an_unchanged_file_is_not_sent_again`.
3. **Сторінка, закешована ще з DEV-014** (старий відбиток у шляху, і модель, яку стара картка імпортує відносно) — обидва файли віддаються з поточного вмісту. Тест — Task 1 `test_the_digest_path_of_dev_014_serves_the_current_files`.
4. **Зміна файлу картки на диску без перезапуску HA** (розробник правит файл, або оновлення до перезапуску) — наступний запит віддає новий вміст (немає кешу відповіді в пам'яті, як у `CachingStaticResource`). Тест — Task 1 `test_a_changed_file_is_served_at_once`.
5. **HA без фронтенду** — нічого не реєструється, Mode працює як раніше. Тест — наявний `test_without_the_frontend_nothing_is_registered` (Task 1 оновлює його під view).

---

### Task 1: Сталий шлях картки під `/api/` з `no-cache`

**Files:**
- Modify: `custom_components/bestiarium_mode/card.py` (увесь модуль: прибрати `_digest`, `hashlib`, `StaticPathConfig`)
- Test: `tests/custom_components/bestiarium_mode/test_card.py` (переписати)

**Interfaces:**
- Consumes: `CARD_DIR`, `CARD_FILES` (як є).
- Produces:
  - `CARD_URL = "/api/bestiarium_mode/frontend"` (було `/bestiarium_mode/frontend`)
  - `LEGACY_URL = "/bestiarium_mode/frontend/{digest}"` — шлях DEV-014
  - `class CardView(HomeAssistantView)`: `url = f"{CARD_URL}/{{filename}}"`, `extra_urls = [f"{LEGACY_URL}/{{filename}}"]`, `name = "api:bestiarium_mode:card"`, `requires_auth = False`; `async def get(self, request: web.Request, filename: str, digest: str | None = None) -> web.StreamResponse`
  - `async_register_card(hass)` — та сама сигнатура; реєструє `CardView()` і `add_extra_js_url(hass, f"{CARD_URL}/power-flow-card.js")`.

Фікстура тестів: справжній `http` (`async_setup_component(hass, "http", {})`), `hass.config.components.add("frontend")`, клієнт `hass_client_no_auth`, `add_extra_js_url` підмінено як зараз (`ADD_JS`).

- [ ] **Step 1: Write the failing tests**

```python
async def test_the_card_is_loaded_from_a_stable_url_under_api(hass, frontend) -> None:
    # /api/ is network-only in HA's service worker; no digest: a cached page asks the current file
    with patch(ADD_JS) as add_js:
        assert await async_setup_component(hass, DOMAIN, {})
    add_js.assert_called_once_with(hass, "/api/bestiarium_mode/frontend/power-flow-card.js")

@pytest.mark.parametrize("name", CARD_FILES)
async def test_the_card_files_are_served_uncached(hass, client, name) -> None:
    response = await client.get(f"/api/bestiarium_mode/frontend/{name}")
    assert response.status == 200
    assert await response.read() == (FRONTEND / name).read_bytes()
    assert response.headers["Cache-Control"] == "no-cache"
    assert response.content_type == "text/javascript"
    assert response.headers["ETag"]

async def test_an_unchanged_file_is_not_sent_again(hass, client) -> None:
    url = "/api/bestiarium_mode/frontend/power-flow-card.js"
    etag = (await client.get(url)).headers["ETag"]
    response = await client.get(url, headers={"If-None-Match": etag})
    assert response.status == 304

@pytest.mark.parametrize("name", ["card.py", "__init__.py", "..%2Fconst.py", "power-flow-card.js.map"])
async def test_only_the_card_files_are_served(hass, client, name) -> None:
    assert (await client.get(f"/api/bestiarium_mode/frontend/{name}")).status == 404

@pytest.mark.parametrize("name", CARD_FILES)
async def test_the_digest_path_of_dev_014_serves_the_current_files(hass, client, name) -> None:
    # A page cached before this update asks the old digest, and its card imports the model beside it
    response = await client.get(f"/bestiarium_mode/frontend/0123abcd/{name}")
    assert response.status == 200
    assert await response.read() == (FRONTEND / name).read_bytes()
    assert response.headers["Cache-Control"] == "no-cache"

async def test_a_changed_file_is_served_at_once(hass, client, tmp_path) -> None:
    # patch CARD_DIR to tmp_path with both files; GET; rewrite card file; GET → new bytes
    ...
```

`test_without_the_frontend_nothing_is_registered` лишається: без `"frontend"` у `hass.config.components` — `add_js.assert_not_called()` і `GET /api/bestiarium_mode/frontend/power-flow-card.js` → `404`. Видалити `test_the_version_follows_the_files` (відбитка більше нема).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/custom_components/bestiarium_mode/test_card.py -v`
Expected: FAIL — `add_extra_js_url` called with `/bestiarium_mode/frontend/<digest>/power-flow-card.js`; `GET /api/…` → 404.

- [ ] **Step 3: Implement `CardView` and the new `async_register_card` in `card.py`**

`get`: `if filename not in CARD_FILES: raise web.HTTPNotFound`; `return web.FileResponse(CARD_DIR / filename, headers={hdrs.CACHE_CONTROL: "no-cache"})`. `digest` ігнорується (лише для шляху DEV-014). Docstring модуля — переписати: чому сталий шлях під `/api/` (SW network-only, `no-cache` → перепит із `304`), чому другий URL. `aiohttp` уже є залежністю HA — нових вимог нема.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/custom_components/bestiarium_mode/test_card.py -v`
Expected: PASS (усі).

- [ ] **Step 5: Lint, full test run, commit**

Run: `make lint-check && make test` — зелено (pytest + `node --test`).

```bash
git add custom_components/bestiarium_mode/card.py tests/custom_components/bestiarium_mode/test_card.py
git commit -m "fix(mode): serve the card from a stable network-only URL so a cached page keeps it"
```

### Task 2: Документація і ручна перевірка на розробницькому HA

**Files:**
- Modify: `docs/bestiarium_mode.md` (розділ «Power flow card», абзац «Why in Mode» і дерево файлів — `card.py`: «stable URL, no-cache view»)

- [ ] **Step 1: Переписати абзац про роздачу** — шлях `/api/bestiarium_mode/frontend/<файл>`, `Cache-Control: no-cache`, чому `/api/` (таблиця SW скорочено: `/api/` — лише мережа; решта — спершу стара копія), шлях DEV-014 як другий URL. Додати **обмеження HA** (критерій 4): сторінка, відкрита ще до першого встановлення картки, не має її модуля — картку видно після одного оновлення сторінки; так само після першого встановлення сторінка з кешу SW може раз показати «Помилку конфігурації».
- [ ] **Step 2: Ручна перевірка в браузері хоста** (розробницький HA, `make develop` у devcontainer, порт 8123 проброшено — `http://localhost:8123`, це безпечний контекст, тож service worker працює): кроки — як у розділі «Ручний тест» звіту `DEV-015-04-report.md` (нижче). Зафіксувати в DevTools → Network: документ сторінки `(ServiceWorker)`, файл картки — з мережі (`200`/`304`), не `(ServiceWorker)`.
- [ ] **Step 3: Commit**

```bash
git add docs/bestiarium_mode.md
git commit -m "docs(mode): the card's stable URL and the first-install limit of HA"
```

### Task 3: deslop, пуш, PR-чернетка, звіт

- [ ] **Step 1:** `/anti-slop:deslop` на diff гілки; якщо щось змінив — `make lint-check && make test`, коміт `refactor(mode): …`.
- [ ] **Step 2:** повний прогін `make lint-check && make test` — зелено; лише тоді `git push -u origin dev-015-card-cache`; PR-чернетка в `master`.
- [ ] **Step 3:** звіт `D:\bestiarium-smart-home\handoff\DEV-015-04-report.md` за шаблоном; у ньому — факт пушу, deslop і ручний тест:
  1. На `master` (DEV-014) запустити HA, відкрити дашборд із карткою в Chrome на `http://localhost:8123`, дочекатися картки.
  2. Перейти на гілку `dev-015-card-cache`, перезапустити HA, **не оновлюючи сторінку** → картка й далі показує схему (критерій 2).
  3. Оновити сторінку звичайно (F5) → картка є, без «Помилки конфігурації»; у Network документ — `(ServiceWorker)`, файл картки — `/bestiarium_mode/frontend/<відбиток>/…` з мережі (перехід із DEV-014, критерій 1).
  4. Ще раз F5 → файл картки — `/api/bestiarium_mode/frontend/power-flow-card.js`, `304` або `200`.
  5. Змінити видимий підпис у `power-flow-card.js` (наприклад, «Дім» → «Дім*»), перезапустити HA, сторінку не чіпати → картка є (критерій 2).
  6. F5 один раз → документ із кешу SW, а картка вже з «Дім*» (критерії 1 і 3).
  7. Повернути підпис, перезапустити HA, F5 → «Дім».

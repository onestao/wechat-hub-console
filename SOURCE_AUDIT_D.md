# SOURCE AUDIT D - Decoupled Console

Work package: D / `wechat-console`  
Branch: `feat/decoupled-console`  
Audit date: 2026-08-31

## 1. Upstream actually read

Primary upstream:

- `xiaoguiwucan/linux-wechat-agent` @ `58b2c43ff18597c6d0c9ec47270eb40e4fb0b2bb`

Local source baseline:

- `work/console` is an independent copy of the upstream repository on `feat/decoupled-console`.
- `upstream` remote remains `https://github.com/xiaoguiwucan/linux-wechat-agent.git`.

Shared contract inputs read before implementation:

- `docs/INTERFACE_CONTRACT_V1.md`
- `stack/contracts/openapi.yaml`
- `stack/mock-core/app.py`
- `stack/mock-core/tests/test_app.py`
- `docs/SOURCE_MAP.md`
- `docs/WORK_PACKAGE_HANDOFFS.md`

## 2. Source files actually read

### Existing Agent Console

- `agent_console/app.py`
  - `Handler.do_GET` / `Handler.do_POST`: dependency-free HTTP routing.
  - `chat_summary`, `chat_chats`, `chat_messages`, `chat_search`, `chat_types`: delegate the chat viewer to `web/app.py`.
  - `suite_status`: delegates service presentation to `status/app.py`.
  - reply/outbox functions and routes under `/api/reply/*`.
  - config/model/persona/talk/skills/memory/photos/logs routes.
  - `main`: starts the legacy LLM, semantic-memory, image-ingest, auto-reply and login-guard loops in-process.
- `agent_console/static/index.html`
  - Existing information architecture: overview, chats, services, models, persona, talk, skills, memory, database, photos, logs and test.
- `agent_console/static/app.js`
  - Existing navigation and refresh model.
  - Chat API adapters (`/api/chats*`).
  - Existing message rendering, filters, service page, skills/memory/photos/logs workflows.
- `agent_console/static/styles.css`
  - Existing visual system and layout are retained as the design reference rather than replaced with an unrelated dashboard.
- `agent_console/daily_report.py`
  - Existing group/member/report logic is AI/Agent-oriented and therefore becomes an optional Agent integration, not a Console-owned hard dependency.
- `agent_console/wechat_controller.py`
  - Existing GUI sender/window primitives are Runtime/Core concerns after decoupling and must not remain a direct Console dependency.
- `agent_console/builtin_skills/*/SKILL.md`
  - Skill presentation is retained conceptually, but execution/configuration moves behind the optional Agent integration.

### Existing read-only message viewer

- `web/app.py`
  - `api_summary`, `api_chats`, `api_messages`, `api_search`, `api_types`.
  - `display_for_message` and media URL shaping.
  - The useful UI contract is reused, but its direct reads of `runtime/memory/wechat_memory.sqlite` are not reused in decoupled mode.

### Existing status service

- `status/app.py`
  - `api_status` service aggregation and health presentation.
  - Existing probes/container descriptions are useful UI concepts.
  - Direct Docker socket access and direct memory/AI SQLite reads are not retained in the decoupled Console.

### Existing visual references

- `design_mockups/agent-console-*.html/png`
- `design_mockups/overview-hexgraph-*.html/png`
- `design_mockups/memory-*.html/png`
- `design_mockups/energy-core-graph-*.png`

These are treated as visual/interaction references. The work package does not introduce a separate visual language.

## 3. Existing Console functionality inventory

| Existing capability | Decision | D ownership after split |
|---|---|---|
| Chat/session browser | Keep and migrate | Core API |
| Message filter/search presentation | Keep, constrained by Core V1 data available | Core events/chat metadata |
| Media preview | Keep and migrate | Core `/v1/media/{media_id}` |
| Service/account health | Keep and migrate | Core `/health`, `/v1/accounts`; optional integrations reported separately |
| Logs | Keep for Console-owned operations | Console local durable log; optional Agent status can be proxied |
| Manual send/reply entry | Keep and migrate | Core `/v1/send/*` |
| Memory universe / semantic review | Move to optional Agent | Optional Agent integration |
| Model configuration/testing | Move to optional Agent | Optional Agent integration |
| Persona/talk scoring | Move to optional Agent | Optional Agent integration |
| Skill management/execution | Move to optional Agent | Optional Agent integration |
| Photo AI ingestion/review | Move to optional Agent; Core media remains viewable | Optional Agent + Core media |
| Docker container control | Remove from Console hard path | Runtime/Core/operator layer |
| Login guard / GUI auto-click | Remove from Console hard path | Runtime/Core |
| Direct SQLite DB browser/import/export | Remove from decoupled mode | Console must not open Core SQLite |
| Saved Messages | Add | Console-owned durable SQLite + archive directory |

## 4. Code that can be directly reused

The following upstream design/code patterns are reused in the implementation rather than rewritten from an unrelated framework:

- `http.server.BaseHTTPRequestHandler` + `ThreadingHTTPServer` service style from `agent_console/app.py`, `web/app.py` and `status/app.py`.
- Static-file serving and JSON response patterns from `web/app.py`/`agent_console/app.py`.
- Existing chat UI terminology and data-shaping concepts from `web.app.api_*` and `agent_console/static/app.js`.
- Existing account/service health presentation concepts from `status.app.api_status`.
- Existing Console layout/visual system from `agent_console/static/index.html` and `styles.css`.

## 5. Code that must be modified/replaced

### Chat/message access

Old:

`web/app.py -> db_connect() -> runtime/memory/wechat_memory.sqlite`

New:

`wechat_console/core_client.py -> Core HTTP V1`

The Core V1 contract currently exposes chats and durable events, not an arbitrary historical-message query endpoint. The decoupled Console therefore builds its live message view from `message.created`/`message.updated`/`message.removed` durable events and keeps only Console-side presentation cache/state. It never opens a Core database file.

### Service status

Old:

`status/app.py -> Docker socket + sync/AI SQLite/status files`

New:

`Core /health + /v1/accounts`, with optional HTTP probes for Agent/EFB only when configured.

### Sending

Old:

`agent_console.app -> wechat_controller.py -> xdotool/clipboard/window`

New:

`Core /v1/send/text|image|file` with `Idempotency-Key`.

### AI/Memory/Skills

Old in-process functions and loops in `agent_console/app.py` become optional integration surfaces. They are not started by the new Console service.

## 6. New functionality required because it does not exist upstream

The upstream Console has no durable Saved Messages subsystem matching the taskbook. D therefore must add:

- Console-owned `saved_messages` table.
- Console-owned `saved_message_media` table.
- immutable message snapshot JSON.
- editable note.
- normalized tags.
- permanent attachment archive copied from Core media at save time.
- CRUD/search/filter API for saved items.
- archive media serving from Console storage after the original Core media disappears.

The upstream Console also has no Core HTTP V1 client because the upstream project predates this split, so a small dependency-free client is new code.

## 7. Explicitly not reused and why

- `web.app.db_connect` / direct memory SQLite queries: violates the frozen C/D/E HTTP boundary.
- `status.app.db_counts`: direct memory/AI SQLite access violates decoupling.
- `status.app.docker_request` and hard-coded container names: Console may be run without Docker socket and with EFB/Agent absent.
- `agent_console.wechat_controller`: sending/window ownership belongs to Core/Runtime.
- in-process LLM/semantic/image/auto-reply/login-guard loops in `agent_console.app.main`: those make Agent capabilities a required Console dependency and defeat the five-component split.
- global single-account paths (`MEMORY_DB`, `MEDIA_DIR`, `WECHAT_DISPLAY`, `WECHAT_CONTAINER`): incompatible with account-scoped Core V1 identities.

## 8. Test entrypoints

Planned/required D tests:

- `python -m unittest discover -s wechat_console/tests -v`
- Mock Core integration using `../../stack/mock-core/app.py`:
  - contract version check;
  - list accounts/chats;
  - poll message events;
  - Core media proxy/archive;
  - send text idempotency;
  - Saved Messages create/update/list/delete;
  - durable archive still readable after Core is unavailable.
- `python -m py_compile wechat_console/*.py wechat_console/tests/*.py`

## 9. Risks

1. Core V1 has no historical `GET messages` endpoint. The Console can show durable retained events but cannot guarantee complete historical browsing until Core adds a contract endpoint in a future compatible revision.
2. Permanent media archive increases Console disk usage. Archive writes must be atomic and namespaced by saved-message ID.
3. A saved snapshot may contain `vendor_specific` data; the UI must render it as data, never execute it.
4. Optional Agent/EFB endpoints are not frozen in Contract V1. Their integration must fail soft and must not block Console startup.
5. Core media may be unavailable at save time. Saved-message metadata must still persist and record archive failure instead of silently pretending permanence.

## 10. Real modification locations for D

- `wechat_console/` - new decoupled service derived from existing stdlib Console/server patterns.
- `wechat_console/static/` - migrated Console surface using the existing layout/visual vocabulary, with Saved Messages added.
- `wechat_console/tests/` - Core-contract and persistence tests.
- `Dockerfile.console` - standalone source build entrypoint.
- `README.console.md` - run/dependency/decoupling documentation.

The original `agent_console/`, `web/`, `status/` and `design_mockups/` remain in the derived repository for attribution, regression reference and later optional-Agent compatibility work. The pinned upstream commit does not contain a `LICENSE`, `COPYING` or `NOTICE` file (`git ls-tree` verified); D did not remove or replace any upstream license file.

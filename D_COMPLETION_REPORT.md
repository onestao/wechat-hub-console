# Work Package D Completion Report

Branch: `feat/decoupled-console`  
Base upstream: `xiaoguiwucan/linux-wechat-agent`  
Base commit: `58b2c43ff18597c6d0c9ec47270eb40e4fb0b2bb`

## Result

Work package D now provides a runnable decoupled `wechat-console` whose only
required runtime dependency is the frozen Core HTTP V1 contract.

Implemented operator workflows:

- Core/account status and multi-account scope selection;
- Runtime account lifecycle management through Core (create/start/stop/restart/remove);
- Core registry hot-reload status presentation and soft-fail behavior when the optional management extension is absent;
- Core chat list;
- message-event projection with text/image/file/reply metadata display;
- Core media preview;
- Core outbox text send with idempotency key;
- Console-owned logs;
- optional Agent/EFB health presentation that fails soft;
- Saved Messages with durable first snapshot, editable title/note/tags and
  permanent attachment archive;
- archived attachment serving after original Core availability is lost.

The original model/persona/talk/skills/AI-memory/photo-understanding paths are
not silently discarded. Their ownership is explicitly moved to optional
`wechat-agent`; GUI sender/login guard moves to Runtime/Core. The Console's
Agent page documents that migration and optional health state.

## Upstream used

```text
https://github.com/xiaoguiwucan/linux-wechat-agent.git
@ 58b2c43ff18597c6d0c9ec47270eb40e4fb0b2bb
```

The derived repository keeps the upstream remote, original source tree and
attribution history. The pinned upstream commit contains no `LICENSE`,
`COPYING` or `NOTICE` file (`git ls-tree` was checked), so D had no upstream
license file to copy or preserve and did not remove one.

## Reused code

| Original file | Original class/function/behavior | New location | Modification |
|---|---|---|---|
| `agent_console/app.py` | `BaseHTTPRequestHandler`/`ThreadingHTTPServer` Console server shape; route-oriented API architecture | `wechat_console/app.py` | Kept dependency-free stdlib service style; replaced direct GUI/DB/AI calls with Core V1 HTTP and optional probes. |
| `web/app.py` | `api_summary`, `api_chats`, `api_messages`, `api_search`, `api_types`; message/media presentation contract | `wechat_console/app.py`, `wechat_console/store.py`, `wechat_console/static/app.js` | Retained chat/message viewer workflow and filter concepts; data now comes from Core chats + durable event projection, never Core SQLite. |
| `status/app.py` | `api_status` service/account health presentation | `ConsoleService.status`, `integration_status`, Services UI | Retained service-health workflow; removed Docker socket and direct memory/AI DB counts. Core is required, Agent/EFB optional. |
| `agent_console/static/index.html` | sidebar information architecture, top account/scope controls, chat/service/log operator flows | `wechat_console/static/index.html` | Preserved operator-console structure while making Agent-owned capabilities an explicit optional area and adding Saved Messages. |
| `agent_console/static/app.js` | navigation, refresh, chat/message rendering, filter and service status workflows | `wechat_console/static/app.js` | Reimplemented adapters against decoupled Console/Core APIs; kept workflow semantics and account-scoped UI. |
| `agent_console/static/styles.css` | dark palette, 246px sidebar, cards, muted/accent status hierarchy | `wechat_console/static/styles.css` | Reused the existing visual vocabulary and responsive structure rather than introducing an unrelated dashboard. |
| `design_mockups/*` | existing Console visual references | `design_mockups/d-console-*-live.png` | Used as visual baseline; added live QA captures of overview/chat/Saved screens. |

## New code

### `wechat_console/core_client.py`

New because upstream predates the frozen WeChat Hub Core V1 split. It provides
the required HTTP-only boundary for health, accounts, chats, events, ACK,
media and send operations, with contract-version enforcement and structured
errors.

### `wechat_console/store.py`

New because upstream has no Console-owned Saved Messages subsystem and because
the new Console may not open Core SQLite. It owns only Console projection
state, logs and the required `saved_messages` / `saved_message_media` tables.

### Saved attachment archive

New because upstream media was served from the shared memory runtime. The task
requires a permanent Saved Message attachment copy. Archive writes are
SHA-256-addressed within the saved-message namespace and atomically replaced.

### `wechat_console/tests/`

New integration coverage is required for the split architecture. Tests start
the project's actual `stack/mock-core/app.py`; no fake private Core database is
used.

## Not reused

| Upstream code | Reason |
|---|---|
| `web.app.db_connect` and direct `runtime/memory/wechat_memory.sqlite` queries | Violates the frozen C/D/E HTTP-only Core boundary. |
| `status.app.db_counts` | Reads memory/AI SQLite directly and couples Console to implementation storage. |
| `status.app.docker_request` / hard-coded container names | Docker socket is not a Console requirement and prevents non-Docker/partial deployments. |
| `agent_console/wechat_controller.py` direct xdotool/clipboard/window sending | GUI/window ownership belongs to Runtime/Core; Console sends only through Core outbox endpoints. |
| in-process LLM, semantic-memory, style-persona, image-ingest, auto-reply and login-guard loops from `agent_console.app.main` | Makes AI/GUI services mandatory and defeats the component split; AI paths move to optional Agent and GUI guard to Runtime/Core. |
| global single-account constants/paths | Incompatible with account-scoped Core V1 identities. |

## Saved Messages proof

Durable schema created by `ConsoleStore.initialize()`:

```text
saved_messages
saved_message_media
```

The first saved snapshot is kept immutable on duplicate save. Operator edits
update only title, note and normalized tags. Media is fetched only through
Core `/v1/media/{media_id}` and copied into Console-owned archive storage.

If archive copy fails, the Saved Message remains and the media row records
`archive_failed` plus the error for explicit retry. A successfully archived
copy is not downgraded if a later retry cannot reach Core.

## Verification

Commands run from `work/console`:

```text
python -m py_compile wechat_console\__init__.py wechat_console\core_client.py wechat_console\store.py wechat_console\app.py wechat_console\tests\test_console.py
node --check wechat_console\static\app.js
python -m unittest discover -s wechat_console\tests -v
```

Result:

```text
Ran 8 tests
OK
```

Covered by the eight tests:

1. Core contract, account and chat access.
2. Durable event projection, cursor persistence and replay safety.
3. Saved snapshot/note/tags plus permanent image archive.
4. First snapshot immutability across duplicate save.
5. Core outbox text-send idempotency.
6. Full Console HTTP surface against Mock Core including saved-media serving,
   deletion, login-session state and no-store login-window snapshot proxying.
7. Runtime management discovery plus account create/stop/delete through the
   optional Core management extension and Mock Core, including the direct
   scan-login data path.
8. Structured behavior when Core is unavailable.

Visual QA was also run against live Mock Core + the new Console with Microsoft
Edge headless at 1440x1000. Captures:

```text
design_mockups/d-console-overview-live.png
design_mockups/d-console-chat-live.png
design_mockups/d-console-saved-live.png
```

The loaded screenshots confirm the retained dark sidebar/card visual system,
account switching, chat flow and Saved Messages detail UI render correctly.

## Known contract limitation

Core V1 currently has no historical message pagination endpoint. D therefore
uses the retained durable `message.*` event stream as a local **display
projection**, without bypassing the contract via Core SQLite. Complete historic
browsing should be added later through a compatible Core API revision rather
than a storage-layer shortcut.

## Session 0 integration handoff

See `INTEGRATION_NOTES_D.md`. Session 0 should expose port 8078 and map a named
`console-data` volume to `/data/wechat-console`. Agent/EFB must remain optional
and should not be added as required `depends_on` dependencies.

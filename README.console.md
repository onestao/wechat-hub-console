# WeChat Hub Console - Work Package D

This directory contains the decoupled Console derived from the existing
`linux-wechat-agent` `agent_console/` and `web/` implementation.

The upstream UI/source remains in this repository for attribution and
regression reference. The production entrypoint for work package D is now:

```text
wechat_console/
```

## Dependency boundary

Required:

```text
wechat-console -> wechat-core HTTP Interface Contract V1
```

Optional:

```text
wechat-console -> wechat-agent /health
wechat-console -> efb-multi /health
```

The optional probes fail soft. The Console starts and its Core-backed features
remain usable when EFB and Agent are absent.

When Core advertises the optional Runtime-management extension, Console also
shows a **微信账号** page. It can create/start/stop/restart/remove Runtime
accounts through Core HTTP. Console still never receives Docker Socket access:
Core bridges these operator requests to Runtime's private Unix control socket.
If the extension is absent or unavailable, the page reports that state while
normal account/chat/message/Saved workflows continue to work.

For a running account that is not yet online, the account page exposes
**扫码登录**. Console asks Core for an ephemeral Runtime login session and
shows a no-store PNG snapshot of that account's verified WeChat window inside
a login dialog. The dialog polls login state and closes the normal workflow on
`online`; no QR/window snapshot is written to Console SQLite, Saved Messages or
the attachment archive. **打开完整微信桌面** remains available for agreement,
security-verification, update or other screens that need direct interaction.

The Console never imports or opens Core SQLite files. Its own SQLite database
is separate and stores only:

- a display projection of durable Core events;
- Saved Messages;
- Saved Message attachment metadata;
- Console-owned operational logs.

## Run against Mock Core

From the project root, start the contract simulator:

```powershell
python stack\mock-core\app.py --host 127.0.0.1 --port 8080
```

Then from `work\console`:

```powershell
python -m wechat_console.app `
  --core-url http://127.0.0.1:8080 `
  --db runtime\wechat-console\console.sqlite `
  --archive-dir runtime\wechat-console\saved-attachments
```

Open `http://127.0.0.1:8078`.

## Environment variables

| Variable | Default in source | Purpose |
|---|---|---|
| `WECHAT_CORE_URL` | `http://127.0.0.1:8080` | Required Core V1 base URL |
| `WECHAT_CONSOLE_HOST` | `0.0.0.0` | HTTP bind host |
| `WECHAT_CONSOLE_PORT` | `8078` | HTTP port |
| `WECHAT_CONSOLE_RUNTIME_DIR` | `work/console/runtime/wechat-console` | Durable root; Docker sets `/data/wechat-console` |
| `WECHAT_CONSOLE_DB` | `<runtime>/console.sqlite` | Console-owned SQLite |
| `WECHAT_CONSOLE_ARCHIVE_DIR` | `<runtime>/saved-attachments` | Permanent saved attachment copies |
| `WECHAT_CONSOLE_SYNC_INTERVAL` | `2` | Durable event poll interval seconds |
| `WECHAT_DESKTOP_URL` | empty | Optional explicit Selkies desktop URL used by the login-dialog fallback; blank derives host `:3000` for HTTP or `:3001` for HTTPS |
| `WECHAT_AGENT_URL` | empty | Optional Agent health URL base |
| `EFB_MULTI_URL` | empty | Optional EFB health URL base |

The Dockerfile uses `http://wechat-core:8080` as its default Core URL and marks
`/data/wechat-console` as a volume. In the integrated Compose stack, use a
named volume for this path so Saved Messages survive container replacement.

## Saved Messages persistence

The Console database contains the required tables:

```text
saved_messages
saved_message_media
```

Saving a message persists its first snapshot and lets the operator edit only
the title, note and tags later. If the snapshot has a `media_id`, the Console
immediately downloads the bytes through Core `/v1/media/{id}` and atomically
copies them into its own archive directory. The archived file is thereafter
served by the Console and does not depend on Core media retention.

If media cannot be copied at save time, the Saved Message still exists and the
media row is marked `archive_failed` with the error. The UI exposes a retry.

## Core V1 historical-message limitation

Core Interface Contract V1 does not expose a historical `GET messages`
operation. To stay within the frozen contract, the Console builds its live
message browser from retained `message.created`, `message.updated` and
`message.removed` durable events.

This is intentionally not replaced with a direct Core SQLite query. A future
Core contract may add historical pagination without changing the Console's
storage boundary.

## Tests

From `work\console`:

```powershell
python -m py_compile wechat_console\*.py wechat_console\tests\*.py
python -m unittest discover -s wechat_console\tests -v
```

The integration tests start the project's real `stack/mock-core/app.py` and
cover:

- contract-version enforcement;
- accounts/chats;
- Runtime management discovery and create/start/stop/delete lifecycle through
  the optional Core extension;
- direct login-session status and no-store WeChat-window snapshot proxying;
- durable event projection and cursor persistence;
- text-send idempotency;
- Saved Messages snapshot/note/tags;
- permanent media archive;
- archived media availability with Core unavailable;
- HTTP surface and Saved Message deletion.

## Source audit

See `SOURCE_AUDIT_D.md` for the required upstream-use audit, retained/moved
feature inventory, new-code justification, risks and exact source locations.

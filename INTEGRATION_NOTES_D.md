# Integration Notes D

The D work package is self-contained in `work/console` and intentionally does
not edit the shared `stack/docker-compose.yml`, because the other work-package
branches are integrated by Session 0.

For the implementation profile, Session 0 should wire the Console service with
the following equivalent settings:

```yaml
wechat-console:
  build:
    context: ../work/console
  depends_on:
    - wechat-core
  environment:
    WECHAT_CORE_URL: http://wechat-core:8080
  ports:
    - "${WECHAT_CONSOLE_PORT:-8078}:8078"
  volumes:
    - console-data:/data/wechat-console
```

and add:

```yaml
volumes:
  console-data:
```

The named volume is important. The image declares `/data/wechat-console` as a
volume, but a named Compose volume makes Saved Messages and archived
attachments explicit, discoverable and stable across container replacement.

Optional integrations may be added later without changing the required Core
dependency:

```yaml
environment:
  WECHAT_AGENT_URL: http://wechat-agent:<agent-port>
  EFB_MULTI_URL: http://efb-multi:<efb-port>
```

Do not add `depends_on` entries for those optional services merely to satisfy
the Console. An absent Agent/EFB is a supported Console state.

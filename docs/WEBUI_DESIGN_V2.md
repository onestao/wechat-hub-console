# WeChat Hub Console — WebUI 设计 v2

日期：2026-09-02
适用范围：`work/console/wechat_console/static/`
设计概念画板：`work/console/design_v2/concepts/concepts.html`（每屏一个 `#anchor`；加 `?theme=dark` 强制深色）

---

## 1. 视觉理念

Console 从「工程控制台」改成「在 NAS 上添加、登录、运行和管理多个微信的应用」。

三条判断标准：

1. **账号优先。** 一级对象是「个人微信 / 工作微信 / 备用微信」，不是 Core / Runtime / Agent / EFB。
2. **状态要告诉用户下一步。** 每个账号一行、一个用户状态、一个主要操作。
3. **技术事实不被视觉掩盖。** Provider 能力差异、`uncertain` 发送结果、Beta 标记、Desktop Gateway 边界都按后端真实语义表达，只是搬到合适的层级。

气质：现代 NAS 应用 + 桌面通信工具。**默认跟随系统的浅色/深色**，浅色是设计基准；单层容器、
1px 边界与背景层级承担分隔，阴影只有三级且都很轻。微信绿只出现在主要动作与「已连接」正向状态。

### 候选方向与选择理由

无人值守执行，因此产出两个方向并自行选定（画板 `#dir-a` / `#dir-b`）：

| | 方向 A · 清爽列表（**采用**） | 方向 B · 分栏工作台（未采用） |
|---|---|---|
| 首页 | 需要处理 → 我的微信 → 最近消息 | 指标条 + 账号卡片矩阵 |
| 账号 | 开放列表行，一行一账号 | 3 列卡片 |
| 优点 | 「哪个微信需要我做什么」一眼可见；移动端天然折行 | 信息密度高 |
| 问题 | — | 指标（今日消息数、自动化任务数）对家庭 NAS 没有决策价值，且部分数字后端并不提供；卡片矩阵把主次操作摊平成同等权重；横向空间浪费，移动端仍要退回单列 |

方向 A 更符合任务书「轻量、亲和、非工程师化」的要求，故实现全部按 A 执行。

---

## 2. 信息架构

```text
首页        需要关注什么（需要处理 / 我的微信 / 最近消息）
微信        产品核心页：账号列表 + 添加 + 登录 + 生命周期
消息        双栏：会话列表 + 会话详情 + capability-aware 发送
收藏        原 Saved Messages（数据结构不变）
自动化      能力导向的 Agent 入口（未启用时是功能引导）
设置        常规 / 微信 / Telegram 集成 / AI 助手 / 数据与存储 / 高级与诊断
```

降级到「设置 → 高级与诊断」的内容：Core 健康与 URL、contract version、registry 热加载、事件游标与同步状态、Runtime Provider / PID / UID / Display / HOME / image、agent server health、账号级 sender capability、Console 日志、Agent / EFB 探针。

Core 正常时主 UI 不出现 Core 字样；异常时侧栏状态条变红并在首页出现「WeChat Hub 暂时无法连接」空状态。

---

## 3. Design tokens

实现文件：`static/css/tokens.css`。

### 3.1 主题模型：跟随系统 + 手动覆盖

存储的是**偏好**（`system` / `light` / `dark`），生效的是**解析结果**（`light` / `dark`）：

```text
localStorage["wechat-hub.theme"]     偏好，默认 system
        ↓  js/theme-boot.js（首屏渲染前的同步脚本）
<html data-theme="light|dark" data-theme-preference="system|light|dark">
        ↓
CSS 只需一套深色覆盖 :root[data-theme="dark"]
```

要点：

- `theme-boot.js` 是 `<head>` 里的**非 defer 经典脚本**，在首次绘制前就把 `data-theme` 落到 `<html>`，
  因此深色环境下不会出现白色闪屏（FOUC）。
- `:root` 上有 `color-scheme: light dark`，即使脚本被阻止，原生控件、滚动条、表单也仍然跟随系统。
- 偏好为 `system` 时监听 `matchMedia("(prefers-color-scheme: dark)")` 的 `change`，
  操作系统切换主题时**无需刷新**即时生效；偏好被显式设为 light/dark 后不再响应系统变化。
- 深色不是「把浅色调暗」：`:root[data-theme="dark"]` 里每个语义色都是独立取值。
  组件样式完全共用一套，没有 `.dark-xxx` 分叉。
- `localStorage` 不可用（隐私模式）时静默退回 `system`，不报错。

### 3.2 颜色

每个语义色有两个变体，用途严格区分：

- `--x`：**填充面**（主按钮底色、开关轨道、品牌标记）。
- `--x-text`：该色**承载文字或图标**时使用。填充值在页面/卡片背景上做文字往往不足 4.5:1。

| 用途 | 变量 | 浅色 | 深色 |
|---|---|---|---|
| 页面背景 | `--bg-page` | `#F5F6F7` | `#16181C` |
| 主内容 | `--bg-surface` | `#FFFFFF` | `#1F2227` |
| 次级背景 | `--bg-subtle` | `#FAFBFC` | `#24272D` |
| 下沉背景 | `--bg-sunken` | `#F0F2F4` | `#191C20` |
| 边界 | `--border` | `#E7E9EC` | `#2F343B` |
| 强边界 | `--border-strong` | `#D6DAE0` | `#3C424B` |
| 主要文字 | `--text-primary` | `#1F2329` | `#E9ECF0` |
| 次要文字 | `--text-secondary` | `#646C76` | `#9AA2AD` |
| 三级文字 | `--text-tertiary` | `#868E98` | `#828A95` |
| 品牌填充 | `--brand` | `#07C160` | `#2ECC7A` |
| 品牌文字 | `--brand-text` | `#04814A` | `#4FD894` |
| 品牌上的文字 | `--text-on-brand` | `#FFFFFF` | `#10251A` |
| 危险 / 危险文字 | `--danger` / `--danger-text` | `#D94B4B` / `#B33737` | `#E97B7B` / `#F09A9A` |
| 警告 / 警告文字 | `--warning` / `--warning-text` | `#C7831D` / `#9A6412` | `#DDA23F` / `#E8B45C` |
| 进行中 / 文字 | `--info` / `--info-text` | `#3A72D8` / `#2C5CB4` | `#7EA6F0` / `#96B8F5` |

浅色仍保留任务书建议的 `#07C160` 作为品牌填充；深色下提亮到 `#2ECC7A` 并把按钮文字改成深墨
`#10251A`（对比度 7.7:1），因为白字压在提亮绿上只有约 2.6:1。

`--text-secondary` 从任务书建议的 `#7D858F` 加深到 `#646C76`：前者在 `#F5F6F7` 上只有 3.45:1，
对 13–14px 中文正文偏薄。这是有意偏离，记录在第 11 节。

### 3.3 登录画面底板

微信登录窗口截图**永远是浅色**，所以它的底板独立于主题：

```text
--login-plate      #FFFFFF   两种主题都白（截图本身就是白的）
--login-mat        #ECEEF1 / #CFD2D7   letterbox 区域，深色下压暗但不发黑
--login-mat-border #D6DAE0 / #AEB3BA
--login-ink        #646C76   白底板上的占位文字，不随主题变化
```

不使用任何 `filter` / `mix-blend-mode` —— 那会改变二维码对比度，也可能让安全确认按钮变得难以辨认。

### 3.4 间距 / 圆角 / 阴影

```text
--sp-1..9   4 8 12 16 20 24 32 40 48        固定刻度
--gutter        clamp(16px, 2.4vw, 40px)     页面左右留白，随视口伸缩
--section-gap   clamp(20px, 2vw, 32px)       分区纵向节奏
--r-xs..lg  6 8 10 14        --r-pill 999
--shadow-1  常规 surface     --shadow-2  popover/toast/drawer     --shadow-3  dialog
```

### 3.5 排版

系统字体栈覆盖 Windows / macOS / Linux / Android / iOS 中文：

```css
-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
"Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC",
"Source Han Sans SC", "Helvetica Neue", Arial, sans-serif
```

字号是**流体**的，在手机与约 1600px 之间线性插值后夹住，因此 4K 面板不会呈现「放大的手机版」：

| 层级 | clamp | 390px | 1440px | 1920px+ |
|---|---|---|---|---|
| Page title | `clamp(21px, 1.35vw + 15px, 28px)` | 21 | 28 | 28 |
| Section title | `clamp(16px, .4vw + 14.5px, 19px)` | 16 | 19 | 19 |
| Item title | `clamp(15px, .25vw + 14px, 16.5px)` | 15 | 16.5 | 16.5 |
| Body | `clamp(14px, .22vw + 13.2px, 15.5px)` | 14 | 15.5 | 15.5 |
| Label | `clamp(13px, .15vw + 12.4px, 14.5px)` | 13 | 14.5 | 14.5 |
| Caption | `clamp(12px, .12vw + 11.5px, 13.5px)` | 12 | 13.2 | 13.5 |
| Mono | `clamp(12.5px, .1vw + 12px, 13.5px)` | 12.5 | 13.4 | 13.5 |

行高改用无单位倍数（1.35–1.6），所以字号变化时行距同步跟随。
`base.css` 显式给 `button / input / select / textarea` 设 `font-family: inherit` 与 `--fs-body`，
不会落回浏览器默认字号；`html { text-size-adjust: 100% }` 保证用户在浏览器里放大字号时布局照常工作。

### 3.6 控件尺寸随输入方式变化

```text
--control-h      36px → 42px      (pointer: coarse)
--control-h-sm   30px → 36px
--control-h-lg   42px → 48px
--row-min-h      44px → 48px
```

判据是 `@media (pointer: coarse)`，不是视口宽度 —— 一台 1024px 的触摸面板需要大热区，
而一台 1024px 的小笔记本不需要。

---

## 4. 组件解剖

### 4.1 List row（产品核心组件）

```text
[头像 40px] [名称 + 能力 pill]            [主要操作] [···]
            [状态 + 最近活动]
```

- 头像色调跟随账号状态（good / warn / bad / 中性）。
- 状态使用 `.status[data-tone]`：**形状 + 文案 + 颜色**三重编码 —— 实心圆（已连接）、旋转圆环（正在启动）、菱形（等待登录）、方块（异常）、空心圆（已停止）。不依赖颜色单独区分。
- 主要操作只有一个；重启 / 停止 / 重新登录 / 高级信息 / 移除进入 `···`。
- 桌面 `···` 是 popover，手机是 bottom sheet，同一份菜单数据。

### 4.2 状态 → 文案 → 主操作映射

集中在 `js/account-view-model.js`，UI 不自行解释后端枚举。

| 后端事实 | 用户状态 | 主要操作 |
|---|---|---|
| `state=online` 且 agent server 健康 | 已连接 | 打开微信 / 查看消息 |
| 运行中 + `starting` / 无 snapshot | 正在启动 | （等待，按钮 disabled） |
| 运行中 + 未登录 | 等待登录 | 扫码登录 |
| `attention` | 需要在微信中确认 | 继续登录 |
| `agent_server_healthy === false` | 微信服务异常 | 重新启动 |
| 未运行 | 已停止 | 启动 |
| Runtime 控制通道不可用 | 微信管理暂时不可用 | 重试 |
| Core 不可用 | WeChat Hub 暂时不可用 | 重新连接 |

### 4.3 Dialog / Drawer / Sheet

全部用原生 `<dialog>`：焦点陷阱、`::backdrop`、ESC 由平台提供；危险动作对话框显式拦截 `cancel` 事件。

- Dialog：160ms fade + scale(0.985→1)，最大宽 560px（宽版 720px）。
- Drawer（账号高级信息）：右侧 420px，220ms slide。
- Sheet（手机行操作）：底部圆角，220ms slide-up，含 safe-area padding。
- Toast：右下角，桌面 380px，手机横向铺满并避开 tab bar。

### 4.4 登录 Dialog

登录画面是唯一视觉焦点：

- `.login-frame` 固定 `aspect-ratio: 3/4`，白底、1px 边界、`object-fit: contain` —— snapshot 尺寸变化不跳版，也**不会裁掉可能含安全确认按钮的区域**。
- 四周只有一句指引 + 一句「登录画面只在当前会话中临时显示，不会保存」，没有技术字段。
- image load / error / empty / refresh 四态都有设计：占位骨架 + indeterminate 进度条（不伪造百分比）。
- `aria-live="polite"` 播报状态变化，`<img alt>` 说明用途。
- 不出现 token、Gateway path、upstream host。

### 4.5 发送结果

`.send-result[data-state]` 四态：`sending` / `sent` / `failed` / `uncertain`。

`uncertain` 用警告色而非危险色，文案：

```text
发送结果未知
微信可能已经收到这条消息。为避免重复发送，系统没有自动重试。
[查看消息]  [仍然重新发送]
```

「仍然重新发送」走二次确认 Dialog。

---

## 5. 响应式与屏幕自适应

### 5.1 断点阶梯

| 视口 | 导航 | 布局 |
|---|---|---|
| ≥1600px（4K / 宽屏） | 左侧常驻，最宽 260px | 内容列最宽 1560px；消息双栏 30/70 |
| 1280–1599px | 左侧常驻 | 内容列 `clamp(720px, 78vw, 1180px)`；双栏 34/66 |
| 1024–1279px | 左侧常驻 | 双栏 35/65，gutter 收窄 |
| 768–1023px（平板） | 顶栏 + 抽屉（overlay，`min(300px, 84vw)`） | 设置左导航变横向 chips |
| ≤767px（手机） | 底部 5 项 tab bar | 双栏 → 单栏切页；行主按钮整行 |
| ≤380px（窄手机） | 同上 | gutter 收到 12px，账号名截断 11ch |
| 高度 ≤560px 且宽 ≥768px（横屏 / 小面板） | 同宽度规则 | `--section-gap` 压到 16px，双栏高度重算 |

关键点：不是靠断点跳变，而是 `--gutter`、`--nav-width`、`--content-max`、字号全部用 `clamp()`，
断点之间连续插值。上表只是校验点。

### 5.2 高度自适应

用 `dvh` 而非 `vh`，避免移动端浏览器地址栏收起/展开时布局跳动：

```text
.split      height: clamp(480px, calc(100dvh - 220px), 900px)
dialog      max-height: calc(100dvh - 2 * var(--gutter))
drawer      height: 100dvh
sheet       max-height: 82dvh
```

登录弹窗在矮视口（`max-height: 700px`）下把画面框按**高度**而非宽度限制，
保证微信窗口始终是焦点且不需要滚动才能看到二维码。

### 5.3 输入方式与其他系统偏好

| 媒体查询 | 效果 |
|---|---|
| `pointer: coarse` | 控件与行高提升到 42–48px |
| `hover: none` | 消息气泡的「收藏」按钮常显（触摸设备没有 hover） |
| `prefers-color-scheme` | 偏好为「跟随系统」时决定主题，并在系统切换时实时更新 |
| `prefers-contrast: more` | 边界加深、次要文字加深；浅色下主按钮底色换成 `#04803F`，白字达 5.0:1 |
| `prefers-reduced-motion: reduce` | 全部动画与过渡降到 0.001ms |

### 5.4 已验证视口

`1920×1080`、`1440×900`、`1280×800`、`1024×768`、`768×1024`、`390×844`、`360×780`，
以及深色 / 浅色各一轮。截图在 `design_v2/concepts/` 与 `design_v2/qa/`。

手机额外规则：触摸目标 ≥44px，最小字号 12px，长中文账号名截断（14ch / 窄屏 11ch）
并把主按钮下沉整行，登录弹窗顶部提示「建议在电脑或平板上打开此页面，再使用手机微信扫码」。

---

## 6. 登录流程实现

```text
点击「扫码登录」/「创建并登录」
  → POST /api/runtime/accounts/<id>/login      启动 Runtime 登录会话
  → GET  /api/runtime/accounts/<id>/login      读取状态（3s 轮询）
  → GET  .../login/snapshot?t=<ts>             取窗口画面（no-store）
  → state=online → 显示成功 → 停止轮询
  → 关闭弹窗 / 成功 / 错误 → 立即 clearInterval
```

前端状态判定完全来自 Core `runtime_login_status()` 返回的 `state` / `login_flow_state` / `snapshot_available` / `login_flow_error`：

| 后端 | UI |
|---|---|
| `state=starting` 或 `snapshot_available=false` | A. 准备中（骨架 + indeterminate） |
| `state=waiting` | B. 等待扫码（显示画面） |
| `login_flow_state=phone_confirm` | C. 已扫描，等待手机确认 |
| `state=attention` | D. 需要安全确认 |
| `state=online` | E. 登录成功 |
| `state=stopped` | F. 微信已停止 |
| `login_flow_state=error/timeout` 或 `login_flow_error` | G. 登录窗口暂时不可用 |
| `agent_server_healthy=false` | H. 微信服务异常 |

C 状态只在后端真的给出 `phone_confirm` 时出现（`agent_wechat_runtime.py` 的 FSM 会写这个值），不做前端猜测。没有二维码倒计时，没有假进度百分比。

---

## 7. Capability 显示

`js/capabilities.js` 从账号 `runtime.sender_capabilities` 派生：

```js
{ canSendText, canSendImage, canSendFile, canOpenDesktop,
  canLogin, canRestart, providerLabel, providerTechnical }
```

- AgentWechat 账号：显示图片 / 文件按钮。
- Legacy 账号：不显示这两个按钮（后端 `text/image/file` 全为 false 时连发送也 disabled 并给出简短原因）。
- 顶层 Core capability 只作为旧 Core 的兼容回退，不覆盖账号级事实。
- Provider 文案：普通层「推荐模式（Beta）」/「兼容模式」；技术名 `AgentWechat` / `Legacy` 只在高级信息抽屉与诊断页出现。真实 NAS acceptance 完成前 Beta 标记保留。

---

## 8. 高级诊断披露

三层：

1. **主 UI**：只有用户状态与主操作。
2. **账号高级信息抽屉**（`···` → 高级信息）：account_id、runtime_provider、runtime_health、agent_server_healthy、PID、UID、Display、HOME、image、autostart、sender capability、窗口数。
3. **设置 → 高级与诊断**：服务状态（Core 必需 / Agent、EFB 可选）、Core URL 与 contract、registry 热加载、事件游标与最近同步、Console 日志（等级 + 搜索）。

Agent / EFB 未配置时显示「未配置属于正常状态」，不显示 probe 报错。

---

## 9. 文件结构

```text
static/
├─ index.html                 语义骨架 + 6 个 <dialog>
├─ styles.css                 兼容入口：@import 四个 css 分片
├─ css/
│  ├─ tokens.css              颜色 / 间距 / 圆角 / 字体 / 动效 / 深色覆盖 / 媒体偏好
│  ├─ base.css                元素重置、排版工具类、reduced-motion
│  ├─ layout.css              app shell、导航、断点、split、settings
│  └─ components.css          按钮、状态、行、表单、dialog、登录、消息、收藏、诊断
└─ js/
   ├─ theme-boot.js           首屏前解析主题（经典脚本，非 module，不可 defer）
   ├─ app.js                  入口：路由 + 装配 + 生命周期
   ├─ api.js                  唯一 fetch 边界
   ├─ state.js                应用状态与订阅
   ├─ router.js               hash 路由
   ├─ account-view-model.js   后端状态 → 用户文案 / 主操作
   ├─ capabilities.js         账号能力派生
   ├─ format.js               时间 / 数字 / HTML 转义
   ├─ icons.js                stroke-only 图标 sprite
   ├─ components/             dialog / toast / menu / status / login-flow / detail-drawer
   └─ views/                  home / accounts / messages / saved / automation / settings
```

`<head>` 加载顺序：`theme-boot.js`（同步）→ `styles.css` → `<script type="module" src="/app.js">`。
`theme-boot.js` 必须在样式表之前或紧随其后同步执行，否则深色环境会闪一次白底。

依赖：无。仍是 Python stdlib HTTP backend + HTML + CSS + 原生 ES Modules，无构建步骤、无 Node runtime、镜像内容不变（`Dockerfile` 仍只 `COPY wechat_console`）。未迁移框架，因为 Vanilla + ES Modules 已足够，迁移只会给 NAS 场景增加镜像体积与启动复杂度。

---

## 10. 动效

| 场景 | 时长 |
|---|---|
| Dialog 进入 | 160ms fade + scale |
| Drawer / Sheet | 220ms slide |
| 状态切换 / snapshot 更新 | 160ms fade |
| 账号行删除 | 220ms collapse + fade |
| 视图切换 | 120ms content fade |
| Toast | 160ms |

只有两个循环动画：登录准备中的 indeterminate 进度条、按钮/状态的 spinner，都限定在明确的「进行中」语义内。无常驻 pulse、shimmer、渐变背景。全部包在 `@media (prefers-reduced-motion: reduce)` 兜底里（动画与过渡降到 0.001ms）。

轮询策略：登录弹窗打开时 3s，关闭立即停止；首页/账号状态 30s 低频；`visibilitychange` 回到前台时刷新一次。没有新增 WebSocket / SSE 常驻连接。

---

## 11. 可访问性

- 所有 icon-only 按钮有 `aria-label`（含每个账号行的「<名称>的更多操作」）。
- 导航用 `aria-current="page"`，会话列表用 `aria-selected`，过滤 chips 用 `aria-pressed`。
- 原生 `<dialog>` 提供焦点陷阱；ESC 关闭非危险 Dialog；危险确认拦截 `cancel`。
- 登录状态区 `aria-live="polite"`；toast 容器 `role="status"`。
- 表单错误用 `aria-describedby` 关联输入并加 `.has-error` 边框，不用 `alert()`。
- 状态不只靠颜色（形状 + 文案 + 颜色三重编码）。
- `:focus-visible` 统一 2px 表面色 + 2px 品牌色光环；焦点环用 `var(--bg-surface)` 做内圈，
  因此深色下不会出现白边。

### 对比度

`design_v2/qa/contrast_audit.py` 把调色板里所有承载文字的组合列成表并计算 WCAG 比值，
低于 4.5:1 即退出码非 0。改动 token 必须在同一次提交里更新该表。

浅色最低值：`--text-secondary` 4.9:1（页面）/ 5.3:1（卡片）；`--brand-text` 4.6:1；`--warning-text` 4.6:1。
深色全部 ≥4.6:1，多数在 7:1 以上。

两处有意例外，均已在脚本中标注原因：

| 组合 | 比值 | 为什么可接受 |
|---|---|---|
| `--text-tertiary` 在浅色页面上 | 3.1:1 | 只用于附属说明（如「概念稿占位」「12 KB」），从不单独承载语义；同一行必有达标文字 |
| 白字压在 `--brand` `#07C160` 填充上 | 2.4:1 | 保留微信品牌绿作为主按钮身份色。按钮同时有形状、位置、`aria` 语义；`prefers-contrast: more` 下自动换成 `#04803F`（5.0:1） |

### 有意偏离任务书建议值

任务书 §8.2 建议 `次要文字 #7D858F`。实测在 `#F5F6F7` 上仅 3.45:1，对 13–14px 中文正文偏薄，
故加深到 `#646C76`（4.9:1），色相不变。`--text-tertiary` 同理从 `#A2A9B3` 调到 `#868E98`。
其余建议色（页面背景、主内容、主要文字、边界、微信绿、危险、警告）逐值采用。

---

## 12. 安全边界（未改动）

- Console → Core → Runtime 私有控制通道，Console 无 Docker Socket、不读 Core SQLite。
- Desktop 一律通过 Core `/desktop` 描述符（scheme + port + path）打开 Desktop Gateway；前端校验 port 范围与 path 前缀，`window.open(..., "noopener,noreferrer")`。AgentWechat upstream `:6174` 不发布、token 不进入浏览器。
- 登录 snapshot 每次带时间戳直取，`Cache-Control: no-store` 由后端给出；前端不写入 localStorage / IndexedDB / Cache API。
- 「移除微信」始终走 preserve（不带 `purge_data`），确认文案明确说明登录数据保留；Legacy default account 仍禁止删除。

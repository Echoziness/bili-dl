# AGENTS.md — bili-dl 项目记忆

> 本文件沉淀"读代码难悟到"的项目事实；与全局规则配合使用。

## 1. 技术栈

- **语言**：Python 3.11+（v0.2.0 起提基线，因 `tomllib` 3.11 入 stdlib）
- **构建**：hatchling（`pyproject.toml` 声明，`src/` 布局，版本号动态读取 `__init__.py`）
- **运行时依赖**：`qrcode`（本地二维码）、`cryptography`（B 站 RSA-OAEP 续期协议）、`filelock`（跨进程会话事务锁）；HTTP、CLI、配置与彩色输出仍优先使用标准库。
- **外部程序依赖**：`yt-dlp`（必需，找不到则报错退出）、`ffmpeg`（可选，缺失则降级跳过音频提取/容器修复）。
- **工具链**：ruff（lint+format）、mypy（strict 模式，CI 强制）、pytest（测试）+ coverage（CI 报告）。
- **分发**：目标 PyPI，包名 `bili-dl`，脚本入口 `bili-dl`。

## 2. 已踩通的坑

### 2.1 原 bd.ps1 的 PowerShell 作用域 bug（重构根因）
- **现象**：`bd.ps1` 在主流程定义 `$commonOpt`，在函数 `Invoke-DownloadVideo` 内用 `@commonOpt` 引用；PowerShell 函数默认读不到调用者作用域的普通变量，导致 Cookie/Referer 参数被静默丢弃，非会员 1080P 实际下载退化为匿名。
- **根因**：作用域隔离 + 静默失败。
- **解法**：Python 版把 common 选项作为显式 `list[str]` 传入 `downloader.download()`，参数显式 threading，该 bug 类无法复现。位置：`src/bili_dl/downloader.py` 的 `_common_args()` + `download()` 签名。
- **教训**：任何"胶水脚本把公共参数定义在一处、函数内隐式引用"的设计都要在移植时显式化。

### 2.2 ffmpeg List[string].AddRange 在 PowerShell 不可用
- 原 bd.ps1 重构时用 `New-Object System.Collections.Generic.List[string]` + `.AddRange(@(...))` 会抛 `MethodException`：`Object[]` 无法转 `IEnumerable[string]`。
- 解法：改用 PowerShell 原生数组 `@(...) += @(...)`。Python 版无此问题。

### 2.3 音频容器标准化（foobar2000 兼容）必须覆盖两条路径
- **现象**：B 站 DASH 音频原始容器 `moov` 在尾、`major_brand=M4A`，foobar2000 起播慢或比特率显示异常。
- **根因**：容器布局，非编码问题。
- **解法**：`ffmpeg -i in -map 0:a -c:a copy -map_metadata 0 -movflags +faststart out`，零损失重封装为 `moov` 前置的 isom 容器。
- **关键**：`all` 模式（从视频抽流）和 `a` 模式（直接下载音频）**两条路径都要走 `repair_audio_container`**。这是本项目的"产品差异化"。
- **验证**：ffprobe 看到 `compatible_brands=M4A isom iso2`，`moov` 在 offset 36（紧跟 ftyp）。
- **位置**：`src/bili_dl/ffmpeg.py` 的 `repair_audio_container()` / `extract_audio()`，后者提取后必调前者。

### 2.4 Cookie 隐私模型（核心不变量）
- **过滤规则**：用 `"bilibili" in line` 匹配，保留所有 B 站域名行（含 `.bilibili.com` 和 `www.bilibili.com`）；其他站点 Cookie 不解析、不存储、不外发。这是项目隐私承诺，任何 PR 不得破坏。
- **源文件自动检测**（v0.1.5 起）：扫描 cookie 目录下任意 `.txt` 文件（排除输出文件 `cookies_bilibili.txt`），第一个含 bilibili 条目的即用作源。不再要求特定文件名 `cookies_all.txt`，旧文件名仍透明兼容。
- **模块拆分**（v0.1.7 起）：原 `cookies.py` 拆为 `cookiesource.py`（检测 + 导入）和 `cookiestore.py`（产物路径 + 校验 + `ensure_cookie` 编排）。依赖方向：`cookiestore → cookiesource`（单向）。
- 位置：`src/bili_dl/cookiesource.py` 的 `import_cookie()` + `find_source()`；`src/bili_dl/cookiestore.py` 的 `validate()` + `ensure_cookie()`。
- 测试锚定：`tests/test_cookiesource.py` 的 `test_extract_drops_other_sites()` 断言 `other_secret` 不泄漏；`test_www_bilibili_kept()` 断言 `www.bilibili.com` 行被保留；`test_any_txt_filename_detected()` 断言任意文件名可识别；`tests/test_cookiestore.py` 的 `test_ensure_cookie_*()` 验证编排流程。

### 2.5 nav API 在线校验的降级策略
- **现象**：在线校验 Cookie 需调 `https://api.bilibili.com/x/web-interface/nav`，但网络/SSL 错误时不能因此阻断下载（本地格式可能仍有效）。
- **解法**：`_online_check()` 返回 `True`（已登录）/`False`（未登录）/`None`（网络错误）；`None` 时降级为仅本地格式校验并打印警告，仍返回 True。
- 位置：`src/bili_dl/cookies.py::_online_check()` + `test_cookie_valid()`。

### 2.6 nav API 必须伪装浏览器 User-Agent（412 根因，非 SSL）
- **现象**：Python 版上线后实测每次都走降级（"无法在线验证 Cookie ... 降级为本地格式校验"），而原 bd.ps1 PowerShell 的 `Invoke-RestMethod` 一直能成功显示已登录用户名。曾被误判为本机证书链问题。
- **根因**：B 站 nav API 对 urllib 默认 UA `Python-urllib/3.x` 返回 **HTTP 412 Precondition Failed**（反爬）。`Invoke-RestMethod` 内部默认带 PowerShell/browser UA 故一直成功。代码的 `except Exception` 把 412 也吞进"网络/SSL 错误"分支，导致降级提示与真实根因描述不符——既误导用户也让 SSL 甩锅。
- **解法**：`config.py::USER_AGENT` 放一个固定 Chrome UA 字符串；当前由 `transport.request()` 统一加入所有 B 站认证/会话请求，`cookiestore._nav_probe()` 不再自行拼请求头。yt-dlp 下载阶段自带 UA。
- **验证**：实跑 `bili-dl`（无 URL）开头即显示 `[OK] Cookie 有效 | 已登录: <uname>`。
- **启示**：`except Exception` 太宽会把业务级 HTTP 错误（412/403/404）混进"网络问题"分支。**v0.1.8 已改**：`_nav_probe` 单独 catch `HTTPError` 拿 status，返回 `NavProbeResult(error="http:{status}")`；`validate` 据此报"HTTP 412（可能被风控）"而非笼统"网络/SSL 错误"。
- **关联**：用户曾因 `-k` 也无效而怀疑证书；`-k` 只影响 yt-dlp 的 `--no-check-certificate`，**对 urllib 探测无影响**（urllib 默认就校验，且本机 CA 没问题）。下次有人误以为证书，先查 UA。
- **v0.1.6 优化**：原先在线校验成功后又发一次完全相同的 nav 请求仅为拿 `uname`，现已合并为单次 `_nav_probe()` 调用，`isLogin` 和 `uname` 从同一响应提取。

### 2.7 TLS 证书校验默认开（安全硬化）
- 原 bd.ps1 无条件 `--no-check-certificate` 是降级项；Python 版默认启用校验，仅 `-k/--insecure` 显式关闭，用于自签证书环境。
- 位置：`cli.py` 的 `--insecure` 参数 → `downloader.py::_common_args()` 条件追加 `--no-check-certificate`。

### 2.8 跨平台路径不绑平台 API
- 原 bd.ps1 用 `[Environment]::GetFolderPath('MyVideos')` 是 Windows 专属；Python 版 `paths.py` 用 `sys.platform` 分支 + XDG 约定，同一代码三平台通用。
- Windows 用 `%APPDATA%\bili-dl` 存 cookie，`~/Videos` 改为 `Path.home()/"Videos"`（避免 SHGetKnownFolderPath 零依赖约束）。

### 2.9 Windows 文件名预测只对机器通道强制 UTF-8（v0.4.1 修正）
- **历史现象**：初版只给父进程 `subprocess.run(..., encoding="utf-8")`，却没有让 yt-dlp 同样输出 UTF-8；在 CP936 Windows 上会把子进程字节错误解码，导致普通中文标题的预测路径乱码。全局 `sys.stdout.reconfigure("utf-8")` / `PYTHONUTF8` 也会改变用户终端行为，因此当时回退到宿主 locale。
- **新证据（2026-09）**：标题 `【诗岸⧸洛天依】` 的 `⧸`（U+29F8）不属于 CP936。yt-dlp 的 `--print filename` 管道按 CP936 输出时会丢掉该字符，但真实下载通过 Windows Unicode 文件系统保留它；phase1 预测为“诗岸洛天依”，phase2 实际写成“诗岸⧸洛天依”，`out_path.exists()` 误判失败。旧有“GBK 外字符在预测和磁盘两侧会一致丢失”的假设已被反证。
- **解法**：仅在 phase1 机器可读通道给 yt-dlp 传官方 `--encoding utf-8`，父进程同时用 `encoding="utf-8"` 解码。phase2、CLI stdio 和用户终端完全不变；仍禁止全局 reconfigure 或设置 `PYTHONUTF8`。
- **测试锚定**：`tests/test_downloader.py::test_download_predict_uses_utf8_for_non_cp936_filename` 固定 `⧸` 回归；实测 BV1XkKL6nEQP 的预测路径与已下载文件一致。
- **原则**：进程间的机器可读协议应由发送方与接收方显式约定同一编码；不能只改单侧解码，也不能把局部协议要求扩大成全局终端策略。

### 2.10 `-v` 被占用，`--version` 用 `-V`（CLI 短选项冲突）
- **问题**：`-v` 已用于 `--video`（仅视频模式），再加 `-v/--version` 会被 argparse 拒绝（mutually exclusive group 与 version action 重复 dest）。
- **惯例**：`yt-dlp`、`curl`、`pip` 等都用大写 `-V` 表示 `--version`，与 `-v`/`--verbose` 区分。沿用此惯例用户零学习成本。
- **解法**：`cli.py::_build_parser()` 中 `--version` 用 `action="version"`，短选项 `-V`（不进 mutually exclusive group，否则会与 mode 冲突触发 `SystemExit(0)` 之外的限制验证）。
- **实测**：`bili-dl -V` → `bili-dl 0.1.0`；`bili-dl --version` 同效；`bili-dl -v <url>` 仍仅视频模式。
- **教训**：CLI 短选项是稀缺资源（26 字母），命名时优先排雷 `-V/--version`、`-v/--verbose`、`-h/--help` 这类高频占用。本工具占用清单：`-a/-v` 模式、`-k` insecure、`-V` version、`-h` help。

### 2.11 Phase 2 下载 returncode 检查——文件优先，退出码警告（v0.2.2 修正）
- **现象（v0.1.8 引入的 bug）**：Phase 2 用 `returncode != 0 or not out_path.exists()` 判断失败。yt-dlp 在很多场景下返回非零退出码（警告/合并警告/版本差异），但文件已完整写盘。用户看到 `[失败]` 但文件实际可用。
- **根因**：把 yt-dlp 给人类消费的退出码当作程序级成败信号。yt-dlp 的退出码粒度不足以区分"真失败"和"警告"。
- **解法（v0.2.2 修正）**：Phase 2 只检查 `not out_path.exists()` —— **文件不存在 = 失败；文件存在 = 成功**。`returncode != 0` 时追加 `[警告]` 消息（告知用户 yt-dlp 有意见），但不报失败、不阻断后处理流程。Phase 1 predict 保持不变（无文件可查，必须看 returncode + stdout）。
- **教训**：外部程序的退出码不是可靠的成功/失败信号。程序的"真实产出"（文件/网络请求结果）才是。检查产出存在性 > 信任退出码。

### 2.12 分层架构：逻辑层与展示层分离（v0.1.7 重构）
- **原则**：逻辑模块（`cookiesource`/`cookiestore`/`ffmpeg`/`downloader`）只返回结构化结果（`*Result` dataclass + `messages: list[tuple[str, str]]`），**绝不直接调 `ui.*`**。控制器（`cli.py`）是唯一的展示层，通过 `_emit()` 将 `messages` 映射到 `ui.info/ok/warn/error`。
- **收益**：逻辑模块变为纯函数（无 stdout 副作用），可在测试中调用不产生终端噪音；展示逻辑集中一处可统一修改；逻辑模块理论上可被非 CLI 消费者复用（如 GUI、库）。
- **代价**：约 +60 行映射代码（Result dataclass + `_emit`）；每个 Result 需定义 dataclass。
- **教训**：CLI 工具的"逻辑层打 print"是常见的隐性耦合。分离后 `pytest` 输出干净无垃圾——这是可观测的验证。
- **位置**：`cli.py::_emit()` + `cli.py::_EMITTERS` 字典；各逻辑模块的 `*Result` 返回类型。

### 2.13 `ensure_cookie` 封装领域编排（v0.1.7 重构）
- **问题**：原 `cli.py::_prepare_cookie` 直接协调 `test_cookie_valid → import_bili_cookie → test_cookie_valid`，控制器知道了 cookie 模块内部的协作细节（校验失败要导入、导入后要再校验）。
- **解法**：`cookiestore.ensure_cookie()` 封装"校验→导入→再校验"为单个领域操作。cli 只调一次，不关心内部步骤。
- **教训**：控制器编排"做什么"，领域模块编排"怎么做"。当控制器开始知道领域内部的步骤顺序时，编排逻辑就泄漏了。

### 2.14 `DownloadConfig` 参数对象化（v0.1.7 重构）
- **问题**：`download()` 有 11 个 keyword-only 参数，调用站点冗长，加字段需改所有调用方签名。
- **解法**：`DownloadConfig` dataclass 打包所有下载参数；`download(url, cfg)` 只需 2 个参数。加字段只改 dataclass，不破坏调用方。
- **教训**：超过 4-5 个参数的函数是"参数对象"重构信号。dataclass 比 `**kwargs` 更安全（有类型、有默认值、IDE 可补全）。

### 2.15 REPL EOFError 处理（v0.1.8）
- **问题**：`ui.prompt` 用 `input()`，stdin 关闭/重定向（如 `echo "" | bili-dl`）时抛 `EOFError` 导致崩溃。
- **解法**：`cli._repl` 的 prompt 调用包 `try/except EOFError: break`，干净退出返回 0。
- **测试**：`test_main_repl_eof_exits_cleanly` mock `ui.prompt` 抛 `EOFError`，断言 `main([]) == 0`。

### 2.16 最小化优先——"不能再少"而非"更多"（v0.1.9 回归 KISS）
- **背景**：v0.1.6→v0.1.8 轨迹是"更多、更多、更多"——更多测试、更多类型、更多 CI 版本。受 Bryan Cantrill《The Peril of Laziness Lost》启发（LLM 工作成本为零，倾向于堆叠而非简化），v0.1.9 做减法。
- **删除清单**：
  - `MsgLevel = Literal["info","ok","warn","error"]`——4 个字符串值不值得类型系统重型机械。`_EMITTERS` 运行时 KeyError 就能捕获拼写错误。删后 5 个文件少一个 import。
  - `NavProbeResult` dataclass——只在一个函数、一个调用者之间使用。降为 `tuple[Optional[dict], Optional[str]]` 返回，少 10 行。
  - 25 个同义反复测试——测 Python 语言本身（dict 查找、dataclass 默认值、`==` 运算符）而非我们的代码。测试从 101 降到 76，覆盖率从 91% 降到 87%——删掉的都是零信号测试。
  - CI Python 矩阵 3.9-3.13 五版本砍回 3.9+3.13 两版本——零依赖、无版本特定代码的 500 行包，中间版本不增加信号。（v0.2.0 基线升 3.11 后矩阵为 3.11+3.13。）
- **判断标准**（Cantrill）：每次改动前问"这让系统更简单了，还是只是更大了？"默认答案是"不加"。验证到边际收益递减就停。
- **教训**：S 级不是"更多"，是"不能再少"。最好的工程总是诞生于约束——人类的有限时间迫使开发清晰抽象，LLM 的零成本倾向于堆叠垃圾千层饼。

### 2.17 clig.dev 合规——消息流/环境变量/交互（v0.2.1）
- **审查依据**：[clig.dev](https://clig.dev/)（Docker Compose 作者编撰的 CLI 设计指南）
- **stdout/stderr 分离**：所有终端消息输出改到 `stderr`（`ui.info/ok/warn/error` 用 `print(..., file=sys.stderr)`）。主要输出是磁盘上的文件，所有终端文字都是 messaging。`bili-dl URL | grep` 现在干净无日志污染。
- **`NO_COLOR` 环境变量**：`ui._init()` 检查 `NO_COLOR` 非空即禁用颜色。符合 [no-color.org](https://no-color.org/) 标准。
- **`TERM=dumb`**：`ui._init()` 检查 `TERM=dumb` 即禁用颜色。
- **`--no-color` flag**：显式禁用颜色，`cli.main()` 在解析后立即调 `ui.disable_color()`。
- **stdin TTY 检查**：无 URL 且 stdin 非 TTY 时，报错"非交互模式需要提供 URL"并返回 1，不进 REPL。clig.dev §Interactivity："Only use prompts if stdin is a TTY"。
- **`HTTP_PROXY`/`HTTPS_PROXY` 环境变量**：`_merge_settings()` 代理优先级链：CLI `--proxy` > config.toml `proxy` > `HTTPS_PROXY` > `HTTP_PROXY` > 空。clig.dev §Configuration："Check HTTP_PROXY, HTTPS_PROXY... if you're going to perform network operations"
- **help 文本加示例和 issue 链接**：`--help` 显示 4 个示例 + GitHub issues URL。clig.dev §Help："Lead with examples" + "Provide a support path"

### 2.18 TOML 配置文件化（v0.2.0）
- **选型**：用 TOML 而非 INI/JSON/YAML——TOML 是 Python 生态标准（PEP 518/621），`tomllib` 3.11 入 stdlib，零依赖约束保持。
- **版本基线提升**：`requires-python` 从 3.9 提到 3.11。3.9 发布于 2020，2026 年提 3.11 合理。CI 矩阵同步改为 3.11 + 3.13。
- **配置优先级**：CLI 参数 > `config.toml` > 内置默认值。CLI 参数用 `default=None` 区分"未指定"和"显式传空"，`_merge_settings()` 据此决定是否用配置值。
- **文件位置**：`config_dir() / config.toml`，与 cookie 同目录。`--config FILE` 可覆盖路径。
- **模块**：`src/bili_dl/settings.py`——`Settings` dataclass + `load(path)` 函数。纯逻辑，返回 Settings 对象，无 ui 调用。

### 2.19 批量下载（v0.2.0）
- **接口**：`--batch-file FILE`，读取文本文件，每行一个 URL，`#` 开头为注释，空行跳过。
- **实现**：`cli._read_batch_urls()` 解析文件 → `cli._batch_download()` 顺序下载并统计成功/失败数。
- **返回码**：全部成功返回 0，任一失败返回 1。空文件返回 0（无 URL 不是错误）。

### 2.20 全面鲁棒性审查（v0.2.4）
- **背景**：用户帮朋友配好环境后，下载报光秃秃 `[失败]`，同版本自己的机器正常。排查发现 v0.1.8 的 returncode regression（§2.11 v0.2.2 修）+ 错误消息无上下文（§2.11 v0.2.3 修）。随后做全面审查发现 8 个问题。
- **错误消息全部带原因**：
  - Phase 1 predict 失败 → 带 yt-dlp stderr 最后一行
  - ffmpeg repair/extract 失败 → 带 ffmpeg stderr（`_run` 从 `subprocess.call` 改为 `subprocess.run(capture_output=True)`，返回 `(rc, stderr)`）
  - 文件不存在 → 带路径
- **所有 OSError 包 try/except**：`mkdir`/`replace`/`stat`/`write_text`/`read_text`（权限拒绝、磁盘满、WinError 32 文件锁定、路径过长）
- **`json.JSONDecodeError` catch**：`_nav_probe` 原来只 catch `HTTPError`/`URLError`/`OSError`，B站返回 HTML 而非 JSON 时崩溃
- **顶层异常处理**：`main()` 包 `try/except Exception`，未预期错误打印友好消息 + issues URL，不喷栈
- **`--retries 10`**：yt-dlp 默认重试 3 次（.part 重命名），提到 10 次以应对 Windows 文件锁定
- **教训**：每个 `subprocess.run` 的 stderr 都应该被消费——要么实时显示（Phase 2 继承），要么捕获后带入错误消息（Phase 1 / ffmpeg）。光秃秃的 `[失败]` 是最差的用户体验。

### 2.21 审查优化与覆盖率守门（v0.2.7）
- **背景**：审查发现覆盖率脱节（实测 63% vs 文档 §2.16 写 87%）+ CI 无 fail-under 闸门 + 5 个轻微问题。本轮做系统优化。
- **覆盖率**：当时从 63% 提升到 **98%**（107→159 测试）。CI `coverage report --fail-under=70` 设闸门，覆盖率下滑会让 CI 变红。2026-09 新增扫码/续期与统一传输层后的实测整体覆盖率为 **87%**（218 测试，transport 96%）；新增网络协议的异常分支以 mock 覆盖关键事务，不应把历史 98% 当作当前数字。
- **轻微问题修复**：
  - 代理环境变量认大小写（`HTTPS_PROXY`/`https_proxy`/`HTTP_PROXY`/`http_proxy`），符合 curl/git/requests 惯例。Windows 环境变量本身大小写不敏感，Linux/macOS 区分（故大小写优先级测试无法在 Windows 跑，仅测小写被识别）。
  - `settings.load` 对 `insecure` 做 `isinstance(bool)` 校验，非 bool 值（如 `"yes"`、`1`）coerce 为 `None`，防止下游 `cfg.insecure or False` 拾取 truthy 字符串。
  - `_extract_sessdata` 改为按 Netscape 列 6（`fields[5] == "SESSDATA"`）精确匹配，替代原先的 `"SESSDATA" in line` 子串匹配，杜绝 value 含该字样的误判。域名校验同步改为 `fields[0]` 列精确含 `bilibili.com`。
  - `cookiesource` 抽 `_first_bili_source()` 共享扫描逻辑，消除 `find_source` 与 `import_cookie` 的重复循环。
  - `ffmpeg` 临时文件名加 `uuid.uuid4().hex[:8]` 随机后缀，消除同 stem 并发冲突隐患。测试相应改为从 `args[-1]` 取输出路径，与随机命名解耦。
- **教训**：覆盖率是"可审计"的量化指标，必须配 CI 闸门否则会无声下滑；文档中的数字必须随版本同步，过时的数字比没有数字更糟（制造虚假信任）。

### 2.22 `data.get("proxy") or None` 吞掉「显式禁用」语义（v0.2.9 踩坑）
- **现象（第三方审查发现）**：用户在 `config.toml` 写 `proxy = ""` 想禁用代理，但系统有 `HTTPS_PROXY` 环境变量时，bili-dl 反而走了系统代理。用户「明明禁了代理」却看到代理生效——配置文件语义被实现细节反转。
- **根因**：`settings.load` 的 `data.get("proxy") or None` 把空字符串（用户显式写 `""`）归一为 `None`（与「未配置该键」不可区分）。下游 `_merge_settings` 对 `None` 的处理是「未配置 → 查环境变量」——空串用户的意图恰恰相反是「我明确要 None，不要查环境」。
- **解法（v0.2.9）**：移除 `or None`，改为 `data.get("proxy")`。`tomllib` 对不存在的 key 返回 `None`，对 `proxy = ""` 返回 `""`——两者语义不同，应该保留。`_merge_settings` 的 `if proxy is None` 自然区分：`None` → 未配置 → 查 env；`""` → 显式禁用 → 跳过 env。
- **v0.4 前补全**：仅在配置合并层保留空串还不够；若不给 yt-dlp 传 `--proxy ""`，子进程仍会重新读取继承的代理环境变量。现在 `downloader._common_args()` 始终传 `--proxy <resolved>`，空串使用 yt-dlp 官方定义的“直连”语义；认证 HTTP 则用 `ProxyHandler({})`。两条传输路径由同一个解析结果驱动。
- **测试锚定**：`test_empty_proxy_in_config_blocks_env_proxy` 断言 `proxy=""` 不被 env 覆盖；`test_load_empty_proxy_preserved` 断言 `""` 不被 `or None` 吞掉。
- **教训**：`or None` 只能用于「不存在」与「空串」语义等价时。凡是用户可能主动写空值的配置字段，都要区分「没写」（None）与「写了但为空」（""）——否则显式意图会被静默反转，用户极难自我诊断。

### 2.23 Windows 换行符假 diff：`.gitattributes` 必须显式声明（v0.3.0 踩坑）
- **现象**：`git status` 报 `config.py`/`paths.py` 已修改，但 `git diff`（含 `--ignore-all-space`）完全为空。用户困惑于"有改动却 diff 不到"。
- **根因**：仓库此前**只有 `core.autocrlf=true`，没有 `.gitattributes`**。autocrlf 只在读写时做 CRLF↔LF 转换，但 index 里缓存的是 stat（size/mtime），记录的是 CRLF 时代的文件大小；工作区某次被工具改写为 LF 后，stat 不匹配 → `git status` 判 modified，而 `git diff` 按内容比较（LF 规范化后一致）→ 显示无差异。两者结论冲突。
- **解法（v0.3.0）**：新增 `.gitattributes`：
  ```gitattributes
  * text=auto
  *.py text eol=lf
  ```
  然后 `git add --renormalize .` 统一 index 规范化，删除工作区残留 CRLF 文件后 `git checkout HEAD -- <file>` 强制重建（注意：`git checkout-index -f` 不会重写 stat 未变的文件，需先删再 checkout）。
- **验证**：全库 `src/**/*.py` + `tests/**/*.py` 字节扫描 CR=0（纯 LF）；160 测试通过、ruff 干净。
- **教训**：跨平台仓库不声明 `.gitattributes` 等于把行尾策略交给各贡献者机器的 autocrlf 猜测。`eol=lf` 明确后，Windows/WSL/Linux 提交的行尾由仓库统一控制，假 diff 从根源消除。
- **关联**：本项目 §2.9 的 CJK 编码策略与行尾无关（那是指子进程 stdout 解码，非文件存储），但两者常被混淆。文件存储行尾用 `.gitattributes` 管，进程间文本交换用宿主 locale。

### 2.24 Windows 受控文件夹访问（CFA）静默拦截 ffmpeg（v0.3.0 踩坑）
- **现象**：`bili-dl --all` 下载视频成功，但音频提取/容器修复失败。首个报错 `[out#0/ipod] Could not write header ... Bad file descriptor`，随后连锁 `UnicodeDecodeError: 'gbk' codec ...` 和 `'NoneType' object has no attribute 'strip'`——三个错误看似三种 bug，实则全是同一根因的连锁反应。
- **根因**：Windows Defender **受控文件夹访问（Controlled Folder Access, CFA）** 已启用（注册表 `EnableControlledFolderAccess=1`）。CFA 拦截**未签名**程序写入库目录（`~/Videos`、`~/Music` 等），且**静默伪装**——不是返回"拒绝访问"，而是让程序看到 `No such file or directory`（PowerShell `Set-Content` 同样报 `Could not find file`，而 cmd/Python 能写，极具迷惑性）。
- **触发条件**：ffmpeg 升级换新二进制即触发（旧版曾放行/白名单过，新版 9.0.1 未签名被判未知）。`yt-dlp`/miniforge `python.exe` 也全部 `NotSigned`。**任何"换了个 exe"的操作都可能再次触发。**
- **关键坑（junction）**：scoop 的 `current` 是 junction 指向真实版本目录（`C:\Users\16697\scoop\apps\ffmpeg\current` → `...\9.0.1`）。CFA 事件日志记录的是**解析后的真实路径** `...\ffmpeg\9.0.1\bin\ffmpeg.exe`，因此把 `current\bin\ffmpeg.exe` 加入 `Add-MpPreference -ControlledFolderAccessAllowedApplications` **无效**——必须加真实版本路径。ffmpeg 升级换版本号后需重加。
- **解法**：管理员 `Add-MpPreference -ControlledFolderAccessAllowedApplications <真实路径>` 白名单 ffmpeg/ffprobe/ffplay（PowerShell 7 提权：`Start-Process powershell -Verb RunAs -EncodedCommand`）。或 Windows 安全中心 GUI → 受控文件夹访问 → 允许应用。验证：`ffmpeg -y -i in.m4a -map 0:a -c:a copy out.m4a` 写入 `~/Music` 成功。
- **定位方法**：查 `Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-Windows Defender/Operational'; Id=1123}` 能看到精确的"已阻止...通过受控文件夹访问权限...路径..."事件，比猜错误码快得多。
- **产品决策**：不因此改默认输出目录——CFA 是系统安全配置不是产品 bug，改目录（如 `~/Downloads`）只是绕开不根治，且破坏 Videos/Music 库目录的用户预期。README 加了 Windows 提示段落（v0.3.0），根治是用户自己放行。
- **教训**：外部程序写文件失败时，如果错误是"文件不存在"但目录明明可写，先怀疑安全软件（CFA/杀毒）而非路径本身。三连环报错（Bad fd → 编码 → NoneType）往往源自第一个被掩盖的 OS 拦截，逐层溯源优于修表面。
- **v0.3.0 代码增强（把教训变成自诊断）**：
  - `ffmpeg.py` 新增 `_cfa_enabled()`（winreg 读 `EnableControlledFolderAccess`，非 win32/读失败返回 False）和 `_cfa_hint()`——判定条件刻意苛刻：父目录存在 **且** 可写（`os.access W_OK`）**且** CFA 开启，三者同时成立才提示，避免误报。
  - `repair_audio_container` / `extract_audio` 失败分支追加 hint（`[提示] ...` warn 消息），用户不再对裸 `No such file or directory` 抓瞎。
  - `cli.main` 顶层 except 从只打印 `{e}` 改为 `traceback.print_exc(file=sys.stderr)` + 环境行（版本/Python/platform）。**注意**：此前 §2.20 特意"不喷栈"，v0.3.0 改为喷完整栈——因为裸消息无法诊断，栈进 stderr 不影响 stdout 管道。
  - 测试锚定：`test_ffmpeg.py::test_cfa_enabled_registry_returns_1/0`（mock `sys.modules["winreg"]` 注入假模块——因 `winreg` 是函数内 import，`ff.winreg` 在非 win32 不存在，只能注入 `sys.modules`）；`test_cfa_hint_*`；`test_cli.py::test_main_top_level_exception_wrapped` 断言栈与环境行。
  - **环境陷阱**：venv 只装了 pytest 没有 coverage，`uv run coverage` 会静默落到系统环境（miniforge）的旧 bili-dl 上导致假失败。排障时先 `uv pip install coverage` 再测。venv 里也应补装 ruff/mypy 避免路径错乱。

### 2.25 独立扫码登录与 B 站要求的会话续期（v0.4.0）
- **边界**：`bili-dl login` 建立独立 B 站 Web 会话，不读取浏览器 Profile/Cookie、不假设任何浏览器存在。只有显式 login 才展示二维码；下载、批处理与 REPL 绝不隐式等待扫码。
- **配置一致性**：`login`、`--status` 与下载必须解析同一份 `config.toml` 和 `cookie_dir`；非默认配置文件通过 `bili-dl login --config FILE` 指定，CLI `--cookie-dir` 仍优先于配置值。
- **可观测性**：`bili-dl --status` 是只读状态入口，不要求 yt-dlp/ffmpeg，不下载、不续期、不扫码；它显示已登录账号、B 站此刻是否要求续期、自动续期是否已启用。它不显示无法预测的"下次续期时间"或实现内部的节流日期。
- **状态**：QR poll 成功的完整 B 站 Cookie 先经 `nav` 验证再原子写 `cookies_bilibili.txt`；返回的 `refresh_token` 与当前 `SESSDATA` 的单向 SHA-256 指纹共同写入同目录 `auth_state.json`（POSIX `0600`，两者及临时文件均须 Git 忽略）。每次续期前必须先验证指纹匹配，严禁把旧 token 用于替换或导入后的另一份 Cookie。状态写盘失败不得掩盖“Cookie 已可下载”的事实，但必须清除旧状态并警告自动续期不可用。
- **能力区分**：下载有效只要求可在线验证的 `SESSDATA`；自动续期还要求同一 Cookie 会话含 `bili_jct` 和匹配的 `refresh_token`。缺少续期条件时可继续下载，但 `--status` 必须报告自动续期不可用。
- **并发**：扫码落盘与整段自动续期事务必须持有同目录 `.auth_state.lock` 的跨进程 OS 锁，禁止两个进程交错写 Cookie/state。锁竞争时 login 明确失败并提示重试；下载只跳过本次续期，不得因锁竞争阻断已有有效 Cookie。
- **续期语义**：不是"永久登录"。只在 Cookie 已通过 nav 校验后、每 UTC 日首次下载前查询 `cookie/info`；B 站要求刷新才走 `correspond → refresh → confirm`。新 Cookie 必须 nav 验证并将新 token 与待确认的旧 token 一并持久化，之后才向 B 站确认旧 token；确认失败时保留待确认状态并在后续下载前重试，不得静默遗忘。网络或协议失败保留当前有效 Cookie 并继续下载。会话已被 B 站撤销时只提示用户显式重跑 login。
- **依赖**：二维码和 RSA-OAEP 是产品的标准能力，`qrcode`、`cryptography` 随正常安装提供；不得手写密码学或将二维码 URL/凭证发往第三方服务。
- **统一传输边界**：二维码、`nav`、`cookie/info`、`correspond`、refresh、confirm 的 HTTP 请求只能经 `transport.py`；协议模块不得各自封装 `urlopen`、请求头、超时或错误正文。传输错误只返回不含响应正文/凭证的结构化 `HttpFailure`。
- **代理贯穿**：代理完全由用户管理，项目不探测可用性、不自动选择、不改写系统设置。CLI/config/env 代理只允许由 `cli._resolve_proxy()` 解析一次，优先级固定为 CLI > config > `HTTPS_PROXY` > `https_proxy` > `HTTP_PROXY` > `http_proxy` > 空串。解析后的值必须显式传到下载、扫码、nav 校验与续期全链路；认证/存储层的 `proxy` 参数保持 keyword-only。`proxy=""` 明确禁用环境代理，`None` 只供库调用表示沿用 urllib 默认环境。
- **TLS 边界**：`-k/--insecure` 只传给 yt-dlp，绝不作用于登录与会话 API；认证传输始终使用系统默认 TLS 校验。

### 2.26 sdist 必须使用白名单（v0.4.0）
- **现象**：Hatch 默认 sdist 会读取工作区内容；即使 `output/`、`tmp/` 没有被 Git 跟踪，本地 `uv build` 仍曾把研究文档、整个第三方仓库和其中的 `.env*` 文件装入 tar.gz。wheel 因已指定 `packages = ["src/bili_dl"]` 不受影响，但手动上传该 sdist 会造成供应链污染和潜在凭证泄露。
- **解法**：`pyproject.toml` 的 `[tool.hatch.build.targets.sdist]` 使用显式 `include` 白名单，只允许 `src/`、`tests/`、README、CHANGELOG、LICENSE、pyproject 与 uv.lock；根目录 `/output/`、`/tmp/` 同时加入 `.gitignore`，但白名单才是发布安全边界。
- **发布守门**：`publish.yml` 在上传前必须依次执行 `twine check`、扫描 sdist 禁止 `tmp/output/.env/Cookie/auth_state`、安装并启动 wheel、校验 `v<version>` 标签与包版本完全一致。不得只因 CI 源码测试通过就跳过产物验证。

### 2.27 urllib 不会自动解压 B 站 gzip 响应（v0.4.1 修正）
- **现象**：`cookie/info` 正确返回 `refresh=true`，但自动续期报“B 站未返回会话续期口令”。Cookie、refresh token、RSA 公钥、时间戳和 HTML 选择器均正常。
- **根因**：`correspond` 返回 `HTTP 200 text/html` 和 `Content-Encoding: gzip`，正文以 gzip 魔数 `1f 8b` 开头；服务端即使收到 `Accept-Encoding: identity` 仍返回 gzip。`urllib` 不会自动解压，旧 `transport.fetch_text()` 直接把压缩字节按 UTF-8 解码，解析器自然找不到 `id="1-name"`。
- **解法**：统一传输层在文本或 JSON 解析前集中处理 `Content-Encoding`；支持标准库可解的 `gzip`/`x-gzip` 和 `identity`，损坏或未知编码返回不含正文的 `HttpFailure("bad_data")`。不得在 `authrefresh` 内局部特判，否则其他认证接口仍会重复踩坑。
- **安全性**：该失败发生在 refresh POST 之前，不会消耗 refresh token、替换 Cookie 或产生待确认事务；保留原会话继续下载是正确的降级行为。
- **测试锚定**：`tests/test_transport.py` 覆盖 gzip JSON、gzip HTML、损坏 gzip 与原有未压缩响应。

### 2.28 `uv run` 的 `PYTHONHOME` 不得泄漏给外部 yt-dlp（v0.4.1 修正）
- **现象**：仓库内执行 `uv run bili-dl -a URL` 时，预测阶段报 `AssertionError: SRE module mismatch`；同一个 `C:\Users\16697\miniforge3\Scripts\yt-dlp.exe` 在普通 PowerShell 中直接运行正常。
- **根因**：`uv run` 为项目的 uv Python 3.11 设置 `PYTHONHOME`，外部 yt-dlp 启动器实际绑定 Miniforge Python 3.14。子进程继承该变量后，3.14 解释器加载了 3.11 标准库，`_sre` 与 `re._compiler` 版本不匹配，甚至在 yt-dlp 解析参数前就崩溃。
- **解法**：两个 yt-dlp 子进程都接收父环境的副本，但显式移除 `PYTHONHOME`，让外部可执行文件使用自身解释器与标准库。保留 `PATH`、代理及其他用户环境；不全局修改当前进程，也不影响原生 ffmpeg。
- **测试锚定**：`tests/test_downloader.py::test_download_does_not_leak_pythonhome_to_external_ytdlp` 断言两个阶段均移除 `PYTHONHOME` 且保留无关环境变量；真实 `uv run` 预测调用已验证成功。

## 3. 项目结构

```
bili-dl/
├── pyproject.toml                 # hatchling（wheel/sdist 白名单）+ 工具链 + dev 依赖
├── README.md                      # 宣传门面（务必随版本同步功能表）
├── CHANGELOG.md                   # Keep a Changelog 格式
├── LICENSE                        # MIT + 依赖合规说明
├── .python-version                # pyenv/uv 用，固定 3.11（v0.2.0 起基线）
├── .gitattributes                 # * text=auto + *.py eol=lf（行尾统一，v0.3.0 起，见 §2.23）
├── .gitignore                     # 含 cookies_*.txt 与媒体文件，防泄密
├── AGENTS.md                      # 本文件
├── src/bili_dl/
│   ├── __init__.py                # __version__（hatchling dynamic version 源）
│   ├── __main__.py                # python -m bili_dl
│   ├── cli.py                     # 控制器 + REPL + main() + 配置合并 + 批量下载 — 唯一展示层（ui.* 只在此调）
│   ├── config.py                  # 纯常量，无可变状态
│   ├── paths.py                   # 跨平台路径（Win/macOS/Linux）+ config_file_path()
│   ├── settings.py                # TOML 配置文件加载（tomllib，纯逻辑，无 ui）
│   ├── authstate.py                # refresh_token 独立状态原子读写（纯逻辑，无 ui）
│   ├── authqr.py                   # 独立 B 站 Web 扫码登录协议（纯逻辑，无 ui）
│   ├── authrefresh.py              # B 站 Web Cookie 每日检查/续期协议（纯逻辑，无 ui）
│   ├── transport.py                # B 站认证 HTTP 统一边界（请求头/代理/超时/安全错误）
│   ├── cookiesource.py            # Cookie 源文件检测 + 提取导入（纯逻辑，无 ui）
│   ├── cookiestore.py             # Cookie 校验 + ensure_cookie 编排（纯逻辑，无 ui）
│   ├── ffmpeg.py                  # ffprobe/ffmpeg 探测 + 零损失重封装/提取（纯逻辑，无 ui）
│   ├── downloader.py              # yt-dlp 两阶段下载 + DownloadConfig（纯逻辑，无 ui）
│   ├── ui.py                      # ANSI 彩色输出到 stderr（clig.dev 合规）+ NO_COLOR/TTY 检查
├── tests/
│   ├── test_cookiesource.py       # 隐私核心测试（其他站点不泄漏）+ 导入逻辑
│   ├── test_cookiestore.py        # 校验 + ensure_cookie + _nav_probe mock（网络/HTTP/成功）
│   ├── test_authqr.py              # QR 状态机 + Cookie 域隔离
│   ├── test_authrefresh.py         # 每日检查/刷新/确认协议 mock
│   ├── test_authstate.py           # 刷新凭证状态的原子读写
│   ├── test_transport.py           # 认证传输契约、代理三态与安全错误
│   ├── test_downloader.py         # 参数拼装 + download() mock subprocess
│   ├── test_ffmpeg.py             # repair/extract mock subprocess 全分支
│   ├── test_cli.py                # argparse parser + config 合并 + 批量下载 + main() mock
│   ├── test_ui.py                 # _init TTY + colorize ANSI
│   ├── test_settings.py           # TOML 配置加载（missing/complete/partial/malformed）
│   ├── test_paths.py              # 跨平台路径分支（mock platform）
│   ├── test_package.py            # 包导入冒烟测试
│   └── data/sample_cookies_all.txt
└── .github/workflows/
    ├── ci.yml                     # lint(ruff+mypy) + test(3平台×2版本) + coverage
    └── publish.yml                # push v* tag → test 前置(ruff+mypy+pytest) → 自动发布 PyPI
```

## 4. 关键约定

### 4.1 依赖方向
`cli → settings → config`；`cli → cookiestore → cookiesource/authstate/authrefresh/transport`；`cli → downloader → ffmpeg`；`cli → paths → config`；`authqr/authrefresh/cookiestore → transport → config`；其他逻辑模块按需依赖 `config`。
- `config` 是叶节点（只导出常量），任何模块可依赖它，它不依赖任何内部模块。
- `ui` 也接近叶节点（仅 `mode_label` 懒导入 `config`）。
- **`ui` 只被 `cli.py` 依赖**（v0.1.7 起分层架构，逻辑模块不直接调 `ui.*`）。
- 禁止反向依赖或循环导入。

### 4.2 依赖保持克制
- `qrcode`（本地二维码渲染）、`cryptography`（RSA-OAEP）和 `filelock`（跨进程会话锁）是登录体验、协议安全与事务一致性所必需的标准依赖；它们随正常安装提供，不要求用户理解 extra。
- HTTP 继续使用 `urllib.request`，彩色输出使用 ANSI + `ctypes`；不为便利引入 `requests`、`colorama`、`rich` 等无明确产品收益的依赖。
- 新依赖必须有清晰的用户价值、安全维护性和移除困难度评估；"零依赖"本身不是目标。

### 4.3 命名
- Python 模块用 `snake_case`；CLI 旗帜沿袭 Unix 惯例（`--all/-v/-a` 模式、`--proxy`、`-k/--insecure`、`-V/--version`）。短选项占用清单见 §2.10。
- yt-dlp format 串集中放 `config.py`（`FMT_AV`/`FMT_AUDIO`），不散落。

### 4.4 提交规范
- Conventional Commits 中文风格可接受（项目面向中文用户为主），但英文 commit 便于国际贡献者，建议英文。
- 任何改动到 cookiesource.py / cookiestore.py / ffmpeg.py 者必须跑对应测试，不得破坏隐私断言。

### 4.5 开发环境规范（v0.3.0 起）
- **所有 dev 工具都在 `pyproject.toml` 的 `[dependency-groups] dev` 里声明**（pytest/ruff/mypy/coverage），`uv sync` 一键装齐。**禁止**直接往本机全局环境（miniforge 等）pip install 项目相关包——那会让 `uv run <tool>` 静默落到系统环境的旧包（§2.24 环境陷阱）。
- **开发只通过 `uv run <cmd>`**，不裸用本机 `pytest`/`ruff`/`mypy`。判断标准：`uv run python -c "import shutil; print(shutil.which('ruff'))"` 应指向 `.venv\Scripts`。
- 装新 dev 依赖：改 `pyproject.toml` 的 `[dependency-groups] dev` → `uv sync` → 提交 `pyproject.toml` + `uv.lock`。
- `uv.lock` 必须随 pyproject.toml 一起提交（v0.2.6 起已提交，保证可复现构建）。
- 国内网络 `uv sync` 需走镜像：`uv sync --default-index "https://mirrors.aliyun.com/pypi/simple/"`（pypi.org 直连 TLS 经常握手失败）。换环境跑 sync 若网络超时，先加这个参数。
- dev 组工具不进入发布包；正式运行时依赖以 `[project].dependencies` 为唯一事实来源。

## 5. 常用命令

```bash
# 开发环境（一键装齐项目 + dev 工具到 .venv）
uv sync --default-index "https://mirrors.aliyun.com/pypi/simple/"  # 国内网络用镜像

# 检验（全部走 .venv，绝不碰系统环境）
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src/bili_dl
uv run pytest -q

# 实测下载（需 yt-dlp + ffmpeg，且 cookie 目录有 cookies_bilibili.txt）
bili-dl https://www.bilibili.com/video/BVxxxxx
bili-dl -a https://www.bilibili.com/video/BVxxxxx   # 验证音频 faststart

# 独立扫码登录 / 只读会话状态
uv run bili-dl login
uv run bili-dl --status

# ffprobe 检查产物（验证 moov 在前 + isom 容器）
ffprobe -v error -show_entries format=format_name:format_tags=major_brand,compatible_brands path.m4a
ffmpeg -v trace -i path.m4a -f null - 2>&1 | findstr /R "moov mdat"   # Win
ffmpeg -v trace -i path.m4a -f null - 2>&1 | grep -E "moov|mdat"       # Unix

# 构建（CI publish.yml 自动处理，本地调试用）
uv pip install build
uv run python -m build
```

## 6. 环境特异事实（开发者备注）

- **SteamTools MITM 拦截（历史，现已恢复）**：WSL 环境曾有 SteamTools 在本地做 HTTPS 中间人代理，`api.github.com` 证书 issuer 为 `CN=SteamTools Certificate`（非正规 CA），导致 `gh` CLI（Go TLS）不信任该 CA、所有 API 调用失败，当时用 `curl -k` 绕过。**现状：`gh` CLI 已可直接用于 GitHub API 操作**（创建 Release 等）。若将来 `gh` 再次报 TLS 错误，先查是否 SteamTools 重新拦截，再回退 `curl -k`。
  - `git clone/pull/push` 用 **SSH** 不受影响（已配 `ssh.github.com:443`）。
  - `pip install bili-dl`（PyPI→GitHub 不走代理）如遇证书错误，加 `--trusted-host pypi.org --trusted-host files.pythonhosted.org`。
- **PyPI 镜像延迟**：ustc/清华等国内镜像对新发布的版本有滞后（数小时到一天）。ci 自动发布后如需立刻验证安装，用官方 PyPI + `--trusted-host`。
- **SSH 22 端口被封，走 443**：`git push` 实测 `ssh: connect to host github.com port 22: Connection refused`。解法：`git remote set-url origin ssh://git@ssh.github.com:443/Echoziness/bili-dl.git`，首次 push 用 `GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new"` 登记 `ssh.github.com:443` 的 host key（ED25519）。此 remote URL 已固化在本地仓库，后续 push 无需再处理。不改全局 config，避免影响其他仓库。
- **PowerShell 编码**：PowerShell 调子进程时需注入 `chcp 65001` + UTF8，但纯 Python 跨平台版不涉及此（Python3 默认 UTF-8）。此条仅对 bd.ps1 维护有意义。

## 7. 发布 checklist

- [x] 替换 `pyproject.toml` 中 `authors`、`project.urls` 占位（handle=Echoziness，邮箱用 GitHub noreply）
- [x] GitHub Actions CI（lint + test 三平台矩阵）已配置
- [x] PyPI 自动发布（`.github/workflows/publish.yml` — push `v*` tag 触发 Trusted Publisher 构建上传）
- [x] 首发 v0.1.0 / v0.1.1 / v0.1.2 已发布
- [x] v0.1.3 已发布（2026-06-28）
- [x] v0.1.4 已发布（2026-06-28）
- [x] v0.1.5 已发布（2026-06-28）
- [x] v0.1.6 已发布（2026-06-28）
- [x] v0.1.7 已发布（2026-06-28）
- [x] v0.1.8 已发布（2026-06-28）
- [x] v0.1.9 已发布（2026-06-28）
- [x] v0.2.0 已发布（2026-06-28）
- [x] v0.2.1 已发布（2026-06-28）
- [x] v0.2.2 已发布（2026-06-28）
- [x] v0.2.3 已发布（2026-06-28）
- [x] v0.2.4 已发布（2026-06-28）
- [x] v0.2.5 已发布（2026-06-28）
- [x] v0.2.6 已发布（2026-06-28）
- [x] v0.2.7 已发布（2026-06-28）
- [x] v0.2.8 已发布（2026-06-28）
- [x] v0.2.9 已发布（2026-06-28）
- [x] v0.3.0 已发布（2026-08-14）
- [x] v0.4.0 已发布（2026-09-11）
- [x] v0.4.1 已发布（2026-09-19）

### 发版流程（当前）
> 任何一步不绿不得进入下一步。

1. 改版本号：仅 `src/bili_dl/__init__.py`（`pyproject.toml` 用 hatchling `dynamic = ["version"]` 自动读取）
2. 写 CHANGELOG（Keep a Changelog 格式）
3. 本地全量验证（**四项缺一不可**）：
   ```bash
   uv run ruff check src tests        # 逻辑 lint
   uv run ruff format --check src tests  # 格式检查
   uv run mypy src/bili_dl            # 类型检查 (strict)
   uv run pytest                      # 单元测试
   ```
   - 若 format 报 `Would reformat`，先 `uv run ruff format src tests` 再提交。
4. `uv build` 后检查 wheel/sdist 内容并执行 `twine check dist/*`；产物不得包含工作区临时文件或凭证。
5. `git commit -m "release: vX.Y.Z"`
6. 先 push 主分支并等待 CI 全绿，再 `git tag -a vX.Y.Z -m "vX.Y.Z"` 并 push tag。
7. 等 Publish 工作流全绿并从 PyPI 实际安装验证。
8. `gh release create vX.Y.Z --title "vX.Y.Z" --notes "<从 CHANGELOG 取本版本段落>"` 创建 Release。

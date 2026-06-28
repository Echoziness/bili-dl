# AGENTS.md — bili-dl 项目记忆

> 本文件沉淀"读代码难悟到"的项目事实；与全局规则配合使用。

## 1. 技术栈

- **语言**：Python 3.11+（v0.2.0 起提基线，因 `tomllib` 3.11 入 stdlib，保零依赖）
- **构建**：hatchling（`pyproject.toml` 声明，`src/` 布局，版本号动态读取 `__init__.py`）
- **运行时依赖**：**零** —— 仅用标准库（`subprocess`、`urllib`、`pathlib`、`ctypes`、`argparse`、`json`、`shutil`）。
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
- **解法**：`config.py::USER_AGENT` 放一个固定 Chrome UA 字符串；`cookiestore.py` 的 `_nav_probe()` urllib request header 加 `"User-Agent": USER_AGENT`。仅 nav 探测用，yt-dlp 下载阶段自带 UA。
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

### 2.9 Windows CJK 编码策略：绝不强制 UTF-8（踩坑沉淀）
- **现象**：初版曾加 `subprocess.run(..., encoding="utf-8")` 解码 yt-dlp 的 `--print filename` 输出 + `sys.stdout.reconfigure("utf-8")`。结果**任何非 ASCII 标题的下载都 `[失败]`**：phase1 预测路径与 phase2 yt-dlp 实际写入磁盘的文件名不一致，`out_path.exists()` 返回 False。
- **根因**：yt-dlp 在 Windows 默认按系统 locale（cp936/gbk）编码 stdout。强制用 UTF-8 解码 cp936 字节得到的是真·乱码 unicode，与 yt-dlp 用 Win32 UTF-16 写入磁盘的正确文件名不匹配；反之默认 locale 解码（yt-dlp encode 与 Python decode 同为 cp936）虽然 unicode 含 GBK 外字符会丢字符，但 predict 路径字符串与磁盘文件名（同样经 yt-dlp encode）完全一致，`exists()` 稳定 True。
- **解法（已固化）**：`src/bili_dl/cli.py` 顶部注释明确"**不** reconfigure stdio、**不**设 `PYTHONUTF8`"，`downloader.py` predict 用 `subprocess.run(..., text=True)` **不传 encoding**，让两侧都用宿主默认 locale。
- **权衡**：常见汉字全在 GBK 范围内（99% 标题无丢失），少数生僻字/emoji 在 yt-dlp 内部已 replace，不影响路径定位。这是最少惊讶、跨平台最稳的方案。**任何"加 UTF-8 更现代"的 PR 都必须先验证 CJK 标题下载不破。**
- **测试锚定**：暂无单测（需真实 yt-dlp 子进程）；集成验证靠实跑 BV1froEBxEcX（《春死诀》全 CJK 标题）确认 `[完成!]` 而非 `[失败]`。
- **环境差异**：bd.ps1 原版在 PowerShell 下靠 `chcp 65001 + PYTHONUTF8=1` 强制全程 UTF-8（PowerShell 调子进程的固有编码坑）；纯 Python 跨平台版不沿用，因 Python 与终端/yt-dlp 默认 locale 自洽。

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
- **背景**：全面审查评估为 A-，扣分集中在覆盖率脱节（实测 63% vs 文档 §2.16 写 87%）+ CI 无 fail-under 闸门 + 5 个轻微问题。本轮做系统优化达 A。
- **覆盖率**：从 63% 提升到 **98%**（107→158 测试）。补齐 cli/ui/ffmpeg/cookiesource/cookiestore/downloader/settings/paths 全模块短板分支。CI `coverage report --fail-under=70` 设闸门，覆盖率下滑会让 CI 变红。
- **轻微问题修复**：
  - 代理环境变量认大小写（`HTTPS_PROXY`/`https_proxy`/`HTTP_PROXY`/`http_proxy`），符合 curl/git/requests 惯例。Windows 环境变量本身大小写不敏感，Linux/macOS 区分（故大小写优先级测试无法在 Windows 跑，仅测小写被识别）。
  - `settings.load` 对 `insecure` 做 `isinstance(bool)` 校验，非 bool 值（如 `"yes"`、`1`）coerce 为 `None`，防止下游 `cfg.insecure or False` 拾取 truthy 字符串。
  - `_extract_sessdata` 改为按 Netscape 列 6（`fields[5] == "SESSDATA"`）精确匹配，替代原先的 `"SESSDATA" in line` 子串匹配，杜绝 value 含该字样的误判。域名校验同步改为 `fields[0]` 列精确含 `bilibili.com`。
  - `cookiesource` 抽 `_first_bili_source()` 共享扫描逻辑，消除 `find_source` 与 `import_cookie` 的重复循环。
  - `ffmpeg` 临时文件名加 `uuid.uuid4().hex[:8]` 随机后缀，消除同 stem 并发冲突隐患。测试相应改为从 `args[-1]` 取输出路径，与随机命名解耦。
- **教训**：覆盖率是"可审计"的量化指标，必须配 CI 闸门否则会无声下滑；文档中的数字必须随版本同步，过时的数字比没有数字更糟（制造虚假信任）。

## 3. 项目结构

```
bili-dl/
├── pyproject.toml                 # hatchling + ruff + pytest 配置（单文件）
├── README.md                      # 宣传门面（务必随版本同步功能表）
├── CHANGELOG.md                   # Keep a Changelog 格式
├── LICENSE                        # MIT + 依赖合规说明
├── .python-version                # pyenv/uv 用，固定 3.11（v0.2.0 起基线）
├── .gitignore                     # 含 cookies_*.txt 与媒体文件，防泄密
├── AGENTS.md                      # 本文件
├── src/bili_dl/
│   ├── __init__.py                # __version__（hatchling dynamic version 源）
│   ├── __main__.py                # python -m bili_dl
│   ├── cli.py                     # 控制器 + REPL + main() + 配置合并 + 批量下载 — 唯一展示层（ui.* 只在此调）
│   ├── config.py                  # 纯常量，无可变状态
│   ├── paths.py                   # 跨平台路径（Win/macOS/Linux）+ config_file_path()
│   ├── settings.py                # TOML 配置文件加载（tomllib，纯逻辑，无 ui）
│   ├── cookiesource.py            # Cookie 源文件检测 + 提取导入（纯逻辑，无 ui）
│   ├── cookiestore.py             # Cookie 校验 + ensure_cookie 编排（纯逻辑，无 ui）
│   ├── ffmpeg.py                  # ffprobe/ffmpeg 探测 + 零损失重封装/提取（纯逻辑，无 ui）
│   ├── downloader.py              # yt-dlp 两阶段下载 + DownloadConfig（纯逻辑，无 ui）
│   ├── ui.py                      # ANSI 彩色输出到 stderr（clig.dev 合规）+ NO_COLOR/TTY 检查
├── tests/
│   ├── test_cookiesource.py       # 隐私核心测试（其他站点不泄漏）+ 导入逻辑
│   ├── test_cookiestore.py        # 校验 + ensure_cookie + _nav_probe mock（网络/HTTP/成功）
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
`cli → settings → config`；`cli → cookiestore → cookiesource`；`cli → downloader → ffmpeg`；`cli → paths → config`；`cookiestore/cookiesource/ffmpeg/downloader → config`。
- `config` 是叶节点（只导出常量），任何模块可依赖它，它不依赖任何内部模块。
- `ui` 也接近叶节点（仅 `mode_label` 懒导入 `config`）。
- **`ui` 只被 `cli.py` 依赖**（v0.1.7 起分层架构，逻辑模块不直接调 `ui.*`）。
- 禁止反向依赖或循环导入。

### 4.2 零运行时依赖（硬约束）
- 不可引入 `requests`/`colorama`/`rich` 等三方包。HTTP 用 `urllib.request`，彩色输出用 ANSI + `ctypes`，路径用 `pathlib`。
- 任何"加个依赖更方便"的提案都需先权衡"零依赖"这个卖点。

### 4.3 命名
- Python 模块用 `snake_case`；CLI 旗帜沿袭 Unix 惯例（`--all/-v/-a` 模式、`--proxy`、`-k/--insecure`、`-V/--version`）。短选项占用清单见 §2.10。
- yt-dlp format 串集中放 `config.py`（`FMT_AV`/`FMT_AUDIO`），不散落。

### 4.4 提交规范
- Conventional Commits 中文风格可接受（项目面向中文用户为主），但英文 commit 便于国际贡献者，建议英文。
- 任何改动到 cookiesource.py / cookiestore.py / ffmpeg.py 者必须跑对应测试，不得破坏隐私断言。

## 5. 常用命令

```bash
# 开发安装 (uv 管理 venv + editable)
uv venv --python 3.11 .venv
uv pip install -e . ruff pytest

# 检验
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest -q

# 实测下载（需 yt-dlp + ffmpeg，且 cookie 目录有 cookies_bilibili.txt）
bili-dl https://www.bilibili.com/video/BVxxxxx
bili-dl -a https://www.bilibili.com/video/BVxxxxx   # 验证音频 faststart

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
4. `git commit -m "release: vX.Y.Z"`
5. `git tag -a vX.Y.Z -m "vX.Y.Z"` → push commit + tag
6. 等 CI 全绿（lint[ruff+mypy] + test[3平台×2版本] + coverage + Publish 前置）确认无红色
7. `gh release create vX.Y.Z --title "vX.Y.Z" --notes "<从 CHANGELOG 取本版本段落>"` 创建 Release
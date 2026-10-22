# AGENTS.md — bili-dl 项目约定

本文件补充全局指令，记录开发时必须保留的项目行为。使用方法见 [README](README.md) 和[使用指南](docs/usage.md)；实现细节见[开发说明](docs/development.md)，历史问题见[排障记录](docs/troubleshooting.md)。

## 项目入口

- Python 3.11+、`src/` 布局、hatchling 构建；依赖及工具配置以 `pyproject.toml` 为准，开发环境由 `uv.lock` 复现。
- `cli.py` 负责命令行，`interactive.py → terminal.py` 负责交互流程和终端展示；两种前端共用 `actions.py`。
- `downloader.py` 分发媒体与字幕任务；评论由 `comments.py → commentapi.py → wbi.py` 协作，HTTP 统一经过 `transport.py`。
- `cookiestore.py` 编排 Cookie 校验、导入、扫码落盘和续期；协议分别位于 `authqr.py`、`authrefresh.py`，续期状态由 `authstate.py` 管理。

## 架构与关键行为

### 展示与交互

- 业务模块返回结构化结果与 `messages`，不导入 `ui`、Rich、Questionary 或前端。阶段、计数回调由前端提供，不解析普通日志作为业务协议。
- 无 URL / 批量文件且 stdin 为 TTY 时先打开菜单，不探测网络、登录或外部工具；非 TTY 时直接报缺少目标。只有用户明确选择登录才等待扫码。
- `Terminal.page()` 划分页面。输入时用同一 prompt_toolkit 布局管理正文与控件，按实时行列数重排并保留输入和选中项；长内容可翻页。Rich Live 负责等待阶段，二维码只能完整显示。
- 信息页等待明确返回；任务 Ctrl+C 返回首页，首页 Ctrl+C / EOF 退出。`TERM=dumb` / stderr 重定向使用普通数字菜单。
- 任务设置与会话默认值分开。配置导出只新建文件，不覆盖已有 TOML，也不保存代理认证信息。
- CLI 消息发到 stderr，帮助和版本发到 stdout；尊重 `--no-color`、`NO_COLOR`、`TERM=dumb`。帮助按显示单元换行，默认路径保留符号占位。

### 媒体与文件

- yt-dlp 使用当前 Python 的 `-m yt_dlp`，不依赖全局命令。FFmpeg 显式覆盖优先，其次由 imageio-ffmpeg 解析包内 / Conda / PATH 程序；同一任务的合并、提取、修复必须使用同一路径，两次 yt-dlp 都传 `--ffmpeg-location` 和 `--ignore-config`。仅执行媒体任务时解析工具。
- 保留 yt-dlp 两阶段流程：预查询输出路径，再下载。预查询两端显式约定 UTF-8；下载阶段继承终端，不改变用户 stdio 编码，不全局设置 `PYTHONUTF8`。两个子进程都移除继承的 `PYTHONHOME`。
- 下载前停止界面刷新，将终端交给 yt-dlp 原生进度；`DownloadConfig.stage` 只报告阶段。失败或警告时保留原生诊断到用户离开结果页。
- 预查询只接受单行路径；多目标（含同名重复行）在下载前报错，暂不支持旧式分段 FLV。单一路径可用、下载后目标文件存在时，非零退出码作为警告，不单独判失败。结果文件通过 `output_path` / `additional_paths` 返回。
- `all` 模式提取音频和 `a` 模式直接下载音频都须在 ffmpeg 可用时走无损容器修复；`extract_audio()` 提取后调用 `repair_audio_container()`。
- 字幕只取 API 首条轨道，不回退、不做 ASR。SRT 固定 UTF-8 BOM + CRLF；验证字节，避免 `read_text()` 的换行归一化掩盖问题。短链和字幕 CDN 请求不携带登录 Cookie。
- 评论必须保留去重、上限、归属校验与完整性语义。热门请求不重放含糊失败；计数不作为完成百分比。精简投影在协议校验之后，`--full` 也去掉内嵌回复预览；失败和中断不覆盖旧文件。

### 认证与传输

- Cookie 导入只保留 B 站条目，兼容 `.bilibili.com`、`www.bilibili.com`、任意源 `.txt` 文件名；不存储或外发其他站点凭证。
- 认证与内容 API 共用 `transport` 的请求头、代理、超时、解压和安全失败结果；错误不携带响应正文、Cookie、续期凭证或签名 URL。
- 代理由 `actions.resolve_proxy()` 统一解析：CLI > config > `HTTPS_PROXY` > `https_proxy` > `HTTP_PROXY` > `http_proxy` > 直连。保留 `None` 与空串区别；显式直连必须同时传到 urllib 和 yt-dlp。项目不探测或管理代理。
- `-k/--insecure` 仅影响 yt-dlp；登录、续期、字幕和评论 API 始终校验 TLS。在线校验已有 Cookie 失败可告警降级，新扫码 / 续期 Cookie 必须在线验证后才能替换旧文件。
- 扫码落盘与整个续期事务持有同目录跨进程锁。续期凭证须匹配当前会话指纹；新 Cookie 和待确认旧凭证持久化后才能确认旧凭证，失败保留待确认状态。
- 续期状态写入失败须清理旧状态并告警，不掩盖已保存 Cookie 可下载的事实；锁竞争、网络或续期失败不阻断已有有效会话。`--status` 只读，不续期或扫码。

认证事务、WBI、评论分页和路径边界的详细约定见[开发说明](docs/development.md)。

## 开发与验证

所有项目开发工具通过 `uv run` 使用，不向全局 Python 环境安装项目工具：

```bash
uv sync
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src/bili_dl
uv run pytest
```

- 网络确有需要时可给 `uv sync` 指定镜像；不把某次网络故障当作默认关闭 TLS 的理由。
- 依赖变更同步 `pyproject.toml` 与 `uv.lock`；仅改构建元数据时检查锁是否仍有效，不强行改写。开发依赖放 dev 组。
- 测试验证可观察行为、数据与副作用；网络协议使用 mock，终端交互按需用真实伪终端验证。修改 Cookie / 会话 / ffmpeg 必须跑对应测试；媒体集成用例通过本机 HTTP 验证真实 yt-dlp 和包内 FFmpeg，`uv run pytest -m media`。
- CI 覆盖率只统计业务源码，命令为 `uv run coverage run -m pytest -q`、`uv run coverage report --include="src/bili_dl/*" --fail-under=70`；平台和 Python 矩阵以 `.github/workflows/ci.yml` 为准，不在此维护易过时的数量。
- Python 文件遵循 `.gitattributes` 的 LF 约定。项目临时工具与检查记录放 `tmp/`，独立预览及实测产物放 `output/`，二者均不进入发布包。

## 发布流程

涉及提交、推送、标签和外部发布的步骤须有对应授权。前一步检查未通过，不进入后一步。

1. 日常用户可见变化写到 `CHANGELOG.md` 的 `[Unreleased]`。发版时只在 `src/bili_dl/__init__.py` 手填包版本，再运行：

   ```bash
   uv run python scripts/release.py prepare
   uv run python scripts/release.py check
   ```

   检查归档内容、日期与比较链接，不创建空发布。

2. 运行上面的 lint、格式、mypy、pytest 四项全量检查；格式失败先修正再提交。
3. `uv build` 后精确检查当前版本的 wheel / sdist，执行 `twine check`，验证隔离安装与命令入口，并检查包内容及隐私；不要用含旧版本的 `dist/*` 代替本版产物集合。
4. sdist 采用 `pyproject.toml` 的 include 白名单；只纳入源码、脚本、测试、`docs/` 和列明的根文件。新增目录先核实用途与内容，不能依赖 Git 忽略代替构建边界。Cookie、续期状态、`.env*`、临时目录不得入包。
5. 将本次相关源码、测试、文档（含本文件）及必要锁文件纳入提交，不混入无关改动。提交后先推 main，等待 CI 全绿。
6. 手填一次带注释标签 `git tag -a vX.Y.Z -m "Release"`，推送该标签；Publish 校验标签、包版本和 CHANGELOG 一致。
7. 等 Publish 的测试、PyPI 上传、GitHub Release 三个 job 全绿，从官方 PyPI 实际安装验证。Release 正文由对应 CHANGELOG 自动生成，重跑覆盖同一正文。

本文件不写入 wheel / sdist；开发说明和用户文档随 `docs/` 进入 sdist。发布工作流以 `.github/workflows/publish.yml` 为准。

## 文档维护

README 展示功能、界面和上手流程；`docs/usage.md` 解释使用行为，`docs/development.md` 解释实现约束，`docs/troubleshooting.md` 保存问题证据及复测条件。本文件只保留执行时需要的约定与入口，不复刻源码树、依赖清单、变更日志或历史测试数字。

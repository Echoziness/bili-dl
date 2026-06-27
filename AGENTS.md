# AGENTS.md — bili-dl 项目记忆

> 本文件沉淀"读代码难悟到"的项目事实；与全局规则配合使用。

## 1. 技术栈

- **语言**：Python 3.9+（目标 3.9 兼容，CI 跑 3.9/3.13）
- **构建**：hatchling（`pyproject.toml` 声明，`src/` 布局）
- **运行时依赖**：**零** —— 仅用标准库（`subprocess`、`urllib`、`pathlib`、`ctypes`、`argparse`、`json`、`shutil`）。
- **外部程序依赖**：`yt-dlp`（必需，找不到则报错退出）、`ffmpeg`（可选，缺失则降级跳过音频提取/容器修复）。
- **工具链**：ruff（lint+format）、pytest（测试）。无 mypy 强制。
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
- 永远只从 `cookies_all.txt` 提取含 `bilibili` 的行；其他站点 Cookie 不解析、不存储、不外发。这是项目隐私承诺，任何 PR 不得破坏。
- 位置：`src/bili_dl/cookies.py` 的 `import_bili_cookie()` —— 用列表推导显式过滤 `"bilibili" in line`。
- 测试锚定：`tests/test_cookies.py` 的 `test_extract_drops_other_sites()` 断言 `other_secret` 不泄漏。

### 2.5 nav API 在线校验的降级策略
- **现象**：在线校验 Cookie 需调 `https://api.bilibili.com/x/web-interface/nav`，但网络/SSL 错误时不能因此阻断下载（本地格式可能仍有效）。
- **解法**：`_online_check()` 返回 `True`（已登录）/`False`（未登录）/`None`（网络错误）；`None` 时降级为仅本地格式校验并打印警告，仍返回 True。
- 位置：`src/bili_dl/cookies.py::_online_check()` + `test_cookie_valid()`。

### 2.6 nav API 必须伪装浏览器 User-Agent（412 根因，非 SSL）
- **现象**：Python 版上线后实测每次都走降级（"无法在线验证 Cookie ... 降级为本地格式校验"），而原 bd.ps1 PowerShell 的 `Invoke-RestMethod` 一直能成功显示已登录用户名。曾被误判为本机证书链问题。
- **根因**：B 站 nav API 对 urllib 默认 UA `Python-urllib/3.x` 返回 **HTTP 412 Precondition Failed**（反爬）。`Invoke-RestMethod` 内部默认带 PowerShell/browser UA 故一直成功。代码的 `except Exception` 把 412 也吞进"网络/SSL 错误"分支，导致降级提示与真实根因描述不符——既误导用户也让 SSL 甩锅。
- **解法**：`config.py::USER_AGENT` 放一个固定 Chrome UA 字符串；`cookies.py` 的两处 `urllib.request.Request` header 都加 `"User-Agent": USER_AGENT`。仅 nav 探测用，yt-dlp 下载阶段自带 UA。
- **验证**：实跑 `bili-dl`（无 URL）开头即显示 `[OK] Cookie 有效 | 已登录: <uname>`。
- **启示**：`except Exception` 太宽会把业务级 HTTP 错误（412/403/404）混进"网络问题"分支。后续若想精确分级，可在 `_online_check` 单独 catch `urllib.error.HTTPError` 拿 status，把 412/403 报为"被风控"而非"网络错误"。当前简单加 UA 已解。
- **关联**：用户曾因 `-k` 也无效而怀疑证书；`-k` 只影响 yt-dlp 的 `--no-check-certificate`，**对 urllib 探测无影响**（urllib 默认就校验，且本机 CA 没问题）。下次有人误以为证书，先查 UA。

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

## 3. 项目结构

```
bili-dl/
├── pyproject.toml                 # hatchling + ruff + pytest 配置（单文件）
├── README.md                      # 宣传门面（务必随版本同步功能表）
├── CHANGELOG.md                   # Keep a Changelog 格式
├── LICENSE                        # MIT + 依赖合规说明
├── .python-version                # pyenv/uv 用，固定 3.9（向下兼容基准）
├── .gitignore                     # 含 cookies_*.txt 与媒体文件，防泄密
├── AGENTS.md                      # 本文件
├── src/bili_dl/
│   ├── __init__.py                # __version__
│   ├── __main__.py                # python -m bili_dl
│   ├── cli.py                     # argparse + REPL + main()（主入口）
│   ├── config.py                  # 纯常量，无可变状态
│   ├── paths.py                   # 跨平台路径（Win/macOS/Linux）
│   ├── cookies.py                 # Netscape 提取 + 在线/本地校验
│   ├── ffmpeg.py                  # ffprobe/ffmpeg 探测 + 零损失重封装/提取
│   ├── downloader.py              # yt-dlp 两阶段下载（预测路径 → 实下）
│   └── ui.py                      # ANSI 彩色输出（Win10+ 启用 VT）
├── tests/
│   ├── test_cookies.py            # 隐私核心测试（其他站点不泄漏）
│   ├── test_paths.py              # 跨平台路径分支（mock platform）
│   ├── test_package.py            # 包导入冒烟测试
│   └── data/sample_cookies_all.txt
└── .github/workflows/
    ├── ci.yml                     # Win/macOS/Linux × Py3.9/3.13 矩阵
    └── publish.yml                # push v* tag → 自动构建并发布 PyPI
```

## 4. 关键约定

### 4.1 依赖方向
`cli → downloader → ffmpeg`；`cli → cookies`；`cli → paths → config`；`ffmpeg/downloader → ui/config`。
- `config` 是叶节点（只导出常量），任何模块可依赖它，它不依赖任何内部模块。
- `ui` 也接近叶节点（仅 `mode_label` 懒导入 `config`）。
- 禁止反向依赖或循环导入。

### 4.2 零运行时依赖（硬约束）
- 不可引入 `requests`/`colorama`/`rich` 等三方包。HTTP 用 `urllib.request`，彩色输出用 ANSI + `ctypes`，路径用 `pathlib`。
- 任何"加个依赖更方便"的提案都需先权衡"零依赖"这个卖点。

### 4.3 命名
- Python 模块用 `snake_case`；CLI 旗帜沿袭 Unix 惯例（`--all/-v/-a` 模式、`--proxy`、`-k/--insecure`、`-V/--version`）。短选项占用清单见 §2.10。
- yt-dlp format 串集中放 `config.py`（`FMT_AV`/`FMT_AUDIO`），不散落。

### 4.4 提交规范
- Conventional Commits 中文风格可接受（项目面向中文用户为主），但英文 commit 便于国际贡献者，建议英文。
- 任何改动到 cookies.py / ffmpeg.py 者必须跑 `tests/test_cookies.py` / 相关测试，不得破坏隐私断言。

## 5. 常用命令

```bash
# 开发安装 (uv 管理 venv + editable)
uv venv --python 3.9 .venv
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

- **SteamTools MITM 拦截**：WSL 环境有 SteamTools 在本地做 HTTPS 中间人代理，`api.github.com` 证书 issuer 为 `CN=SteamTools Certificate`（非正规 CA）。影响：
  - `git clone/pull/push` 用 **SSH** 不受影响（已配 `ssh.github.com:443`）。
  - `gh` CLI（Go TLS）不信任 SteamTools CA，所有 API 调用失败。**解法**：GitHub API 操作改用 `curl -k` 绕过证书校验。示例：
    ```bash
    curl -k -X POST https://api.github.com/repos/.../releases -H "Authorization: token $(gh auth token)" ...
    ```
  - `pip install bili-dl`（PyPI→GitHub 不走代理）需加 `--trusted-host pypi.org --trusted-host files.pythonhosted.org`。
  - `git config --global http.sslVerify false` 对 `git` 本身有用，对 `gh`（Go）无效。
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

### 发版流程（当前）
> 任何一步不绿不得进入下一步。

1. 改版本号：`pyproject.toml` + `src/bili_dl/__init__.py`
2. 写 CHANGELOG（Keep a Changelog 格式）
3. 本地全量验证（**三项缺一不可**）：
   ```bash
   uv run ruff check src tests        # 逻辑 lint
   uv run ruff format --check src tests  # 格式检查
   uv run pytest                       # 单元测试
   ```
   - 若 format 报 `Would reformat`，先 `uv run ruff format src tests` 再提交。
4. `git commit -m "release: vX.Y.Z"`
5. `git tag -a vX.Y.Z -m "vX.Y.Z"` → push commit + tag
6. 等 CI 全绿（lint + test 矩阵 + Publish to PyPI）确认无红色
7. `curl -k` 调 GitHub API 创建 Release
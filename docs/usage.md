# 详细使用说明

[返回项目首页](../README.md)

[安装与升级](#安装与升级) · [登录与会话](#登录与会话) · [交互界面](#交互界面) · [字幕](#字幕) · [评论与楼中楼](#评论与楼中楼) · [目录与配置](#目录与配置) · [隐私与文件安全](#隐私与文件安全) · [常见问题](#常见问题)

## 安装与升级

需要 Python 3.11+ 和 pipx：

```bash
pipx install bili-dl
pipx upgrade bili-dl
```

yt-dlp 和 imageio-ffmpeg 是项目依赖，pipx 安装时一起获取。yt-dlp 由同一 Python 环境启动；FFmpeg 默认使用 imageio-ffmpeg 随 wheel 提供的可执行文件，不要求系统 PATH 中另有这两个命令。工具更新由包管理器完成，bili-dl 不自行下载或替换程序。

需要重新解析并安装全部依赖时，可运行 `pipx reinstall bili-dl`，重建它的隔离环境。使用普通虚拟环境时，可运行 `python -m pip install --upgrade --upgrade-strategy eager bili-dl`，同时更新满足项目约束的依赖。

### 系统与架构

imageio-ffmpeg 0.6 系列的内置程序支持如下平台；还需满足 Python 3.11+ 自身的系统要求：

| 系统 | 架构 | 内置 FFmpeg |
| --- | --- | --- |
| Windows | x86-64、x86 32 位 | 提供；32 位构建版本较旧 |
| macOS | Intel x86-64 | 提供；wheel 标记 macOS 10.9+ |
| macOS | Apple Silicon ARM64 | 提供；macOS 11+ |
| Linux | x86-64、ARM64 / aarch64 | 提供；manylinux2014 / glibc 2.17+ |

Windows 原生 ARM64、Alpine / musl、32 位 ARM / Linux 没有对应的内置程序包。在这些平台安装源码包不会自动下载 FFmpeg，可安装系统 FFmpeg 并加入 PATH，或按下面方式指定可执行文件。字幕、评论、登录与账号状态不需要运行媒体工具。

### 指定外部 FFmpeg

查找顺序为 `IMAGEIO_FFMPEG_EXE` > 包内程序 > 当前环境的 Conda FFmpeg > 系统 PATH。找到的同一个程序用于 yt-dlp 合并和 bili-dl 的音频处理。指定路径不需要连同 ffprobe 一起提供。

PowerShell：

```powershell
$env:IMAGEIO_FFMPEG_EXE = 'C:\tools\ffmpeg\bin\ffmpeg.exe'
bili-dl
```

macOS / Linux：

```bash
IMAGEIO_FFMPEG_EXE=/usr/local/bin/ffmpeg bili-dl
```

填写可执行文件路径，不要把参数写进变量。显式指定的程序不存在、无法执行或版本检查超时，将报告 FFmpeg 不可用，不悄悄改用另一个程序；可修正路径或清除变量以恢复自动查找。

媒体任务固定忽略独立 yt-dlp 的配置文件。请通过 bili-dl 的选项和 `config.toml` 配置下载，避免其他程序的后处理设置影响结果。

## 登录与会话

### 扫码登录

```bash
bili-dl login
bili-dl login --url-only
bili-dl --status
```

`login` 建立独立的 B 站 Web 会话，不读取浏览器 Profile 或 Cookie。需要交互终端，无需 yt-dlp 或 ffmpeg。只有显式执行登录或在菜单中选择登录，才会等待扫码。

二维码完整放得下时显示二维码，否则显示同一个确认网址。`--url-only` 跳过绘图，仍会等待手机确认并保存会话。网址通常约 180 秒内有效，在手机上打开后能否唤起 B 站 App 取决于设备与 App；电脑浏览器打开网址不等于完成授权。

进入确认页后切换 App 账号，可能一直停在“已扫码未确认”。此时取消并重新运行 `login`，为新账号生成新的二维码。确认网址包含临时登录信息，只在自己控制的设备上打开。

扫码成功后先用 B 站 `nav` 接口验证新会话，再原子替换 `cookies_bilibili.txt`。验证失败时保留原 Cookie。具备续期条件时，将续期凭证及会话指纹保存在同目录的 `auth_state.json`。

### 导入已有 Cookie

在浏览器中登录 B 站，导出 **Netscape 格式**的 Cookie 文本，将 `.txt` 文件放入[会话目录](#目录与配置)，文件名任意。下次任务会自动识别并提取 B 站条目，其他站点条目不进入输出文件。

典型 Cookie 行由 Tab 分隔：

```text
# Netscape HTTP Cookie File
.bilibili.com	TRUE	/	FALSE	0	SESSDATA	<session>
.bilibili.com	TRUE	/	FALSE	0	bili_jct	<csrf>
```

可用 `--cookie-dir DIR` 指定另一会话目录。导入的会话可用于下载，但不一定具备自动续期所需的凭证。

### 续期与状态

下载前先验证 Cookie；每 UTC 日首次下载至多查询一次 B 站是否要求续期。只有服务端要求时才刷新。新 Cookie 验证、保存后再确认旧续期凭证；确认失败会保存待确认状态，下次下载前重试。

续期凭证绑定当前 Cookie 的单向 SHA-256 指纹。导入或更换 Cookie 后不会误用另一会话的旧凭证。扫码落盘与续期事务使用跨进程锁；下载遇到锁竞争时跳过本次续期，继续使用已有有效 Cookie。

网络或续期协议失败会保留当前有效会话并告警；会话被撤销后需重新登录。自动续期不保证永久登录。

`--status` 只读查询登录账号、B 站当前是否要求续期、自动续期是否启用，不下载、不续期、不生成二维码，也不预测下次续期时间。

## 交互界面

运行 `bili-dl` 进入菜单；打开首页不需要登录、联网或安装外部下载器。

| 操作 | 按键或方式 |
| --- | --- |
| 选择 / 确认 | 方向键 / Enter |
| 取消当前操作并回首页 | Ctrl+C |
| 首页退出 | Ctrl+C 或选择“退出” |
| 查看超出当前页面的内容 | PgUp / PgDn |
| 输入路径 | 支持补全，含空格的路径可直接输入 |

终端缩放或改变窗口尺寸后，页面和输入框一起重排，保留已输入内容和选中项。信息页等待明确返回；导航切换当前页，显示所在位置。`TERM=dumb` 或 stderr 重定向时使用数字菜单，输入 `q` 返回；EOF 干净退出。

执行前显示任务摘要，可临时调整目录、模式、代理等。任务中的调整仅作用于当前任务；首页设置作用于本次交互会话。主动导出才会新建 TOML，导出不覆盖已有文件、不保存代理认证信息。导出到默认 `config.toml` 时下次启动自动读取，其他位置用 `--config FILE` 指定。输入历史不持久保存。

音视频下载保留预查询和 yt-dlp 原生进度；下载阶段停止界面刷新，将终端交给子进程。音视频流可能分别达到 100%，随后仍需合并、提取或整理容器。失败或有警告时保留原始下载诊断，直至离开结果页。批量任务可仅重试失败项。

评论显示实际获取条数和页数，不用可能包含子回复的接口总数计算百分比。`--no-color` 或非空 `NO_COLOR` 禁用颜色。

## 字幕

```bash
bili-dl -s "https://www.bilibili.com/video/BV1Got26ZE5K/"
bili-dl -s "https://www.bilibili.com/video/BV1Got26ZE5K/?p=2" --output-dir ./subtitles
bili-dl -s --batch-file urls.txt --output-dir ./subtitles
```

交互菜单中选择下载后选择“字幕”，或用 `mode = "s"` 配置默认模式。字幕复用登录、续期、代理与视频输出目录，无需 yt-dlp 或 ffmpeg。

- 支持普通投稿 BV / AV 链接、裸 BV / AV 号和 `b23.tv` 短链；不支持番剧、课程链接。
- 按接口顺序只取第一条轨道，不排序、不选择语言、不回退到后续轨道，不做语音识别。
- `?p=N` 指定分 P，默认 P1。每句保留起止时间，精确到毫秒。
- 输出名如 `标题 [BV…] p1.ai-zh.srt`。文件名清理跨平台非法字符，并截短过长标题。
- SRT 在所有平台使用 **UTF-8 BOM + CRLF**。Python 读取时可用 `encoding="utf-8-sig"`。
- 无字幕或请求失败时返回失败；完整写入后才替换旧文件。

字幕 API 始终校验 TLS；短链与字幕 CDN 请求不携带登录 Cookie。`-k` 仅影响 yt-dlp。

## 评论与楼中楼

```bash
# 全部可获取的主评论，默认最新排序
bili-dl comments BV1Got26ZE5K

# 热门排序，最多 100 条主评论
bili-dl comments BV1Got26ZE5K --sort hot --limit 100

# 一条主评论及其回复，最多 50 条，包含主评论本身
bili-dl replies BV1cSec6tEux 317745878352 --limit 50

# 保留原始字段
bili-dl comments BV1Got26ZE5K --full
```

两种命令均支持普通视频链接、裸 BV / AV 号及 `b23.tv`，复用登录、续期和代理，不调用 yt-dlp / ffmpeg。`ROOT_ID` 来自主评论导出 JSON 的 `comments` 数组中主评论的 `rpid_str`。

### 范围与排序

主评论默认 `newest`，置顶优先并计入上限；`hot` 使用服务端热门顺序。`--limit N` 为正整数，限制去重后的记录数；楼中楼包含根评论，因此 `--limit 50` 至多保存主评论及 49 条回复。

“全部”表示本次运行当前账号可枚举的内容，不保证与网页总数一致。B 站主评论总数可能包含子回复，删除、审核和下载期间新增也会影响数字。

热门页可能返回相同 offset 却继续推进内容，因此同一会话串行获取，不能只靠 offset 恢复。热门传输失败时停止，避免重放已推进的请求而漏页。最新排序和楼中楼数字页对暂态网络、429、5xx 做有限重试；每页 WBI 签名被拒绝时至多重新获取一次密钥。

### 文件与字段

输出为 UTF-8 JSON（`schema_version: 2`），包含视频元数据、请求、评论数组和最终结果；每条评论在文件中独占一行。

| 输出 | 文件名示例 |
| --- | --- |
| 最新主评论 | `标题 [BV…].comments.json` |
| 热门主评论 | `标题 [BV…].comments.hot.json` |
| 楼中楼 | `标题 [BV…].comment-ROOT_ID.json` |

默认保留便于阅读与处理的字段：

- `rpid_str`、`root_str`、`parent_str`：字符串 ID，避免大整数精度损失。
- `ctime`、`time`：时间戳及本地时区可读时间。
- `like`、`rcount`：点赞和回复数。
- 作者 `mid`、`uname`、可用时的等级与认证信息。
- 正文、图片网址，以及可用时的 `up_liked`、`location`、`pinned`。

`--full` 保留原始评论对象；两种模式都去掉内嵌 `replies` 预览，避免已保存条数与上限语义冲突。头像、装扮等协议展示数据在默认模式中不保存。处理结果可通过 `request.fields` 的 `lean` / `full` 区分。

### 完整性与中断

只有主评论 API 明确结束或楼中楼返回空页时，`result.complete` 才为真。短页、数量达到服务端 count 都不单独证明完成。到达上限时为 `stopped_reason = "limit"`、`complete = false`。文件还记录任务起止时间和首次、末次接口总数，便于理解实时快照。

完整写入后才原子替换旧文件。失败或 Ctrl+C 保留旧文件并丢弃本次临时数据；命令行 Ctrl+C 返回 130。当前不支持续传。

## 目录与配置

以下路径保留符号占位，不展开当前机器用户名。

| 系统 | 配置与会话 | 视频 / 字幕 / 评论 | 音频 |
| --- | --- | --- | --- |
| Windows | `%APPDATA%\bili-dl` | `%USERPROFILE%\Videos\bilibili_videos` | `%USERPROFILE%\Music\bilibili_audio` |
| macOS | `~/Library/Application Support/bili-dl` | `~/Movies/bilibili_videos` | `~/Music/bilibili_audio` |
| Linux | `$XDG_CONFIG_HOME/bili-dl` | `$XDG_DOWNLOAD_DIR/bilibili_videos` | `$XDG_DOWNLOAD_DIR/bilibili_audio` |

Linux 未设置 `XDG_CONFIG_HOME` 时使用 `~/.config`，未设置 `XDG_DOWNLOAD_DIR` 时使用 `~/Downloads`。当前读取环境变量，不读取 `user-dirs.dirs`；会话文件与配置放在同一目录。Windows 不查询重定向的 Known Folders，重定向目录请显式指定。

### 默认配置

在配置目录自行创建 `config.toml`，或通过交互菜单导出。所有字段可选：

```toml
mode = "a"                      # all / v / a / s
proxy = ""                      # 明确直连；未配置时读取环境代理
insecure = false                 # 仅影响 yt-dlp 的证书校验
video_dir = "./downloads/video"  # 同时保存字幕和评论
audio_dir = "./downloads/audio"
cookie_dir = "./session"
```

相对路径以执行命令时的工作目录为基准；Windows 路径可用正斜杠，或用 TOML 单引号字符串保留反斜杠。手写配置路径不会自动展开 `~` 或环境变量，请填写实际路径。交互路径输入支持展开 `~` 和环境变量。

命令行参数覆盖配置，未配置项使用内置默认值。代理按以下顺序解析一次，并显式传给下载及 B 站 API：

```text
--proxy > config.toml 的 proxy > HTTPS_PROXY > https_proxy > HTTP_PROXY > http_proxy > 直连
```

空字符串是明确禁用环境代理。程序不发现、测试或管理代理。`-k/--insecure` 只跳过 yt-dlp 的 TLS 校验，登录、续期、字幕和评论 API 始终使用默认校验。

### 自定义位置

```bash
bili-dl -a URL --audio-dir ./audio
bili-dl comments BV1Got26ZE5K --output-dir ./comments
bili-dl login --config my.toml
bili-dl --config my.toml --status
```

子命令选项放在子命令之后。`--cookie-dir` 仅改变会话目录，不移动配置文件；配置位置由 `--config FILE` 指定。`login`、状态查询与下载共用同一份配置解析规则。

### 批量文件

```text
# 每行一个链接，UTF-8
https://www.bilibili.com/video/BV1Got26ZE5K/
https://www.bilibili.com/video/BV1cSec6tEux/
```

```bash
bili-dl --batch-file urls.txt
bili-dl -s --batch-file urls.txt --output-dir ./subtitles
```

忽略空行和以 `#` 开头的行，按当前模式顺序执行，不并发。命令行全部成功返回 0，任一失败返回 1。

## 隐私与文件安全

- Cookie 导入仅提取 B 站条目，其他站点条目不保存或外发。导入替换已有输出前会尝试备份原 Cookie。
- 扫码、会话校验与续期只经 B 站相关接口，不读取浏览器资料，无遥测。音视频由 yt-dlp 访问输入链接及其下载地址；字幕和评论使用 B 站内容 / CDN 接口。
- `cookies_bilibili.txt` 是登录凭证；`auth_state.json` 含续期凭证、会话指纹，必要时还含等待确认的旧凭证。不要公开分享这些文件。
- 续期状态及临时文件在 POSIX 上使用 `0600`；项目忽略 Cookie、续期状态和锁文件。`.auth_state.lock` 不含凭证，用于协调会话事务。
- 交互配置导出不保存代理认证信息，不覆盖现有 TOML；下载结果、配置导出和会话文件各自遵循相应写入规则。

## 常见问题

### Windows 下载成功，提取音频却失败

如果开启 Windows Defender“受控文件夹访问”，未获允许的 ffmpeg 可能无法写入视频或音乐库，错误可能表现为 `No such file or directory` 或 `Bad file descriptor`。

确认拦截后，在 **Windows 安全中心 → 病毒和威胁防护 → 勒索软件防护 → 受控文件夹访问 → 允许应用** 中添加需要的可执行文件。默认 FFmpeg 位于 pipx 环境的 `site-packages/imageio_ffmpeg/binaries/` 中；用 `pipx runpip bili-dl show imageio-ffmpeg` 查看安装位置，再在 `binaries` 中找到程序。使用外部 FFmpeg 时放行实际指定的程序；升级后路径变化时需重新检查。

### 进度已到 100%，为什么还在工作

yt-dlp 可能分别下载音频和视频，之后还需合并、提取音频或整理容器。单条流的 100% 不代表整个任务结束，以最终结果页或命令退出为准。

### 能否下载整个合集或跳过已有文件

媒体下载固定使用 `--no-playlist`，不自动遍历合集、收藏夹或多分 P；请分别提供各分 P 链接或写入批量文件。当前没有已完成文件跳过或续传的产品选项；重新运行时可能下载或替换已有产物，请按目录安排保留需要的版本。

旧式分段 FLV 可能把单个链接展开为多个输出目标。此类任务会在预查询后停止，不开始下载；批量文件中的其他链接仍按顺序处理。

### 如何报告问题

附上版本（`bili-dl -V`）、操作系统、所执行命令及错误信息，前往 [Issues](https://github.com/Echoziness/bili-dl/issues)。分享前去掉 Cookie、续期凭证、带账号密码的代理地址和个人路径。

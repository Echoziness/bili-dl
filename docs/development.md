# 开发说明

[项目首页](../README.md) · [使用指南](usage.md) · [排障记录](troubleshooting.md)

## 职责与依赖

| 层 | 入口 | 职责 |
| --- | --- | --- |
| 命令行前端 | `cli.py`、`ui.py` | 解析、配置合并、命令分派、消息展示 |
| 交互前端 | `interactive.py`、`terminal.py` | 任务流程、表单、页面与终端控制 |
| 共用操作 | `actions.py`、`models.py` | 已解析选项、下载与认证操作、结构化结果 |
| 内容后端 | `downloader.py`、`media.py`、`subtitles.py`、`comments.py` | 下载分派、处理与文件事务 |
| 协议与会话 | `commentapi.py`、`wbi.py`、`authqr.py`、`authrefresh.py`、`cookiestore.py`、`authstate.py`、`cookiesource.py` | HTTP 协议、分页、Cookie 与续期事务 |
| 基础设施 | `transport.py`、`paths.py`、`settings.py`、`config.py`、`ffmpeg.py` | 传输、路径、配置、常量与外部程序封装 |

前端调用共用操作和领域编排；业务模块不反向导入前端。它们可以执行 HTTP、文件或进程操作，但不直接输出界面消息。`messages` 由调用方展示，阶段与计数通过显式回调报告。不要把控制器中的业务过程复制成第二套交互实现。

`Options` 保存解析后的会话配置，`DownloadConfig` 保存下载参数。Cookie 的“校验 → 导入 → 再校验”封装在 `ensure_cookie()`，前端只判断结果；`actions` 共用扫码、状态、媒体和评论操作。

## 终端与帮助

交互入口惰性加载 UI；帮助格式化按需加载 Rich 做显示宽度换行。`-v` 为视频模式，版本使用 `-V`。主命令、login、comments、replies 的帮助遵循相同层次，不在构建帮助时读取个人目录或联网。

`Terminal` 缓存页面语义内容，在输入时把正文和 Questionary 控件放进同一个 prompt_toolkit 布局。尺寸变化只重排现有 buffer / 控件；长正文可翻页，不重建正在编辑的输入。等待、扫码阶段由 Rich Live 全屏展示；完整二维码放不下时回退确认网址。

下载阶段先停止 Live，再让 yt-dlp 继承 stdout / stderr。不要把原生进度文本当作可解析协议。失败或有警告的结果页保留未捕获诊断，并使用普通输入控件，避免全屏界面遮住它们。

CLI 消息使用 stderr，帮助与版本使用 stdout；yt-dlp 保留自己的原生输出。顶层未预期异常当前会将 traceback 与版本 / Python / 平台信息写到 stderr，保留诊断依据。

## 媒体与编码

yt-dlp 是 Python 依赖，命令固定为当前 `sys.executable -m yt_dlp`，不查找 PATH 中的独立启动器。FFmpeg 由 `ffmpeg.find_ffmpeg()` 惰性解析：显式 `IMAGEIO_FFMPEG_EXE`（须通过版本检查）优先，否则由 imageio-ffmpeg 选择包内程序、Conda 或 PATH 回退。返回实际程序路径，同一任务共用于 yt-dlp 的 `--ffmpeg-location` 和项目音频处理；CLI / 交互批量任务只解析一次。

两个 yt-dlp 调用都传 `--ignore-config`，下载设置由 bili-dl 自己管理。预查询必须同时给 yt-dlp 传 `--encoding utf-8`、给父进程设置 `encoding="utf-8"`；两端是同一个机器协议。下载阶段不强制改用户终端编码。两个调用继承环境副本，但移除 `PYTHONHOME`，让子进程按自己的 Python 环境解析标准库。

预查询以单行可用 stdout 路径为准；多行（含重复路径）表示当前不支持的多个输出目标，在下载前失败，避免旧式分段 FLV 同名冲突。非零退出码但有单一路径时告警。下载阶段以目标文件是否存在判断结果，非零退出码但文件存在时告警并继续后处理。这是当前约定，不代表仅凭退出码或文件存在能证明任意外部任务完整。

音频修复通过 `ffmpeg -c:a copy -movflags +faststart` 无损重封装；直接音频下载与从视频提取音频都要覆盖。运行时不依赖 ffprobe；如开发者另行安装了 ffprobe，可用以下命令辅助诊断：

```bash
ffprobe -v error -show_entries format=format_name:format_tags=major_brand,compatible_brands path.m4a
```

需要更细检查时查看 ffmpeg trace 中的 `moov` / `mdat` 顺序。不要把某次样本的精确 atom offset 写成通用不变量。

项目捕获的 FFmpeg 诊断显式按 UTF-8 解码，并替换非法字节，避免 Windows CP936 环境下损坏媒体的 Unicode 路径导致诊断读取异常；这不改变终端编码或 yt-dlp 原生输出。

SRT 固定 UTF-8 BOM + CRLF，原子替换；测试检查原始字节。媒体文件预测、终端编码与仓库行尾是三个边界，分别处理。

## Cookie 与续期事务

下载有效和自动续期可用是两件事。下载需要有效 `SESSDATA`；续期还需要同一会话的 `bili_jct`、refresh token 及匹配的单向指纹。已有会话在线验证失败可告警后使用本地格式；服务端明确未登录仍失败。新扫码 / 刷新 Cookie 则必须在线验证成功才能替换现有会话。

扫码落盘和完整续期过程由会话目录 `.auth_state.lock` 串行化。扫码遇锁竞争明确失败；下载遇竞争跳过本次续期。凭证存储及临时文件须忽略版本跟踪；扫码 Cookie、续期状态及其临时文件在 POSIX 上使用 `0600`。

续期在已校验 Cookie 的下载路径中触发，每 UTC 日首次检查 B 站 `cookie/info`，有要求才执行 `correspond → refresh → confirm`。有待确认事务时先重试确认，再决定是否进行当日查询：

1. 验证续期状态与当前 Cookie 指纹、CSRF 条件匹配。
2. 获取新 Cookie，在线验证并持久化。
3. 保存新 refresh token、指纹以及待确认旧 token。
4. 向 B 站确认旧 token；成功后清除待确认标记，失败则保留待重试状态。

写续期状态失败时清理旧状态并告警，不能把旧凭证绑定到已更换的 Cookie，也不能把 Cookie 已可下载的事实报成完全失败。`--status` 只查询登录与续期要求，不执行这段事务。

## 传输与代理

认证及内容 HTTP 统一经过 `transport.py`，共用浏览器 UA、Referer、超时、gzip 解压和结构化失败。`HttpFailure` 不保留响应正文、凭证或签名 URL；HTTP 状态、网络失败、非 JSON / 异常数据须能区分。

`actions.resolve_proxy()` 按 CLI > config > 大小写 HTTPS_PROXY / HTTP_PROXY > 直连解析，前端将结果显式传下去。`None` 只供库调用沿用 urllib 环境；`""` 明确禁用，既要使用 `ProxyHandler({})`，也要给 yt-dlp 传 `--proxy ""`。仅在配置合并时保留空值还不够。

`-k` 只属于 yt-dlp。认证、字幕和评论 API 不接收关闭 TLS 校验的选项；短链解析和字幕 CDN 不携带登录 Cookie。

## WBI 与评论

`wbi.py` 只负责提取、校验密钥和签名，不管理 HTTP 或分页。按键排序，值移除 `!'()*`，使用等价于 `encodeURIComponent` 的编码（空格为 `%20`）。签名不改变调用方参数；密钥字符保持原样，包括大小写。无效 URL / 密钥归一为安全失败。

`CommentClient` 管理任务内密钥；每页 `-403` 至多重新获取一次。主评论走 WBI main，`newest` 为 mode 2，`hot` 为 mode 3；楼中楼走 root + pn + ps。热门页 offset 不是可靠的恢复令牌，失败不自动重放；最新和数字页允许有限暂态重试。

分页需校验 ID、归属、页码与列表；不把去重、上限和完成判定分散到前端。主评论 API 明确 `is_end`、楼中楼空页才证明完成；动态 count、短页或无新增 ID 不等价于正常结束。

`commentapi._comment()` 先完成协议校验，再按 lean / full 处理字段。两者均去掉内嵌 replies；仅 lean 内联 `pinned`。`comments.py` 负责去重、限制、进度和原子文件事务，内存只保留 ID 集合。输出契约见[使用指南](usage.md#评论与楼中楼)。

## 路径、测试与文档

路径由 `paths.py` 统一处理：Windows 使用用户目录与 APPDATA，macOS 使用 Movies / Music 与 Application Support；Linux 读取 XDG_CONFIG_HOME、XDG_DOWNLOAD_DIR，未设置时回退。当前不读取 `user-dirs.dirs`，不查询 Windows 重定向 Known Folders。配置文件与会话目录可独立指定。

手工配置中的路径不会自动展开 `~` / 环境变量；交互输入会展开。`settings.export()` 只创建新文件，不能用重写 TOML 的方式丢失用户注释。

测试入口按领域使用 `tests/test_media.py`、`test_terminal.py`、`test_interactive.py`、`test_cookiestore.py`、`test_transport.py` 等。网络协议由 mock 提供确定响应；真实终端、登录或下载的验证范围需单独说明。覆盖率按 CI 的源码 include 统计，不把含测试代码的总覆盖率当业务覆盖率。

`tests/test_media_integration.py`（`pytest -m media`）使用内置 FFmpeg 生成有效 H.264 / AAC 样本，通过 loopback HTTP 交给真实 yt-dlp，清空 PATH 后验证 DASH 合并、视频、音频和无损容器修复。验证完整解码、轨道和音频 faststart；不请求 B 站或使用真实 Cookie。三系统 CI 的测试矩阵执行这些用例。

README 保持展示与上手重点；详细行为在 `docs/usage.md`。菜单图从真实程序导出，使用不含账号或个人路径的画面。公开文档的图片与文档链接兼顾 GitHub / PyPI，发布前检查当前本地文件存在，并在构建后确认 sdist 包含 `docs/`。

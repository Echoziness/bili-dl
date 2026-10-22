# 开发排障记录

整理于 2026-10-20。以下是原项目记录中仍有定位价值的案例；未注明实测日期的案例只保留历史版本背景，不视为当前环境结论。源码已修复的问题由回归测试约束；本次整理未重新触发历史网络或系统故障。

[开发说明](development.md) · [面向使用者的常见问题](usage.md#常见问题)

## FFmpeg 失败时 Unicode 路径触发解码异常

- **范围与现象**：2026-10-22，Windows CP936、Python 3.11.16、imageio-ffmpeg 0.6.0 的 FFmpeg 7.1；修复名含中文 / emoji 的损坏 M4A 时，原文件保留，但诊断读取线程抛出 `UnicodeDecodeError`，后续访问 stderr 又触发异常。
- **已确认原因**：FFmpeg 写入 UTF-8 诊断，`subprocess.run(text=True)` 默认按系统 GBK 解码。
- **有效修复**：仅给捕获 FFmpeg 输出的调用设置 `encoding="utf-8", errors="replace"`，保留结构化失败与原文件，不改用户 stdio 或全局环境。
- **验证入口**：`tests/test_ffmpeg.py::test_captured_diagnostics_handle_unicode_and_invalid_bytes`、`tests/test_media_integration.py::test_invalid_unicode_audio_keeps_original_and_returns_error`；包含有效 UTF-8、非法字节和真实损坏文件。

## 预查询文件名丢失 Unicode 字符

- **范围与现象**：v0.4.1 修正，2026-09 的 Windows CP936 样本。标题中的 `⧸`（U+29F8）在预测输出中丢失，实际文件系统保留；因此下载成功却因预测路径不匹配报失败。
- **已确认原因**：父进程按 UTF-8 解码不足以约定子进程编码；旧有“GBK 外字符两侧会一致丢失”的假设不成立。
- **有效修复**：预查询发送方 `--encoding utf-8`、接收方 `encoding="utf-8"` 同时设置，保留普通下载与用户终端编码。
- **验证入口**：`tests/test_media.py::test_download_predict_uses_utf8_for_non_cp936_filename`；重新验证时使用 GBK 外字符标题，比较预测路径与磁盘文件名。

## 外部 yt-dlp 启动时报 SRE module mismatch

- **范围与现象**：v0.4.1 修正；原开发环境中，`uv run` 下失败，而同一外部 yt-dlp 在普通终端正常。
- **已确认原因**：项目解释器的 `PYTHONHOME` 被外部启动器继承，Python 3.14 加载了 3.11 标准库，尚未处理下载参数就崩溃。
- **有效修复**：仅在两个 yt-dlp 子进程的环境副本中移除 `PYTHONHOME`，保留 PATH、代理和其他变量，不修改全局环境。
- **验证入口**：`tests/test_media.py::test_download_child_resolves_its_own_pythonhome`。当前改为同一环境的 `python -m yt_dlp`，继续验证子进程移除 `PYTHONHOME`；历史故障涉及不同解释器绑定。

## nav 在线校验总是降级或返回 HTTP 412

- **范围与现象**：早期 Python 实现，原记录未注明实测日期；默认 urllib UA 对 nav 返回 HTTP 412，被宽泛异常处理混成网络 / SSL 故障。
- **已确认原因与修复**：使用浏览器 UA，并区分 HTTP 状态与网络失败。当前请求头集中在 `transport.request()`，`_nav_probe()` 返回结构化失败，不能沿用已移除的旧结果类型。
- **验证入口**：`tests/test_cookiestore.py` 的 HTTP / network 降级测试。真实复测先看状态码、UA 和代理，不根据同一条降级消息推断证书链故障。

## 续期 HTML 返回成功却找不到口令

- **范围与现象**：v0.4.1 修正；`correspond` 返回 HTTP 200，但未找到 HTML 中的口令节点。
- **已确认原因**：正文带 gzip 魔数，服务端即使收到 identity 请求也可能返回 gzip；urllib 不会自动解压。
- **有效修复**：在统一传输层先按 Content-Encoding 解压，再解析文本或 JSON。损坏 / 未知编码返回安全失败，而不是在续期模块局部绕过。
- **验证入口**：`tests/test_transport.py` 的 gzip JSON、gzip HTML、损坏 gzip 测试。原失败发生在 refresh POST 前，没有消耗续期凭证。

## 配置显式直连仍使用环境代理

- **范围与现象**：v0.2.9 起修正，后补齐子进程路径；`proxy = ""` 被 `or None` 归一，或未传到 yt-dlp，环境代理再次生效。
- **已确认原因与修复**：未配置和明确空值语义不同。解析保留空串，urllib 与 yt-dlp 均显式收到直连设置。
- **验证入口**：`tests/test_settings.py`、`test_cli.py`、`test_media.py`、`test_transport.py` 的代理测试。复测同时覆盖环境中存在代理的条件。

## ffmpeg 写文件失败但目录看起来可写

- **范围与现象**：v0.3.0 原 Windows 环境；视频下载后提取音频报 `Bad file descriptor`、`No such file or directory`，伴随编码及空值的连锁错误。
- **已确认原因**：当次 Windows Defender 受控文件夹访问拦截未获允许的 ffmpeg；拦截日志事件 1123 记录真实可执行路径。Scoop `current` junction 入口与该路径可能不同。
- **有效处理**：确认事件证据后由使用者放行需要的真实程序路径；升级后重新检查。不能把这些错误码一概解释为 CFA，也不因单机拦截更改产品默认目录。
- **验证入口**：ffmpeg 的 CFA 提示测试；系统诊断可读 `Microsoft-Windows-Windows Defender/Operational` 的 1123 事件，再用同一可执行文件和目标目录复测。代码提示要求父目录存在、可写且 CFA 开启，但仍只是提示。

## Git 状态显示修改而内容 diff 为空

- **范围与现象**：v0.3.0 原 Windows 工作区；CRLF / LF 转换后状态与内容比较不一致。
- **原记录解释**：autocrlf 与工作区 / index 的行尾、stat 缓存不一致。当前仓库已用 `.gitattributes` 声明 `*.py text eol=lf`。
- **复测条件**：分别检查属性、原始字节与实际内容差异；不要为清除状态而覆盖未提交工作。进程编码问题与文件行尾问题分开定位。

## Python 3.14 在添加 help 参数时崩溃

- **范围与现象**：v0.4.5 修正；help 中的裸 `%` 触发 `badly formed help string`。
- **已确认原因**：argparse 把参数 help 当作插值格式串；Python 3.14 在添加参数阶段提前校验，旧版本通常在格式化帮助时才暴露。
- **有效修复**：如需字面百分号使用 `%%`。四个 parser 都实际调用 `format_help()`，而不是只测参数解析。当前 CI 还包含额外的新版本行为检查，具体矩阵见工作流。
- **验证入口**：`tests/test_cli.py::test_every_parser_formats_help_without_crashing`。

## 本地 sdist 混入未跟踪临时目录

- **范围与现象**：v0.4.0 修正；本地构建曾包含 output / tmp 中的研究材料和第三方文件，包括 `.env*`。Git 未跟踪不能保证构建排除。
- **有效修复**：Hatch sdist 使用显式 include 白名单；wheel 只包含 `src/bili_dl`。发布前核实当前版本的两种归档，不扫描错旧版文件。
- **复测条件**：检查归档路径、符号链接及凭证模式，并确认合法文档存在。不要通过复用真实凭证来测试泄露防护。

## 字幕播放器乱码或整段显示

- **证据范围**：2026-10-15，使用者确认 UTF-8 BOM + CRLF 组合解决本机微软播放器案例；播放器版本未记录，两个因素没有独立隔离验证。
- **当前约定**：所有平台输出同样的 SRT 字节格式，测试检查 BOM 和 CRLF。不能据此宣称所有播放器均已实测兼容。
- **复测条件**：保留原始字节与具体播放器版本；读取文件时注意文本模式会归一化换行。

## 交互页面缩放后错位或丢失上下文

- **证据范围**：2026-10-20，0.5.0 本地实现，Windows ConPTY。旧实现分别绘制静态 Rich 正文与 Questionary 输入，且只在启动时判断尺寸。
- **有效修复**：输入页面由同一布局管理，按实时行列数重排；等待状态单独由 Live 管理，下载阶段交回原生终端。
- **验证范围**：实际改变 120×36、96×32、42×14、38×12、30×10 的终端尺寸，检查选中项、部分输入、帮助翻页和返回；没有由该项实测推断 Linux / macOS 的真实终端表现。
- **回归入口**：`tests/test_terminal.py::test_full_page_keeps_text_during_live_resize`；真实验证另查当前页面，而非仅在历史输出里搜索提示文字。

## 网络与工具链的旧环境记录

原记录中，某 WSL 开发环境曾因 SteamTools 中间人代理导致 GitHub TLS 失败，另有 SSH 22 连接受限、镜像发布延迟。这些记录未注明日期，本次未复测，不能当作仓库或其他机器的默认网络状态。

再次遇到问题时记录当时的代理、证书 issuer、端点、错误和工具版本；检查仓库实际 remote 与工具所在环境。不要沿用旧文档中的关闭 TLS 校验或添加 trusted-host 作为默认解决办法。项目开发工具由 `uv sync` 安装到 `.venv`，避免 `uv run` 意外找到全局旧工具。

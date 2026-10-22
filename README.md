# bili-dl

B 站视频、音频、字幕与评论下载工具。支持 Windows、macOS、Linux，提供中文交互菜单和命令行两种用法。

[![CI](https://github.com/Echoziness/bili-dl/actions/workflows/ci.yml/badge.svg)](https://github.com/Echoziness/bili-dl/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/bili-dl)](https://pypi.org/project/bili-dl/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/Echoziness/bili-dl/blob/main/LICENSE)

![bili-dl 交互菜单：下载、评论、批量任务、登录和设置](https://raw.githubusercontent.com/Echoziness/bili-dl/main/docs/assets/interactive.png)

运行 `bili-dl` 打开菜单。选择任务、粘贴链接、确认设置，即可开始；也可直接用命令行执行。

[安装](#安装) · [快速上手](#快速上手) · [常用命令](#常用命令) · [文件与配置](#文件与配置) · [详细使用说明](https://github.com/Echoziness/bili-dl/blob/main/docs/usage.md)

## 能做什么

| 内容 | 保存结果 | 特点 |
| --- | --- | --- |
| 视频 | 带声音的视频 | 按账号权限获取可用画质；可额外提取独立音频 |
| 音频 | 优先 M4A | 无损整理容器，改善 foobar2000 等播放器的起播体验 |
| 字幕 | 带时间戳的 SRT | 直接获取已有字幕，无需下载视频 |
| 主评论 / 楼中楼 | UTF-8 JSON | 主评论可排序；支持数量上限与精简 / 原始字段 |

**交互操作**覆盖下载、评论、批量任务、登录和设置。页面随终端尺寸重排，执行前展示任务摘要；批量失败时可只重试失败项。

**扫码登录**通过 B 站 App 建立独立会话，下载前按需检查续期。音视频下载保留 **yt-dlp 原生进度**，显示下载速度和剩余时间。

## 安装

需要 Python 3.11+。使用已安装的 pipx 安装到独立环境：

```bash
pipx install bili-dl
bili-dl -V
```

没有 pipx 时，可在自己的 Python 虚拟环境中运行 `python -m pip install bili-dl`。

**yt-dlp 和 FFmpeg 随依赖自动安装**，常见 Windows、macOS、Linux 平台无需另行下载或配置 PATH。FFmpeg 由 imageio-ffmpeg 的平台安装包提供，安装完成后运行时无需再下载工具。

macOS 支持 Intel / Apple Silicon，Linux 支持 glibc 2.17+ 的 x86-64 / ARM64，Windows 提供 x86-64 / x86 包。Windows 原生 ARM64、Alpine 等未提供对应内置程序的平台可使用系统 FFmpeg；详见[安装与升级](https://github.com/Echoziness/bili-dl/blob/main/docs/usage.md#安装与升级)。

## 快速上手

**1. 打开交互菜单**

```bash
bili-dl
```

选择“登录与账号状态 → 扫码登录”，用 B 站 App 扫码并确认。也可直接运行 `bili-dl login`。会话保存后可供后续任务复用。

**2. 选择要保存的内容**

在首页选择下载、主评论、楼中楼或批量任务。交互界面支持视频链接、BV / AV 号及分享文本；楼中楼需要主评论的 `rpid_str`。

**3. 确认任务并开始**

开始前可检查保存内容、目录和网络设置。执行结束后显示结果文件；下载中按 Ctrl+C 取消并返回首页，在首页按 Ctrl+C 退出。

方向键选择，Enter 确认；小窗口中可用 PgUp / PgDn 查看较长内容。设置默认只影响当前会话，需主动导出配置才能在下次启动时复用。

## 常用命令

下面的 `URL` 替换为视频链接。命令行同样复用登录状态与配置。

| 任务 | 命令 |
| --- | --- |
| 视频 + 独立音频（内置默认） | `bili-dl URL` |
| 仅视频，保留声音 | `bili-dl -v URL` |
| 仅音频 | `bili-dl -a URL` |
| 仅字幕 | `bili-dl -s URL` |
| 最多 100 条主评论 | `bili-dl comments URL --limit 100` |
| 热门主评论 | `bili-dl comments URL --sort hot` |
| 一条主评论及其楼中楼 | `bili-dl replies URL ROOT_ID` |
| 批量下载 | `bili-dl --batch-file urls.txt` |
| 查询登录与续期状态 | `bili-dl --status` |

批量文件使用 UTF-8，每行一个链接；空行和 `#` 注释会被跳过。任务顺序执行。

```bash
bili-dl --help
bili-dl login --help
bili-dl comments --help
bili-dl replies --help
```

## 文件与配置

视频、字幕和评论共用输出目录，音频使用独立目录：

| 系统 | 视频 / 字幕 / 评论 | 音频 |
| --- | --- | --- |
| Windows | `%USERPROFILE%\Videos\bilibili_videos` | `%USERPROFILE%\Music\bilibili_audio` |
| macOS | `~/Movies/bilibili_videos` | `~/Music/bilibili_audio` |
| Linux | `$XDG_DOWNLOAD_DIR/bilibili_videos` | `$XDG_DOWNLOAD_DIR/bilibili_audio` |

Linux 未设置 `XDG_DOWNLOAD_DIR` 时使用 `~/Downloads`。用 `--output-dir DIR` 和 `--audio-dir DIR` 覆盖目录，或在交互菜单中调整。

`config.toml` 可保存默认模式、目录和代理；**命令行参数 > 配置文件 > 内置默认值**。代理未配置时再读取环境变量，`--proxy ""` 明确直连。

配置文件需自行创建，或从交互菜单导出为新文件；已有文件不会被导出操作覆盖。配置位置、会话文件和完整示例见[目录与配置](https://github.com/Echoziness/bili-dl/blob/main/docs/usage.md#目录与配置)。

## 使用前了解

- **画质受账号权限和 B 站接口限制**；媒体模式按单个视频处理，不自动遍历合集、收藏夹或全部分 P。
- 旧式分段 FLV 暂不支持。媒体参数由 bili-dl 管理，不读取独立 yt-dlp 的配置文件。
- **字幕只取接口返回的第一条已有轨道**，支持 `?p=N` 指定分 P；不进行语音识别。
- **评论是当前账号可见的实时快照**，数量可能变化；楼中楼上限包含主评论本身，暂不支持续传。
- **扫码会话可能失效**，失效后需重新登录。导入 Cookie 时仅提取 B 站条目；扫码登录不访问浏览器资料，无遥测。
- 开启 Windows“受控文件夹访问”后，ffmpeg 写入视频或音乐目录可能被拦截，处理方法见[常见问题](https://github.com/Echoziness/bili-dl/blob/main/docs/usage.md#常见问题)。

## 文档与反馈

[详细使用说明](https://github.com/Echoziness/bili-dl/blob/main/docs/usage.md) · [更新记录](https://github.com/Echoziness/bili-dl/blob/main/CHANGELOG.md) · [问题反馈](https://github.com/Echoziness/bili-dl/issues)

开发维护：[开发说明](https://github.com/Echoziness/bili-dl/blob/main/docs/development.md) · [排障记录](https://github.com/Echoziness/bili-dl/blob/main/docs/troubleshooting.md)

本项目使用 [MIT](https://github.com/Echoziness/bili-dl/blob/main/LICENSE) 许可证。依赖及 FFmpeg 可执行程序使用各自的许可证，见[第三方组件说明](https://github.com/Echoziness/bili-dl/blob/main/docs/third-party.md)。

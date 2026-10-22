# 第三方组件与许可证

bili-dl 自身代码继续使用根目录 [MIT 许可证](../LICENSE)。依赖和独立可执行程序保留各自的许可证，不由项目 MIT 许可证覆盖。

## 媒体工具

| 组件 | 来源与许可证 | 项目使用方式 |
| --- | --- | --- |
| yt-dlp Python 包 | [源码与 PyPI 包：Unlicense](https://github.com/yt-dlp/yt-dlp#licensing) | pip 安装依赖，通过当前 Python 的 `-m yt_dlp` 启动 |
| imageio-ffmpeg Python 包装层 | [BSD-2-Clause](https://github.com/imageio/imageio-ffmpeg/blob/v0.6.0/LICENSE) | 获取 FFmpeg 程序路径 |
| FFmpeg 可执行程序 | [取决于实际构建的 LGPL / GPL 条款](https://ffmpeg.org/legal.html) | 作为独立进程，通过命令行参数和媒体文件完成处理 |

imageio-ffmpeg 的 BSD-2-Clause 只描述包装层，不能据此认为其自带 FFmpeg 也是 BSD。2026-10-22 核对的 0.6.0 Windows x64 包内 FFmpeg 7.1 启用 `--enable-gpl --enable-version3`，自身报告 GPLv3 或更高版本。其他平台和未来版本应按实际二进制核对，不根据 Python 包分类推断。

FFmpeg 二进制由 imageio-ffmpeg 的平台 wheel 提供；bili-dl 的 wheel / sdist 声明依赖，不复制或嵌入这些二进制，也不链接 FFmpeg 库。独立程序协作的判断参考 [GNU FAQ 关于程序组合与聚合的说明](https://www.gnu.org/licenses/gpl-faq.en.html#MereAggregation)。

相关来源：

- [imageio-ffmpeg 发布文件](https://pypi.org/project/imageio-ffmpeg/#files)
- [imageio 二进制仓库](https://github.com/imageio/imageio-binaries/tree/master/ffmpeg)
- [imageio-ffmpeg 打包脚本](https://github.com/imageio/imageio-ffmpeg/blob/v0.6.0/tasks.py)
- [FFmpeg 源码与下载入口](https://ffmpeg.org/download.html)

如以后自行分发包含 FFmpeg 的安装包、可执行文件集合或容器镜像，需要针对实际构建保留许可证和版权声明，并按适用条款提供对应源码及构建信息。当前通过 pip 声明依赖的交付方式不应与重新打包二进制混为一谈。

## 其他 Python 依赖

其余依赖的许可证以各自发行包中的许可证文件和元数据为准，安装时随依赖保留。本页重点记录媒体工具的分层关系，不替代第三方许可证原文。

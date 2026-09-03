# Music CoverOverlay (Music_CoverOverlay) — Agent Instructions

**源码目录**: `F:\desktop on f\QQMusicOverlayNG\`
**安装/分发目录**: `D:\Music CoverOverlay\`（原 `D:\QQMusicOverlay NG\`）
**AI Box 知识系统**: `D:\AI Box\`

---

## 启动时

读以下文件以理解本项目和用户偏好：
1. `D:\AI Box\README.md` — AI Box 系统概述
2. `D:\AI Box\global\user-profile-summary.md` — 用户画像
3. `D:\AI Box\global\error-patterns.md` — 8 种 Bug 模式
4. `D:\AI Box\memory\MEMORY.md` — 项目记忆索引

## 项目概述

Music CoverOverlay（项目名 `Music_CoverOverlay`）— 适用于 QQ Music 和 NetEase Cloud Music 的封面悬浮窗程序。在桌面上叠加显示当前播放歌曲的封面、进度、歌词等信息。

- **技术栈**: Python 3.10~3.14 + pyglet + OpenGL + Win32 API + tkinter
- **入口**: 托盘常驻程序，`python main.py` 或 `run.bat` 启动
- **构建**: `python build.py` → PyInstaller + Inno Setup 6 (`Music_CoverOverlay.spec`)
- **配置**: `overlay_config.json`
- **数据**: `%APPDATA%\Music_CoverOverlay\` (兼容 `%APPDATA%\QQMusicOverlayNG\`)

## 项目结构

```
├── main.py              ← 入口：进程清理 + DPI + 启动序列
├── run.bat              ← 便捷启动脚本
├── config.py            ← 声明式配置 schema
├── version.py           ← 版本号 + stamp_build()
├── build.py             ← PyInstaller + Inno Setup 构建脚本
├── Music_CoverOverlay.spec ← PyInstaller 配置文件
├── setup.iss            ← Inno Setup 6 安装包配置
├── core/                ← 数据层：reader, smtc_engine, progress, lyrics...
├── render/              ← OpenGL 渲染：gl_engine, painter, compositor, text...
├── scene/               ← 场景图：graph, node, nodes, layout
├── animation/           ← 动画/状态机：state_machine, timeline, transitions
├── ui/                  ← 用户界面：window, settings, tray, monitor
├── debug/               ← 诊断工具
├── tests/               ← 测试用例与测试夹具
└── dist/                ← PyInstaller 构建输出
```

## 核心约束

本项目有 21 条硬约束 (`D:\AI Box\memory\hard-constraints-index.md`)。
修改代码前务必检查涉及的子系统硬约束。

关键子系统：
- DWM/窗口合成层 (HC-001~006)
- 进度追踪层 (HC-007~013)
- GL/纹理层 (HC-014~016)
- 托盘/通信层 (HC-017~019)
- 自适应 FPS 层 (HC-020)
- 数据/状态机层 (HC-021)

## 工作规则

遵循 `D:\AI Box\global\universal-working-rules.md` (UR-01~UR-15)。
项目特定覆盖见 `D:\AI Box\memory\working-rules.md`。

## 诊断方法

```
正确: 症状 → 读代码(确认现状) → 对照 memory(理解历史) → 假设 → 验证
错误: 症状 → 查 memory → 假设 → 读代码
```

先查 `D:\AI Box\memory\code-verification-log.md` 确认修复是否已验证。

## 调试

```powershell
# 启动
python main.py

# 查看日志
Get-Content $env:TEMP\qqoverlay_render.log -Tail 5
Get-Content $env:TEMP\qqoverlay_smtc.log -Tail 5
Get-Content $env:TEMP\qqoverlay_errors.log -Tail 10

# 独立监控
python ui\monitor.py
```

## 自动打包规则

- 只要任务修改了源码、配置、构建脚本或会影响运行行为的项目文档，在最终回复前必须自动执行 `python build.py --quick`。
- 打包完成后必须核对 `dist\Music_CoverOverlay\Music_CoverOverlay.exe`、`Music_CoverOverlay_Setup.exe` 以及版本号安装包是否已更新，并在回复中给出路径。
- 自动打包不等于自动安装；除非用户明确要求安装，否则不要写入 `D:\Music CoverOverlay\` 或 `D:\QQMusicOverlay NG\`。
- 纯诊断、只读审计、纯聊天或用户明确要求不打包时，可以跳过，但需要在回复中说明原因。

# QQMusicOverlay NG — Agent Instructions

**源码目录**: `F:\desktop on f\QQMusicOverlayNG\`
**安装/分发目录**: `D:\QQMusicOverlay NG\`
**AI Box 知识系统**: `D:\AI Box\`

---

## 启动时

读以下文件以理解本项目和用户偏好：
1. `D:\AI Box\README.md` — AI Box 系统概述
2. `D:\AI Box\global\user-profile-summary.md` — 用户画像
3. `D:\AI Box\global\error-patterns.md` — 8 种 Bug 模式
4. `D:\AI Box\memory\MEMORY.md` — 项目记忆索引

## 项目概述

QQMusicOverlay NG — Windows 桌面音乐浮层。在桌面上叠加显示当前播放歌曲的封面、进度、歌词等信息。

- **技术栈**: Python 3.14 + pyglet + OpenGL + Win32 API + tkinter
- **入口**: 托盘常驻程序，`python main.py` 启动
- **构建**: `python build.py` → PyInstaller + Inno Setup 6
- **配置**: `overlay_config.json`
- **数据**: `%APPDATA%\QQMusicOverlayNG\`

## 项目结构

```
├── main.py              ← 入口：进程清理 + DPI + 启动序列
├── config.py            ← 声明式配置 schema
├── version.py           ← 版本号 + stamp_build()
├── build.py             ← PyInstaller 构建脚本
├── core/                ← 数据层：reader, smtc_engine, progress, lyrics...
├── render/              ← OpenGL 渲染：gl_engine, painter, compositor, text...
├── scene/               ← 场景图：graph, node, nodes, layout
├── animation/           ← 动画/状态机：state_machine, timeline, transitions
├── ui/                  ← 用户界面：window, settings, tray, monitor
├── debug/               ← 诊断工具
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

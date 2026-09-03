# Music CoverOverlay

<div align="center">

<img src="assets/app_icon.png" alt="Music CoverOverlay Logo" width="120" height="120" />

### 适用于 QQ Music 和 NetEase Cloud Music 的封面悬浮窗程序
**A Modern Desktop Music Cover & Playback Overlay for QQ Music and NetEase Cloud Music**

[![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011%20(64--bit)-0078D6?logo=windows&logoColor=white)](https://microsoft.com)
[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.14-3776AB?logo=python&logoColor=white)](https://python.org)
[![Graphics](https://img.shields.io/badge/Render-OpenGL%203.3+%20%7C%20Pyglet-5586A4?logo=opengl&logoColor=white)](https://pyglet.org)
[![Supported Players](https://img.shields.io/badge/Players-QQ%20Music%20%7C%20NetEase%20Cloud%20Music-1890FF)](#适应范围与兼容性)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

</div>

---

## 项目简介 (Overview)

**Music CoverOverlay** 是一款为 Windows 平台打造的高性能、低占用音乐伴侣悬浮窗。

在日常办公、编码或全屏游戏时，你无需频繁切换或呼出音乐播放器完整窗口，悬浮窗即可在桌面上安静而优雅地呈现当前播放歌曲的**高分辨率封面、实时进度条、歌曲名、歌手以及动态唱片动画**。

- **OpenGL 3.3 硬件加速渲染**：基于 FBO 离屏渲染与 Windows DWM 硬件透明窗口合成，动画丝滑无锯齿。
- **双平台原生级兼容**：无缝支持 **QQ 音乐 (QQ Music)** 与 **网易云音乐 (NetEase Cloud Music)**，支持多播放器状态智能识别与自动切换。
- **防误触设计**：支持一键开启鼠标穿透（锁定模式），悬浮窗置顶不挡视线，鼠标完全穿透防误触。
- **所见即所得设置中心**：现代深色垂直 Tab 设置面板，尺寸、圆角、字号、发光、模糊、布局一键微调，内置多款精美预设。

---

## 适应范围与兼容性 (Compatibility)

### 1. 支持的音乐播放器
| 播放器 | 支持程度 | 推荐设置 | 数据捕获方式 |
| :--- | :---: | :--- | :--- |
| **QQ 音乐 (PC 客户端)** | **原生完整支持** | 开启「设置 -> 常规/播放 -> 系统媒体传输控制 (SMTC)」 | Windows SMTC 协议 + 进程/窗口句柄探测 + 本地封面缓存匹配 + Web API 补全 |
| **网易云音乐 (PC 客户端)** | **原生完整支持** | 开启「设置 -> 播放 -> 使用系统媒体传输控件 (SMTC)」 | Windows SMTC 协议 + 进程探测 + 云端封面 API 联动 |
| **通用媒体源 (Spotify / 浏览器等)** | **标准媒体识别** | 任何支持 Windows SMTC 的播放源 | Windows SMTC 协议实时提取歌曲元数据与专辑画 |

> **提示**：当 QQ 音乐与网易云音乐同时开启时，程序支持在设置中自定义识别优先级（默认优先响应当前活跃播放的会话）。

### 2. 系统与硬件环境要求
- **操作系统**：Windows 10（64 位，Version 1809 及以上）或 Windows 11（64 位）
- **显卡/图形驱动**：支持 **OpenGL 3.3** 及以上规范，已启用 Windows DWM 窗口桌面合成
- **Python 运行时**（仅源码运行需）：Python 3.10 ~ 3.14（推荐 Python 3.12 或 3.14）

---

## 核心特性 (Key Features)

### 封面与视觉特效
- **多形态封面模式**：
  - **圆角卡片**：支持自由定义圆角半径（平滑矩形、超椭圆圆角）。
  - **圆形黑胶唱片**：模拟真实唱片质感，支持播放时无级旋转，切歌/暂停时具备真实阻尼缓冲启停。
- **光影与模糊**：
  - 支持外发光辉光（Glow）、自定义阴影距离与柔和度。
  - 基于 GLSL Shader 实现的背景毛玻璃动态模糊（Blur）。

### 进度条与动态时间
- **3 种进度条形态**：
  - **环形/弧形进度 (Arc)**：围绕圆形唱片边缘包裹。
  - **水平胶囊条 (Bar)**：悬浮在封面下方或侧边，高亮指示播放百分比。
  - **全包围胶囊 (Roundrect)**：环绕封面轮廓流动。
- **智能进度补偿算法**：SMTC 离散脉冲与高精度 Wall-Clock 自动插值推算，拖动进度条即刻校准，歌曲切换无撕裂感。
- **时间标签**：当前进度、剩余时间、总时长动态计算与格式化显示。

### 桌面交互与低能耗优化
- **自由定位与记忆**：鼠标直接拖拽悬浮窗至任意屏幕位置，自动持久化记录坐标。
- **无级透明度调节**：鼠标移入滚轮滚动，即时调节全局透明度（5% ~ 100%）。
- **鼠标穿透模式 (Click-Through)**：开启后鼠标点击完全穿透至下层桌面或游戏，彻底消除误触可能。
- 动态自适应 FPS (Adaptive Frame Rate)：
  - 播放动画时以 60/120 FPS 满帧极速渲染；
  - 音乐暂停或静止状态自动降频休眠，CPU/GPU 占用近乎 0%，轻盈省电。

---

## 实现原理简述 (Architecture & Implementation)

Music CoverOverlay 的底层架构主要由以下三个核心子系统协同运转：

1. **数据采集与会话管道 (Data Ingestion Pipeline)**：
   - **SMTC 媒体会话监听**：基于 Windows 运行时组件（`winrt-Windows.Media.Control`），在独立后台线程常驻监听系统当前活跃的全局媒体控制会话，捕获当前播放源、曲目名、歌手以及内嵌封面媒体流。
   - **客户端窗口与状态嗅探**：结合 Win32 API 周期性探测 QQ 音乐与网易云音乐主窗口句柄与进程活跃态，结合前后台优先级规则决定激活源。
   - **智能封面感知比对 (pHash)**：优先提取 SMTC 原生封面流；当封面存在空白或格式异常时，自动扫描本地音乐客户端下载目录，利用感知哈希（pHash 8x8 / 16x16）进行汉明距离模糊匹配；未命中时通过 Web API 发起异步云端补全。

2. **状态机与动画管道 (Animation & State Pipeline)**：
   - **声明式场景图 (Scene Graph)**：将封面、黑胶唱片、标题、歌手、进度条、时间文字拆解为树状渲染节点。
   - **状态机分发 (State Machine)**：统一管理 Idle（空闲）、Appear（浮现）、Stay（停留展示）、Disappear（淡出收起）等生命周期。
   - **缓动与阻尼模拟 (Tween & Easing)**：唱片旋转采用平滑角速度积分，切歌与暂停时注入物理阻尼缓冲，避免瞬间骤停与跳帧。

3. **高性能 OpenGL 离屏渲染与 DWM 硬件透明合成 (Hardware Compositing)**：
   - 基于 pyglet 构建无边框全透明窗口，利用 Framebuffer Object (FBO) 执行 2D/3D 元素着色与亚像素抗锯齿绘制。
   - 自定义 GLSL 着色器实现高性能黑胶唱片旋转采样、外发光辉光（Glow）以及高斯毛玻璃动态模糊（Blur）。
   - 渲染完成的像素缓冲通过 `glReadPixels` 高速注入 Windows 桌面窗口管理器（DWM），通过 `UpdateLayeredWindow` API 实现真正的全硬件加速透明桌面置顶合成，不抢占系统焦点、不破坏其他桌面窗口重绘。

---

## 播放进度定位机制与局限性说明 (Progress Tracking & SMTC Limitations)

### 1. 为什么只能依赖 SMTC？
在 Windows 平台上，国内外主流媒体应用存在显著的生态差异：
- 部分海外播放器（如 Spotify）提供了规范的本地 IPC 协议、WebSocket 端口或开放 Web API，允许第三方客户端以毫秒级精度同步播放器内部核心时钟与帧级播放位置。
- **国内主流播放器（QQ 音乐、网易云音乐）目前均未向第三方开发者开放任何官方的本地实时播放进度与时钟对齐 API**。
- 为实现通用、非侵入式（不注入 DLL、不 Hook 内存、不篡改客户端核心组件以保证账号安全与系统稳定）的伴随显示，**Windows 系统的 SMTC (System Media Transport Controls) 是目前唯一合规且通用的系统级数据获取通道**。

### 2. 仅依赖 SMTC 产生的客观进度定位缺陷
由于底层播放器客户端对 Windows SMTC 协议的实现参差不齐，完全依赖 SMTC 采集播放时间戳会不可避免地产生以下缺陷与技术挑战：

1. **网易云音乐客户端完全缺失进度与时长广播**
   - **现象**：播放网易云音乐时，悬浮窗无法显示进度条与时间（或处于关闭状态）。
   - **根本原因**：网易云音乐 Windows 客户端在向系统注册 SMTC 媒体会话时，其 `TimelineProperties` 对象中的 `Position`（当前位置）与 `EndTime`（总时长）**长期保持为 0 或未实现数据广播**。因此，任何不采用内存 Hook 的外部工具均无法从网易云音乐获取实时进度。

2. **离散心跳脉冲导致的时间步进跳跃与延迟**
   - **现象**：进度时间无法做到绝对平滑，或偶尔出现阶梯状前进。
   - **根本原因**：Windows SMTC 属于低频事件驱动模型，播放器客户端通常仅在切歌、播放/暂停或特定心跳间隔（如 1~5 秒）才向系统刷新一次 Timeline 快照，而不是连续推送毫秒级时钟。

3. **用户手动拖拽进度条 (Seek) 时的滞后与回弹**
   - **现象**：在播放器中用鼠标拉动进度条后，悬浮窗的进度显示有 1~3 秒的感知延迟，有时甚至在短暂推算后发生向后或向前的微小回跳。
   - **根本原因**：用户手动 Seek 发生后，播放器底层缓冲更新与系统 SMTC 属性广播存在异步时间差；悬浮窗本地的时钟推算引擎在收到滞后的新位置脉冲前仍按旧时钟前进，收到新快照后执行纠偏，从而产生视觉上的纠偏跳跃。

4. **长音频与电台流媒体的时间推算漂移**
   - **现象**：遇到 7~10 分钟以上的长音乐、串烧或播客时，进度条可能提前走完或推算严重滞后。
   - **根本原因**：部分长音频采用动态比特率 (VBR) 或流式加载，播放器向系统上报的总时长往往存在浮动误差，本地时间推算累计误差逐渐放大。

### 3. 本项目采取的缓解与平滑推算方案
为最大程度降低仅依靠 SMTC 带来的不良体验，Music CoverOverlay 在 `core/progress.py` 中内置了复合定位补偿引擎：
- **高精度 Wall-Clock 动态壁钟插值**：在两次 SMTC 脉冲之间，以高精度系统单调时钟（`time.monotonic()`）模拟毫秒级平滑时间推进。
- **历史时长学习库 (`.learned_durations.json`)**：自动记录并学习已知歌曲的真实完整播放时长，弥补 SMTC 上报时长不准的问题。
- **离散脉冲平滑对齐与异常值过滤**：当检测到位置突然跳零（播放器内部缓冲重置）或异常跳变时，启动验证窗口，避免进度条剧烈闪烁或瞬间拉伸。
- **来源自适应智能降级**：检测到网易云音乐等无进度支持的播放源时，自动隐藏进度条与时间模块，避免向用户呈现失效的零刻度。

---

## 使用方式 (Usage Guide)

### 方式一：使用安装包直接运行（推荐日常使用）
1. 下载最新发布版本的 `Music_CoverOverlay_Setup.exe`。
2. 按照向导安装至指定路径（默认安装到 `D:\Music CoverOverlay\`）。
3. 安装完成后启动应用，程序将静默驻留在右下角系统托盘。
4. 打开 QQ 音乐或网易云音乐播放歌曲，悬浮窗将自动浮现并同步展示。

---

### 方式二：源码安装与运行（开发者 / 极客模式）

#### 1. 克隆仓库
```bash
git clone https://github.com/Arnabest/Music_CoverOverlay.git
cd Music_CoverOverlay
```

#### 2. 配置 Python 虚拟环境与依赖
推荐使用 Python 3.12 或 3.14：
```bash
# 创建虚拟环境
python -m venv .venv

# 激活虚拟环境 (Windows PowerShell)
.venv\Scripts\Activate.ps1

# 安装运行所需依赖
pip install -r requirements.txt
```

#### 3. 启动程序
你可以选择以下任意一种启动方式：
- **方式 A (推荐脚本)**：直接双击运行根目录下的 `run.bat`。
- **方式 B (命令行)**：
  ```bash
  python main.py
  ```

---

## 播放器前置设置 (Player Configuration)

为了确保悬浮窗能够精准捕获播放状态与封面，请检查播放器以下设置：

### QQ 音乐设置
1. 打开 QQ 音乐客户端，点击右上角菜单 -> **「设置」**。
2. 进入 **「常规设置」** 或 **「音效与播放」**。
3. 勾选 **「启用系统媒体传输控制 (SMTC)」**（若有该选项）。
4. （可选）在设置中开启「下载高品质封面」以获得最佳高清封面体验。

### 网易云音乐设置
1. 打开网易云音乐客户端，点击右上角设置图标（齿轮）。
2. 在左侧切换到 **「播放」** 标签页。
3. 下拉找到 **「系统媒体控制」**，勾选 **「使用系统媒体传输控件 (SMTC)」**。

---

## 快捷操作与托盘控制 (Controls & Shortcuts)

| 触发操作 | 功能描述 |
| :--- | :--- |
| **鼠标左键按住拖拽** | 移动悬浮窗至桌面任意位置，松开即保存坐标 |
| **悬浮窗上滑动滚轮** | 实时调大/调小悬浮窗透明度 |
| **鼠标双击悬浮窗** | 快速呼出「设置中心」面板 |
| **右键系统托盘图标** | 唤起右键管理菜单：<br>• **显示/隐藏悬浮窗**<br>• **窗口置顶**（勾选开启始终置顶）<br>• **鼠标穿透 (锁定)**（开启后无法选中拖动，防误触）<br>• **设置**（打开参数配置界面）<br>• **退出** |

> **解除穿透模式**：当开启「鼠标穿透」后，悬浮窗将不再接收任何鼠标事件。若需恢复拖拽或关闭穿透，请在屏幕右下角任务栏托盘中**右键托盘图标 -> 取消勾选「鼠标穿透」**。

---

## 设置中心与主题调优 (Settings & Themes)

通过托盘菜单或双击窗口进入设置中心：

<div align="center">
  <b>现代深色设置中心 · 12 项专属分类垂直导航</b>
</div>

```
├── 窗口：位置坐标、置顶、穿透、透明度微调
├── 封面：尺寸、缩放比例、圆角大小、黑胶唱片模式、旋转转速
├── 标题：字体名称、字号大小、颜色、加粗、描边宽度与描边色
├── 歌手：歌手文字排版、字体样式、上下边距
├── 歌词：实时桌面歌词显示开关、主副歌词颜色配置
├── 进度：进度条样式 (Arc / Bar / Roundrect)、填充色、轨道底色、粗细
├── 时间：当前播放进度与剩余时间展示、字体与间距
├── 动画：淡入淡出时长、切歌过渡效果、阻尼衰减系数
├── 来源：QQ 音乐与网易云音乐检测优先级调度、封面下载目录关联
├── 封面库：本地已缓存封面管理与 Hash 对齐
├── 主题：内置 5 套现代主题预设快速切换，支持导出/新建自定义预设
└── 关于：版本信息、构建编号、日志审计与运行说明
```

---

## 目录结构 (Project Structure)

```
Music_CoverOverlay/
├── main.py                     # 程序主入口：进程互斥、DPI 初始化、系统托盘与主循环
├── run.bat                     # 便捷启动批处理脚本（已处理 Python 3.14 mimalloc 适配）
├── config.py                   # 声明式配置 Schema 与主题管理逻辑
├── version.py                  # 版本定义、构建时间戳与自动 Stamp 引擎
├── overlay_config.default.json # 出厂预设配置文件模板
├── requirements.txt            # Python 运行所需依赖声明
├── core/                       # 数据与底层捕获层
│   ├── reader.py               # 音乐数据整合调度引擎
│   ├── smtc_engine.py          # Windows SMTC 系统媒体控制异步引擎
│   ├── progress.py             # 高精度 Wall-Clock 与 SMTC 进度对齐插值算法
│   ├── playback_detector.py    # 播放器活跃状态识别器
│   ├── window_detect.py        # 播放器窗口句柄与进程检测
│   ├── cover_cache.py          # 本地封面感知哈希 (pHash) 与缓存管理
│   ├── web_api.py              # 云端封面补齐接口
│   └── logs.py                 # 日志记录与自动回滚剪裁
├── render/                     # 图形渲染层
│   ├── gl_engine.py            # OpenGL 3.3 + Pyglet + DWM 硬件透明窗口合成引擎
│   ├── painter.py              # 高性能 2D 几何、圆角、弧线着色器绘制器
│   ├── compositor.py           # 元素图层合成管理器
│   ├── shaders.py              # GLSL 着色器加载与编译封装
│   └── text.py                 # FreeType / PIL 矢量文本渲染
├── scene/                      # 场景图节点与自适应布局
├── animation/                  # 动画补间器、状态机与过渡控制器
├── ui/                         # UI 交互层（设置中心、系统托盘、悬浮窗管理）
│   ├── window.py               # 悬浮窗生命周期与事件调度
│   ├── settings.py             # 现代深色垂直导航设置中心
│   ├── settings_theme.py       # 设置界面色彩与 Design Tokens
│   └── tray.py                 # 系统托盘菜单与动态状态同步
├── assets/                     # 图标、默认封面、着色器脚本与内置主题
└── tests/                      # 单元测试与测试用例 (含 smoke_test.py)
```

---

## 常见问题排查 (FAQ & Troubleshooting)

### Q1: 悬浮窗显示了，但封面一直是默认图标？
- **排查步骤**：
  1. 确认使用的播放器（QQ 音乐 / 网易云音乐）已按照上文指南**开启了系统媒体控制 (SMTC)**。
  2. 检查 Windows 系统音量弹窗（按键盘音量加减键）是否会出现歌曲信息和封面；若系统弹窗没有，说明播放器未向 Windows 广播 SMTC 会话。
  3. 部分独播无版权或电台歌曲可能缺少内嵌专辑画，程序会尝试通过 Web API 补全，请确保网络通畅。

### Q2: 悬浮窗无法拖动了？
- **原因**：可能无意中启用了「鼠标穿透 (锁定)」模式。
- **解决方法**：在右下角系统托盘找到 Music CoverOverlay 图标，**鼠标右键点击 -> 取消勾选「鼠标穿透 (锁定)」**，悬浮窗即可恢复自由拖拽。

### Q3: 运行时报错或异常退出，如何排查？
- 可以在控制台运行诊断测试：
  ```bash
  python smoke_test.py
  ```
- 查看运行时日志输出（存储在系统临时目录）：
  ```powershell
  Get-Content $env:TEMP\qqoverlay_render.log -Tail 20
  Get-Content $env:TEMP\qqoverlay_smtc.log -Tail 20
  Get-Content $env:TEMP\qqoverlay_errors.log -Tail 20
  ```

---

## 开源许可证 (License)

本项目基于 [MIT License](LICENSE) 开源。欢迎提交 Issue 与 Pull Request 共同改进！

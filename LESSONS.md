# QQMusicOverlay NG 错题本

## 2026-07-19: Python urllib 继承系统代理 → 封面拉取静默失败

- **症状**: overlay 主界面封面无法显示；任务栏音量控件也无封面；SMTC 持续 `no thumbnail`
- **根因**: `yeshayunCore` 自启动设系统代理 `127.0.0.1:7892`，但代理已挂 → Python `urllib.request.urlopen` 继承系统代理 → 所有 QQ 音乐 API HTTPS 请求 `SSL: UNEXPECTED_EOF_WHILE_READING` → `_search_qqmusic()` 静默 `return None, None, 0` → 无 songmid/albummid → 无封面
- **修复**: `core/web_api.py` 所有 6 处 `urllib.request.urlopen` 改为 `_urlopen()`，内置 `ProxyHandler({})` 绕过系统代理直连；`_search_qqmusic()` 加错误日志
- **教训**: 
  - 国内直连 API（QQ 音乐、网易云）不应走系统代理 — 代理挂了会导致完全不可用
  - `urllib.request.urlopen` 默认读系统代理 → 需要显式绕过
  - API 调用决不能静默 `except Exception: return None` — 必须记录失败原因
- **关联**: [[python-system-proxy]], [[web_api-proxy-bypass]]

## 2026-07-19: 两个代理客户端并存 → 端口冲突

- **症状**: 系统代理指向 `127.0.0.1:7892`（yeshayunCore），但 Clash Verge 实际监听 `:7897`
- **根因**: yeshayunCore 自启动 + Clash Verge `proxy_guard: false` → yeshayunCore 抢走系统代理设置 → 端口不匹配
- **修复**: 关闭 yeshayunCore；Clash Verge 设 `enable_system_proxy: true` 正确指到 `:7897`
- **教训**: 
  - 多个代理客户端不能共存 — 它们会争夺同一个系统代理注册表键
  - `netstat -ano | findstr :port` + `Get-Process -Id PID` 可快速定位端口归属
  - `urllib.request.getproxies()` 查看 Python 所见系统代理

## 2026-07-19: SMTC 缩略图是整个封面验证链路的单点故障

- **症状**: 代理挂了 → QQ 音乐客户端也无封面 → Windows SMTC 无缩略图 → overlay 感知哈希验证（`_image_hash`）无参考图像 → `auto_idx=-1` → 占位封面
- **根因**: `_automatically_match_cover()` 的哈希匹配路径完全依赖 SMTC 缩略图作为参考；无 SMTC 时回退到本地文件名匹配（需 score≥3），对大多数歌曲不命中
- **现状态**: 代理绕过修复后, SMTC 缩略图恢复 + QQ 音乐 API 直连成功 → 封面正常。但当 SMTC 无缩略图时（如网易云播放、QQ 音乐不提供缩略图的歌曲），`auto_match: no SMTC → QQ Music API candidate[0]` 是正确的回退，无需额外修复
- **教训**: 
  - 验证链路不能依赖外部系统（SMTC）作为**唯一**锚点
  - 哈希验证失败时应有降级策略 — 当前 "信任 QQ 音乐 API" 是正确的一级回退
  - `has_cover_ready()` 的 8 秒超时等待在断网场景下是无意义的 — 超时后仍只有占位封面
- **关联**: [[cover-pipeline]], [[smtc-thumbnail-dependency]]

## 2026-07-19: 窗口自动隐藏周期掩盖封面问题

- **症状**: `overlay_state` 持续 `hidden`，即使音乐正在播放
- **根因**: `stay_forever` 在活跃配置中不是 true → 窗口出现后 ~11s 自动隐藏。用户看到封面消失以为封面加载失败，实际是窗口隐藏了
- **教训**: 
  - 现象层和根因层要区分 — "封面不显示" ≠ "封面数据缺失"
  - `curl http://127.0.0.1:8766/state | jq .overlay_state` 是区分两层的快速诊断手段
  - 日志三重验证: reader.log（数据是否到达）→ window.log（状态机位置）→ render.log（是否绘制）

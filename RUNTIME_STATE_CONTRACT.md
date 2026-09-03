# QQMusicOverlayNG Runtime State Contract (schema v1)

**状态**: 正式长期接口（2026-07-18 定稿）。Desktop Agent 已撤除心跳/日志解析回退，只消费本契约的两条通道。
**变更纪律**: 字段语义/命名变更前必须先同步消费方（`D:\AI Desktop Agent\app\qqmusic_overlay_state.py`）并递增 `schema`；只增不改可保持 `schema=1`。

## 通道（消费方按序取用）

| # | 通道 | 地址 | 说明 |
|---|---|---|---|
| 1 | HTTP API | `http://127.0.0.1:8766/state` | `QQMUSIC_OVERLAY_RUNTIME_PORT` 可改端口；`/health` 探活；`/`、`/music` 同 `/state` |
| 2 | JSON 镜像 | `%TEMP%\qqoverlay_music_state.json` | atomic write；**HTTP 端口被占导致 API 起不来时仍持续写入**（兜底） |

## 生产方（本仓库）

- `core/runtime_api.py` `OverlayRuntimeApiServer` — `main.py` 随进程启停（停止不删镜像文件，靠新鲜度语义判活）
- `ui/window.py` `_publish_runtime_state()` — 帧更新路径发布，≥0.25s 节流

## 字段（schema=1）

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema` | int | 契约版本，当前 1 |
| `app` | str | `"QQMusicOverlayNG"` |
| `source` | str | `"runtime_api"` |
| `updated_at_epoch` | float | 发布时刻 epoch 秒 — **消费方新鲜度判定依据** |
| `updated_at` | str | ISO8601 UTC |
| `title` / `artist` / `album` | str | 当前曲目（`album` 暂恒为空串） |
| `player` | str | `qqmusic` / `netease` / `browser` / `unknown` / 空 |
| `playback_status` | str | SMTC 原始态 `PLAYING`/`PAUSED`/`OPENED`/`CHANGING`/`STOPPED`/空 |
| `is_paused` | bool | 进度层暂停位（消费方优先于 playback_status） |
| `playing` | bool | reader 播放位 |
| `position_sec` / `duration_sec` / `progress` | float | 进度（progress=pos/dur, dur=0 时为 0） |
| `no_timeline` | bool | 无时间轴标记 |
| `overlay_state` | str | 浮层可见态（`hidden`/`visible` 等） |
| `always_on_top` / `click_through` | bool | 窗口配置快照 |
| `state_url` | str | 本接口地址 |

## 新鲜度语义（消费方实现约定）

- HTTP: `now - updated_at_epoch ≤ 8s` 有效
- JSON 镜像: `≤ 12s` 有效；超期视为 overlay 不在线（陈旧文件不删除也无害）
- `title`/`artist`/`duration`/`position` 全空 → 视为无内容快照，按不可用处理

## 消费方

- Desktop Agent `app/qqmusic_overlay_state.py` → `app/music_context_bridge.py` → `system_media_info` / `system_media_snapshot` / `desktop_context_snapshot` 工具
- 心跳文件 `%TEMP%\qqoverlay_heartbeat.txt` 与 `%TEMP%\qqoverlay_*.log` 自本契约定稿起**仅为本仓库内部诊断用途**，外部消费方不得再解析
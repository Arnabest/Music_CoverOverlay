# QQMusicOverlay NG — 切歌动画实现方案

> 参考原版 `QQMusicOverlay_切歌动画流程.md`，在场景图 + 动画时间线架构下重新设计。
> 日期: 2026-06-24

---

## 一、原版核心需求 → NG 架构映射

| # | 原版需求 | 原版实现 | NG 实现策略 |
|---|---------|---------|------------|
| 1 | 淡入淡出 | `_do_fade` 帧计数线性 alpha | Timeline.tween(node, "opacity", ...) |
| 2 | 旧歌渐隐→新歌渐起 | transition_cover_out 两阶段 | Sequence(progress_out, cover_out, call(load), appear) |
| 3 | 停在最后画面 | canvas 不清理 | `_frozen_snapshot` 保持最后一帧 PIL Image |
| 4 | 封面旋转不停 | loading 阶段封面已转 | `compositor._cover_rotation` 独立于 reader 更新 |
| 5 | 不闪白 | frozen_data 快照 | `_frozen_snapshot` 替代 live data |
| 6 | SMTC 跳零不理 | 60s 冷却 + 双重验证 | 已在 `progress.py:ProgressTracker` 中实现 ✓ |
| 7 | 进度渐透 | `_progress_alpha` 0→1 逐帧 | compositor 增加 `_progress_alpha` 属性 |
| 8 | 进度比封面先消失 | progress_out 0.25s → cover_out 0.35s | Sequence 已实现顺序 ✓ |
| 9 | 歌放完画面留 | transition_end_out cover_out 保留文字 | frozen_snapshot + 纯 alpha 降窗 |
| 10 | 停留超时收起 | `_on_stay_timeout` → anim_out | `_on_auto_hide()` → disappear + hide ✓ |
| 11 | 长歌壁钟 | `_wall_locked` | 已在 `progress.py` 中实现 ✓ |
| 12 | 常驻不淡出 | stay_forever 跳过计时器 | 已实现 ✓ |

---

## 二、NG 需要补齐的能力

### A. 冻结快照（Frozen Snapshot）

**现状**: `_frozen=True` 跳过 `reader.update()`，但 compositor 仍从 reader 读取最新数据，
transition 期间 reader 的数据可能已被 `_load_new_song()` 替换。

**方案**: 在 transition 开始前捕获一帧完整快照，transition 期间 compositor 使用快照数据。

```python
# ui/window.py — OverlayWindow

def _on_song_changed(self):
    self._frozen = True
    # ★ 捕获旧歌快照
    self._frozen_snapshot = {
        "cover_image": self.reader.cover_image.copy() if self.reader.cover_image else None,
        "song_name": self.reader.song_name,
        "artist_name": self.reader.artist_name,
        "lyrics_text": self._get_current_lyrics_line(),
        "progress_time": format_time(self.progress.position),
        "progress_pos": self.progress.position,
        "progress_dur": self.progress.duration,
    }
    ...

def _provide_data(self):
    # ★ 冻结期间使用快照
    if self._frozen and self._frozen_snapshot:
        return self._frozen_snapshot
    # ...正常数据流
```

### B. 进度透明度（Progress Alpha）

**现状**: progress 节点 opacity 用 tween 控制，但和文字/封面一起淡出，无法"先消失"。

**方案**: compositor 增加 `_progress_alpha` 独立属性，progress 渲染时 color→bg blend。

```python
# render/compositor.py — Compositor

class Compositor:
    def __init__(self, config):
        ...
        self._progress_alpha = 0.0  # 0=不可见(纯背景色), 1=完全可见

    # 每帧 showing 状态: progress_alpha 逐帧 +0.05 → 1.0
    def tick_progress_alpha(self):
        if self._progress_alpha < 1.0:
            self._progress_alpha = min(1.0, self._progress_alpha + 0.05)

    def reset_progress_alpha(self):
        self._progress_alpha = 0.0

    # progress 渲染时 blend:
    def _blend_to_bg(self, color, alpha):
        """将进度颜色按 alpha 混合到背景色。""
        bg = ImageColor.getrgb(self.bg_color)
        fg = ImageColor.getrgb(color)
        return tuple(int(fg[i] * alpha + bg[i] * (1 - alpha)) for i in range(3))
```

**transition 时序中的 progress_alpha**:

```
progress_out (0.25s):  progress_alpha: current → 0    (线性衰减，进度"溶解"到背景)
cover_out (0.35s):     progress_alpha 已为 0，窗口 alpha 降为 0
load:                  progress_alpha 重置为 0
showing:               progress_alpha: 0 → 1 (逐帧 +0.05)
```

### C. cover_out 阶段不重绘

**现状**: NG transition 每帧都调用 `compositor.render_to_pil()` 重新合成。

**原版行为**: cover_out 只改变窗口 `-alpha` 属性，canvas 保持旧歌最后一帧不动。

**方案**: compositor 增加 `frozen=True` 模式，返回缓存的上一帧 PIL Image 而不重新渲染。

```python
# render/compositor.py
class Compositor:
    def __init__(self, config):
        self._last_frame: Image.Image | None = None
        self._frozen_frame: Image.Image | None = None

    def freeze_frame(self):
        """保存当前帧为冻结帧，后续 render_to_pil 直接返回它。"""
        self._frozen_frame = self._last_frame.copy() if self._last_frame else None

    def unfreeze(self):
        self._frozen_frame = None

    def render_to_pil(self, scene, data, width, height):
        if self._frozen_frame is not None:
            return self._frozen_frame  # ← 不重绘，直接返回冻结帧
        img = self._do_render(...)
        self._last_frame = img
        return img
```

**transition 中的使用**:

```
progress_out (0.25s):  每帧渲染（progress_alpha 逐帧降低 → 进度溶解）
cover_out (0.35s):     freeze_frame() → 后续不重绘，只降 window alpha
load → appear:         unfreeze() → 恢复重绘
```

### D. 窗口 alpha 动画

**现状**: Timeline 只 tween 节点属性（`node.opacity`），不控制窗口。

**方案**: 扩展 Timeline 支持 tkinter 窗口属性 tween，或增加专用 WindowAlpha tween。

```python
# 新增一种 Tween 目标: 窗口对象
class WindowAlphaTween:
    def __init__(self, engine, to_alpha, duration, easing="linear"):
        self.engine = engine
        self.to_alpha = to_alpha
        ...

    def update(self, dt):
        ...
        self.engine.root.attributes("-alpha", current_value)
```

### E. 可中断淡出

**原版行为**: anim_out 期间新歌出现 → `_fade_aborted=True` → 立即转入 loading。

**NG 方案**:

```python
def _on_song_changed(self):
    # ★ 如果正在淡出，立即停止
    if self.timeline.is_playing:
        self.timeline.stop()  # 强制结束当前动画
        self.compositor.unfreeze()
    # 开始新过渡...
```

### F. Canvas 持久化

**原版行为**: anim_out 完成后 window alpha=0 但 canvas 不清空，保留旧歌画面。

**NG 方案**: `engine.hide()` 时仅设置 alpha=0 + withdraw，不清空 canvas。`show_window()` 时先恢复再渲染。

```python
# render/engine.py
def hide(self):
    self.root.attributes("-alpha", 0.0)
    self.root.withdraw()
    # 不调用 canvas.delete("all") — 保留旧帧

def show(self):
    self.root.deiconify()
    self.root.state("normal")
    # PhotoImage 仍在 canvas 上，下一帧 render 会覆盖
```

---

## 三、完整过渡时序（NG 版本）

### 切歌（song_change）

```
showing → reader.update() song_changed=True
  │
  ├─ [0ms]  捕获 frozen_snapshot (cover.copy(), song, artist, lyrics, progress_time)
  ├─ [0ms]  若 timeline 正在播放 → stop() + unfreeze()
  ├─ [0ms]  frozen = True
  │
  ├─ Phase 1: progress_out (250ms, linear)
  │    progress_alpha: 当前 → 0 (逐帧递减，颜色 blend 到背景)
  │    其他元素: opacity 不变，使用 frozen_snapshot 渲染
  │    每帧重新渲染（progress_alpha 在变化）
  │
  ├─ Phase 2: cover_out (350ms, linear)
  │    compositor.freeze_frame() — 不再重绘
  │    window alpha: 当前 → 0 (纯 tkinter 属性变化)
  │    canvas 保持 progress_out 最后一帧 (progress 已溶解，封面+文字还在)
  │
  ├─ Phase 3: load (瞬间)
  │    record_position(旧歌) → 学习时长
  │    progress.reset() → 新歌进度
  │    scene.resolve_layout()
  │    compositor.unfreeze()
  │    compositor.reset_progress_alpha() → 0
  │    engine.show() (若隐藏)
  │    frozen = False
  │    frozen_snapshot = None
  │
  └─ Phase 4: appear (400ms, ease-out)
       所有节点 opacity: 0 → 1
       progress_alpha: 0 → 1 (逐帧 +0.05, 在 showing 继续递增)
       window alpha: 目标透明度
```

### 歌曲结束（song_end）

```
showing → is_playing=False
  │
  ├─ Phase 1: progress_out (250ms)
  │   同切歌 progress_out
  │   record_completed(progress_pos) → 学习时长
  │
  ├─ Phase 2: cover_out (300ms)
  │   同切歌 cover_out，但最终状态 → hidden
  │
  └─ 结束: _visible=False, 不进入 loading
```

### 停留超时（auto_hide）

```
showing → 停留计时器到期
  │
  ├─ Phase 1: disappear (800ms, ease-in)
  │   所有节点 opacity: 1 → 0
  │   每帧渲染（节点透明度在变化）
  │   progress_alpha 不变
  │
  ├─ Phase 2: wait (300ms)
  │
  └─ 结束: engine.hide() (canvas 保留, window alpha=0)
```

---

## 四、实现任务清单

| # | 任务 | 文件 | 优先级 |
|---|------|------|--------|
| 1 | frozen_snapshot 捕获 + `_provide_data` 冻结返回 | `ui/window.py` | P0 |
| 2 | compositor `_progress_alpha` + `_blend_to_bg` | `render/compositor.py` | P0 |
| 3 | compositor `freeze_frame` / `unfreeze` | `render/compositor.py` | P0 |
| 4 | 窗口 alpha tween（cover_out 纯 alpha 降窗） | `animation/timeline.py` | P1 |
| 5 | 可中断淡出（`timeline.stop()` 在 song_change 前） | `ui/window.py` | P1 |
| 6 | progress_out 子阶段：progress_alpha blend 而非 opacity | `animation/transitions.py` | P1 |
| 7 | engine.hide() 保留 canvas 不清空 | `render/engine.py` | P1 |
| 8 | showing 状态 progress_alpha 逐帧递增 | `ui/window.py` + `render/compositor.py` | P2 |

---

## 五、与原版的关键设计差异

| 方面 | 原版 (overlay.py) | NG (新架构) |
|------|------------------|------------|
| 状态管理 | 7 状态字符串枚举 | Timeline.is_playing + _frozen 标志 |
| 过渡驱动 | 帧计数 `_transition_frame` + 固定步数 | 秒级时间线 `update(dt)` |
| 快照 | 手动字段 `_frozen_cover/song/artist/lyrics` | 结构化 frozen_snapshot dict |
| 进度渐隐 | `_progress_alpha` → `blend_color(fg, bg)` | compositor._progress_alpha → `_blend_to_bg` |
| cover_out | `_do_cover_fade_alpha` 纯 window alpha | compositor.freeze_frame + window alpha tween |
| 中断淡出 | `_fade_aborted=True` | `timeline.stop()` + `compositor.unfreeze()` |
| 进度渐显 | 逐帧 `_progress_alpha += 0.05` | 同逻辑，在 compositor 中实现 |
| 画布持久 | hidden 不清理 canvas | engine.hide() 保留 PhotoImage |

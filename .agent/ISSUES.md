---
description:
alwaysApply: true
---

# 问题清单与优化路线图

**生成日期**: 2026-05-27（2026-08-01 更新）
**项目版本**: dev 分支

---

## 一、问题汇总

### 1.1 严重问题 (🔴)

> 可能导致 bug 或安全漏洞的问题

| 问题 | 位置 | 说明 |
|------|------|------|
| `platform/ssh.py` 路径错误 | DEVICE.md §2.6 | 文档称 SSH 在 `module/device/platform/`，实际在 `module/base/ssh.py`；`module/device/platform/` 为 `platform_windows.py`/`platform_mac.py`/`platform_base.py` + `emulator_*.py` 架构 |
| `os_grid.py` 导出类名错误 | MAP-DETECTION.md | 文档称导出 `OSGrid`，实际为 `OSGridInfo` 和 `OSGridPredictor`，不存在 `OSGrid` 类 |
| `os_run.py` 类名与职责错误 | CAMPAIGN.md | 文档称 `class OpsiRun`（大世界战役运行），实际为 `class OSCampaignRun(OSMapOperation)`，方法名为 `opsi_*` |
| `ambush_1_1.py` 定位错误 | CAMPAIGN.md | 文档称"战役地图定义 + 战斗逻辑"，实际为 `class Ambush11`（继承 `CampaignRun, FleetEquipment, Retirement`），专用于 1-1 伏击刷关/钻石 farming |

### 1.2 中等问题 (🟡)

> 影响可维护性或性能的问题

| 问题 | 位置 | 说明 |
|------|------|------|
| 文档行号/行数系统性过时 | 全部文档 | 约半数文档的逐行分析（`Lxxx-xxx`）与文件实际行数不符（如 al_ocr.py 557→970、map_base.py 832→1083、app.py 5060→363） |
| `RESTART_SENSITIVE_TASKS` 已删除 | ENTRY-ALAS.md | 文档称常量在 alas.py，实际已删除，改为 `Error_StrictRestart + {task}.Scheduler.Sensitive` 动态判断（alas.py L1401） |
| `call_tool` 分发重构 | ENTRY-MCP-SERVER.md | 文档称 if/elif 分发链，实际已改为 `TOOL_HANDLERS` 字典分发（L466-485），`call_tool` 仅 10 行 |
| 任务数量过时 | ENTRY-ALAS.md / CLAUDE.md | 文档称 55 个任务方法，实际 93 个（alas.py L678-1174），新增大量 island/opsi 子任务 |
| 商店类名带日期后缀 | GAME-FUNCTIONS.md | 文档称 `GeneralShop`/`MedalShop`/`CoreShop`，实际为 `GeneralShop_250814`、`MedalShop2_250814`、`CoreShop_250814` |
| `smart_scheduling_utils.py` 已删除 | OS-SYSTEM.md | 文档 tasks 表仍列出，实际已并入 `scheduling.py` |
| WebUI app.py 拆分重构 | INFRASTRUCTURE.md | 文档称 app.py 5060+ 行导出 `AlasGUI`，实际仅 363 行，已拆分至 `app_*` 系列约 50 个文件 |

### 1.3 建议问题 (🟢)

> 可优化的代码风格或结构

| 问题 | 位置 | 说明 |
|------|------|------|
| utils.py 过大 | BASE.md | 1288 行，建议拆分为 color/image/area 等子模块 |
| config.py 过大 | CONFIG.md | 910 行，建议将调度逻辑（`get_next_task`/`task_delay`/`opsi_task_delay`）提取到 scheduler.py |
| server.py 全局变量 | CONFIG.md | 全局 `server = 'cn'` 影响所有资源加载，测试困难，建议改为依赖注入 |
| 测试覆盖 | 全部 | 核心工具函数（`crop()`、`color_similarity()`）与 `deep.py` 边界条件缺少单元测试 |

---

## 二、整体重构/优化路线图

### 2.1 短期优化

- 统一更新 `.agent/` 各文档的行号引用（与当前代码对齐）
- 为 `module/auto_equip`、`module/storage`、`module/game_setting`、`module/template` 补充模块分析
- 修正 ENTRY-GUI.md 的 gui.py 重构描述（依赖同步服务、worker_registry、双栈 socket）

### 2.2 中期优化

- config.py 调度逻辑拆分（scheduler.py）
- 服务器管理重构（依赖注入替代全局变量）
- WebUI app.py 拆分后的文档对齐

### 2.3 长期优化

- 为核心工具函数（`crop()`、`color_similarity()`、`deep_*`）补充单元测试
- 建立文档与代码的自动一致性检查（如行号引用校验）

---

## 三、问题统计

| 严重程度 | 数量 | 状态 |
|---------|------|------|
| 🔴 严重 | 4 | 已确认 |
| 🟡 中等 | 7 | 已确认 |
| 🟢 建议 | 4 | 已确认 |

---

## 四、模块问题索引

| 模块 | 文档链接 | 主要问题 |
|------|---------|---------|
| 入口文件 | [ENTRY-ALAS.md](ENTRY-ALAS.md) | 任务数/常量/行号过时 |
| 基础层 | [BASE.md](BASE.md) | 缺 ssh.py、行数偏差 |
| 配置系统 | [CONFIG.md](CONFIG.md) | 缺 5 个子模块、行数偏差 |
| 设备层 | [DEVICE.md](DEVICE.md) | ssh.py 位置错误、method/ 列表过时 |
| UI 导航 | [UI.md](UI.md) | 缺 setting.py |
| OCR 系统 | [OCR.md](OCR.md) | 缺 ppocr_v6/windows_ml.py |
| 处理器层 | [HANDLER.md](HANDLER.md) | 基本准确，行数偏差 |
| 战斗系统 | [COMBAT.md](COMBAT.md) | 行号漂移、方法签名过时 |
| 战斗 UI | [COMBAT-UI.md](COMBAT-UI.md) | 缺 3 个主题变体（Nier/AzureCore） |
| 地图处理 | [MAP.md](MAP.md) | 继承关系描述错误 |
| 地图检测 | [MAP-DETECTION.md](MAP-DETECTION.md) | OSGrid 类名错误 |
| 战役执行 | [CAMPAIGN.md](CAMPAIGN.md) | os_run/ambush_1_1 定位错误 |
| 游戏功能 | [GAME-FUNCTIONS.md](GAME-FUNCTIONS.md) | 缺 8 个模块、island 清单不全 |
| 大世界 | [OS-SYSTEM.md](OS-SYSTEM.md) | 行数过时、tasks 表缺/错 |
| 基础设施 | [INFRASTRUCTURE.md](INFRASTRUCTURE.md) | webui 章节整体过时 |

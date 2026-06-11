# 岛屿功能扩展设计文档

> 本文档记录岛屿模块新增功能的详细设计方案。

---

## 实现进度 TODO

- [ ] **功能1**: 矿山/森林按库存阈值执行 — 检测仓库数量，低于阈值才安排生产
- [ ] **功能2**: 每日订单功能 — 自动检测订单类型（紧急/挑战/轻松），根据物品条件交付或驳回
- [ ] **功能3**: 货运委托功能 — 重写货运委托系统，五种颜色状态检测+对应处理逻辑
- [ ] **功能4**: 每周珍珠售卖 — 按配置日自动全量卖出珍珠
- [ ] **功能5**: 经营模块智能分波 — 季节限定数量检测替换 + 加成商品检测替换
- [ ] **功能6**: 自动采集角色体力检测 — 体力 < 100 自动换人
- [ ] **功能7**: 摸猫/JUU速运任务 — 每日互动任务
- [ ] **功能8**: 自动清空开发季商店 — 花光开发季 PT 买空商店
- [ ] **功能9**: 给三个小人打招呼 — NPC 互动
- [ ] 配置系统：更新 [`argument.yaml`](module/config/argument/argument.yaml) 和 [`task.yaml`](module/config/argument/task.yaml)
- [ ] i18n：更新翻译文件
- [ ] 运行配置生成器

---

## 目录

1. [功能1: 矿山/森林按库存阈值执行](#功能1-矿山森林按库存阈值执行)
2. [功能2: 每日订单功能](#功能2-每日订单功能)
3. [功能3: 货运委托功能](#功能3-货运委托功能)
4. [功能4: 每周珍珠售卖](#功能4-每周珍珠售卖)
5. [功能5: 经营模块智能分波](#功能5-经营模块智能分波)
6. [功能6: 自动采集角色体力检测换人](#功能6-自动采集角色体力检测换人)
7. [功能7: 摸猫/JUU速运任务](#功能7-摸猫juu速运任务)
8. [功能8: 自动清空开发季商店](#功能8-自动清空开发季商店)
9. [功能9: 给三个小人打招呼](#功能9-给三个小人打招呼)
10. [配置变更总览](#配置变更总览)
11. [任务调度集成](#任务调度集成)

---

## 功能1: 矿山/森林按库存阈值执行

### 目标

在现有 [`IslandMineForest`](module/island/island_mine_forest.py:9) 基础上增加仓库库存检测。当前逻辑是用户配置了哪些岗位就运行哪些岗位（Mining1~4、Felling1~4），不考虑仓库中该产品是否已经足够。改为：先检查仓库库存数量，若该产品库存已超过阈值则跳过该岗位，避免重复生产。

### 现有代码分析

[`IslandMineForest.run()`](module/island/island_mine_forest.py:46) 流程：

```
run()
  ├── 初始化 8 个岗位的时间变量（4 矿 + 4 木）
  ├── 进入生产管理页面 → 切换到生产模式
  ├── 遍历 8 个岗位配置：
  │     └── 如果 product_config != None：
  │           └── run_single_post(post_id, product_name, time_var_name)
  │                 ├── post_close() → post_open(post_id)
  │                 ├── 截图检测是否正在工作中
  │                 │     ├── 工作中 → OCR 剩余时间
  │                 │     └── 空闲 → post_get_and_add() 分配产品 → OCR 工作时间
  │                 └── post_close()
  └── _process_finish_times()
        ├── 收集所有岗位的完成时间
        ├── 添加 6 小时后作为兜底
        └── 设置 task_delay 为最早的完成时间
```

#### 产品配置映射

[`PRODUCT_CONFIGS`](module/island/island_mine_forest.py:13) 定义了 9 种产品（5 矿 + 4 木）与 UI 按钮的对应关系：

```python
PRODUCT_CONFIGS = {
    "Copper":    (SELECT_COPPER, SELECT_COPPER_CHECK, POST_COPPER),
    "Aluminium": (SELECT_ALUMINIUM, SELECT_ALUMINIUM_CHECK, POST_ALUMINIUM),
    "Iron":      (SELECT_IRON, SELECT_IRON_CHECK, POST_IRON),
    "Sulphur":   (SELECT_SULPHUR, SELECT_SULPHUR_CHECK, POST_SULPHUR),
    "Silver":    (SELECT_SILVER, SELECT_SILVER_CHECK, POST_SILVER),
    "Elegant":   (SELECT_ELEGANT, SELECT_ELEGANT_CHECK, POST_ELEGANT),
    "Practical": (SELECT_PRACTICAL, SELECT_PRACTICAL_CHECK, POST_PRACTICAL),
    "Selected":  (SELECT_SELECTED, SELECT_SELECTED_CHECK, POST_SELECTED),
    "Natural":   (SELECT_NATURAL, SELECT_NATURAL_CHECK, POST_NATURAL),
}
```

#### 仓库 OCR 能力

[`WarehouseOCR`](module/island/warehouse.py:6) 已提供：

- 6×2 仓库格子网格：`origin=(301, 150)`, `delta=(142, 167)`, `button_shape=(104, 110)`
- [`ocr_item_quantity(screenshot, template)`](module/island/warehouse.py:16) — 通过模板匹配找到物品格子，OCR 读取右下角数字
- 返回 `int`，匹配不到则返回 0

### 修改方案

修改文件: [`module/island/island_mine_forest.py`](module/island/island_mine_forest.py)

#### 新增方法: `_check_product_needs()`

```python
def _check_product_needs(self, product_name):
    """
    检查指定产品当前库存是否达到阈值。
    使用 WarehouseOCR.ocr_item_quantity() 检测仓库库存。
    
    Args:
        product_name (str): 产品名，如 'Copper', 'Elegant'
    
    Returns:
        bool: True = 需要生产（库存 < 阈值），False = 库存已充足
    """
    template = self.PRODUCT_TEMPLATES[product_name]
    count = self.ocr_item_quantity(self.device.image, template)
    threshold = getattr(self.config, f'IslandMineForest_Min{product_name}', 0)
    return count < threshold
```

#### 新增配置阈值

```yaml
IslandMineForest:
  MinCopper: 0          # 铜矿最低库存阈值，下同
  MinAluminium: 0
  MinIron: 0
  MinSulphur: 0
  MinSilver: 0
  MinElegant: 0
  MinPractical: 0
  MinSelected: 0
  MinNatural: 0
```

#### `run()` 方法修改

```python
def run(self):
    # ... 原有初始化代码 ...

    # 进入仓库页面，截图一次用于库存检测
    self.goto_warehouse()
    self.device.screenshot()

    # 遍历所有岗位，检测库存是否充足
    for post_id, product_config, time_var_name in all_configs:
        if product_config is None:
            continue

        # 检查库存
        if not self._check_product_needs(product_config):
            logger.info(f'{product_config} 库存已充足，跳过岗位')
            continue

        # 库存不足，正常执行
        self.run_single_post(post_id, product_config, time_var_name)
```

### 新增模板资产

需要每个产品对应一个用于仓库识别的 Template：

| 资产 | 说明 |
|------|------|
| `TEMPLATE_COPPER` | 铜矿仓库图标模板 |
| `TEMPLATE_ALUMINIUM` | 铝矿仓库图标模板 |
| `TEMPLATE_IRON` | 铁矿仓库图标模板 |
| `TEMPLATE_SULPHUR` | 硫磺仓库图标模板 |
| `TEMPLATE_SILVER` | 银矿仓库图标模板 |
| `TEMPLATE_ELEGANT` | 优雅木材仓库图标模板 |
| `TEMPLATE_PRACTICAL` | 实用木材仓库图标模板 |
| `TEMPLATE_SELECTED` | 精选木材仓库图标模板 |
| `TEMPLATE_NATURAL` | 自然木材仓库图标模板 |

> **注意**：这些 Template 用于仓库页面的 OCR 识别，和使用中的产品选择按钮（`SELECT_XXX`）不同。

---

## 功能3: 每日订单功能

### 目标

从岛屿手机页面进入每日订单界面，自动检测订单类型（紧急/挑战/轻松），根据物品条件交付或驳回，支持紧急委托的独立刷新计时。

**不支持"标准"类型订单。**

### 新文件

- [`module/island/island_daily_order.py`](module/island/island_daily_order.py) — 每日订单主逻辑
- [`module/island_daily_order/assets.py`](module/island_daily_order/assets.py) — 每日订单专用资源

### 核心流程

```
IslandDailyOrder.run()
  │
  ├── 1. 导航到岛屿手机页面（page_island_phone）
  │
  ├── 2. OCR 检测本周剩余紧急委托数量
  │     ├── 数量为 0 → 紧急委托刷新时间直接设为下周
  │     └── 数量 > 0 → 正常进入
  │
  ├── 3. 进入每日订单界面
  │
  ├── 4. 主循环 ①→②→③→④（详见流程图）
  │
  └── 5. 回到岛屿手机页面
```

### 详细流程图

```
run()
│
├── 进入每日订单界面
│
├── ═══════════════════════════════════════════════
├── ① 检测紧急委托（每次进入页面首次执行）
├── ═══════════════════════════════════════════════
│     │
│     ├── 刷新时间未到 → 跳到 ②
│     │
│     └── 刷新时间已过 → 检测紧急图标（模板匹配）
│           │
│           ├── 没有 → 跳到 ②
│           │
│           └── 有 → 点击 → 点击交付（紧急专用交付按钮）
│                 ├── 资源不足弹窗 → 关闭
│                 │     → OCR 刷新时间（紧急图标下方偏移区域）
│                 │     ├── 成功 → 记录刷新时间 → 回 ①
│                 │     └── 失败 → 回退 8h → 回 ①
│                 │
│                 └── 交付成功
│                       ├── 右侧为空 → 退出重进（回 ①）
│                       └── 否 → 跳到 ②
│
├── ═══════════════════════════════════════════════
├── ② 检测右侧订单页面状态
├── ═══════════════════════════════════════════════
│     │
│     ├── 右侧为空（DAILY_ORDER_CHECK 消失）
│     │     ├── 首次进入 → 延时到第二天
│     │     └── 非首次 → 退出重进（回 ①）
│     │
│     ├── 无驳回按钮（当前是紧急委托页面）→ 跳到 ③
│     │
│     ├── 有驳回按钮 + 可交付 → 交付（普通交付按钮）
│     │     └── 交付后
│     │           ├── 右侧为空 → 退出重进（回 ①）
│     │           └── 否 → 回 ①（不退出重进）
│     │
│     └── 有驳回按钮 + 不可交付 → 驳回
│           ├── 成功 → 筹备中 → 跳到 ③
│           └── 失败（弹窗）→ 季节订单不可操作 → 跳到 ③
│
├── ═══════════════════════════════════════════════
├── ③ 检测左侧所有挑战/轻松图标（match_multi）
├── ═══════════════════════════════════════════════
│     │
│     ├── 挨个点击图标，检测右侧状态
│     │     ├── 可交付（普通交付按钮）→ 跳到 ②
│     │     └── 筹备中 → 点下一个图标
│     │
│     └── 没有更多图标 → 跳到 ④
│
├── ═══════════════════════════════════════════════
├── ④ 退出判断
├── ═══════════════════════════════════════════════
│     │
│     ├── 右侧是筹备中 → OCR 时间，延时等待
│     │
│     └── 右侧不是筹备中 → 延时到第二天
│
└── 回到岛屿手机页面
```

### 关键设计要点

- **紧急委托有专用交付按钮**，与普通订单交付按钮不同
- **紧急委托无驳回按钮**，出现紧急页面时直接跳到 ③
- **驳回可能失败**（季节订单），检测弹窗后跳到 ③
- **右侧为空检测**：首次进入为空则直接第二天，非首次则退出重进
- **挑战/轻松用 match_multi** 获取所有匹配，逐个处理
- **冷却时间 OCR**：资源不足弹窗关闭后，在紧急图标下方固定偏移区域 OCR 冷却时间，失败回退 8h
- **[`UrgentDetectRefreshTime`](module/config/argument/argument.yaml)** 用于控制紧急委托检测频率

---

## 功能4: 货运委托功能

### 目标

重写岛屿货运委托系统。从岛屿手机页面进入货运委托界面，检测三个委托栏位的五种状态并执行对应逻辑：

1. **蓝色（可委托）** — 检测三种物品是否都能装载 → 全部装载 或 切换委托
2. **黄色（可领取）** — 领取奖励 → 关闭领取界面 → 检查周常日常奖励弹窗
3. **黄色（运输中）** — 不做任何操作，等待自然结束
4. **白色（空白项）** — 打开更换委托页面 → 选最上方可用委托切换
5. **灰色（不可委托）** — 跳过，延后处理

### 状态流转生命周期

```
                    用户配置物品充足
                  ┌────────────────────┐
                  │                    ▼
     ⚪ 白色空白 ──►  🔵 蓝色可委托 ──► 点击"全部装载"
          ▲                                │
          │                                ▼
          │                        🟡 黄色运输中
          │                         （等待 2 小时）
          │                                │
          │                                ▼
          │                        🟡 黄色可领取
          │                                │
          │                         点击领取按钮
          │                                │
          │                                ▼
          │                       领取物品弹窗
          │                         关闭弹窗
          │                                │
          │              ┌─────────────────┘
          │              ▼
    白色空白 或 ──►  检查更换委托列表
    蓝色可委托              │
                     ┌─────┴──────┐
                     │            │
                     ▼            ▼
                ⚫ 灰色不可委托   🔵 蓝色可委托
                     │            │
                     │            └──► 委托后变 🟡 运输中
                     ▼
              全部灰色 → 延后 18:00/次日 3:00
              混合灰+运输中 → 2 小时后
```

**核心规则**：
- 蓝色可委托 → 点击开始委托 → 变成黄色运输中
- 黄色运输中 → 等待 2 小时（固定延时）→ 变成黄色可领取
- 黄色可领取 → 领取流程 → 栏位释放为白色空白（或可委托状态）
- 白色空白 → 打开更换列表 → 选定一个新委托：
  - 变成蓝色可委托 → 正常委托 → 进入运输中循环
  - 变成灰色不可委托 → 跳过
- 退出判断：
  - **全部灰色不可委托** → 延后到当天 18:00 或次日 3:00
  - **混合灰色 + 运输中** → 2 小时后再看
  - **所有委托/切换列表都不可完成** → 2 小时后再看

### 新文件

- [`module/island/island_business_transport.py`](module/island/island_business_transport.py) — 货运委托主逻辑
- [`module/island_business_transport/assets.py`](module/island_business_transport/assets.py) — 货运委托专用资源（按钮/模板/OCR 区域）
- [`module/island/assets.py`](module/island/assets.py) — 复用已有按钮定义（`ISLAND_TRANSPORT`, `ISLAND_TRANSPORT_CHECK`, `TRANSPORT_RECEIVE`, `TRANSPORT_START` 等）

### 核心架构

#### 状态枚举

```python
from enum import Enum

class TransportSlotStatus(Enum):
    BLUE_READY = "blue_ready"           # 蓝色 - 可委托（三种物品可装载）
    BLUE_PARTIAL = "blue_partial"       # 蓝色 - 部分物品不可装载（需要切换委托）
    YELLOW_CLAIM = "yellow_claim"       # 黄色 - 可领取奖励
    YELLOW_RUNNING = "yellow_running"   # 黄色 - 正在运输中
    WHITE_EMPTY = "white_empty"         # 白色 - 空白项（需要切换委托）
    GREY_LOCKED = "grey_locked"         # 灰色 - 不可委托
    UNKNOWN = "unknown"                 # 无法识别
```

#### 状态检测流程

```
detect_slot_status(image, slot_index)
  ├── 裁剪指定栏位区域
  ├── 检测颜色特征判断状态：
  │     ├── 灰色区域占比 > 阈值 → GREY_LOCKED
  │     ├── 检测到黄色 "领取" 按钮 → YELLOW_CLAIM
  │     ├── 检测到黄色 "运输中" 时间条 → YELLOW_RUNNING
  │     ├── 检测到蓝色 "开始委托" 按钮 → 进一步判断：
  │     │     ├── 三种物品都可装载 → BLUE_READY
  │     │     └── 有不可装载物品 → BLUE_PARTIAL
  │     └── 无任何按钮 → WHITE_EMPTY
  └── 返回 TransportSlotStatus
```

### 五种状态处理逻辑

#### 1. 蓝色 - 可委托 (`BLUE_READY`)

```
handle_blue_ready(slot_index)
  ├── 检测右侧 TRANSPORT_START 装载按钮颜色
  │     ├── 灰色 → 货物包含牛奶（内置黑名单），转为 BLUE_PARTIAL 逻辑
  │     └── 蓝色 → 点击 TRANSPORT_START
  │           └── 等待装载完成 → 检查是否回到货运委托界面
  └── 返回处理结果
```

**检测说明**：
- 直接检测右侧装载按钮 `TRANSPORT_START` 的区域颜色
- 蓝色 → 可装载，所有货物均可运
- 灰色 → 货物中包含**牛奶**（内置硬编码黑名单），需要切换委托
- **不需**检测蓝色对号、**不需**检测空白格、**不需**检测货物图标

#### 2. 蓝色 - 部分不可装载 (`BLUE_PARTIAL`)

```
handle_blue_partial(slot_index)
  ├── 打开更换委托页面
  │     └── 点击 TRANSPORT_REFRESH（刷新/更换按钮）
  │
  ├── 在更换列表中逐行尝试（从第 1 行开始）：
  │     ├── 切换该行委托到当前栏位
  │     ├── 检测弹窗（两种内容，均点击确定）
  │     ├── 检测 ISLAND_TRANSPORT_CHECK 确认回到货运界面
  │     ├── 检测右侧 TRANSPORT_START 装载按钮颜色
  │     │     ├── 蓝色 → 点击装载 → 成功
  │     │     └── 灰色（仍含牛奶）→ 打开更换列表换下一行
  │     └── 直到找到可装载的委托或遍历完所有行
  │
  ├── 遍历完所有行仍无可装载：
  │     ├── 检测右上角刷新按钮
  │     │     ├── 蓝色 → 点击刷新 → 等待 5s → 重新尝试
  │     │     └── 灰色 → 标记不可委托 → 延时 2 小时
  │     └── 退回上一页
  │
  └── 返回处理结果
```

#### 3. 黄色 - 可领取 (`YELLOW_CLAIM`)

```
handle_yellow_claim(slot_index)
  ├── 点击黄色领取按钮（TRANSPORT_RECEIVE）
  │
  ├── 等待领取物品弹窗出现
  │     └── 检测 GET_ITEMS_ISLAND 弹窗
  │
  ├── 点击安全区关闭弹窗
  │     └── 使用 BUSINESS_REWARD_SAFE_AREA（安全区域 (1100,600,1200,700)）
  │
  ├── 关闭后检查周常/日常奖励弹窗
  │     ├── 检测到 → 逐一点击关闭
  │     └── 未检测到 → 跳过
  │
  ├── 确认回到货运委托界面（ISLAND_TRANSPORT_CHECK）
  │
  └── 返回处理结果
```

#### 4. 黄色 - 运输中 (`YELLOW_RUNNING`)

```
handle_yellow_running(slot_index)
  └── 不做任何操作，直接返回（统一延时 2 小时后再看）
```

#### 5. 白色 - 空白项 (`WHITE_EMPTY`)

```
handle_white_empty(slot_index)
  ├── 打开更换委托页面
  │     └── 点击 TRANSPORT_REFRESH
  │
  ├── 检测是否在更换委托页面（特定图片检测）
  │     └── 如果不在 → 等待重试
  │
  ├── 检测委托列表是否为空
  │     ├── 空 → 直接跳到刷新按钮检查
  │     └── 非空 → 扫描可用委托
  │
  ├── 扫描更换委托列表（最多 6 行，第一页 4 行）
  │     ├── 逐行检测：第一个格子为空 → 该行不存在，停止
  │     ├── 找到第一个全部货物都有蓝色对号的委托 → 点击切换
  │     │     ├── 检测弹窗（两种内容，均点击确定）
  │     │     ├── 检测 ISLAND_TRANSPORT_CHECK 确认回到货运界面
  │     │     └── 检测 TRANSPORT_START 按钮颜色
  │     │           ├── 蓝色 → 装载成功
  │     │           └── 灰色 → 继续换下一行
  │     └── 全部不可用 → 跳到刷新按钮检查
  │
  ├── 刷新按钮检查（列表为空 或 全部不可用时）
  │     ├── 检测右上角刷新按钮颜色
  │     │     ├── 蓝色 → 点击刷新 → 等待 5s（服务器延迟）→ 重新扫描
  │     │     └── 灰色 → 不可刷新，返回上一页
  │     │
  │     └── 标记此栏位为"不可委托"（当做运输中处理，统一延时 2 小时）
  │
  └── 返回处理结果
```

#### 6. 灰色 - 不可委托 (`GREY_LOCKED`)

```
handle_grey_locked(slot_index)
  ├── 不做任何操作
  ├── 记录 "本栏位不可委托，需延后执行"
  ├── 设置全局延时标记：delay_to = 18:00（当天）或 03:00（次日）
  └── 返回处理结果
```

### 主执行流程

```python
class IslandBusinessTransport(IslandUI):
    """货运委托业务处理器"""

    # 货运委托按钮盲点区域（不检测直接点击）
    TRANSPORT_BLIND_CLICK_AREA = (900, 350, 1000, 400)

    def run(self):
        """
        货运委托完整执行流程。

        Pages:
            in: page_island_phone
            out: page_island_phone
        """
        logger.hr('Island Business Transport Run', level=1)
```

#### 流程图

```
IslandBusinessTransport.run()
  │
  ├── 1. 导航到岛屿手机页面（page_island_phone）
  │
  ├── 2. 盲点进入货运委托
  │     └── 点击区域 (900, 350, 1000, 400)
  │
  ├── 3. 等待并检测是否进入货运委托界面
  │     └── 循环检测 self.island_in_transport()（ISLAND_TRANSPORT_CHECK）
  │           ├── 检测到 → 继续
  │           └── 超时 → 记录日志，返回
  │
  ├── 4. 逐栏位处理（3 个委托栏位各处理一次）
  │     ├── 处理前重新截图检测当前栏位状态
  │     │
  │     ├── 情况 YELLOW_CLAIM：
  │     │     ├── 执行领取流程
  │     │     ├── 领取后重新截图检测当前栏位状态
  │     │     │     └── 可能变为 WHITE_EMPTY / BLUE_READY / BLUE_PARTIAL
  │     │     └── 继续处理当前栏位的新状态
  │     │
  │     ├── 情况 BLUE_READY：
  │     │     ├── 点击全部装载
  │     │     └── 栏位变为 YELLOW_RUNNING
  │     │
  │     ├── 情况 BLUE_PARTIAL：
  │     │     ├── 打开更换列表 → 切换委托或标记不可委托
  │     │     └── 切换成功后栏位变为 BLUE_READY 或 GREY_LOCKED
  │     │
  │     ├── 情况 WHITE_EMPTY：
  │     │     ├── 打开更换列表 → 切换委托或标记不可委托
  │     │     └── 切换成功后栏位变为 BLUE_READY 或 GREY_LOCKED
  │     │
  │     ├── 情况 YELLOW_RUNNING：
  │     │     └── 不做任何操作，跳出当前栏位，处理下一个
  │     │
  │     └── 情况 GREY_LOCKED：
  │           └── 记录延时需求，跳出当前栏位，处理下一个
  │
  │     └── 处理完一个栏位后，下一个循环重新截图检测下一栏位
  │
  ├── 5. 退出判断
  │     ├── 所有栏位均为 GREY_LOCKED：
  │     │     └── 延后到当天 18:00 或次日 3:00
  │     │
  │     ├── 有不可完成或无可切换：
  │     │     └── 延时 2 小时等待刷新
  │     │
  │     └── 返回退出原因 + 下次运行时间建议
  │
  └── 6. 检测 ISLAND_BACK 按钮并点击返回岛屿手机页面
        └── 循环检测 ISLAND_BACK → 点击 → 确认回到 page_island_phone
```

### 退出条件与调度集成

| 退出原因 | 下次运行时间 | 说明 |
|----------|-------------|------|
| 全部 GREY_LOCKED | 当天 18:00 或次日 3:00 | 不可委托时段直接跳过 |
| 有不可完成或无可切换 | 2 小时后 | 等待刷新 |

### 新增配置项

```yaml
IslandBusinessTransport:
  Enabled: true                              # 是否启用货运委托
  Blacklist: "Milk"                          # 货物黑名单
                                             # help: 仅支持 Milk（牛奶）。
```

> 延时参数均为固定值：灰色不可委托延后到当天 18:00 或次日 3:00，其他情况统一延时 2 小时。

### 新增资产（assets）需求

需要从游戏截图中提取以下资源：

| 资产 | 类型 | 说明 |
|------|------|------|
| `REFRESH_BUTTON_BLUE` | Button（颜色检测） | 更换委托页面右上角蓝色可刷新按钮（固定位置，检测颜色） |
| `REFRESH_BUTTON_GREY` | Button（颜色检测） | 更换委托页面右上角灰色不可刷新按钮（固定位置，检测颜色） |
| `REPLACE_PAGE_CHECK` | Button（图片匹配） | 更换委托页面特征图片，用于确认是否在更换页面 |
| `EMPTY_LIST_CHECK` | Button（图片匹配） | 更换委托列表为空时的特征图片 |
| `TEMPLATE_MILK` | Template | 牛奶物品模板（内置黑名单，直接比对货物格子） |

**复用已有按钮**（定义在 [`module/island/assets.py`](module/island/assets.py)）：

| 按钮 | 区域 |
|------|------|
| `ISLAND_TRANSPORT` | 岛屿手机页面位置 `(905, 335, 986, 358)`，点击区域 `(898, 328, 1031, 459)` |
| `ISLAND_TRANSPORT_CHECK` | 货运委托界面检测 `(264, 154, 317, 180)` |
| `TRANSPORT_RECEIVE` | 领取按钮 `(938, 206, 1065, 235)` |
| `TRANSPORT_START` | 装载/开始按钮 `(1142, 170, 1174, 281)` |
| `TRANSPORT_REFRESH` | 刷新/更换按钮 `(1062, 207, 1090, 235)` |
| `ISLAND_BACK` | 返回按钮 `(6, 21, 99, 66)` |

### 内置黑名单

- **牛奶**（`TEMPLATE_MILK`）：代码内置硬编码黑名单，直接比对三个货物格子区域，匹配到牛奶模板则装载按钮显示为灰色，需切换委托
- 当前不提供用户配置黑名单的 GUI 渠道
- **不做**物品名称/数量 OCR 识别

---

## 功能4: 每周珍珠售卖

### 目标

每周一（或配置的某天）自动进入珍珠商店，将库存中的珍珠全部卖出。

### 设计方案

**新文件**: [`module/island/island_pearl_sell.py`](module/island/island_pearl_sell.py)

#### 核心类: `IslandPearlSell`

```python
class IslandPearlSell(Island):
    """每周珍珠售卖"""
    
    WEEKDAY_MAP = {
        'monday': 0,
        'tuesday': 1,
        # ...
    }
    
    def run(self):
        """
        流程：
        1. 检查今天是否是设定的售卖日
        2. 导航到岛屿商店
        3. 进入珍珠商店页签
        4. 检测当前珍珠数量
        5. 如果有珍珠，执行全量卖出
        6. 设置下周同一时间运行
        """
```

#### 流程详解

```
run()
  ├── if today != config.sell_day: skip
  ├── navigate_to_island_shop()
  ├── switch_to_pearl_tab()
  ├── detect_pearl_count()        # OCR 检测珍珠数量
  ├── if count > 0:
  │     ├── click_sell_all()       # 点击全部卖出
  │     └── confirm_sell()         # 确认
  └── schedule_next_week()        # 设置下周运行
```

#### 珍珠售卖UI

- 珍珠商店有专门页签
- 显示当前珍珠数量
- 有"全部卖出"按钮
- 确认弹窗

#### 配置项

```yaml
IslandPearlSell:
  Enabled: false                     # 默认关闭
  SellDay: monday                    # 售卖日（周一~周日）
  SellTime: "09:00"                  # 售卖时间
```

---

## 功能5: 经营模块智能分波

### 目标

对现有 [`IslandBusiness`](module/island/island_business.py:39) 经营模块进行三项增强：

1. **分两波经营**：将 5 个商店分成两波执行，避免单次运行时间过长
2. **季节限定检测替换**：检测季节限定餐品库存数量，如果 < 7 则替换为另一个可配置菜品
3. **加成商品检测替换**：检测当天具有加成的商品，如果条件满足则替换，优先从下往上替换

### 设计方案

修改文件: [`module/island/island_business.py`](module/island/island_business.py)

#### 6.1 分两波经营

将 5 个商店分成两个 Wave：

```python
WAVE_CONFIG = {
    'wave1': {
        'shops': ['有鱼餐馆', '白熊饮品', '啾啾简餐'],  # 前3个
        'priority': 70,
    },
    'wave2': {
        'shops': ['乌鱼烤肉', '啾咖啡'],                # 后2个
        'priority': 71,
    }
}
```

**配置项**：

```yaml
IslandBusiness:
  WaveEnabled: true              # 启用分波
  Wave1Shops: [1, 2, 3]         # 第一波商店索引
  Wave2Shops: [4, 5]            # 第二波商店索引
  Wave1Time: "08:00"            # 第一波执行时间
  Wave2Time: "14:00"            # 第二波执行时间
```

**执行流程**：

```python
def should_run_wave1(self):
    """判断第一波是否需要执行"""
    now = datetime.now()
    wave1_time = now.replace(hour=8, minute=0, second=0)
    return now >= wave1_time

def should_run_wave2(self):
    """判断第二波是否需要执行"""
    now = datetime.now()
    wave2_time = now.replace(hour=14, minute=0, second=0)
    return now >= wave2_time

def run_wave(self, shop_list):
    """执行指定商店列表的经营"""
    for shop in shop_list:
        self._process_single_shop(shop)
```

#### 6.2 季节限定餐品数量检测替换

核心逻辑：在经营前检查仓库中季节限定餐品的数量，如果 < 7 则替换为备用菜品。

```python
def check_seasonal_dish_quantity(self, shop_name):
    """
    检查指定商店的季节限定餐品库存数量。
    
    Returns:
        list: 需要替换的餐品列表 [(原餐品, 替换餐品), ...]
    """
    replacement_plan = []
    
    # 获取当前商店的季节限定餐品
    seasonal_items = self._get_seasonal_items_for_shop(shop_name)
    if not seasonal_items:
        return replacement_plan
    
    # 遍历当前激活的餐品配置，检查季节限定品
    for product in self.active_products.get(shop_name, []):
        if product['name'] in seasonal_items:
            # 通过仓库 OCR 检测该餐品数量
            count = self._check_product_quantity(product['name'])
            if count < 7:
                logger.info(
                    f"{shop_name}: 季节限定 {product['name']} 库存 {count} < 7，"
                    f"需要替换"
                )
                # 查找备用菜品配置
                fallback = self._get_fallback_product(shop_name, product['name'])
                if fallback:
                    replacement_plan.append((product, fallback))
    
    return replacement_plan
```

**备用菜品配置**：

```yaml
IslandBusinessShop1:        # 有鱼餐馆
  # ... 现有 Product1~5 配置
  SeasonalFallback1: hearty_meal   # 季节限定餐品1的备用替换
  SeasonalFallback2: fo_tiao       # 季节限定餐品2的备用替换
```

#### 6.3 加成商品检测替换

核心逻辑：进入商店后，检测当天具有加成的商品（UI 上带有"推荐""流行"等标记），如果加成条件满足则替换当前商品，**优先从下往上替换**（即先替换最后一个槽位的商品）。

```python
def check_boosted_products(self, shop_name):
    """
    检测当天具有加成的商品，从下往上替换。
    
    加成判定：
    - 游戏 UI 中商品可能有特殊标记（如"推荐"标签、高亮边框等）
    - 通过模板匹配或颜色检测识别加成标记
    
    替换策略（从下往上）：
    - 假设有 5 个商品槽位：P1, P2, P3, P4, P5
    - 优先替换 P5 → P4 → P3 → P2 → P1
    """
    # 1. 截图商品区域
    self.device.screenshot()
    
    # 2. 检测所有商品的加成状态
    boosted_products = self._detect_boosted(shop_name)
    if not boosted_products:
        logger.info(f"{shop_name}: 没有检测到加成商品")
        return
    
    # 3. 获取当前已选择的商品列表（从下往上遍历）
    current_products = self.active_products.get(shop_name, [])
    # 倒序，从最后一个槽位开始检查
    for product in reversed(current_products):
        # 检查是否有加成商品可以替换这个槽位
        for boosted in boosted_products:
            if boosted['name'] != product['name']:
                # 检查加成条件是否满足
                if self._check_boost_condition(boosted):
                    logger.info(
                        f"{shop_name}: 加成商品 {boosted['name']} "
                        f"替换槽位商品 {product['name']}（从下往上）"
                    )
                    # 执行替换
                    self._replace_product(shop_name, product, boosted)
                    break
```

**加成商品检测方法**：

```python
def _detect_boosted(self, shop_name):
    """
    检测加成商品。
    
    方法1: 模板匹配 — 检测商品图标上的"推荐"标签
    方法2: 颜色检测 — 检测商品边框的特殊颜色
    方法3: OCR — 读取商品名称旁边的特殊文本
    """
    products = self.shop_products.get(shop_name, [])
    boosted = []
    
    for p in products:
        b = p.get('button')
        if not b:
            continue
        
        # 检测商品区域是否有"推荐"模板
        if hasattr(self, f'TEMPLATE_BOOSTED_{p["name"].upper()}'):
            template = getattr(self, f'TEMPLATE_BOOSTED_{p["name"].upper()}')
            if self.appear(template, offset=30):
                boosted.append(p)
                continue
        
        # 或者检测商品图标上叠加的加成标记
        TEMPLATE_BOOSTED_TAG = getattr(self, 'TEMPLATE_BOOSTED_TAG', None)
        if TEMPLATE_BOOSTED_TAG:
            area_img = crop(self.device.image, b.area)
            if TEMPLATE_BOOSTED_TAG.match(area_img, similarity=0.8):
                boosted.append(p)
    
    return boosted
```

**配置项**：

```yaml
IslandBusiness:
  BoostedReplace: true            # 启用加成商品替换
  BoostThreshold: 1.2             # 加成倍率阈值（如 1.2 倍以上才替换）
  BoostReplaceDirection: bottom_up  # 替换方向：bottom_up（从下往上）
```

---

## 功能6: 自动采集角色体力检测换人

### 目标

在 [`IslandDailyGather`](module/island/island_daily_gather.py:14) 每日自动采集流程中，选择角色时检测其剩余体力（疲劳值），如果低于 100 则换人。

### 设计方案

修改文件: [`module/island/island_daily_gather.py`](module/island/island_daily_gather.py)

#### 核心逻辑

```python
def select_collector_with_stamina(self):
    """
    选择采集角色时检测体力，低于 100 则换人。
    
    流程：
    1. 进入角色选择界面
    2. 对角色列表按"生活等级"升序排序
    3. 遍历角色列表，OCR 检测每个角色的体力值
    4. 如果体力 >= 100，选择该角色
    5. 如果所有角色体力都 < 100，选择体力最高的
    
    OCR 区域说明：
    - 体力值通常显示在角色头像旁边的数字
    - 格式如 "100/100" 或纯数字 "100"
    - 使用 Digit 或 Duration 进行 OCR
    """
```

#### 体力检测区域

```python
# 每个角色卡片上体力值显示区域（相对角色卡片区域的偏移）
STAMINA_AREA_RELATIVE = (80, 5, 120, 25)  # (x1, y1, x2, y2) 相对角色按钮
```

#### 修改后的角色选择流程

```python
def _select_characters_with_stamina_check(self):
    """
    修改后的角色选择流程（替换原来的简单选择）。
    
    步骤：
    1. 打开角色选择界面 → 按生活等级排序
    2. 对每个 "+" 按钮：
       a. 遍历角色列表（从上到下）
       b. 对每个角色截图体力区域 → OCR 读取数值
       c. 如果体力 >= 100 → 选中该角色
       d. 如果体力 < 100 → 跳过，继续找下一个
       e. 如果遍历完所有角色都 < 100 → 选体力最高的
    3. 确认选择
    """
    for slot_idx in range(3):  # 3 个采集槽位
        # 点击 "+" 按钮
        # 等待角色选择界面出现
        
        best_char = None       # (button, stamina)
        for attempt in range(max_swipes):
            self.device.screenshot()
            
            # 遍历当前页面的角色
            for char_button in self._get_visible_characters():
                stamina = self._ocr_stamina(char_button)
                logger.info(f"角色体力: {stamina}")
                
                if stamina >= 100:
                    # 找到体力充足的，直接选中
                    self.device.click(char_button)
                    self.device.sleep(0.5)
                    self.device.click(SELECT_UI_CONFIRM)
                    return
                
                # 记录体力最高的
                if best_char is None or stamina > best_char[1]:
                    best_char = (char_button, stamina)
            
            # 向下滑动查看更多角色
            self._swipe_down()
        
        # 没有体力 >= 100 的角色，选体力最高的
        if best_char:
            logger.warning(f"没有体力>=100的角色，选择体力最高的: {best_char[1]}")
            self.device.click(best_char[0])
            self.device.sleep(0.5)
            self.device.click(SELECT_UI_CONFIRM)
```

#### OCR 体力值

```python
def _ocr_stamina(self, char_button):
    """
    对角色按钮区域的体力值进行 OCR。
    
    Args:
        char_button: 角色按钮
        
    Returns:
        int: 体力值，OCR 失败返回 0
    """
    # 体力区域：角色按钮右上方的小数字
    stamina_area = (
        char_button.area[0] + self.STAMINA_AREA_RELATIVE[0],
        char_button.area[1] + self.STAMINA_AREA_RELATIVE[1],
        char_button.area[0] + self.STAMINA_AREA_RELATIVE[2],
        char_button.area[1] + self.STAMINA_AREA_RELATIVE[3],
    )
    
    ocr_btn = Button(
        area=stamina_area, color=(),
        button=stamina_area,
        file={'cn': '', 'en': '', 'jp': '', 'tw': ''}
    )
    
    ocr = Digit(ocr_btn, letter=(255, 255, 255), threshold=200)
    try:
        value = ocr.ocr(self.device.image)
        return int(value) if value else 0
    except:
        return 0
```

#### 配置项

```yaml
IslandDailyGather:
  StaminaThreshold: 100         # 体力阈值，低于此值换人
  StaminaCheckEnabled: true     # 启用体力检测
```

---

## 功能7: 摸猫/JUU速运任务

### 目标

每日在岛屿上执行两个小互动：

1. **摸猫**: 点击岛上出现的猫，获得好感度和随机奖励
2. **JUU速运**: 在岛屿场景中找到并点击啾啾速运的NPC/建筑物，领取或提交速运任务

### 设计方案

**新文件**: [`module/island/island_daily_interact.py`](module/island/island_daily_interact.py)

#### 核心类: `IslandDailyInteract`

```python
class IslandDailyInteract(Island):
    """每日互动任务：摸猫、JUU速运"""
    
    def run(self):
        """执行每日互动"""
        self.goto_island()
        self.pet_cat()
        self.juu_express()
    
    def pet_cat(self):
        """
        摸猫流程：
        1. 在岛屿主场景中寻找猫
        2. 猫会随机出现在岛屿各个位置
        3. 通过模板匹配检测猫
        4. 点击猫 → 出现互动弹窗
        5. 点击"抚摸"按钮
        6. 关闭奖励弹窗
        """
    
    def juu_express(self):
        """
        JUU速运流程：
        1. 在岛屿场景中寻找JUU速运点
        2. 通常在地图固定位置
        3. 点击进入速运界面
        4. 领取已完成速运的奖励
        5. 如果有可用速运，接受
        """
```

#### 摸猫检测

- 猫的图像模板存储在 [`assets/`](assets/) 中
- 猫可能出现在多个位置，使用模板匹配扫描全屏
- 如果没有检测到猫，说明已经被摸过了或不在场景中

#### JUU速运检测

- JUU速运在岛屿地图上有固定位置的建筑物/NPC
- 需要先进入岛屿场景，然后寻找速运入口
- 速运界面有：领取奖励、查看速运列表

#### 配置项

```yaml
IslandDailyInteract:
  PetCat: true               # 启用摸猫
  JuuExpress: true           # 启用JUU速运
```

---

## 功能8: 自动清空开发季商店

### 目标

开发季活动期间，自动进入开发季商店，用金币买空商店中有价值的物品（如"开发核心""繁荣之基""发展支柱"等）。

### 设计方案

**新文件**: [`module/island/island_dev_shop.py`](module/island/island_dev_shop.py)

#### 核心类: `IslandDevShop`

```python
class IslandDevShop(Island):
    """自动清空开发季商店"""

    # 购买优先级：稀有物品优先
    PURCHASE_PRIORITY = [
        'development_core',      # 开发核心（高价值）
        'prosperity_foundation', # 繁荣之基
        'development_pillar',    # 发展支柱
        # ... 其他物品按价值降序
    ]

    def run(self):
        """检查金币 → 进入开发季商店 → 按优先级购买 → 花光金币"""
```

#### 流程

```
run()
  ├── goto_island_shop()
  ├── switch_to_dev_shop_tab()         # 切换到开发季商店页签
  ├── detect_gold()                    # OCR 当前持有金币数量
  ├── if gold == 0: return             # 没钱就跳过
  │
  ├── scan_shop_items()                # 扫描商店中所有可购买物品
  │     ├── 模板匹配识别物品图标
  │     └── OCR 物品价格（金币数）
  │
  ├── generate_purchase_plan()         # 按优先级生成购买计划
  │     ├── 优先购买稀有物品（开发核心等）
  │     ├── 剩余金币购买常规资源
  │     └── 金币不够买高优物品时跳过
  │
  └── execute_purchase_plan()          # 执行购买
        ├── click_item()
        ├── confirm_purchase()
        └── repeat until gold runs out
```

#### 金币检测

```python
def detect_gold(self):
    """OCR 检测当前持有金币数量（在商店页面顶部显示）"""
    ocr = Digit(OCR_GOLD, letter=(255, 255, 255), threshold=200)
    return ocr.ocr(self.device.image)
```

#### 购买优先级配置

```yaml
IslandDevShop:
  Enabled: true                # 启用自动清空
  ReserveGold: 0               # 保留金币数量（不花光）
```

#### 物品识别

- 使用模板匹配识别开发季商店中的各个物品
- 物品图标需要从游戏中截图后通过 [`button_extract.py`](dev_tools/button_extract.py) 提取

#### 配置项

```yaml
IslandDevShop:
  Enabled: true
  ReserveGold: 0
```

---

## 功能9: 给三个小人打招呼

### 目标

岛屿场景中会出现三个可互动的小人（NPC/游客），点击给他们打招呼可以获得友好度或小奖励。

### 设计方案

在 [`module/island/island_daily_interact.py`](module/island/island_daily_interact.py) 中扩展。

#### 核心逻辑

```python
def greet_npcs(self):
    """
    给三个小人打招呼流程：
    1. 进入岛屿主场景
    2. 扫描场景中出现的 NPC 小人
    3. 小人有三种类型/位置
    4. 依次点击每个小人
    5. 点击"打招呼"按钮
    6. 关闭弹窗
    """
    
    NPC_TEMPLATES = [
        TEMPLATE_NPC_1,   # 小人1的模板
        TEMPLATE_NPC_2,   # 小人2的模板
        TEMPLATE_NPC_3,   # 小人3的模板
    ]
```

#### NPC 检测

- 三个小人在岛屿场景中的固定或半固定位置
- 使用模板匹配检测
- 可能出现在不同区域，需要扫描
- 如果已被打过招呼，小人可能消失或变为不同状态

#### 挑战与注意事项

1. 小人可能不在当前屏幕视口中，需要滑动地图寻找
2. 小人出现有随机性，不是每天都会出现
3. 点击后的弹窗需要处理

#### 配置项

```yaml
IslandDailyInteract:
  GreetNpcs: true            # 启用打招呼
  GreetNpcCount: 3           # 打招呼数量
```

---

## 配置变更总览

### [`argument.yaml`](module/config/argument/argument.yaml) 新增配置

```yaml
# ==================== Island 新增配置 ====================

IslandMine:
  # 现有 Mining1~4
  # --- 新增阈值 ---
  MinCopper: 0
  MinAluminium: 0
  MinIron: 0
  MinSulphur: 0
  MinSilver: 0

IslandForest:
  # 现有 Felling1~4
  # --- 新增阈值 ---
  MinElegant: 0
  MinPractical: 0
  MinSelected: 0
  MinNatural: 0

IslandBusiness:
  # --- 分波经营 ---
  WaveEnabled: true
  Wave1Shops: [1, 2, 3]
  Wave2Shops: [4, 5]
  Wave1Time: "08:00"
  Wave2Time: "14:00"
  # --- 季节限定替换 ---
  SeasonalReplaceEnabled: true
  SeasonalThreshold: 7
  # --- 加成商品替换 ---
  BoostedReplace: true
  BoostThreshold: 1.2
  BoostReplaceDirection: bottom_up

IslandBusinessShop1:
  SeasonalFallback1: hearty_meal
  SeasonalFallback2: fo_tiao

IslandBusinessTransport:
  Enabled: true
  MaxRetrySwitch: 3
  DelayEveningHour: 18
  DelayEarlyMorningHour: 3
  FallbackDelayHours: 2

IslandDailyGather:
  StaminaThreshold: 100
  StaminaCheckEnabled: true

IslandDailyOrder:
  Enabled: true
  AutoAccept: true
  MaxOrders: 3

IslandPearlSell:
  Enabled: false
  SellDay:
    value: monday
    option: [monday, tuesday, wednesday, thursday, friday, saturday, sunday]
  SellTime: "09:00"

IslandDevShop:
  Enabled: true
  ReserveGold: 0

IslandDailyInteract:
  PetCat: true
  JuuExpress: true
  GreetNpcs: true
  GreetNpcCount: 3
```

---

## 任务调度集成

各岛屿任务作为 `Island` 组下的独立任务，由 [`alas.py`](alas.py) 调度器按标准流程运行。
关键在于各任务模块内部自行处理运行频率和状态判断，调度器仅负责按配置触发。

### 优先级参考

```
高频率（每次岛屿轮次都执行）：
  收取货运委托
  每日订单
  农场/矿山/牧场
  经营/制造/餐饮

低频率（按条件触发）：
  每周珍珠（仅配置日）
  摸猫/速运/打招呼（每日一次）
  每日采集（每日一次）
  季节任务（按需）
```

---

## 文件结构变化

```
module/island/
├── island.py                    # 已有 - 核心类
├── island_daily_order.py        # 新增 - 每日订单
├── island_pearl_sell.py         # 新增 - 珍珠售卖
├── island_dev_shop.py           # 新增 - 开发季商店清空
├── island_daily_interact.py     # 新增 - 摸猫/速运/打招呼
├── island_business_transport.py # 新增 - 货运委托业务
├── island_mine_forest.py        # 修改 - 增加库存检测
├── island_business.py           # 修改 - 分波 + 季节检测 + 加成检测
├── island_daily_gather.py       # 修改 - 体力检测换人
├── transport.py                 # 已有 - 旧货运委托（可选保留）
├── assets.py                    # 已有 - 通用按钮资源
├── ...
module/island_business_transport/
├── assets.py                    # 新增 - 货运委托专用资源
```

---

## i18n 新增 Key

在 [`zh-CN.json`](module/config/i18n/zh-CN.json) 等翻译文件中新增：

```json
{
  "Island.IslandMine.MinCopper": "铜矿最低库存",
  "Island.IslandMine.MinAluminium": "铝矿最低库存",
  "Island.IslandMine.MinIron": "铁矿最低库存",
  "Island.IslandMine.MinSulphur": "硫磺最低库存",
  "Island.IslandMine.MinSilver": "银矿最低库存",
  "Island.IslandForest.MinElegant": "优雅木材最低库存",
  "Island.IslandForest.MinPractical": "实用木材最低库存",
  "Island.IslandForest.MinSelected": "精选木材最低库存",
  "Island.IslandForest.MinNatural": "自然木材最低库存",

  "Island.IslandBusiness.WaveEnabled": "启用分波经营",
  "Island.IslandBusiness.WaveEnabled.description": "将5个商店分成两波执行",
  "Island.IslandBusiness.SeasonalReplaceEnabled": "季节限定替换",
  "Island.IslandBusiness.SeasonalReplaceEnabled.description": "季节限定菜品库存不足时自动替换",
  "Island.IslandBusiness.SeasonalThreshold": "季节限定替换阈值",
  "Island.IslandBusiness.BoostedReplace": "加成商品替换",
  "Island.IslandBusiness.BoostedReplace.description": "检测当天加成商品自动替换（从下往上）",
  "Island.IslandBusiness.BoostThreshold": "加成倍率阈值",
  "Island.IslandBusinessShop1.SeasonalFallback1": "季节限定备用菜品1",

  "Island.IslandBusinessTransport.Enabled": "启用货运委托",
  "Island.IslandBusinessTransport.Enabled.description": "自动管理岛屿货运委托",
  "Island.IslandBusinessTransport.MaxRetrySwitch": "切换最大重试次数",
  "Island.IslandBusinessTransport.DelayEveningHour": "延后傍晚时间",
  "Island.IslandBusinessTransport.DelayEveningHour.description": "灰色不可委托延后到当天几点",
  "Island.IslandBusinessTransport.DelayEarlyMorningHour": "延后凌晨时间",
  "Island.IslandBusinessTransport.FallbackDelayHours": "回退延时（小时）",
  "Island.IslandBusinessTransport.FallbackDelayHours.description": "特殊情况回退等待小时数",

  "Island.IslandDailyGather.StaminaThreshold": "体力阈值",
  "Island.IslandDailyGather.StaminaThreshold.description": "角色体力低于此值则换人",
  "Island.IslandDailyGather.StaminaCheckEnabled": "启用体力检测",

  "Island.IslandDailyOrder.Enabled": "启用每日订单",
  "Island.IslandPearlSell.Enabled": "启用珍珠售卖",
  "Island.IslandPearlSell.SellDay": "售卖日",
  "Island.IslandDevShop.Enabled": "启用开发季商店清空",
  "Island.IslandDevShop.ReserveGold": "保留金币数量",
  "Island.IslandDailyInteract.PetCat": "摸猫",
  "Island.IslandDailyInteract.JuuExpress": "JUU速运",
  "Island.IslandDailyInteract.GreetNpcs": "打招呼"
}
```

---

## 实现优先级

| 优先级 | 功能 | 预计工作量 |
|--------|------|-----------|
| P0 | 矿山/森林库存阈值 | 小（改现有代码） |
| P0 | 经营模块智能分波 | 中（改现有代码） |
| P0 | 经营模块季节限定替换 | 中（新增逻辑） |
| P1 | 货运委托重写 | 中（新文件：五种状态检测+处理逻辑） |
| P1 | 经营模块加成商品替换 | 中（需截图资源） |
| P1 | 每日订单 | 中（新功能） |
| P1 | 自动采集体力检测换人 | 小（改现有代码） |
| P2 | 珍珠售卖 | 小（新功能） |
| P2 | 摸猫/JUU速运 | 中（需截图资源） |
| P2 | 开发季商店清空 | 中（需截图） |
| P3 | 打招呼 | 中（需截图资源） |

> **注意**: 摸猫、JUU速运、给小人打招呼、加成商品检测等功能需要游戏内截图资源（Button/Template），需先通过 `dev_tools/button_extract.py` 从截图中提取按钮定义才能实现。

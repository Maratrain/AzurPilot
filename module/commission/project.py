"""委托信息解析模块。

负责从委托界面截图中解析单条委托的全部属性，包括名称 OCR 识别、
委托类型匹配、执行时长解析、状态判断和后缀图像提取。

核心类 Commission 封装了一条委托的所有信息，并通过 @Config.when
装饰器为 CN/EN/JP/TW 四个服务器分别实现不同的解析逻辑。

本模块还定义了 COMMISSION_FILTER 过滤器实例，用于根据用户配置的
规则（如 'daily_resource-01:30'）筛选和排序委托列表。

依赖：
    - module.base.filter: 正则过滤器框架
    - module.ocr.ocr: OCR 文字识别（Duration、Ocr）
    - module.commission.project_data: 各服务器的委托名称字典
"""

from datetime import datetime, timedelta

from module.base.decorator import Config
from module.base.filter import Filter
from module.base.utils import *
from module.commission.project_data import *
from module.config.time_source import now as current_time
from module.logger import logger
from module.ocr.ocr import Duration, Ocr
from module.reward.assets import *

class CommissionFilter(Filter):
    """支持价值分层和最短耗时兜底的委托过滤器。"""

    def apply_first(self, objs, count, func=None):
        """应用前若干条普通委托过滤规则。

        ``tier`` 和 ``shortest`` 仅用于控制委托选择策略，不代表具体的
        委托类型，因此不占用高价值过滤器数量。被多条规则匹配的同一委托
        只返回一次。

        Args:
            objs: 待匹配委托。
            count: 从过滤器开头选取的普通规则数量。
            func: 额外可用性检查函数。

        Returns:
            list: 按过滤器优先级排列且去重后的匹配委托。
        """
        count = max(int(count), 0)
        out = []
        applied = 0
        for raw, parsed in zip(self.filter_raw, self.filter):
            if self.is_preset(raw):
                continue
            if applied >= count:
                break
            applied += 1

            for obj in objs:
                if obj in out:
                    continue
                if self.apply_filter_to_obj(obj=obj, filter=parsed):
                    out.append(obj)

        if func is not None:
            out = [obj for obj in out if func(obj)]
        return out

    def apply_tiers(self, objs, func=None):
        """把过滤结果按价值层级分组。

        含 ``tier`` 时，相邻两个 ``tier`` 之间的规则属于同一价值层级，
        层级内保留规则编号，用于计算稳定的有限层内价值；
        不含 ``tier`` 的旧配置保持原行为，每条规则视为一个独立层级。
        ``shortest`` 会把尚未匹配的可用委托放入当前位置对应的最低层级。
        空层级和未匹配规则不会被压缩，避免候选价值随当前可见列表漂移。

        Args:
            objs: 待匹配委托。
            func: 额外可用性检查函数。

        Returns:
            list[list[tuple[int, Commission]]]: 从高到低排列的委托层级，
                元组首项是该层内稳定过滤器编号。
        """
        objs = [obj for obj in objs if func is None or func(obj)]
        has_tier = any(raw.lower() == 'tier' for raw in self.filter_raw)
        groups = []
        shortest_group = None
        matched = set()

        if has_tier:
            groups.append([])
            filter_index = 0
            for raw, parsed in zip(self.filter_raw, self.filter):
                token = raw.lower()
                if token == 'tier':
                    groups.append([])
                    filter_index = 0
                    continue
                if token == 'shortest':
                    shortest_group = len(groups) - 1
                    shortest_filter_index = filter_index
                    filter_index += 1
                    continue

                for obj in objs:
                    identity = id(obj)
                    if identity in matched:
                        continue
                    if self.apply_filter_to_obj(obj=obj, filter=parsed):
                        groups[-1].append((filter_index, obj))
                        matched.add(identity)
                filter_index += 1
        else:
            shortest_filter_index = 0
            for raw, parsed in zip(self.filter_raw, self.filter):
                token = raw.lower()
                if token == 'tier':
                    continue
                groups.append([])
                if token == 'shortest':
                    shortest_group = len(groups) - 1
                    continue

                for obj in objs:
                    identity = id(obj)
                    if identity in matched:
                        continue
                    if self.apply_filter_to_obj(obj=obj, filter=parsed):
                        groups[-1].append((0, obj))
                        matched.add(identity)

        if shortest_group is not None:
            fallback = [obj for obj in objs if id(obj) not in matched]
            fallback.sort(key=lambda obj: (obj.duration, obj.genre, obj.repeat_count))
            groups[shortest_group].extend(
                (shortest_filter_index, obj) for obj in fallback
            )

        return groups

COMMISSION_FILTER = CommissionFilter(
    regex=re.compile(
        '(major|daily|extra|urgent|night)?'
        '-?'
        '(resource|chip|event|drill|part|cube|oil|book|retrofit|box|gem|ship)?'
        '-?'
        '(\d\d?:\d\d)?'
        '(\d\d?.\d\d?|\d\d?)?'
    ),
    attr=('category_str', 'genre_str', 'duration_hm', 'duration_hour'),
    preset=('shortest', 'tier')
)


def crop_suffix_image(image, area):
    """裁剪委托名称右侧的罗马数字后缀图像。

    Args:
        image: 游戏截图。
        area: 委托名称区域。

    Returns:
        后缀裁剪图，黑字白底；未检测到文字时返回 None。
    """
    name_image = crop(image, area)
    name_image = extract_letters(name_image, letter=(255, 255, 255), threshold=128).astype(np.uint8)

    line = cv2.reduce(name_image[5:-5, :], 0, cv2.REDUCE_AVG).flatten()
    columns = np.where(line < 250)[0]
    if not len(columns):
        return None

    # 从最右侧文字向左回看，尽量完整包含罗马数字后缀。
    threshold = 250
    look_back = 10
    for i in range(columns[-1], 0, -1):
        if line[i] > threshold:
            if columns[-1] - i > look_back:
                look_back = columns[-1] - i
                break

    left = columns[-1] - look_back
    right = columns[-1] + 1
    x1, y1 = area[0:2]
    suffix_area = area_offset((left - 3, -3, right + 3, name_image.shape[0] + 3), (x1, y1))
    image = crop(image, suffix_area)
    image = extract_letters(image, letter=(255, 255, 255), threshold=128).astype(np.uint8)
    return image


def image_hash(image):
    """计算图像哈希，用于日志输出。

    Args:
        image: 输入图像。

    Returns:
        图像 MD5；图像为空时返回空字符串。
    """
    if image is None:
        return ''

    import hashlib
    return hashlib.md5(image.tobytes()).hexdigest()


class Commission:
    """单条委托信息。

    封装从委托界面截图中解析出的所有属性，包括名称、类型、状态、时长等。
    支持 CN/EN/JP/TW 四个服务器，通过 `@Config.when` 装饰器分发不同的解析逻辑。
    """

    # 进入委托详情的按钮
    button: Button
    # OCR 识别出的委托名称
    name: str
    # 委托名称是否解析成功
    valid: bool
    # 裁剪出的后缀图像，黑字白底；无后缀时为 None
    suffix_image: np.ndarray
    # 后缀图像哈希，仅用于日志；无后缀时为空字符串
    suffix_hash: str
    # 委托类型名称，定义在 project_data.py 中
    # 值: major_comm, daily_resource, urgent_cube, ...
    genre: str
    # 委托状态
    # 值: finished, running, pending
    status: str
    # 委托执行时长
    duration: timedelta
    # 剩余可启动时间，仅紧急委托有值，其他委托为 0
    available_time: timedelta
    # 最晚可启动时刻，用于规划和跨截图稳定比较；非紧急委托为 None
    deadline_time: datetime | None
    # 过滤器用分类
    # 值: major|daily|extra|urgent|night
    category_str: str
    # 过滤器用类型
    # 值: resource|chip|event|drill|part|cube|oil|book|retrofit|box|gem|ship
    genre_str: str
    # 时长（小时），如 0.5, 1, 1.16, 2.5
    duration_hour: str
    # 时长（HH:MM 格式），如 1:30, 1:45, 2:00, 8:00, 12:00
    duration_hm: str

    def __init__(self, image, y, config):
        """从截图中解析委托信息。

        根据 y 坐标确定委托条目的裁剪区域，调用 commission_parse 解析各项属性，
        并计算过滤器所需的分类和时长字段。

        Args:
            image: 游戏截图。
            y: 委托条目底部的 y 坐标。
            config: AzurPilot 配置对象。
        """
        self.config = config
        self.y = y
        self.area = (188, y - 119, 1199, y)
        self.image = image
        self.valid = True
        self.commission_parse()

        if not self.duration.total_seconds():
            self.valid = False

        self.create_time = current_time()
        self.deadline_time = (
            (self.create_time + self.available_time).replace(microsecond=0)
            if self.available_time else None
        )
        self.repeat_count = 1
        self.category_str = 'unknown'
        self.genre_str = 'unknown'
        self.duration_hour = 'unknown'
        self.duration_hm = 'unknown'
        if self.valid:
            self.category_str, self.genre_str = self.genre.split('_', 1)
            self.duration_hour = str(int(self.duration.total_seconds() / 36) / 100).strip('.0')
            self.duration_hm = str(self.duration).rsplit(':', 1)[0]

    @property
    def is_gem_commission(self):
        return self.valid and self.genre == 'urgent_gem'

    def _commission_available_time_parse(self):
        """识别紧急委托的剩余可启动时间。

        Returns:
            timedelta: 紧急委托的剩余有效时间；非紧急委托返回 0。
        """
        # 紧急委托在时长左侧有红色提示标记，先用颜色判断可避免无效 OCR。
        area = area_offset((-49, 68, -45, 84), self.area[0:2])
        button = Button(area=area, color=(189, 65, 66),
                        button=area, name='IS_URGENT')
        if not button.appear_on(self.image, threshold=30):
            return timedelta(seconds=0)

        area = area_offset((-49, 67, 45, 94), self.area[0:2])
        button = Button(area=area, color=(), button=area, name='DEADLINE')
        available_time = Duration(button).ocr(self.image)
        if not available_time:
            logger.warning('[委托-检测] 紧急委托可启动时间识别失败')
            self.valid = False
        return available_time

    @Config.when(SERVER='en')
    def commission_parse(self):
        """解析委托信息（EN 服务器）。

        EN 服委托名称较长，OCR 裁剪区域与 CN 不同。
        需要对常见 OCR 识别错误进行修正（如 DALY -> DAILY）。

        解析内容：名称、后缀、时长、过期时间、状态。
        """
        # 名称识别——EN 服名称较长，使用更宽的裁剪区域
        area = area_offset((131, 23, 409, 53), self.area[0:2])
        button = Button(area=area, color=(), button=area, name='COMMISSION')
        ocr = Ocr(button, lang='ppocr_v6')
        self.button = button
        result = ocr.ocr(self.image).upper()
        # 修正常见 OCR 识别错误
        result = result.replace('DALY', 'DAILY')
        result = result.replace('NVB', 'NYB')
        result = result.replace('PYEIN', 'VEIN').replace('YEIN', 'VEIN')
        self.name = result
        self.genre = self.commission_name_parse(self.name)

        # 后缀图像识别
        self.suffix_image = crop_suffix_image(self.image, self.button.area)
        self.suffix_hash = image_hash(self.suffix_image)

        # 执行时长
        area = area_offset((290, 68, 390, 95), self.area[0:2])
        button = Button(area=area, color=(), button=area, name='DURATION')
        ocr = Duration(button)
        self.duration = ocr.ocr(self.image)

        # 剩余可启动时间——仅紧急委托有
        self.available_time = self._commission_available_time_parse()

        # 状态识别——通过 RGB 颜色通道判断
        area = area_offset((179, 71, 187, 93), self.area[0:2])
        dic = {
            0: 'finished',
            1: 'running',
            2: 'pending'
        }
        color = np.array(get_color(self.image, area))
        if self.genre == 'daily_event':
            color -= [50, 30, 20]
        self.status = dic[int(np.argmax(color))]

    @Config.when(SERVER='jp')
    def commission_parse(self):
        """解析委托信息（JP 服务器）。

        JP 服 OCR 使用日文模型，需修正阵营缩写识别错误。
        解析内容：名称、后缀、时长、过期时间、状态。
        """
        # 名称识别
        area = area_offset((176, 23, 420, 53), self.area[0:2])
        button = Button(area=area, color=(), button=area, name='COMMISSION')
        ocr = Ocr(button, letter=(201, 201, 201), lang='jp')
        self.button = button
        result = ocr.ocr(self.image).upper()
        # 修正阵营缩写：NB -> NYB，BW -> BIW
        result = result.replace('NB', 'BYB').replace('BW', 'BIW')
        self.name = result
        self.genre = self.commission_name_parse(self.name)

        # 后缀图像识别
        self.suffix_image = crop_suffix_image(self.image, self.button.area)
        self.suffix_hash = image_hash(self.suffix_image)

        # 执行时长
        area = area_offset((290, 68, 390, 95), self.area[0:2])
        button = Button(area=area, color=(), button=area, name='DURATION')
        ocr = Duration(button)
        self.duration = ocr.ocr(self.image)

        # 剩余可启动时间——仅紧急委托有
        self.available_time = self._commission_available_time_parse()

        # 状态识别——通过 RGB 颜色通道判断
        area = area_offset((179, 71, 187, 93), self.area[0:2])
        dic = {
            0: 'finished',
            1: 'running',
            2: 'pending'
        }
        color = np.array(get_color(self.image, area))
        if self.genre == 'daily_event':
            color -= [50, 30, 20]
        self.status = dic[int(np.argmax(color))]

    @Config.when(SERVER='tw')
    def commission_parse(self):
        """解析委托信息（TW 服务器）。

        TW 服繁体中文 OCR 需要修正特定字符的识别错误。
        解析内容：名称、后缀、时长、过期时间、状态。
        """
        # 名称识别
        area = area_offset((176, 23, 420, 53), self.area[0:2])
        button = Button(area=area, color=(), button=area, name='COMMISSION')
        ocr = Ocr(button, lang='tw', threshold=256)
        self.button = button
        result = ocr.ocr(self.image).upper()
        # 训练数据集中没有"艦"字，用"鑑"/"盤"替代后修正
        result = result.replace('鑑', '艦').replace('盤', '艦')
        # 修正"支援土蒙爾島" -> "支援土豪爾島"
        result = result.replace('土蒙爾', '土豪爾')
        # 修正"资源原" -> "资源"
        result = result.replace('源原', '源')
        self.name = result
        self.genre = self.commission_name_parse(self.name)

        # 后缀图像识别
        self.suffix_image = crop_suffix_image(self.image, self.button.area)
        self.suffix_hash = image_hash(self.suffix_image)

        # 执行时长
        area = area_offset((290, 68, 390, 95), self.area[0:2])
        button = Button(area=area, color=(), button=area, name='DURATION')
        ocr = Duration(button)
        self.duration = ocr.ocr(self.image)

        # 剩余可启动时间——仅紧急委托有
        self.available_time = self._commission_available_time_parse()

        # 状态识别——通过 RGB 颜色通道判断
        area = area_offset((179, 71, 187, 93), self.area[0:2])
        dic = {
            0: 'finished',
            1: 'running',
            2: 'pending'
        }
        color = np.array(get_color(self.image, area))
        if self.genre == 'daily_event':
            color -= [50, 30, 20]
        self.status = dic[int(np.argmax(color))]

    @Config.when(SERVER=None)
    def commission_parse(self):
        """解析委托信息（CN 服务器，默认回退）。

        CN 服同样裁剪名称右侧后缀图像，用于后续相似度匹配。
        解析内容：名称、后缀、时长、过期时间、状态。
        """
        # 名称识别
        area = area_offset((176, 23, 420, 53), self.area[0:2])
        button = Button(area=area, color=(), button=area, name='COMMISSION')
        ocr = Ocr(button, lang='cnocr', threshold=256)
        self.button = button
        result = ocr.ocr(self.image).upper()
        # 修正"资源原" -> "资源"
        result = result.replace('源原', '源')
        self.name = result
        self.genre = self.commission_name_parse(self.name)

        # 后缀图像识别
        self.suffix_image = crop_suffix_image(self.image, self.button.area)
        self.suffix_hash = image_hash(self.suffix_image)

        # 执行时长
        area = area_offset((290, 68, 390, 95), self.area[0:2])
        button = Button(area=area, color=(), button=area, name='DURATION')
        ocr = Duration(button)
        self.duration = ocr.ocr(self.image)

        # 剩余可启动时间——仅紧急委托有
        self.available_time = self._commission_available_time_parse()

        # 状态识别——通过 RGB 颜色通道判断
        area = area_offset((179, 71, 187, 93), self.area[0:2])
        dic = {
            0: 'finished',
            1: 'running',
            2: 'pending'
        }
        color = np.array(get_color(self.image, area))
        if self.genre == 'daily_event':
            color -= [50, 30, 20]
        self.status = dic[int(np.argmax(color))]

    def __str__(self):
        """返回委托的可读字符串表示，包含名称、类型、状态和时长。"""
        name = f'{self.name} | {self.suffix_hash}' if self.suffix_hash else self.name
        if not self.valid:
            return f'{name} (Invalid)'
        info = {'Genre': self.genre, 'Status': self.status, 'Duration': self.duration}
        if self.available_time:
            info['Deadline'] = self.deadline_time
        if self.repeat_count > 1:
            info['Repeat'] = self.repeat_count
        info = ', '.join([f'{k}: {v}' for k, v in info.items()])
        return f'{name} ({info})'

    def __eq__(self, other):
        """判断两个委托是否为同一委托。

        通过类型、状态、后缀、时长（允许 120 秒误差）、截止时间和重复次数
        进行综合比较。紧急物资委托还需匹配阵营标签（NYB/BIW）。

        Args:
            other: 要比较的委托对象。

        Returns:
            是否为同一委托。
        """
        if not isinstance(other, Commission):
            return False
        threshold = timedelta(seconds=120)
        if not self.valid or not other.valid:
            return False
        if self.genre != other.genre or self.status != other.status:
            return False
        if self.category_str == 'daily':
            if not self.suffix_match(other):
                return False
        if self.genre == 'urgent_box':
            for tag in ['NYB', 'BIW']:
                if tag in self.name.upper() and tag not in other.name.upper():
                    return False
                if tag not in self.name.upper() and tag in other.name.upper():
                    return False
        if (other.duration < self.duration - threshold) or (other.duration > self.duration + threshold):
            return False
        if (self.deadline_time is None) != (other.deadline_time is None):
            return False
        if self.deadline_time is not None and other.deadline_time is not None:
            if (
                other.deadline_time < self.deadline_time - threshold
                or other.deadline_time > self.deadline_time + threshold
            ):
                return False
        if self.repeat_count != other.repeat_count:
            return False
        if self.genre in ['extra_oil', 'night_oil'] and not self.suffix_match(other):
            return False

        return True

    def __hash__(self):
        """返回委托的哈希值，基于类型和名称。"""
        return hash(f'{self.genre}_{self.name}')

    def suffix_match(self, other, similarity=0.75):
        """判断两个委托的后缀图像是否匹配。

        Args:
            other: 要比较的委托对象。
            similarity: 相似度阈值，范围 0-1。

        Returns:
            后缀是否匹配。
        """
        if self.suffix_image is None and other.suffix_image is None:
            return True
        if self.suffix_image is None or other.suffix_image is None:
            return False

        def match(image, template):
            template = crop(template, (3, 3, template.shape[1] - 3, template.shape[0] - 3), copy=False)
            if image.shape[0] < template.shape[0] or image.shape[1] < template.shape[1]:
                return 0.0

            res = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
            _, sim, _, _ = cv2.minMaxLoc(res)
            return sim

        sim = max(
            match(self.suffix_image, other.suffix_image),
            match(other.suffix_image, self.suffix_image)
        )
        return sim >= similarity

    def parse_time(self, string):
        """解析时间字符串为 timedelta 对象。

        Args:
            string: 时间字符串，格式如 '01:00:00', '05:47:10', '17:50:51'。

        Returns:
            解析后的 timedelta 实例，解析失败时返回 None。
        """
        # OCR 常将 0 识别为 D，此处修正
        string = string.replace('D', '0')
        result = re.search('(\d+):(\d+):(\d+)', string)
        if not result:
            logger.warning(f'无效的时间字符串: {string}')
            self.valid = False
            return None
        else:
            result = [int(s) for s in result.groups()]
            return timedelta(hours=result[0], minutes=result[1], seconds=result[2])

    @Config.when(SERVER='en')
    def commission_name_parse(self, string):
        """根据委托名称匹配委托类型（EN 服务器）。

        先判断是否为活动委托，再遍历 EN 名称字典进行关键词匹配。

        Args:
            string: 委托名称，如 'DAILY RESOURCE EXTRACTION'。

        Returns:
            委托类型字符串，如 'urgent_gem'，无法识别时返回空字符串。
        """
        if self.is_event_commission():
            return 'daily_event'
        for key, value in dictionary_en.items():
            for keyword in value:
                if keyword in string:
                    return key

        logger.warning(f'未知类型的名称: {string}')
        self.valid = False
        return ''

    @Config.when(SERVER='jp')
    def commission_name_parse(self, string):
        """根据委托名称匹配委托类型（JP 服务器）。

        使用 Levenshtein 距离进行模糊匹配，允许最多 2 个字符的 OCR 识别误差。
        先判断是否为活动委托，再遍历 JP 名称字典计算编辑距离。

        Args:
            string: 委托名称，如 '短距離練習航海'。

        Returns:
            委托类型字符串，如 'extra_drill'，无法识别时返回空字符串。
        """
        if self.is_event_commission():
            return 'daily_event'
        import jellyfish
        min_key = ''
        min_distance = 100
        # 移除 ASCII 字符，只保留日文字符进行匹配
        string = re.sub(r'[\x00-\x7F]', '', string)
        for key, value in dictionary_jp.items():
            for keyword in value:
                distance = jellyfish.levenshtein_distance(keyword, string)
                if distance < min_distance:
                    min_key = key
                    min_distance = distance
        if min_distance < 3:
            return min_key

        logger.warning(f'未知类型的名称: {string}')
        self.valid = False
        return ''

    @Config.when(SERVER='tw')
    def commission_name_parse(self, string):
        """根据委托名称匹配委托类型（TW 服务器）。

        先判断是否为活动委托，再遍历 TW 名称字典进行关键词匹配。

        Args:
            string: 委托名称，如 '日常資源開發'。

        Returns:
            委托类型字符串，如 'daily_resource'，无法识别时返回空字符串。
        """
        if self.is_event_commission():
            return 'daily_event'
        for key, value in dictionary_tw.items():
            for keyword in value:
                if keyword in string:
                    return key

        logger.warning(f'未知类型的名称: {string}')
        self.valid = False
        return ''

    @Config.when(SERVER=None)
    def commission_name_parse(self, string):
        """根据委托名称匹配委托类型（CN 服务器，默认回退）。

        先判断是否为活动委托，再遍历 CN 名称字典进行关键词匹配。

        Args:
            string: 委托名称，如 'NYB要员护卫'。

        Returns:
            委托类型字符串，如 'urgent_gem'，无法识别时返回空字符串。
        """
        if self.is_event_commission():
            return 'daily_event'
        for key, value in dictionary_cn.items():
            for keyword in value:
                if keyword in string:
                    return key

        logger.warning(f'未知类型的名称: {string}')
        self.valid = False
        return ''

    def is_event_commission(self):
        """判断是否为活动委托。

        通过检测委托条目左侧区域的颜色来判断。不同时期的活动使用不同的颜色标记，
        当前使用 2023.04.27 度假村复刻活动的粉黄色渐变作为识别依据。

        Returns:
            是否为活动委托。
        """
        # 当前活动委托：粉黄色渐变（度假村复刻 / Idol Master 活动风格）
        area = area_offset((5, 5, 30, 30), self.area[0:2])
        if color_similar(color1=get_color(self.image, area), color2=(235, 173, 161), threshold=30):
            return True

        return False

    def convert_to_night(self):
        """将 extra 类型委托转换为 night 类型。"""
        if self.valid and self.category_str == 'extra':
            self.category_str = 'night'
            self.genre = f'{self.category_str}_{self.genre_str}'

    def convert_to_running(self):
        """将委托状态设为运行中，并将创建时间重置为当前时间。"""
        if self.valid:
            self.status = 'running'
            self.create_time = current_time()
            self.available_time = timedelta(seconds=0)
            self.deadline_time = None

    @property
    def finish_time(self):
        """委托预计完成时间。

        Returns:
            运行中委托的完成时间，非运行状态返回 None。
        """
        if self.valid and self.status == 'running':
            return (self.create_time + self.duration).replace(microsecond=0)
        else:
            return None

    @staticmethod
    def beautify_name(name):
        """将名称末尾的 ASCII 罗马数字转换为 Unicode 特殊字符。

        将 I/II/III/IV/V/VI 替换为对应的 Unicode 罗马数字字符（Ⅰ~Ⅵ）。

        Args:
            name: 原始名称，可能包含 ASCII 罗马数字后缀。

        Returns:
            转换后的名称。
        """
        name = name.strip()
        name = re.sub(r'VI$', 'Ⅵ', name)
        name = re.sub(r'IV$', 'Ⅳ', name)
        name = re.sub(r'V$', 'Ⅴ', name)
        name = re.sub(r'III$', 'Ⅲ', name)
        name = re.sub(r'II$', 'Ⅱ', name)
        name = re.sub(r'I$', 'Ⅰ', name)
        return name

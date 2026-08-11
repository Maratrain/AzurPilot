"""
科研项目数据模型与识别。

本模块定义科研项目的数据结构，并提供从截图中识别科研项目的函数。

包含两个主要数据类：
- ResearchProject: 用于 CN/EN/TW 服务器，通过 OCR 项目名称 + 系列模板匹配
  从项目列表截图中批量识别 5 个项目
- ResearchProjectJp: 用于 JP 服务器，通过逐个点击详情页并使用模板匹配
  识别系列、类型、消耗、舰船蓝图等信息（因 JP 服务器无 OCR 项目名称）

模块还提供以下辅助函数：
- 系列编号识别：通过 Sobel 边缘检测分析罗马数字的笔画结构
- 已完成项目检测：通过状态指示灯的颜色（绿色=已完成）判断
- JP 详情页识别：系列、时长、类型、消耗、舰船蓝图的模板匹配

术语对照：
    系列(Series): 科研系列编号 S1-S9，对应不同的科研舰船池
    类型(Genre): 项目类型代码 B/C/D/E/G/H/Q/T
    蓝图(Blueprint): 科研产出的舰船设计图，用于强化对应舰船
    DR: 决战方案(Dreamship Rarity)，金色稀有度科研舰船
    PRY: 近代方案(Priority Rarity)，紫色稀有度科研舰船
    天运拟合(Fate Simulation): 使用蓝图对已满强化舰船进行的命运模拟
"""
from datetime import timedelta

from scipy import signal

from module.base.decorator import cached_property
from module.base.utils import *
from module.device.method.utils import removesuffix
from module.logger import logger
from module.ocr.ocr import Duration, Ocr
from module.research.assets import *
from module.research.project_data import LIST_RESEARCH_PROJECT
from module.research.series import get_detail_series, get_research_series_3
from module.statistics.utils import *

RESEARCH_SERIES = (SERIES_1, SERIES_2, SERIES_3, SERIES_4, SERIES_5)
RESEARCH_STATUS = [STATUS_1, STATUS_2, STATUS_3, STATUS_4, STATUS_5]
OCR_RESEARCH = [OCR_RESEARCH_1, OCR_RESEARCH_2, OCR_RESEARCH_3, OCR_RESEARCH_4, OCR_RESEARCH_5]
OCR_RESEARCH = Ocr(OCR_RESEARCH, name='RESEARCH', threshold=64, alphabet='0123456789BCDEGHQTMIULRF-')
RESEARCH_DETAIL_GENRE = [DETAIL_GENRE_B, DETAIL_GENRE_C, DETAIL_GENRE_D, DETAIL_GENRE_E, DETAIL_GENRE_G,
                         DETAIL_GENRE_H_0, DETAIL_GENRE_H_1, DETAIL_GENRE_Q, DETAIL_GENRE_T]


def get_research_series_old(image, series_button=RESEARCH_SERIES):
    """
    使用简单的颜色检测获取科研系列（旧版算法）。

    通过计算白色线条数来检测罗马数字。
    已被 get_research_series() 替代，保留用于兼容。

    Args:
        image (np.ndarray): 科研列表页面的截图。
        series_button (tuple): 5 个系列标识区域的按钮定义。

    Returns:
        list[int]: 5 个项目的系列编号列表，如 [1, 1, 1, 2, 3]。
    """
    result = []
    # 设置 'prominence = 50' 以忽略可能的噪声。
    # 2021.07.18 自 07.15 维护后，字母 IV 比 I、II、III 更小。
    #   IV 中 "V" 的 "/" 因抗锯齿变得更暗。
    #   因此将高度降低到 160 以获得更好的检测效果。
    parameters = {'height': 160, 'prominence': 50, 'width': 1}

    for button in series_button:
        im = color_similarity_2d(resize(crop(image, button.area, copy=False), (46, 25)), color=(255, 255, 255))
        peaks = [len(signal.find_peaks(row, **parameters)[0]) for row in im[5:-5]]
        upper, lower = max(peaks), min(peaks)
        # print(peaks)

        # 去除类似 [2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 2] 的噪声
        if upper == 3 and lower == 2 and peaks.count(3) <= 2:
            upper = 2

        if upper == lower and 1 <= upper <= 3:
            series = upper
        elif upper == 3 and lower == 2:
            series = 4
        elif upper == 2 and lower == 1:
            series = 5
        else:
            series = 0
            logger.warning(f'[科研-系列] 未知的科研系列: 按钮={button}, 上限={upper}, 下限={lower}')
        result.append(series)

    return result


def _get_research_series(img):
    """
    通过 Sobel 算子分析单个系列标识的笔画方向。

    内部辅助函数，被 get_research_series() 调用。
    通过检测边缘梯度角判断笔画是竖直（0）还是倾斜（1），
    组合笔画序列映射到系列编号。

    Args:
        img (np.ndarray): 裁剪并缩放后的系列标识图像。

    Returns:
        int: 系列编号（1-6），无法识别返回 0。
    """
    # img = rgb2luma(img)
    img = extract_white_letters(img)
    pos = img.shape[0] * 2 // 5

    img = img[pos - 4:pos + 5]
    img = cv2.GaussianBlur(img, (5, 5), 1)
    img = img[3:6]

    threshold = np.mean(img)
    edge = np.where(np.diff((img[1] > threshold).astype(np.uint8)) == 1)[0]

    grad_x = cv2.Sobel(img, cv2.CV_16S, 1, 0)[1]
    grad_y = cv2.Sobel(img, cv2.CV_16S, 0, 1)[1]

    edge = np.arctan([
        grad_y[i] / grad_x[i]
        for i in edge
    ])
    edge = tuple(
        0 if i > -.1
        else 1
        for i in edge
        if i < .1
    )

    return {
        (0,): 1,
        (0, 0): 2,
        (0, 0, 0): 3,
        (0, 1): 4,
        (1,): 5,
        (1, 0): 6
    }.get(edge, 0)


def get_research_series(image, series_button=RESEARCH_SERIES):
    """
    通过 Sobel 边缘检测识别科研系列编号。

    分析系列标识区域的罗马数字笔画方向，通过边缘梯度角判断
    笔画是竖直（/）还是倾斜（\），从而区分不同系列。

    Args:
        image (np.ndarray): 科研列表页面的截图。
        series_button (tuple): 5 个系列标识区域的按钮定义。

    Returns:
        list[int]: 5 个项目的系列编号列表，如 [1, 1, 1, 2, 3]。
    """
    result = []
    for button in series_button:
        # img = resize(crop(image, button.area), (46, 25))
        img = crop(image, button.area, copy=False)
        img = cv2.resize(img, (46, 25), interpolation=cv2.INTER_AREA)
        series = _get_research_series(img)
        result.append(series)
    return result


def get_research_name(image, ocr=OCR_RESEARCH):
    """
    通过 OCR 识别科研列表中 5 个项目的名称。

    Args:
        image (np.ndarray): 科研列表页面的截图。
        ocr (Ocr): OCR 识别器实例，默认使用 RESEARCH 专用 OCR，
            支持字母数字混合识别（字母表：0123456789BCDEGHQTMIULRF-）。

    Returns:
        list[str]: 5 个项目的名称列表，如
            ['D-057-UL', 'C-038-RF', 'G-185-MI', 'H-339-MI', 'Q-027-MI']。
    """
    names = ocr.ocr(image)
    if not isinstance(names, list):
        names = [names]
    return names


def get_research_finished(image):
    """
    通过状态指示灯颜色检测已完成的科研项目。

    遍历 5 个项目的状态指示灯，通过 RGB 颜色通道分析判断状态：
    - 绿色（G 通道最大）= 已完成
    - 蓝色（B 通道最大）= 运行中
    - 其他颜色 = 异常，跳过

    Args:
        image (np.ndarray): 科研列表页面的截图。

    Returns:
        int: 已完成项目的索引（0-4）。如果没有已完成项目返回 None。
    """
    for index in [2, 1, 3, 0, 4]:
        button = RESEARCH_STATUS[index]
        color = get_color(image, button.area)
        if max(color) - min(color) < 40:
            logger.warning(f'[科研-状态] 异常颜色: {color}')
            continue
        color_index = np.argmax(color)  # R, G, B
        if color_index == 1:
            return index  # 绿色
        elif color_index == 2:
            continue  # 蓝色
        else:
            logger.warning(f'[科研-状态] 异常颜色: {color}')
            continue

    return None


def parse_time(string):
    """
    解析时间字符串为 timedelta 对象。

    Args:
        string (str): 时间字符串，格式为 'HH:MM:SS'，
            如 '01:00:00'、'05:47:10'、'17:50:51'。

    Returns:
        timedelta: 解析后的时间间隔对象，解析失败返回 None。
    """
    result = re.search('(\d+):(\d+):(\d+)', string)
    if not result:
        logger.warning(f'[科研-时间] 无效的时间字符串: {string}')
        return None
    else:
        result = [int(s) for s in result.groups()]
        return timedelta(hours=result[0], minutes=result[1], seconds=result[2])


def match_template(image, template, area, offset=30, similarity=0.85):
    """
    在截图的指定区域内进行模板匹配。

    Args:
        image (np.ndarray): 完整截图。
        template (np.ndarray): 待匹配的模板图像。
        area (tuple): 图像裁剪区域 (x1, y1, x2, y2)。
        offset (int, tuple): 检测区域的扩展偏移量，用于扩大搜索范围。
            整数表示上下对称偏移，元组表示 (左右, 上下) 独立偏移。
        similarity (float): 相似度阈值（0-1），低于此值返回 0.0。

    Returns:
        float: 匹配相似度（0-1），低于阈值返回 0.0。
    """
    if isinstance(offset, tuple):
        offset = np.array((-offset[0], -offset[1], offset[0], offset[1]))
    else:
        offset = np.array((0, -offset, 0, offset))
    image = crop(image, offset + area, copy=False)
    res = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
    _, sim, _, point = cv2.minMaxLoc(res)
    if sim < similarity:
        sim = 0.0
    return sim


def get_research_series_jp_old(image):
    """
    从 JP 服务器详情页识别系列编号（旧版算法）。

    与 get_research_series 基本相同，区别在于按钮区域和无需缩放。
    已被 get_research_series_jp() 替代，保留用于兼容。

    Args:
        image (np.ndarray): 科研详情页的截图。

    Returns:
        str: 系列标识，如 "S4"。
    """
    # 设置 'prominence = 50' 以忽略可能的噪声。
    parameters = {'height': 160, 'prominence': 50, 'width': 1}

    area = SERIES_DETAIL.area
    # JP 服务器只需检查一个区域，无需缩放。
    im = color_similarity_2d(crop(image, area, copy=False), color=(255, 255, 255))
    peaks = [len(signal.find_peaks(row, **parameters)[0]) for row in im[5:-5]]
    upper, lower = max(peaks), min(peaks)
    # print(upper, lower)

    # 去除类似 [2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 2] 的噪声
    if upper == 3 and lower == 2 and peaks.count(3) <= 2:
        upper = 2

    if upper == lower and 1 <= upper <= 3:
        series = upper
    elif upper == 3 and lower == 2:
        series = 4
    elif upper == 2 and lower == 1:
        series = 5
    else:
        series = 0
        logger.warning(f'未知的科研系列: upper={upper}, lower={lower}')

    return f'S{series}'


def get_research_series_jp(image):
    """
    从 JP 服务器详情页识别系列编号。

    通过模板匹配从详情页的系列标识区域识别系列编号。

    Args:
        image (np.ndarray): 科研详情页的截图。

    Returns:
        str: 系列标识，如 "S4"。
    """
    series = get_detail_series(image)
    return f'S{series}'


def get_research_duration_jp(image):
    """
    通过 OCR 识别 JP 服务器详情页中的科研时长。

    Args:
        image (np.ndarray): 科研详情页的截图。

    Returns:
        int: 科研时长，单位为秒。
    """
    ocr = Duration(DURATION_DETAIL)
    duration = ocr.ocr(image).total_seconds()
    return duration


def get_research_genre_jp(image):
    """
    通过模板匹配识别 JP 服务器详情页中的科研类型。

    遍历所有类型模板（B/C/D/E/G/H/Q/T），找到匹配度最高的类型。

    Args:
        image (np.ndarray): 科研详情页的截图。

    Returns:
        str: 类型代码，如 'd'、'c'、'g'。无法识别返回空字符串。
    """
    genre = ''
    for button in RESEARCH_DETAIL_GENRE:
        if button.match(image, offset=(30, 20), similarity=0.9):
            # DETAIL_GENRE_H_0.name.split("_")[2] == 'H'
            genre = button.name.split("_")[2]
            break
    if not genre:
        logger.warning(f'无法识别科研类型!')
    return genre


def get_research_cost_jp(image):
    """
    通过模板匹配识别 JP 服务器详情页中的资源消耗。

    检测详情页中是否包含金币、魔方和部件的消耗图标。
    当科研有 1 个消耗项时模板尺寸为 78x78，有 2 个时为 77x77，
    因此匹配阈值设为较低的 0.8 以提高容错率。

    Args:
        image (np.ndarray): 科研详情页的截图。

    Returns:
        dict: 消耗信息字典，键为 'need_coin'、'need_cube'、'need_part'，
            值为 bool 表示是否需要该资源。
    """
    size_template = (78, 78)
    area_template = (0, 0, 78, 57)
    folder = './assets/stats_basic'
    templates = load_folder(folder)
    costs = {'coin': False, 'cube': False, 'plate': False}
    for name, template in templates.items():
        template = load_image(template)
        template = crop(resize(template, size_template), area_template, copy=False)
        sim = match_template(image=image,
                             template=template,
                             area=DETAIL_COST.area,
                             offset=(10, 10),
                             similarity=0.8)
        if not sim:
            continue
        for cost in costs:
            if re.compile(cost).match(name.lower()):
                costs[cost] = True
                continue

    # 重命名键以匹配 ResearchProjectJp 的属性名
    costs['need_coin'] = costs.pop('coin')
    costs['need_cube'] = costs.pop('cube')
    costs['need_part'] = costs.pop('plate')
    return costs


def get_research_ship_jp(image):
    """
    通过模板匹配识别 JP 服务器详情页中的舰船蓝图。

    从蓝图模板库中找到与详情页蓝图区域最匹配的舰船。
    注意 2.5/5/8 小时的 D 系列有 4 个物品，0.5 小时有 3 个，
    因此 DETAIL_BLUEPRINT 按钮不应只覆盖第一个物品。

    Args:
        image (np.ndarray): 科研详情页的截图。

    Returns:
        str: 舰船名称，如 'azuma'、'drake'。无法识别返回空字符串。
    """
    folder = './assets/research_blueprint'
    templates = load_folder(folder)
    similarity = 0.0
    ship = ''
    for name, template in templates.items():
        sim = match_template(image=image,
                             template=load_image(template),
                             area=DETAIL_BLUEPRINT.area,
                             offset=(10, 10),
                             similarity=0.9)
        if sim > similarity:
            similarity = sim
            ship = name
    if ship == '':
        logger.warning(f'舰船识别失败')
    return ship


def research_jp_detect(image):
    """
    从 JP 服务器详情页完整识别一个科研项目。

    组合调用系列、时长、类型、消耗和舰船识别函数，
    生成完整的 ResearchProjectJp 对象。

    Args:
        image (np.ndarray): 科研详情页的截图。

    Returns:
        ResearchProjectJp: 识别到的科研项目对象。
    """
    project = ResearchProjectJp()
    project.series = get_research_series_jp(image)
    project.duration = removesuffix(str(get_research_duration_jp(image) / 3600), '.0')
    if project.duration == '':
        project.duration = '0'
    project.genre = get_research_genre_jp(image)
    costs = get_research_cost_jp(image)
    for cost in costs:
        project.__setattr__(cost, costs[cost])
    if project.genre.lower() == 'd':
        project.ship = get_research_ship_jp(image).lower()
    if project.ship:
        project.ship_rarity = 'dr' if project.ship in project.DR_SHIP else 'pry'
    project.name = f'{project.series}-{project.genre}-{project.duration}{project.ship}'
    if not project.check_valid():
        logger.warning(f'[科研-项目] 无效的科研项目 {project}')
    return project


def research_detect(image):
    """
    从科研列表截图中批量识别 5 个科研项目。

    通过 OCR 识别项目名称，模板匹配识别系列编号，
    组合生成 ResearchProject 对象列表。

    Args:
        image (np.ndarray): 科研列表页面的截图。

    Returns:
        list[ResearchProject]: 5 个科研项目对象的列表。
    """
    projects = []
    for name, series in zip(get_research_name(image), get_research_series_3(image)):
        project = ResearchProject(name=name, series=series)
        logger.attr('科研项目', project)
        projects.append(project)
    return projects


class ResearchProject:
    """
    科研项目数据模型，用于 CN/EN/TW 服务器。

    通过项目名称（如 'D-057-UL'）和系列编号（如 3）在项目数据库中
    查询匹配的项目信息，解析出类型、编号、时长、消耗需求和产出舰船等属性。

    OCR 识别可能存在错误，构造函数中包含大量的名称修正逻辑，
    例如：'G-185-MI' -> 'C-185-MI'、'D-022-ML' -> 'D-022-MI' 等。

    Attributes:
        valid (bool): 项目是否有效（在数据库中找到匹配项）。
        raw_series (int): 原始系列编号（1-9）。
        series (str): 格式化的系列标识，如 'S3'。
        name (str): 修正后的项目名称，如 'D-057-UL'。
        genre (str): 项目类型代码，如 'D'、'C'、'G'。
        number (str): 项目编号，如 '057'。
        duration (str): 项目时长（小时），如 '0.5'、'2'、'8'。
        ship (str): 产出的舰船名称，如 'azuma'、'drake'。
            非 D 系列项目通常为空字符串。
        ship_rarity (str): 舰船稀有度，'dr' 或 'pry'。
            仅 D 系列有蓝图产出时有值。
        need_coin (bool): 是否消耗金币。
        need_cube (bool): 是否消耗魔方。
        need_part (bool): 是否消耗部件。
        task (str): 项目特殊要求描述，如 'Scrap 8 pieces of gear.'。
        equipment_amount (int): 需要拆解的装备数量（E 系列），0 表示无要求。
        commission_amount (int): 需要完成的委托数量（T 系列），0 表示无要求。
    """
    REGEX_SHIP = re.compile(
        '('
        'neptune|monarch|ibuki|izumo|roon|saintlouis'
        '|seattle|georgia|kitakaze|azuma|friedrich'
        '|gascogne|champagne|cheshire|drake|mainz|odin'
        '|anchorage|hakuryu|agir|august|marcopolo'
        '|plymouth|rupprecht|harbin|chkalov|brest'
        '|kearsarge|hindenburg|shimanto|schultz|flandre'
        '|napoli|nakhimov|halford|bayard|daisen'
        '|goudenleeuw|mecklenburg|dmitri|kansas|vittorio'
        '|valparaiso|maximmelmann|duncan|takahashi|orage'
        ')')
    REGEX_INPUT = re.compile('(coin|cube|part)')
    REGEX_DR_SHIP = re.compile(
        'azuma|friedrich'
        '|drake'
        '|hakuryu|agir'
        '|plymouth|brest'
        '|kearsarge|hindenburg'
        '|napoli|nakhimov'
        '|goudenleeuw|mecklenburg'
        '|valparaiso|maximmelmann'
    )
    # Generate with:
    """
    out = []
    for row in LIST_RESEARCH_PROJECT:
        name = row['name']
        if name.startswith('D'):
            number = name.split('-')[1]
            out.append(number)
    print(out)
    """
    C_PROJECT_NUMBERS = ['153', '185', '038']
    D_PROJECT_NUMBERS = [
        '718', '731', '744', '759', '774', '792', '318', '331', '344', '359', '374', '392', '705', '712', '746', '757',
        '779', '794', '305', '312', '346', '357', '379', '394', '721', '722', '772', '777', '795', '321', '322', '372',
        '377', '395', '708', '763', '775', '782', '768', '308', '363', '375', '382', '368', '719', '778', '786', '788',
        '793', '319', '378', '386', '388', '393', '783', '713', '739', '771', '796', '383', '313', '339', '371', '396',
        '703', '758', '766', '790', '797', '303', '358', '366', '390', '397', '780', '736', '787', '711', '764', '380',
        '336', '387', '311', '364', '737', '781', '732', '740', '747', '337', '381', '332', '340', '347', '418', '431',
        '444', '459', '474', '492', '018', '031', '044', '059', '074', '092', '405', '412', '446', '457', '479', '494',
        '005', '012', '046', '057', '079', '094', '421', '422', '472', '477', '495', '021', '022', '072', '077', '095',
        '408', '463', '475', '482', '468', '008', '063', '075', '082', '068', '419', '478', '486', '488', '493', '019',
        '078', '086', '088', '093', '483', '413', '439', '471', '496', '083', '013', '039', '071', '096', '403', '458',
        '466', '490', '497', '003', '058', '066', '090', '097', '480', '436', '487', '411', '464', '080', '036', '087',
        '011', '064', '437', '481', '432', '440', '447', '037', '081', '032', '040', '047']

    def __init__(self, name, series):
        """
        Args:
            name (str): 如 'D-057-UL'
            series (int): 如 1, 2, 3
        """
        self.valid = True
        # '4'
        self.raw_series = series
        # 'S4'
        self.series = f'S{series}'
        # 'D-057-UL'
        self.name = self.check_name(name)
        if self.name != name:
            logger.info(f'[科研-名称] 科研名称 {name} 修正为 {self.name}')
        # 'D'
        self.genre = ''
        # '057'
        self.number = ''
        # '0.5'
        self.duration = '24'
        # 舰船头像，如 'Azuma'
        self.ship = ''
        # 'dr' 或 'pry'
        self.ship_rarity = ''
        self.need_coin = False
        self.need_cube = False
        self.need_part = False
        # 项目要求，如 'Scrap 8 pieces of gear.'
        self.task = ''

        matched = False
        for data in self.get_data(name=self.name, series=series):
            matched = True
            self.data = data
            self.genre = data['name'][0]
            self.number = data['name'][2:5]
            self.duration = str(data['time'] / 3600).rstrip('.0')
            self.task = data['task']
            for item in data['input']:
                item_name = item['name'].replace(' ', '').lower()
                result = re.search(ResearchProject.REGEX_INPUT, item_name)
                if result:
                    self.__setattr__(f'need_{result.group(1)}', True)
            for item in data['output']:
                item_name = item['name'].replace(' ', '').lower()
                result = re.search(ResearchProject.REGEX_SHIP, item_name)
                if not self.ship:
                    self.ship = result.group(1) if result else ''
                if self.ship:
                    self.ship_rarity = 'dr' if re.search(ResearchProject.REGEX_DR_SHIP, self.ship) else 'pry'
            break

        if not matched:
            logger.warning(f'[科研-项目] 无效的科研项目 {self}')
            self.valid = False

    def __str__(self):
        if self.valid:
            return f'{self.series} {self.name}'
        else:
            return f'{self.series} {self.name} (Invalid)'

    def __eq__(self, other):
        return str(self) == str(other)

    def check_name(self, name):
        """
        修正 OCR 识别中的常见项目名称错误。

        处理多种 OCR 误识别情况，包括：前缀混淆（G/D/C/L）、
        数字误识别（D->0, O->0, S->5）、后缀修正（ML->MI, 0C->UL）、
        特定服务器的已知错误等。

        Args:
            name (str): OCR 识别的原始项目名称。

        Returns:
            str: 修正后的项目名称，如 'D-057-UL'。
        """
        name = name.strip('-')
        # G-185-MI, D-T85-MI -> C-185-MI
        name = name.replace('G-185', 'C-185').replace('D-T85', 'C-185')
        # E-316-MI -> E-315-MI
        if name == '316-MI':
            name = 'E-315-MI'

        parts = name.split('-')
        parts = [i for i in parts if i]
        if len(parts) == 3:
            prefix, number, suffix = parts

            number = number.replace('D', '0').replace('O', '0').replace('S', '5')
            # E-316-MI -> E-315-MI
            number = number.replace('316', '315')
            # [TW] S5 D-349-MI -> S5 D-319-MI
            if prefix == 'D' and number == '349' and self.raw_series == 5:
                number = '319'

            if prefix in ['I1', 'U', '0']:
                prefix = 'D'
            prefix = prefix.strip('I1')
            # LC-038-RF -> C-038-RF
            prefix = prefix.replace('LC', 'C')

            # S3 D-022-MI (S3-Drake-0.5) 因 Drake 的白色衣物被识别为 'D-022-ML'
            suffix = suffix.replace('ML', 'MI').replace('MIL', 'MI').replace('M1', 'MI')
            # S4 D-063-UL (S4-hakuryu-0.5) 被识别为 'D-063-0C'
            # D-057-DC -> D-057-UL
            suffix = suffix.replace('0C', 'UL').replace('UC', 'UL')
            suffix = suffix.replace('DC5', 'UL').replace('DC3', 'UL').replace('DC', 'UL')
            # D-075-UL1 -> D-075-UL
            suffix = suffix.replace('UL1', 'UL').replace('ULI', 'UL').replace('UL5', 'UL')
            # D-037-ULC -> D-037-UL
            suffix = suffix.replace('ULC', 'UL')

            if len(suffix) > 2:
                if 'UL' in suffix:
                    suffix = 'UL'
                elif 'MI' in suffix:
                    suffix = 'MI'
                elif 'RF' in suffix:
                    suffix = 'RF'
            elif len(suffix) == 1:
                if suffix == 'U' or suffix == 'L':
                    suffix = 'UL'
                elif suffix == 'M' or suffix == 'I':
                    suffix = 'MI'
                elif suffix == 'R' or suffix == 'F':
                    suffix = 'RF'

            # TW 服务器 OCR 错误，将 B 转换为 D
            if prefix == 'B' and number in ResearchProject.D_PROJECT_NUMBERS:
                # 保留 B-397-RF，S7 D-397-MI 和 S* B-397-RF 共享 397
                if number == '397' and suffix == 'RF':
                    pass
                else:
                    prefix = 'D'
            # I-483-RF 修正为 -483-RF -> D-483-RF
            if prefix == '' and number in ResearchProject.D_PROJECT_NUMBERS:
                prefix = 'D'
            # L-153-MI -> C-153-MI
            if prefix == 'L' and number in ResearchProject.C_PROJECT_NUMBERS:
                prefix = 'C'
            return '-'.join([prefix, number, suffix])
        elif len(parts) == 2:
            # 尝试插入 '-'，处理类似 H339-MI 的结果
            if name[0].isalpha() and name[1].isdigit():
                return self.check_name(f'{name[0]}-{name[1:]}')
        return name

    def get_data(self, name, series):
        """
        从项目数据库中查询匹配的科研项目数据。

        按优先级依次尝试精确匹配、前缀修正（G/C/D 混淆）、
        后缀模糊匹配等多种策略，以应对 OCR 识别错误。

        Args:
            name (str): 修正后的项目名称，如 'D-057-UL'。
            series (int): 系列编号，如 1, 2, 3。

        Yields:
            dict: 匹配到的项目数据字典，包含 name、series、time、
                task、input、output 等字段。
        """
        for data in LIST_RESEARCH_PROJECT:
            if (data['series'] == series) and (data['name'] == name):
                yield data

        if len(name) and name[0].isdigit():
            for t in 'QGE':
                name1 = f'{t}-{self.name}'
                logger.info(f'[科研-匹配] 测试最相似的候选 {name1}')
                for data in LIST_RESEARCH_PROJECT:
                    if (data['series'] == series) and (data['name'] == name1):
                        self.name = name1
                        yield data

        if name.startswith('D'):
            # 字母 'C' 可能因项目卡片反光被识别为 'D'
            name1 = 'C' + self.name[1:]
            for data in LIST_RESEARCH_PROJECT:
                if (data['series'] == series) and (data['name'] == name1):
                    self.name = name1
                    yield data

        # 仅当编号在当前科研系列中唯一时，忽略类型和后缀进行兜底匹配
        number = name[2:5]
        candidates = [
            data for data in LIST_RESEARCH_PROJECT
            if (data['series'] == series) and (data['name'][2:5] == number)
        ]
        if len(candidates) == 1:
            yield candidates[0]

        for data in LIST_RESEARCH_PROJECT:
            if (data['series'] == series) and (data['name'].rstrip('MIRFUL-') == name.rstrip('MIRFUL-')):
                yield data

        return False

    @cached_property
    def equipment_amount(self):
        # 拆解 8 件装备。
        # 拆解 15 件装备。
        if '8 piece' in self.task:
            return 8
        elif '15 piece' in self.task:
            return 15
        else:
            return 0

    @cached_property
    def commission_amount(self):
        if '2 commissions' in self.task:
            return 2
        elif '4 commissions' in self.task:
            return 4
        elif '6 commissions' in self.task:
            return 6
        else:
            return 0


class ResearchProjectJp:
    """
    科研项目数据模型，用于 JP 服务器。

    JP 服务器的科研项目名称无法通过 OCR 识别，因此使用模板匹配
    逐个检测详情页中的系列、类型、消耗和舰船蓝图信息。
    项目名称由检测结果组合生成，格式为 '{series}-{genre}-{duration}{ship}'。

    Attributes:
        valid (bool): 项目是否有效（通过 check_valid() 验证）。
        name (str): 组合生成的项目标识，如 'S4-D-0.5azuma'。
        series (str): 格式化的系列标识，如 'S4'。
        genre (str): 项目类型代码，如 'd'、'c'、'g'。
        number (str): 项目编号，JP 服务器通常为空字符串。
        duration (str): 项目时长（小时），如 '0.5'、'2'、'8'。
        ship (str): 产出的舰船名称，如 'azuma'。
        ship_rarity (str): 舰船稀有度，'dr' 或 'pry'。
        need_coin (bool): 是否消耗金币。
        need_cube (bool): 是否消耗魔方。
        need_part (bool): 是否消耗部件。
        task (str): 项目特殊要求，JP 服务器通常为空字符串。
        equipment_amount (int): 需要拆解的装备数量（E 系列）。
        commission_amount (int): 需要完成的委托数量（T 系列）。

    类属性:
        GENRE (list[str]): 所有有效的项目类型代码。
        DURATION (list[str]): 所有有效的项目时长。
        SHIP_S1 ~ SHIP_S9 (list[str]): 各系列对应的舰船名称列表。
        SHIP_ALL (list[str]): 所有系列的舰船名称合并列表。
        DR_SHIP (list[str]): 所有 DR（决战方案）舰船名称。
    """
    GENRE = ['b', 'c', 'd', 'e', 'g', 'h', 'q', 't']
    DURATION = ['0.5', '1', '1.5', '2', '2.5', '3', '4', '5', '6', '8', '12']
    SHIP_S1 = ['neptune', 'monarch', 'ibuki', 'izumo', 'roon', 'saintlouis']
    SHIP_S2 = ['seattle', 'georgia', 'kitakaze', 'azuma', 'friedrich', 'gascogne']
    SHIP_S3 = ['champagne', 'cheshire', 'drake', 'mainz', 'odin']
    SHIP_S4 = ['anchorage', 'hakuryu', 'agir', 'august', 'marcopolo']
    SHIP_S5 = ['plymouth', 'rupprecht', 'harbin', 'chkalov', 'brest']
    SHIP_S6 = ['kearsarge', 'hindenburg', 'shimanto', 'schultz', 'flandre']
    SHIP_S7 = ['napoli', 'nakhimov', 'halford', 'bayard', 'daisen']
    SHIP_S8 = ['goudenleeuw', 'mecklenburg', 'dmitri', 'kansas', 'vittorio']
    SHIP_S9 = ['valparaiso', 'maximmelmann', 'duncan', 'takahashi', 'orage']
    SHIP_ALL = SHIP_S1 + SHIP_S2 + SHIP_S3 + SHIP_S4 + SHIP_S5 + SHIP_S6 + SHIP_S7 + SHIP_S8 + SHIP_S9
    DR_SHIP = [
        'azuma', 'friedrich',
        'drake',
        'hakuryu', 'agir',
        'plymouth', 'brest',
        'kearsarge', 'hindenburg',
        'napoli', 'nakhimov',
        'goudenleeuw', 'mecklenburg',
        'valparaiso', 'maximmelmann',
    ]

    def __init__(self):
        self.valid = True
        self.name = ''
        self.series = ''
        self.genre = ''
        self.number = ''
        self.duration = '24'
        self.ship = ''
        self.ship_rarity = ''
        self.need_coin = False
        self.need_cube = False
        self.need_part = False
        self.task = ''

    def check_valid(self):
        """
        验证 JP 服务器科研项目的有效性。

        检查系列、类型、时长是否在有效范围内，
        以及 D 系列项目是否识别到了舰船蓝图。

        Returns:
            bool: 项目是否有效。
        """
        self.valid = False
        if self.series.lower() == "s0":
            return False
        if self.genre.lower() not in self.GENRE:
            return False
        if self.duration not in self.DURATION:
            return False
        if self.ship not in self.SHIP_ALL:
            self.ship = ''
        if self.genre.lower() == 'd' and not self.ship:
            return False
        self.valid = True
        return True

    def __str__(self):
        if self.valid:
            return f'{self.name}'
        else:
            return f'{self.name} (Invalid)'

    def __eq__(self, other):
        return str(self) == str(other)

    @cached_property
    def equipment_amount(self):
        if self.genre == 'E' and self.duration == '2':
            # JP 服务器没有科研名称，无法区分 E-031-MI 和 E-315-MI，
            # 返回最大值 15
            return 15
        else:
            return 0

    @cached_property
    def commission_amount(self):
        if self.genre == 'T':
            if self.duration == '3':
                return 2
            elif self.duration == '4':
                return 4
            elif self.duration == '6':
                return 6
        return 0

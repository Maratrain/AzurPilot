from module.map.camera import Camera
from module.map.map_base import location_ensure
from module.exception import MapDetectionError
from module.os.camera import OSCamera


class FakeMap:
    def __init__(self, shape=(7, 12), sight=(-4, -1, 3, 3)):
        self.shape = shape
        self.camera_sight = sight


class FakeView:
    def __init__(self):
        self.left_edge = False
        self.right_edge = False
        self.lower_edge = False
        self.upper_edge = False


class FakeConfig:
    MAP_ENSURE_EDGE_INSIGHT_CORNER = 'bottom-right'


def test_base_camera_focus_keeps_original_coordinates():
    camera = Camera.__new__(Camera)
    camera.map = FakeMap()
    camera.camera = (-100, -100)

    assert camera._limit_camera_location((-100, -100)) == (-100, -100)
    assert camera.focus_to((-100, -100)) is True
    assert camera.camera == (-100, -100)


def test_os_camera_focus_limits_corner_coordinates():
    camera = OSCamera.__new__(OSCamera)
    camera.map = FakeMap()
    camera.camera = (-100, -100)

    corners = [
        ((-100, -100), (4, 1)),
        ((-100, 100), (4, 9)),
        ((100, -100), (4, 1)),
        ((100, 100), (4, 9)),
    ]
    for location, expected in corners:
        assert camera._limit_camera_location(location) == expected

    assert camera.focus_to('C1') is True
    assert camera.camera == (4, 1)


def test_os_camera_focus_limits_target_before_swipe():
    camera = OSCamera.__new__(OSCamera)
    camera.map = FakeMap()
    camera.camera = (4, 1)
    swipes = []

    def map_swipe(vector):
        swipes.append(tuple(vector))
        return False

    camera.map_swipe = map_swipe

    assert camera.focus_to(location_ensure('A1')) is True
    assert swipes == []


def test_base_camera_swipe_destination_not_limited():
    camera = Camera.__new__(Camera)
    camera.map = FakeMap()
    camera.camera = (4, 1)

    assert camera._limit_swipe_destination((3, 2)) == (3, 2)


def test_os_camera_swipe_destination_clamped_to_boundary():
    camera = OSCamera.__new__(OSCamera)
    camera.map = FakeMap(shape=(12, 12), sight=(-4, -1, 3, 3))
    camera.camera = (8, 9)

    # 终点 (11, 11) 超出可容纳范围 x[4,9] y[1,9]，收窄到边界 (9, 9)
    assert camera._limit_swipe_destination((3, 2)) == (1, 0)


def test_os_camera_swipe_destination_never_reverses():
    camera = OSCamera.__new__(OSCamera)
    camera.map = FakeMap(shape=(12, 12), sight=(-4, -1, 3, 3))
    camera.camera = (10, 5)  # 记账 x 已越界（上限 9）

    # x 轴已越界不做反向滑动，y 轴合法方向照常滑动
    assert camera._limit_swipe_destination((3, 2)) == (0, 2)

    camera.camera = (10, 10)
    # 两轴都无处可滑时返回 (0, 0)，由调用方结束边缘扫描
    assert camera._limit_swipe_destination((3, 2)) == (0, 0)


def test_base_camera_swipe_failure_reraises():
    camera = Camera.__new__(Camera)
    camera.map = FakeMap()
    camera.camera = (4, 1)

    def map_swipe(vector):
        raise MapDetectionError('Image to detect is not in_map')

    camera.map_swipe = map_swipe

    # 基类不做恢复，保持原有异常穿透行为
    try:
        camera.focus_to(location_ensure('F5'))
    except MapDetectionError:
        pass
    else:
        raise AssertionError('基类滑动失败时应保持异常穿透')


def test_os_camera_swipe_failure_recovers_and_retries():
    camera = OSCamera.__new__(OSCamera)
    camera.map = FakeMap(shape=(12, 12), sight=(-4, -1, 3, 3))
    camera.camera = (4, 1)
    camera._prev_view = object()
    camera._prev_swipe = (2, -1)
    swipes = []
    undos = []

    def map_swipe(vector):
        swipes.append(tuple(vector))
        if len(swipes) == 1:
            raise MapDetectionError('Image to detect is not in_map')
        camera.camera = (5, 4)  # 模拟重试成功后相机到位
        return True

    def _map_swipe(vector, box=(239, 128, 993, 628)):
        undos.append(tuple(vector))
        return True

    camera.map_swipe = map_swipe
    camera._map_swipe = _map_swipe

    assert camera.focus_to(location_ensure('F5')) is True
    # 首次滑动失败后按原滑动距离反向撤销，并重试原滑动
    assert swipes == [(1, 3), (1, 3)]
    assert undos == [(-1, -3)]
    # 失败滑动遗留的预测状态应被清空
    assert camera._prev_view is None
    assert camera._prev_swipe is None


def test_os_camera_swipe_failure_gives_up_gracefully():
    camera = OSCamera.__new__(OSCamera)
    camera.map = FakeMap(shape=(12, 12), sight=(-4, -1, 3, 3))
    camera.camera = (4, 1)
    swipes = []
    undos = []

    def map_swipe(vector):
        swipes.append(tuple(vector))
        raise MapDetectionError('Image to detect is not in_map')

    def _map_swipe(vector, box=(239, 128, 993, 628)):
        undos.append(tuple(vector))
        return True

    camera.map_swipe = map_swipe
    camera._map_swipe = _map_swipe

    # 3 次滑动尝试、2 次撤销恢复后放弃，返回 False 而不是抛异常
    assert camera.focus_to(location_ensure('F5')) is False
    assert len(swipes) == 3
    assert len(undos) == 2


def test_os_camera_edge_insight_degrades_on_unrecoverable_swipe():
    camera = OSCamera.__new__(OSCamera)
    camera.map = FakeMap(shape=(12, 12), sight=(-4, -1, 3, 3))
    camera.camera = (4, 1)
    camera.view = FakeView()
    camera.config = FakeConfig()

    def map_swipe(vector):
        raise MapDetectionError('Image to detect is not in_map')

    def _map_swipe(vector, box=(239, 128, 993, 628)):
        return True

    camera.map_swipe = map_swipe
    camera._map_swipe = _map_swipe

    # 边缘扫描中视角持续无法识别时应优雅结束，返回已完成的滑动记录
    record = camera.ensure_edge_insight()
    assert len(record) == 1

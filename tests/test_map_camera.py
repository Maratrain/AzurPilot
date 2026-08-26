from module.map.camera import Camera
from module.map.map_base import location_ensure
from module.os.camera import OSCamera


class FakeMap:
    def __init__(self, shape=(7, 12), sight=(-4, -1, 3, 3)):
        self.shape = shape
        self.camera_sight = sight


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

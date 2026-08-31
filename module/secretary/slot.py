from copy import deepcopy
from module.secretary.assets import SECRETARY_FIRST_SHIP_SLOT
def move_button(btn, dx, dy):

    btn = deepcopy(btn)

    for server in btn.raw_area:
        x1, y1, x2, y2 = btn.raw_area[server]
        btn.raw_area[server] = (
            x1 + dx,
            y1 + dy,
            x2 + dx,
            y2 + dy,
        )

        x1, y1, x2, y2 = btn.raw_button[server]
        btn.raw_button[server] = (
            x1 + dx,
            y1 + dy,
            x2 + dx,
            y2 + dy,
        )

    btn.__dict__.pop("area", None)
    btn.__dict__.pop("_button", None)

    return btn


SECRETARY_SLOT = [
    move_button(
        SECRETARY_FIRST_SHIP_SLOT,
        193 * i,
        0,
    )
    for i in range(5)
]
SECRETARY_SLOT_OFFSET = [
    (193 * i, 0)
    for i in range(5)
]
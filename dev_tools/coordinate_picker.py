import cv2
import sys
try:
    import pyperclip
except:
    pyperclip = None
import numpy as np


if len(sys.argv) < 2:
    print("Usage: python -m dev_tools.coordinate_picker <image>")
    sys.exit(1)


path = sys.argv[1]

original = cv2.imread(path)

if original is None:
    raise SystemExit("Cannot open image")


# 当前显示/编辑图片
orig = original.copy()


# ======================
# View 状态
# ======================

scale = 1.0

min_scale = 0.2
max_scale = 8.0

offset_x = 0
offset_y = 0


# ======================
# 框选状态
# ======================

p1 = None
p2 = None
drag = False


# ======================
# 平移状态
# ======================

pan_drag = False
pan_start = None
pan_origin = None



def get_view():

    h, w = orig.shape[:2]

    view_w = int(w / scale)
    view_h = int(h / scale)


    x1 = int(offset_x)
    y1 = int(offset_y)


    x1 = max(0, min(x1, w - view_w))
    y1 = max(0, min(y1, h - view_h))


    x2 = x1 + view_w
    y2 = y1 + view_h


    crop = orig[y1:y2, x1:x2]


    show = cv2.resize(
        crop,
        None,
        fx=scale,
        fy=scale,
        interpolation=cv2.INTER_LINEAR
    )


    return show, x1, y1



def screen_to_image(x, y):

    ix = int(offset_x + x / scale)
    iy = int(offset_y + y / scale)

    return ix, iy



def redraw():

    show, xoff, yoff = get_view()


    if p1 and p2:

        sx1 = int((p1[0] - xoff) * scale)
        sy1 = int((p1[1] - yoff) * scale)

        sx2 = int((p2[0] - xoff) * scale)
        sy2 = int((p2[1] - yoff) * scale)


        cv2.rectangle(
            show,
            (sx1, sy1),
            (sx2, sy2),
            (0, 255, 0),
            2
        )


        x1, y1 = p1
        x2, y2 = p2


        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))


        txt = f"({x1},{y1},{x2},{y2}) {x2-x1}x{y2-y1}"


        cv2.putText(
            show,
            txt,
            (10, 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0,255,0),
            2
        )


    cv2.imshow(
        "Coordinate Picker",
        show
    )



def mouse(event, x, y, flags, param):

    global p1, p2, drag
    global pan_drag, pan_start, pan_origin
    global scale, offset_x, offset_y


    # 左键选择区域

    if event == cv2.EVENT_LBUTTONDOWN:

        p1 = screen_to_image(x, y)
        p2 = p1

        drag = True



    elif event == cv2.EVENT_MOUSEMOVE:


        if drag:

            p2 = screen_to_image(x, y)


        if pan_drag:

            dx = x - pan_start[0]
            dy = y - pan_start[1]


            offset_x = pan_origin[0] - dx / scale
            offset_y = pan_origin[1] - dy / scale



    elif event == cv2.EVENT_LBUTTONUP:

        p2 = screen_to_image(x, y)

        drag = False



    # 右键拖动

    elif event == cv2.EVENT_RBUTTONDOWN:

        pan_drag = True

        pan_start = (x, y)
        pan_origin = (offset_x, offset_y)



    elif event == cv2.EVENT_RBUTTONUP:

        pan_drag = False



    # 滚轮缩放

    elif event == cv2.EVENT_MOUSEWHEEL:


        old_scale = scale


        if flags > 0:

            scale *= 1.25

        else:

            scale /= 1.25


        scale = max(
            min_scale,
            min(max_scale, scale)
        )


        # 保持鼠标位置固定

        img_x = offset_x + x / old_scale
        img_y = offset_y + y / old_scale


        offset_x = img_x - x / scale
        offset_y = img_y - y / scale



    redraw()



cv2.namedWindow(
    "Coordinate Picker"
)


cv2.setMouseCallback(
    "Coordinate Picker",
    mouse
)


redraw()


print(
"""
╔══════════════════════════════╗
║      Coordinate Picker       ║
╠══════════════════════════════╣
║ 左键拖动   : 选择坐标区域    ║
║ Enter      : 复制坐标        ║
║ C          : 复制 Assets 格式║
║ S          : 保存黑色遮罩    ║
║ R          : 恢复原始图片    ║
║ A          : 重置视图        ║
╠══════════════════════════════╣
║ 滚轮       : 鼠标位置缩放    ║
║ 右键拖动   : 移动图片视野    ║
║ ESC        : 退出程序        ║
╚══════════════════════════════╝
"""
)



while True:


    k = cv2.waitKey(20) & 0xff


    if k == 27:
        break



    # 恢复原图

    if k == ord('r'):

        orig = original.copy()

        redraw()

        print("Reload original image")



    # 重置视图

    if k == ord('a'):

        scale = 1.0

        offset_x = 0
        offset_y = 0

        p1 = None
        p2 = None

        redraw()

        print("Reset view")



    if p1 and p2:


        x1, y1 = p1
        x2, y2 = p2


        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))



        if k == 13:


            s = f"({x1}, {y1}, {x2}, {y2})"

            print(s)

            if pyperclip:

                pyperclip.copy(s)



        elif k == ord('c'):


            s = f'("{path}", ({x1}, {y1}, {x2}, {y2})),'

            print(s)

            if pyperclip:

                pyperclip.copy(s)



        elif k == ord('s'):


            out = np.zeros_like(original)


            out[
                y1:y2,
                x1:x2
            ] = original[
                y1:y2,
                x1:x2
            ]


            cv2.imwrite(
                path,
                out
            )


            print("Saved masked image")



cv2.destroyAllWindows()
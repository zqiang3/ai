from bot import handler

HEIGHT = 780
WIDTH = 600
ROWS = 13
COLS = 10
OFFSET_X = 1197
OFFSET_Y = 310


def grid_coordinates(height=HEIGHT, width=WIDTH, rows=ROWS, cols=COLS,
                     offset_x=OFFSET_X, offset_y=OFFSET_Y):
    """把屏幕(高 height, 宽 width)分成 rows 行 x cols 列的正文格。

    返回一个二维数组：result[row][col] = (x, y)，
    (x, y) 为该格中心点坐标，起点为左上角，并加上偏移 (offset_x, offset_y)。
    """
    cell_w = width / cols
    cell_h = height / rows
    return [
        [
            (int((col + 0.5) * cell_w) + offset_x, int((row + 0.5) * cell_h) + offset_y)
            for col in range(cols)
        ]
        for row in range(rows)
    ]


def loop_sell():
    cdt = grid_coordinates()
    for x, y in cdt:
        handler.C.click(x, y)
        handler.loop_sell()


if __name__ == '__main__':
    loop_sell()

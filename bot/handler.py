import time
from datetime import datetime
from . import proxy
from service import run_file
from service import Controller, AutoBidder
from . import manager

# dict
ACTIONS = {}

C = Controller()
M = manager.Manager()
Bidder = AutoBidder({"a": "b"}, C)


def action(name):
    def decorator(fn):
        if name in ACTIONS:
            raise ValueError(f'action {name} is already registered!')
        else:
            ACTIONS[name] = fn

    return decorator


def execute(commands):
    for data in commands:
        command = data[0]
        if command == "click":
            x, y, wait_time = data[1], data[2], data[3]
            # proxy.click(x, y)
            C.click(x, y)
            time.sleep(wait_time)
        elif command == "scroll":
            amount, wait_time = data[1], data[2]
            C.scroll(amount)
            for i in range(25):
                C.scroll(amount)
                time.sleep(0.1)
        elif command == "scroll_click":
            amount, wait_time = data[1], data[2]
            C.scroll(amount)
            for i in range(2):
                C.scroll(amount)
                time.sleep(0.1)
            x, y = C.position()
            C.click(x, y)
            time.sleep(wait_time)

        elif command == "click_down":
            x, y = C.position()
            C.click(x, y)
            time.sleep(0.5)

        elif command == 'bid':
            price, wait_time = data[1], data[2]
            # proxy.bid(price)
            Bidder.bid_price(price)
            time.sleep(wait_time)
        else:
            pass


@action('测试')
def restore_traffic():
    print('Test message received')


@action('效果很好')
def mark_good():
    run_file("examples/zh_demo.json")
    local_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f'标注\"很好\", 时间{local_now}')


@action('分析架构')
def analyse_arc():
    run_file("examples/init.json")

@action('打开邮箱')
def open_mail():
    commands = [
        ['click_down'],
        ['click', 1730, 69, 0.5],
        ['click', 1621, 1027, 0.5],
        ['click', 983, 989, 1],
        ['click', 983, 989, 1],
        ['click', 1621, 1027, 0.5],
        ['click', 1876, 79, 0.5],
    ]
    execute(commands)


@action('单击')
def click_here():
    commands = [
        ['click_down'],
    ]
    execute(commands)

@action('上架当前物品')
def on_shelf_current_item():
    commands = [
        ['click_down'],
        ['click', 1130, 670, 0.5],
        ['click', 1439, 583, 0.5],
        ['click', 1400, 770, 1],
    ]
    execute(commands)


@action('上架下面物品')
def scroll_down_click():
    commands = [
        ['scroll_click', -200, 1],
        ['click', 1130, 670, 0.5],
        ['click', 1439, 583, 0.5],
        ['click', 1400, 770, 1],
    ]
    execute(commands)

@action('上架上面物品')
def scroll_up_click():
    commands = [
        ['scroll_click', 200, 1],
        ['click', 1130, 670, 0.5],
        ['click', 1439, 583, 0.5],
        ['click', 1400, 770, 1],

    ]
    execute(commands)


@action('领取奖励')
def receive_bonus():
    commands = [
        ['click', 300, 1050, 2],
        ['click', 330, 170, 2],
        ['click', 780, 250, 2],
        ['click', 850, 850, 2],
        ['click', 850, 850, 0.5],
    ]
    execute(commands)


def loop_sell():
    commands = [
        ['click', 1130, 670, 0.5],
        ['click', 1439, 583, 0.5],
        ['click', 1400, 770, 1],
        ['click', 1455, 1041, 2],
    ]
    execute(commands)

@action('弃权')
def quit_match():
    commands = [
        ['bid', '0', 1],
        ['click', 1100, 700, 0.5],
        ['click', 1870, 70, 1],
        ['click', 1050, 700, 0.5],
    ]
    execute(commands)


@action('出售')
def on_shelf():
    commands = [
        ['click', 1400, 750, 1],
        ['click', 1400, 600, 0.5],
        ['click', 1400, 770, 2],
        ['scroll', -200, 0.1],
    ]
    execute(commands)


@action('显示位置')
def show_location():
    x, y = C.position()
    print(f'x ={x}, y ={y}')



@action('上架物品')
def on_shelf_item():
    commands = [
        # ['click', 1400, 750, 1],
        ['click', 1130, 670, 0.5],
        ['click', 1400, 620, 0.5],
        ['click', 1400, 770, 1],
    ]
    execute(commands)


@action('出售物品')
def sell_item():
    commands = [
        # ['click', 1400, 750, 1],
        ['click', 1130, 670, 0.5],
        ['click', 1400, 580, 0.5],
        ['click', 1400, 770, 2],
    ]
    execute(commands)


@action('随机抽检')
def check_random_items():
    commands = [
        ['click', 600, 1060, 0.5],
        ['click', 1200, 1060, 1],
        ['click', 1100, 700, 0.5],
    ]
    execute(commands)


@action('使用道具')
def use_game_tool():
    commands = [
        ['click', 620, 980, 0.5],
        ['click', 350, 980, 1],
        ['click', 1100, 700, 0.5],
    ]
    execute(commands)


@action('开始排位')
def begin_rank():
    commands = [
        ['click', 900, 1060, 0.5],
        ['click', 900, 1060, 0.5],
        ['click', 900, 1060, 1],
        ['click', 1100, 600, 1],
        ['click', 1600, 1050, 0.5],
    ]
    execute(commands)


@action('开始打架')
def mansion():
    commands = [
        ['click', 900, 1060, 0.5],
        ['click', 900, 1060, 0.5],
        ['click', 900, 1060, 1],
        ['click', 1600, 800, 1],
        ['click', 1600, 1050, 0.5],
    ]
    execute(commands)


@action('打开沉船')
def mansion():
    commands = [
        ['click', 900, 1060, 0.5],
        ['click', 900, 1060, 0.5],
        ['click', 900, 1060, 1],
        # ['click', 1611, 493, 1],
        ['click', 1835, 690, 1],
        ['click', 1600, 1050, 0.5],
    ]
    execute(commands)


@action('捡快递')
def delivery():
    commands = [
        ['click', 900, 1060, 0.5],
        ['click', 900, 1060, 0.5],
        ['click', 900, 1060, 1],
        ['click', 700, 400, 1],
        ['click', 1600, 1050, 0.5]
    ]
    execute(commands)


@action('继续')
def continue_game():
    commands = [
        ['click', 900, 1060, 0.5],
    ]
    execute(commands)


def bid(price):
    commands = [
        ['bid', price, 1],
        # ['click', 900, 1060, 0.5],
        ['click', 1100, 700, 0.5],
    ]
    execute(commands)



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


@action('停止出售任务')
def stop_sell_task():
    M.stop()

@action('停止乔治任务')
def stop_qiaozhi_task():
    M.stop_qiaozhi()


@action('开始出售任务')
def run_sell_task():
    M.start()
    cdt = grid_coordinates()
    count = 0

    for row in cdt:
        if not M.enable_sell_task:
            break
        for g in row:
            if not M.enable_sell_task:
                break
            count += 1
            if count % 2 == 1:
                continue
            x, y = g
            C.click(x, y)
            loop_sell()
            print(f'x ={x}, y ={y}')


@action('乔治跑刀')
def run_qiaozhi_task():
    def _rank():
        commands = [
            ['click', 900, 1060, 0.5],
            ['click', 900, 1060, 0.5],
            ['click', 900, 1060, 1],
            ['click', 1100, 600, 1],
            ['click', 1600, 1050, 0.5],
        ]
        execute(commands)


    M.start_qiaozhi()
    cnt = 0
    while M.qiaozhi:
        print('出价6元')
        Bidder.bid_price("6")
        time.sleep(10)
        cnt += 1
        if cnt % 5 == 0:
            _rank()



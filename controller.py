import pyautogui


class Controller:

    def position(self):
        pos = pyautogui.position()
        print(pos)
        return pos

    def move(self, x, y, duration=0.2):
        pyautogui.moveTo(x, y, duration=duration)

    def click(self, x=None, y=None, button="left"):
        if x != None and y != None:
            self.move(x, y)
        pyautogui.click(button=button)

    def scroll(self, amount: int):
        pyautogui.scroll(amount)

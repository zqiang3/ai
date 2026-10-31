


class Manager:
    def __init__(self):
        self.enable_sell_task = True
        self.qiaozhi = False


    def stop(self):
        self.enable_sell_task = False


    def start(self):
        self.enable_sell_task = True

    def start_qiaozhi(self):
        self.qiaozhi = True

    def stop_qiaozhi(self):
        self.qiaozhi = False

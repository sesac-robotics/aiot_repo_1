from PyQt6.QtCore import *
import time

class MyTimer(QThread):
    timeout = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.interval = 0.1

    def setInterval(self, sec):
        self.interval = sec

    def start(self, priority = QThread.Priority.InheritPriority):
        super().start(priority)
        self.running = True

    def run(self):
        while self.running == True:
            self.timeout.emit()
            time.sleep(self.interval)

    def stop(self):
        self.running = False

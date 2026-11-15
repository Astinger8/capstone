from PySide2.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide2.QtCore import Qt
from PySide2.QtGui import QFont

class OccupancyWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Occupancy Counts")
        self.setStyleSheet("background-color: #f0f0f0;") 

        font = QFont()
        font.setPointSize(14)
        font.setBold(True)

        self.entry_count_label = QLabel()
        self.entry_count_label.setFont(font)
        self.entry_count_label.setStyleSheet("background-color: #f0f0f0;")
        self.entry_count_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)

        self.exit_count_label = QLabel()
        self.exit_count_label.setFont(font)
        self.exit_count_label.setStyleSheet("background-color: #f0f0f0;")
        self.exit_count_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)

        self.occupant_count_label = QLabel()
        self.occupant_count_label.setFont(font)
        self.occupant_count_label.setStyleSheet("background-color: #f0f0f0;") 
        self.occupant_count_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)

        layout = QVBoxLayout()
        layout.addWidget(self.entry_count_label)
        layout.addWidget(self.exit_count_label)
        layout.addWidget(self.occupant_count_label)
        self.setLayout(layout)

        self.resize(300, 150)
        self.setWindowFlags(Qt.Window)

        # people counter defines
        self.UNKNOWN_REGION = 0
        self.REGION_1 = 1
        self.REGION_2 = 2
        self.REGION_3 = 3
        self.UNKNOWN_STATE = -1
        self.INCOMING_DETECTED = -2
        self.INCOMING = -3
        self.ENTERED = -4
        self.OUTGOING_DETECTED = -5
        self.OUTGOING = -6
        self.EXITED = -7


    def updateCounts(self, entryCount=None, exitCount=None, OccupantCount=None):
        if entryCount is not None:
            self.entry_count_label.setText(f"Entry Count: {entryCount}")
            self.entry_count_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
            self.entry_count_label.show()
        else:
            self.entry_count_label.hide()

        if exitCount is not None:
            self.exit_count_label.setText(f"Exit Count: {exitCount}")
            self.exit_count_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
            self.exit_count_label.show()
        else:
            self.exit_count_label.hide()
        
        if OccupantCount is not None:
            self.occupant_count_label.setText(f"Current Occupancy: {OccupantCount}")
            self.occupant_count_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
            self.occupant_count_label.show()
        else:
            self.occupant_count_label.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = self.width()

        font = self.entry_count_label.font()
        # Adjust the font size based on the width of the window
        font.setPointSize(int(14 + (width / 50)))

        self.entry_count_label.setFont(font)
        self.entry_count_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.exit_count_label.setFont(font)
        self.exit_count_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.occupant_count_label.setFont(font)
        self.occupant_count_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)

    def get_target_region(self, y_pos):
        if -2.5 <= y_pos <= -1:
            return self.REGION_1
        elif -1 <= y_pos <= 1:
            return self.REGION_2
        elif y_pos >= 1:
            return self.REGION_3
        else:
            return self.UNKNOWN_REGION

    def get_target_state(self, target_history):
        prev_region = target_history['prevRegion']
        current_region = target_history['region']

        if prev_region == self.UNKNOWN_REGION and current_region == self.REGION_1:
            return self.INCOMING_DETECTED
        elif prev_region == self.REGION_1 and current_region == self.REGION_2:
            return self.INCOMING
        elif prev_region == self.REGION_2 and current_region == self.REGION_3:
            return self.ENTERED
        elif prev_region == self.UNKNOWN_REGION and current_region == self.REGION_3:
            return self.OUTGOING_DETECTED
        elif prev_region == self.REGION_3 and current_region == self.REGION_2:
            return self.OUTGOING
        elif prev_region == self.REGION_2 and current_region == self.REGION_1:
            return self.EXITED
        else:
            return target_history['prevState']

    def update_people_count(self, target_region_hist, t_id, y_pos, in_count_total, out_count_total):
        target_region = self.get_target_region(y_pos)
        target_region_hist[t_id]['prevRegion'] = target_region_hist[t_id]['region']
        target_region_hist[t_id]['region'] = target_region
        target_region_hist[t_id]['prevState'] = target_region_hist[t_id]['state']
        target_region_hist[t_id]['state'] = self.get_target_state(target_region_hist[t_id])

        state = target_region_hist[t_id]['state']
        prev_state = target_region_hist[t_id]['prevState']

        if state == self.ENTERED and prev_state == self.INCOMING:
            in_count_total += 1
        elif state == self.EXITED and prev_state == self.OUTGOING:
            out_count_total += 1

        return in_count_total, out_count_total, target_region_hist
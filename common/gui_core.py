# General Library Imports
import json
import time
from serial.tools import list_ports
import os
import subprocess
import sys
from contextlib import suppress
import datetime

# PyQt Imports
from PySide2 import QtGui
from PySide2.QtCore import QTimer, Qt, QUrl, QEvent
from PySide2.QtGui import QKeySequence
from PySide2.QtWidgets import (
    QAction,
    QTabWidget,
    QGridLayout,
    QMenu,
    QGroupBox,
    QLineEdit,
    QLabel,
    QPushButton,
    QComboBox,
    QFileDialog,
    QMainWindow,
    QWidget,
    QShortcut,
    QSlider,
    QCheckBox,
    QMessageBox,
    QWidgetAction,
    QInputDialog,
    QActionGroup,
    QScrollArea,
    QSplitter,
    QDialog,
    QDialogButtonBox,
)
from cv2 import VideoCapture, VideoWriter, VideoWriter_fourcc, cvtColor, COLOR_BGR2RGB

# Local Imports
from cached_data import CachedDataType
from demo_defines import *
from gui_threads import *
from parseFrame import parseStandardFrame
from Baud_Rates_Manager import BaudRatesManager

from Common_Tabs.plot_1d import Plot1D
from Common_Tabs.plot_2d import Plot2D
from Common_Tabs.plot_3d import Plot3D
from Common_Tabs.camera_overlay import CameraHardware

from Demo_Classes.surface_classification import SurfaceClassification
from Demo_Classes.point_cloud_classification import PointCloudClassification
from Demo_Classes.people_tracking import PeopleTracking
from Demo_Classes.gesture_recognition import GestureRecognition
from Demo_Classes.level_sensing import LevelSensing
from Demo_Classes.small_obstacle import SmallObstacle
from Demo_Classes.out_of_box_x843 import OOBx843
from Demo_Classes.out_of_box_x432 import OOBx432
from Demo_Classes.out_of_box_x844 import OOBx844
from Demo_Classes.true_ground_speed import TrueGroundSpeed
from Demo_Classes.long_range_pd import LongRangePD
from Demo_Classes.mobile_tracker import MobileTracker
from Demo_Classes.kick_to_open import KickToOpen
from Demo_Classes.calibration import Calibration
from Demo_Classes.vital_signs import VitalSigns
from Demo_Classes.dashcam import Dashcam
from Demo_Classes.ebikes_x432 import EBikes
from Demo_Classes.multi_pass_detection import MultiPassDetection
from Demo_Classes.intruder_detection import IntruderDetection
from Demo_Classes.SeatBeltReminder import SeatBeltReminder
from Demo_Classes.ChildPresenceDetection import ChildPresenceDetection
from Demo_Classes.LifePresenceDetection import LifePresenceDetection
from Demo_Classes.debug_plots import DebugPlots
from Demo_Classes.smart_toilet import SmartToiletDemo
from Demo_Classes.range_sensing import RangeSensing
from Demo_Classes.people_tracking_6844 import PeopleTracking6844
from Demo_Classes.sleep_monitoring import SleepMonitoring
from Demo_Classes.area_scanner import AreaScanner
from Demo_Classes.tree_and_bush_fam import TreeAndBushFAM

# Logger
import logging
log = logging.getLogger(__name__)


class Window(QMainWindow):
    def __init__(self, parent=None, size=[], title="Applications Visualizer"):
        super(Window, self).__init__(parent)

        self.core = Core()
        self.setWindowIcon(QtGui.QIcon("./images/logo.png"))

        self.shortcut = QShortcut(QKeySequence("Ctrl+W"), self)
        self.shortcut.activated.connect(self.close)

        # Set the layout
        # Create tab for different graphing options
        self.demoTabs = QTabWidget()
        self.demoTabs.tabBar().installEventFilter(self)

        self.gridLayout = QGridLayout()

        self.BaudRatesManager = BaudRatesManager()
        self.setWindowTitle(title)
        self.initMenuBar()
        self.core.replay = False

        # Left panel: scrollable container so controls never get squished
        self.leftWidget = QWidget()
        self.leftLayout = QGridLayout(self.leftWidget)
        self.leftLayout.setRowStretch(10, 1)  # push content to top
        self.leftWidget.setMinimumWidth(200)

        self.leftScrollArea = QScrollArea()
        self.leftScrollArea.setWidget(self.leftWidget)
        self.leftScrollArea.setWidgetResizable(True)
        self.leftScrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.leftScrollArea.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        # Splitter between left config panel and demo tabs
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.leftScrollArea)
        self.splitter.addWidget(self.demoTabs)

        self.gridLayout.addWidget(self.splitter, 0, 0, 1, 2)
        self.gridLayout.setRowStretch(0, 1)

        # Add connect options (initConnectionPane calls loadCachedData, so leftLayout must exist first)
        self.initConfigPane()
        self.initConnectionPane()

        self.leftLayout.addWidget(self.comBox, 0, 0, 1, 1)
        self.leftLayout.addWidget(self.configBox, 1, 0, 1, 1)

        self.core.sl.setMinimum(0)
        self.core.sl.setMaximum(30)
        self.core.sl.setValue(20)
        self.core.sl.setTickPosition(QSlider.TicksBelow)
        self.core.sl.setTickInterval(5)

        self.replayBox = QGroupBox("Replay")
        self.replayLayout = QGridLayout()
        self.replayLayout.addWidget(self.core.sl, 0, 0, 1, 1)
        self.replayBox.setLayout(self.replayLayout)
        self.replayBox.setVisible(False)
        self.gridLayout.addWidget(self.replayBox, 1, 0, 1, 2)

        self.central = QWidget()
        self.central.setLayout(self.gridLayout)

        self.setCentralWidget(self.central)

        self.showMaximized()

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, '_initialSizeApplied', False):
            self._initialSizeApplied = True
            QTimer.singleShot(0, self._applyInitialSplit)
        if not getattr(self, '_screenSignalConnected', False):
            wh = self.windowHandle()
            if wh:
                wh.screenChanged.connect(self._onScreenChanged)
                self._screenSignalConnected = True

    def eventFilter(self, obj, event):
        if obj is self.demoTabs.tabBar() and event.type() == QEvent.Resize:
            QTimer.singleShot(0, obj.repaint)
        return super().eventFilter(obj, event)

    def _onScreenChanged(self, screen):
        if self.isMaximized():
            QTimer.singleShot(0, self.showMaximized)
        QTimer.singleShot(100, self._applyInitialSplit)
        QTimer.singleShot(100, self.demoTabs.tabBar().repaint)

    def _applyInitialSplit(self):
        w = self.splitter.width()
        if w > 0:
            self.splitter.setSizes([int(w * 0.30), int(w * 0.70)])


    # Callback function when the video camera is selected from the menu
    def selectVideoCamera(self, state):
        # Grab the camera option selected
        camSelected = int(self.selectCamActionGroup.checkedAction().text()[-1])
        try:
            # Open the camera selected
            CameraHardware.get_instance().changeVideoSource(camSelected)
            if hasattr(self.core.demoClassDict[self.core.demo], "cameraRadarFusionTab"):
                self.core.demoClassDict[self.core.demo].cameraRadarFusionTab.startRadarlessCameraCapture()
        except:
            print("Could not open camera")

    # Callback function to apply the camera video settings to CameraHardware
    def applyVideoCameraSettings(self, xRes, yRes, xFoV, yFoV, xOffset, yOffset, zOffset):
        camera_hw = CameraHardware.get_instance()
        camera_hw.setResolutionAndFoV(int(xRes.text()),int(yRes.text()),float(xFoV.text()),float(yFoV.text()))
        camera_hw.setRadarCameraOffsets(float(xOffset.text()),float(yOffset.text()),float(zOffset.text()))
        self.cameraSettingsDialog.accept()
 
    # Callback function to ask the camera for the default resolution and field of view settings and fill those values in the appropriate boxes in the popup
    def populateBoxWithDefaultSettings(self):
        [xr,yr,xf,yf] = CameraHardware.get_instance().checkDefaultResolutionAndFoV()
        self.xResLineEdit.setText(str(xr))
        self.yResLineEdit.setText(str(yr))
        self.xFoVLineEdit.setText(str(xf))
        self.yFoVLineEdit.setText(str(yf))

    # Opens the pop up menu for the video camera settings
    def openCameraSettingsDialog(self):
        self.cameraSettingsDialog = QDialog(self)
        self.cameraSettingsDialog.setWindowTitle("Video Camera Settings")

        # Populate fields with the existing values
        camera_hw = CameraHardware.get_instance()
        [xResfromCam,yResfromCam,xFoVFromCam,yFoVFromCam] = camera_hw.getResolutionAndFoV()
        [x_o, y_o, z_o] = camera_hw.getRadarCameraOffsets()

        layout = QGridLayout()
        message = QLabel("Camera Resolution (pixels): ")
        self.xResLineEdit = QLineEdit()
        self.xResLineEdit.setText(str(xResfromCam))
        self.yResLineEdit = QLineEdit()
        self.yResLineEdit.setText(str(yResfromCam))
        byString = QLabel("x")

        layout.addWidget(message,0,0)
        layout.addWidget(self.xResLineEdit,0,1)
        layout.addWidget(byString,0,2)
        layout.addWidget(self.yResLineEdit,0,3)

        message = QLabel("Field of View (degrees) ")
        self.xFoVLineEdit = QLineEdit()
        self.xFoVLineEdit.setText(str(xFoVFromCam))
        self.yFoVLineEdit = QLineEdit()
        self.yFoVLineEdit.setText(str(yFoVFromCam))
        byString = QLabel("x")

        layout.addWidget(message,1,0)
        layout.addWidget(self.xFoVLineEdit,1,1)
        layout.addWidget(byString,1,2)
        layout.addWidget(self.yFoVLineEdit,1,3)

        message = QLabel("Camera's position with respect to Radar")
        self.xOffsetLineEdit = QLineEdit()
        self.xOffsetLineEdit.setText(str(x_o))
        self.yOffsetLineEdit = QLineEdit()
        self.yOffsetLineEdit.setText(str(y_o))
        self.zOffsetLineEdit = QLineEdit()
        self.zOffsetLineEdit.setText(str(z_o))

        layout.addWidget(message,2,0,1,-1,alignment=Qt.AlignCenter)
        layout.addWidget(QLabel("X (m)"),3,0)
        layout.addWidget(self.xOffsetLineEdit,3,1)
        layout.addWidget(QLabel("Y (m)"),3,2)
        layout.addWidget(self.yOffsetLineEdit,3,3)
        layout.addWidget(QLabel("Z (m)"),3,4)
        layout.addWidget(self.zOffsetLineEdit,3,5)

        getDefaultSettingsButton = QPushButton("Get Default Camera Settings")
        getDefaultSettingsButton.clicked.connect(self.populateBoxWithDefaultSettings)
        layout.addWidget(getDefaultSettingsButton,4,0,1,-1)

        # OK Button on the bottom
        self.cameraSettingsDialogDone = QPushButton("Ok")
        layout.addWidget(self.cameraSettingsDialogDone,5,0,1,-1)
        layout.setSpacing(10)
        self.cameraSettingsDialog.setLayout(layout)
        # When you click ok, call the apply settings function
        self.cameraSettingsDialogDone.clicked.connect(lambda : \
            self.applyVideoCameraSettings(self.xResLineEdit,self.yResLineEdit,self.xFoVLineEdit,self.yFoVLineEdit, \
            self.xOffsetLineEdit, self.yOffsetLineEdit, self.zOffsetLineEdit))
        self.cameraSettingsDialog.exec()
        
    # Create the camera option from the top menu
    def createCameraMenu(self):
        # Add main level menu button
        self.level0CameraMenu = QMenu("&Camera Options", self)

        # Create sub-level menu options
        self.level1CameraOnAction = QAction("Display Camera", self)
        self.level1CameraOnAction.setCheckable(True)
        self.level1CameraRecordAction = QAction("Save Camera Stream to File", self)
        self.level1CameraRecordAction.setCheckable(True)
        self.level1CameraRecordOverlayAction = QAction("Save Camera Stream with Radar Overlayed to File", self)
        self.level1CameraRecordOverlayAction.setCheckable(True)
        self.level1CameraPointsColor = QAction("Color Overlay Points by Doppler", self)
        self.level1CameraPointsColor.setCheckable(True)
        self.level1SelectCameraMenu = QMenu("&Select Video Camera", self)
        self.level1CameraSettingsMenu = QAction("&Video Camera Settings", self)

        # Connect buttons to desired function calls
        self.level1CameraOnAction.toggled.connect(self.toggleCameraOn)
        self.level1CameraRecordAction.toggled.connect(self.toggleCameraRecord)
        self.level1CameraRecordOverlayAction.toggled.connect(self.toggleOverlayRecord)
        self.level1CameraPointsColor.toggled.connect(self.togglePointsColorByDoppler)
        self.level1CameraSettingsMenu.triggered.connect(self.openCameraSettingsDialog)

        # Add sub-level options to menu button
        self.level0CameraMenu.addAction(self.level1CameraOnAction)
        self.level0CameraMenu.addAction(self.level1CameraRecordAction)
        self.level0CameraMenu.addAction(self.level1CameraRecordOverlayAction)
        self.level0CameraMenu.addAction(self.level1CameraPointsColor)
        self.level0CameraMenu.addMenu(self.level1SelectCameraMenu)
        self.level0CameraMenu.addAction(self.level1CameraSettingsMenu)
        vid_cam_idx = 0
        self.selectCamActionGroup = QActionGroup(self.level1SelectCameraMenu)

        # Check available camera devices and index them for selection purposes
        videoCamIndexList = CameraHardware.get_instance().getAvailableVideoCameras()
        self.videoCamSelArray = []
        for vid_cam_idx in videoCamIndexList:
            self.videoCamSelArray.append(QAction("&Camera " + str(vid_cam_idx), self.level0CameraMenu, checkable=True))
            self.level1SelectCameraMenu.addAction(self.videoCamSelArray[-1])
            self.selectCamActionGroup.addAction(self.videoCamSelArray[-1])
        self.selectCamActionGroup.setExclusive(True) # Only one camera can be enabled at once
        self.selectCamActionGroup.triggered.connect(lambda idx=vid_cam_idx: self.selectVideoCamera(idx))

        # Set starting state for all buttons to disabled
        self.level1CameraRecordAction.setEnabled(0)
        self.level1CameraRecordOverlayAction.setEnabled(0)
        self.level1SelectCameraMenu.setEnabled(0)
        self.level1CameraPointsColor.setEnabled(0)
        self.level1CameraSettingsMenu.setEnabled(0)

    def initMenuBar(self):
        menuBar = self.menuBar()
        # Creating menus using a QMenu object
        fileMenu = QMenu("&File", self)
        playbackMenu = QMenu("&Playback", self)
        helpMenu = QMenu("&Help", self)
        settingsMenu = QMenu("&Settings", self)
        self.BaudRatesManager.addBaudRatesToSettingsMenu(settingsMenu)
        
        self.createCameraMenu()

        self.logOutputAction = QAction("Log Terminal Output to File", self)
        self.playbackAction = QAction("Load and Replay", self)
        self.helpAction = QAction("Visualizer User Guide", self)

        self.playbackAction.triggered.connect(self.loadForReplay)
        self.playbackAction.setCheckable(True)
        self.logOutputAction.triggered.connect(self.toggleLogOutput)
        self.logOutputAction.setCheckable(True)
        self.helpAction.triggered.connect(self.openUserGuide)

        playbackMenu.addAction(self.playbackAction)
        fileMenu.addAction(self.logOutputAction)
        helpMenu.addAction(self.helpAction)

        menuBar.addMenu(fileMenu)
        menuBar.addMenu(playbackMenu)
        menuBar.addMenu(settingsMenu)
        menuBar.addMenu(self.level0CameraMenu)
        menuBar.addMenu(helpMenu)

        self.menu = menuBar

    def loadForReplay(self, state):
        if (state):
            self.recordAction.setChecked(False)
            self.core.replayFile = QFileDialog.getOpenFileName(self, 'Open Replay JSON File', '.',"JSON Files (*.json)")
            self.core.replay = True
            self.core.loadForReplay(True)

            # Disable COM Ports/Device/Demo/Config
            self.demoList.setEnabled(False)
            self.deviceList.setEnabled(False)
            self.cliCom.setEnabled(False)
            self.dataCom.setEnabled(False)
            self.connectButton.setEnabled(False)
            self.filename_edit.setEnabled(False)
            self.selectConfig.setEnabled(False)
            self.sendConfig.setEnabled(False)
            self.start.setEnabled(True)
            self.start.setText("Replay")

            self.replayBox.setVisible(True)
        else:
            self.core.replay = False

            # Disable COM Ports/Device/Demo/Config
            self.demoList.setEnabled(True)
            self.deviceList.setEnabled(True)
            self.cliCom.setEnabled(True)
            self.dataCom.setEnabled(True)
            self.connectButton.setEnabled(True)
            self.filename_edit.setEnabled(True)
            self.selectConfig.setEnabled(True)
            self.sendConfig.setEnabled(True)
            self.start.setText("Start without Send Configuration")

            self.replayBox.setVisible(False)

    def toggleSaveData(self):
        if self.recordAction.isChecked():
            self.core.parser.setSaveRadarBinary(True)
            self.core.cachedData.setCachedRecord("True")
        else:
            self.core.parser.setSaveRadarBinary(False)
            self.core.cachedData.setCachedRecord("False")
            
    # Callback function to turn the camera on from the menu bar
    def toggleCameraRecord(self):
        camera_hw = CameraHardware.get_instance()
        if self.level1CameraRecordAction.isChecked():
            folder = os.path.join('binData', self.core.fileSaveFolderName)
            os.makedirs(folder, exist_ok=True)
            raw_path = os.path.join(folder, 'videoCapture.mp4')
            self.core.videoFilePath = raw_path
            success = camera_hw.startRawRecording(raw_path)
            if not success:
                logging.error("Failed to start raw camera recording")
                self.level1CameraRecordAction.setChecked(False)
        else:
            camera_hw.stopRawRecording()

        # Save and restore the current tab to prevent unwanted tab switching
        current_tab_index = self.demoTabs.currentIndex()
        if current_tab_index >= 0 and current_tab_index < self.demoTabs.count():
            self.demoTabs.setCurrentIndex(current_tab_index)

    def toggleOverlayRecord(self):
        """Toggle overlay video recording (camera + radar point cloud projection)"""
        if self.level1CameraRecordOverlayAction.isChecked():
            # Start overlay recording for the current demo's camera tab
            current_demo = self.core.demoClassDict.get(self.core.demo)
            if current_demo and hasattr(current_demo, 'cameraRadarFusionTab') and current_demo.cameraRadarFusionTab:
                # Generate output path in binData directory
                overlay_path = self.core.videoFilePath.replace('videoCapture.mp4', 'videoCapture_overlay.mp4')
                success = current_demo.cameraRadarFusionTab.startOverlayRecording(overlay_path)
                if not success:
                    logging.error("Failed to start overlay recording")
                    self.level1CameraRecordOverlayAction.setChecked(False)
            else:
                logging.warning("Camera overlay tab not available for overlay recording")
                self.level1CameraRecordOverlayAction.setChecked(False)
        else:
            # Stop overlay recording
            current_demo = self.core.demoClassDict.get(self.core.demo)
            if current_demo and hasattr(current_demo, 'cameraRadarFusionTab') and current_demo.cameraRadarFusionTab:
                current_demo.cameraRadarFusionTab.stopOverlayRecording()

    def toggleCameraOn(self):
        camera_hw = CameraHardware.get_instance()

        if self.level1CameraOnAction.isChecked():
            # Check if any cameras are available
            if len(self.videoCamSelArray) == 0:
                logging.error("No cameras available. Cannot turn on video.")
                self.level1CameraOnAction.setChecked(False)
                return

            # Set the camera index to the first available camera before turning on
            camera_hw.videoCaptureIdx = int(self.videoCamSelArray[0].text()[-1])
            logging.info(f"Attempting to open camera index: {camera_hw.videoCaptureIdx}")

            # Try to turn on the camera
            success = camera_hw.turnVideoOn()
            if not success:
                logging.error(f"Failed to open camera {camera_hw.videoCaptureIdx}")
                self.level1CameraOnAction.setChecked(False)
                return

            self.level1CameraRecordAction.setEnabled(1)
            self.level1CameraRecordOverlayAction.setEnabled(1)
            self.level1SelectCameraMenu.setEnabled(1)
            self.level1CameraPointsColor.setEnabled(1)
            self.level1CameraSettingsMenu.setEnabled(1)

            # Initialize the camera tab graphics immediately if the demo has a camera tab
            if hasattr(self.core.demoClassDict[self.core.demo], "cameraRadarFusionTab"):
                camera_tab = self.core.demoClassDict[self.core.demo].cameraRadarFusionTab
                if camera_tab is not None:
                    if not camera_tab._fully_initialized:
                        logging.info("Initializing camera graphics on camera enable...")
                        camera_tab._lazy_init_graphics()
                    # Start the camera capture timer to display video frames
                    logging.info("Starting camera capture...")
                    camera_tab.startRadarlessCameraCapture()
        else:
            self.level1CameraRecordAction.setChecked(0)
            self.level1CameraRecordAction.setDisabled(1)
            # Stop overlay recording if active (setChecked alone won't call toggleOverlayRecord)
            if self.level1CameraRecordOverlayAction.isChecked():
                self.level1CameraRecordOverlayAction.setChecked(0)
                current_demo = self.core.demoClassDict.get(self.core.demo)
                if current_demo and hasattr(current_demo, 'cameraRadarFusionTab') and current_demo.cameraRadarFusionTab:
                    current_demo.cameraRadarFusionTab.stopOverlayRecording()
            self.level1CameraRecordOverlayAction.setEnabled(0)
            # Stop camera capture timer if running
            if hasattr(self.core.demoClassDict[self.core.demo], "cameraRadarFusionTab"):
                camera_tab = self.core.demoClassDict[self.core.demo].cameraRadarFusionTab
                if camera_tab is not None:
                    camera_tab.endRadarlessCameraCapture()
            camera_hw.turnVideoOff()  # also calls stopRawRecording() internally
            self.level1CameraRecordAction.setEnabled(0)
            self.level1SelectCameraMenu.setEnabled(0)
            self.level1CameraSettingsMenu.setEnabled(0)

    def togglePointsColorByDoppler(self):
        current_demo = self.core.demoClassDict.get(self.core.demo)
        if current_demo and hasattr(current_demo, 'cameraRadarFusionTab') and current_demo.cameraRadarFusionTab:
            current_demo.cameraRadarFusionTab.togglePointsColorByDoppler()

    def toggleLogOutput(self):
        if (
            self.recordAction.isChecked()
        ):  # Save terminal output to logFile, set 0 to show terminal output
            ts = time.localtime()
            terminalFileName = str(
                "logfile_"
                + str(ts[2])
                + str(ts[1])
                + str(ts[0])
                + "_"
                + str(ts[3])
                + str(ts[4])
                + ".txt"
            )
            sys.stdout = open(terminalFileName, "w")
        else:
            sys.stdout = sys.__stdout__
    
    def openUserGuide(self):
        userGuideURL = QUrl('https://dev.ti.com/tirex/local?id=mmwave_applications_visualizer_user_guide&packageId=radar_toolbox')
        openUserGuide = QtGui.QDesktopServices.openUrl(userGuideURL)
        
        if not openUserGuide:
            QtGui.QMessageBox.warning(self, 'ERROR', 'Unable to open the Visualizer User Guide')
            log.error("Unable to open the Visualizer User Guide")
    
    def initConnectionPane(self):
        self.comBox = QGroupBox("Connect to COM Ports")
        self.cliCom = QLineEdit("")
        self.dataCom = QLineEdit("")
        self.connectStatus = QLabel("Not Connected")
        self.connectButton = QPushButton("Connect")
        self.connectButton.clicked.connect(self.onConnect)
        self.demoList = QComboBox()
        self.deviceList = QComboBox()
        self.recordAction = QCheckBox("Save Radar Data to File", self)

        # TODO Add replay support
        self.demoList.addItems(self.core.getDemoList())
        self.demoList.currentIndexChanged.connect(self.onChangeDemo)
        self.deviceList.addItems(self.core.getDeviceList())
        self.deviceList.currentIndexChanged.connect(self.onChangeDevice)
        self.comLayout = QGridLayout()
        self.comLayout.addWidget(QLabel("Device:"), 0, 0)
        self.comLayout.addWidget(self.deviceList, 0, 1)
        self.comLayout.addWidget(QLabel("CLI COM:"), 1, 0)
        self.comLayout.addWidget(self.cliCom, 1, 1)
        self.comLayout.addWidget(QLabel("DATA COM:"), 2, 0)
        self.comLayout.addWidget(self.dataCom, 2, 1)
        self.comLayout.addWidget(QLabel("Demo:"), 3, 0)
        self.comLayout.addWidget(self.demoList, 3, 1)
        self.comLayout.addWidget(self.connectButton, 4, 0)
        self.recordAction.stateChanged.connect(self.toggleSaveData)
        self.comLayout.addWidget(self.recordAction, 5, 0)
        self.comLayout.addWidget(self.connectStatus, 4, 1)

        self.comBox.setLayout(self.comLayout)
        self.demoList.setCurrentIndex(1)  # initialize this to a stable value
        self.demoList.setCurrentIndex(0)  # initialize this to a stable value

        # Find all Com Ports
        serialPorts = list(list_ports.comports())

        # Find default CLI Port and Data Port
        for port in serialPorts:
            if (
                CLI_XDS_SERIAL_PORT_NAME in port.description
                or CLI_SIL_SERIAL_PORT_NAME in port.description
            ):
                log.info(f"CLI COM Port found: {port.device}")
                comText = port.device
                comText = comText.replace("COM", "")
                self.cliCom.setText(comText)

            elif (
                DATA_XDS_SERIAL_PORT_NAME in port.description
                or DATA_SIL_SERIAL_PORT_NAME in port.description
            ):
                log.info(f"Data COM Port found: {port.device}")
                comText = port.device
                comText = comText.replace("COM", "")
                self.dataCom.setText(comText)

        self.core.isGUILaunched = 1
        self.loadCachedData()

    def initConfigPane(self):
        self.configBox = QGroupBox("Configuration")
        self.selectConfig = QPushButton("Select Configuration")
        self.sendConfig = QPushButton("Start and Send Configuration")
        self.start = QPushButton("Start without Send Configuration")
        self.sensorStop = QPushButton("Send sensorStop Command")
        self.sensorStop.setToolTip("Stop sensor (only works if lowPowerCfg is 0)")
        self.filename_edit = QLineEdit()
        self.selectConfig.clicked.connect(lambda: self.selectCfg(self.filename_edit))
        self.sendConfig.setEnabled(False)
        self.start.setEnabled(False)
        self.sendConfig.clicked.connect(self.sendCfg)
        self.start.clicked.connect(self.startApp)
        self.sensorStop.clicked.connect(self.stopSensor)
        self.sensorStop.setHidden(True)
        self.configLayout = QGridLayout()
        self.configLayout.addWidget(self.filename_edit, 0, 0, 1, 1)
        self.configLayout.addWidget(self.selectConfig, 0, 1, 1, 1)
        self.configLayout.addWidget(self.sendConfig, 1, 0, 1, 2)
        self.configLayout.addWidget(self.start, 2, 0, 1, 2)
        self.configLayout.addWidget(self.sensorStop, 3, 0, 1, 2)
        # self.configLayout.addStretch(1)
        self.configBox.setLayout(self.configLayout)

    def displayErrorPopUp(self):
        popUp = QMessageBox.critical(
                self,
                "ERROR",
                "Ensure that the device is in the proper SOP mode after flashing the correct binary, and that the cfg you are sending is valid")
        popUp.exec_()
    
    def loadCachedData(self):
        self.core.loadCachedData(
            self.demoList, self.deviceList, self.recordAction, self.leftLayout, self.demoTabs, self.menu, self
        )

    # Callback function when device is changed
    def onChangeDevice(self):
        self.core.changeDevice(
            self.demoList, self.deviceList, self.leftLayout, self.demoTabs
        )
        self.core.updateCOMPorts(self.cliCom, self.dataCom)
        self.core.updateResetButton(self.sensorStop)
        self.BaudRatesManager.updateBaudMenuState(self.core.device)

    # Callback function when demo is changed
    def onChangeDemo(self):
        self.core.changeDemo(
            self.demoList, self.deviceList, self.leftLayout, self.demoTabs, self.menu, self
        )
        
        # When 2-Pass Video doorbell is the demo, you cannot send a cfg file over UART
        if(self.core.demo == "Multi Pass Video Doorbell"):
            self.sendConfig.setDisabled(1)
        else:
            self.sendConfig.setDisabled(0)
            
        # If using 6843, it's always 115200 and 921600 for baud rate
        if(self.core.device in ("xWR6843","xWR1843")):
            self.BaudRatesManager.setInputCOMTo115200()
            self.BaudRatesManager.setOutputCOMTo921600()
            
        # If using 6844, it's 115200 and 1250000 for baud rate
        
        elif self.core.device in ("xWRL6844"):
            self.BaudRatesManager.setInputCOMTo115200()
            self.BaudRatesManager.setOutputCOMTo1250000()

        # If using 6432, Baud Rate depends on the demo
        elif self.core.device in ("xWRL6432", "xWR6432", "xWRL1432"):
            # Demos that use 1250000 baud by default
            if(self.core.demo == DEMO_GESTURE or self.core.demo == DEMO_KTO or self.core.demo == DEMO_TOILET or self.core.demo == DEMO_1D_SENSING):
                self.BaudRatesManager.setSingleCOMTo1250000()
            else:
                self.BaudRatesManager.setSingleCOMTo115200()
        else:
            log.error("Unexpected device")

        # If the demo has a camera_overlay tab allowed, then enable the camera
        if(self.core.demo != ""):
            if(hasattr(self.core.demoClassDict[self.core.demo], "cameraRadarFusionTab")):
                self.level1CameraOnAction.setEnabled(1)
                self.level0CameraMenu.setEnabled(1)
            else:
                self.level1CameraOnAction.setChecked(0)
                self.level1CameraOnAction.setDisabled(1)
                self.level0CameraMenu.setDisabled(1)

    # Callback function when connect button clicked
    def onConnect(self):
        if (self.connectStatus.text() == "Not Connected" or self.connectStatus.text() == "Unable to Connect"):
            if self.core.connectCom(self.cliCom, self.dataCom, self.connectStatus, self.BaudRatesManager) == 0:
                self.BaudRatesManager.lockBaudMenuState()
                self.connectButton.setText("Reset Connection")
                # When 2-Pass Video doorbell is the demo, you cannot send a cfg file over UART
                if(self.core.demo == "Multi Pass Video Doorbell"):
                    self.sendConfig.setEnabled(False)
                else:
                    self.sendConfig.setEnabled(True)
                self.start.setEnabled(True)
            else:
                self.sendConfig.setEnabled(False)
                self.start.setEnabled(False)
        else:
            self.core.gracefulReset()
            self.BaudRatesManager.updateBaudMenuState(self.core.device)
            self.connectButton.setText("Connect")
            self.connectStatus.setText("Not Connected")
            self.sendConfig.setEnabled(False)
            self.start.setEnabled(False)
            # Camera streaming is only controlled by "Display Video" — restore independent
            # camera timer that was stopped when radar data began flowing (people_tracking.py)
            if CameraHardware.get_instance().isOn():
                current_demo = self.core.demoClassDict.get(self.core.demo)
                if current_demo and hasattr(current_demo, "cameraRadarFusionTab"):
                    camera_tab = current_demo.cameraRadarFusionTab
                    if camera_tab is not None:
                        camera_tab.startRadarlessCameraCapture()

            # need to do ser.close()

    # Callback function when 'Select Configuration' is clicked
    def selectCfg(self, filename):
        self.core.selectCfg(filename)

    # Callback function when 'Start and Send Configuration' is clicked
    def sendCfg(self):
        self.core.sendCfg()
        if(self.core.parser.comError == 1):
            self.core.parser.comError = 0
            self.displayErrorPopUp()
            
    # Callback function to send sensorStop to device
    def stopSensor(self):
        self.core.stopSensor()
        if(self.core.parser.comError == 1):
            self.core.parser.comError = 0
            self.displayErrorPopUp()

    # Callback function when 'Start without Send Configuration' is clicked
    def startApp(self):
        if (self.core.replay and self.core.playing is False):
            self.start.setText("Pause")
        elif (self.core.replay and self.core.playing is True):
            self.start.setText("Replay")
        self.core.startApp()

class Core:
    def __init__(self):
        self.cachedData = CachedDataType()

        self.device = "xWR6843"
        self.demo = DEMO_OOB_x843

        self.frameTime = 50
        self.fileSaveFolderName = datetime.datetime.now().strftime("%m_%d_%Y_%H_%M_%S")
        self.parser = UARTParser(type="DoubleCOMPort", fileSaveFolderName=self.fileSaveFolderName)

        self.replayFile = "replay.json"
        self.replay = False

        # Set when raw recording starts; used to derive overlay path
        self.videoFilePath = ""

        # set to 1
        self.isGUILaunched = 0

        self.sl = QSlider(Qt.Horizontal)
        self.sl.valueChanged.connect(self.sliderValueChange)
        self.playing = False
        self.replayFrameNum = 0
        self.saveVideoData = False


        # Populated with each demo and it's corresponding object
        self.demoClassDict = {
            DEMO_OOB_x843: OOBx843(),
            DEMO_OOB_x432: OOBx432(),
            DEMO_3D_PEOPLE_TRACKING: PeopleTracking(),
            DEMO_OOB_x844: OOBx844(),
            DEMO_VITALS: VitalSigns(),
            DEMO_SMALL_OBSTACLE: SmallObstacle(),
            DEMO_GESTURE: GestureRecognition(),
            DEMO_SURFACE: SurfaceClassification(),
            DEMO_PC_CLASS: PointCloudClassification(),
            DEMO_LEVEL_SENSING: LevelSensing(),
            DEMO_1D_SENSING: RangeSensing(),
            DEMO_GROUND_SPEED: TrueGroundSpeed(),
            DEMO_LONG_RANGE: LongRangePD(),
            DEMO_MOBILE_TRACKER: MobileTracker(),
            DEMO_KTO: KickToOpen(),
            DEMO_CALIBRATION: Calibration(),
            DEMO_DASHCAM: Dashcam(),
            DEMO_EBIKES: EBikes(),
            DEMO_MULTI_PASS_DETECTION: MultiPassDetection(),
            DEMO_INTRUDER: IntruderDetection(),
            DEMO_SBR : SeatBeltReminder(),
            # DEMO_CPD : ChildPresenceDetection(), no longer supported
            DEMO_LPD : LifePresenceDetection(),
            DEMO_DEBUG_PLOTS: DebugPlots(),
            DEMO_TOILET: SmartToiletDemo(),
            DEMO_3DPT_6844: PeopleTracking6844(),
            DEMO_SLEEP_MONITORING: SleepMonitoring(),
            DEMO_AREA_SCANNER: AreaScanner(),
            DEMO_TREE_BUSH_FAM: TreeAndBushFAM()
        }

    def loadCachedData(self, demoList, deviceList, recordAction, gridLayout, demoTabs, menuBar, app):
        deviceName = self.cachedData.getCachedDeviceName()
        demoName = self.cachedData.getCachedDemoName()
        if self.cachedData.getCachedRecord() == "True":
            recordState = True
        else:
            recordState = False
        if deviceName in self.getDeviceList():
            deviceList.setCurrentIndex(self.getDeviceList().index(deviceName))

        if demoName in self.getDemoList():
            demoList.setCurrentIndex(self.getDemoList().index(demoName))
            self.changeDemo(demoList, deviceList, gridLayout, demoTabs, menuBar, app)

        if recordState:
            self.parser.setSaveRadarBinary(True)
            recordAction.setChecked(True)
        else:
            #default recordAction is false so no need to set that here
            self.parser.setSaveRadarBinary(False)
            recordAction.setChecked(False)

    def getDemoList(self):
        return DEVICE_DEMO_DICT[self.device]["demos"]

    def getDeviceList(self):
        return list(DEVICE_DEMO_DICT.keys())

    def changeDemo(self, demoList, deviceList, gridLayout, demoTabs, menuBar, app):
        # Remove custom menu options from the previous demo before switching
        if hasattr(self, 'demo') and self.demo and self.demo in self.demoClassDict:
            prev_demo = self.demoClassDict[self.demo]
            if hasattr(prev_demo, 'customMenuOptions'):
                menuBar.removeAction(prev_demo.customMenuOptions.menuAction())

        self.demo = demoList.currentText()
            
        if (self.isGUILaunched):
            self.cachedData.setCachedDemoName(self.demo)
            self.cachedData.setCachedDeviceName(deviceList.currentText())

        permanentWidgetsList = ["Connect to COM Ports", "Configuration", "Tabs", "Replay"]
        # Destroy current contents of graph pane
        for _ in range(demoTabs.count()):
            demoTabs.removeTab(0)
        for i in range(gridLayout.count()):
            try:
                currWidget = gridLayout.itemAt(i).widget()
                if currWidget.title() not in permanentWidgetsList:
                    currWidget.hide()
            except AttributeError as e:
                log.log(0, "Demo Tabs don't have title attribute. This is OK")
                continue

        # Make call to selected demo's initialization function
        if self.demo in self.demoClassDict:
            self.demoClassDict[self.demo].setupGUI(gridLayout, demoTabs, self.device, menuBar, app)
        QTimer.singleShot(0, demoTabs.tabBar().repaint)

    def changeDevice(self, demoList, deviceList, gridLayout, demoTabs):
        self.device = deviceList.currentText()

        self.parser.device = self.device

        if (self.isGUILaunched):
            self.cachedData.setCachedDemoName(demoList.currentText())
            self.cachedData.setCachedDeviceName(self.device)

        if DEVICE_DEMO_DICT[self.device]["singleCOM"]:
            self.parser.parserType = "SingleCOMPort"
        else:
            self.parser.parserType = "DoubleCOMPort"

        demoList.clear()
        demoList.addItems(DEVICE_DEMO_DICT[self.device]["demos"])

    def updateCOMPorts(self, cliCom, dataCom):
        if DEVICE_DEMO_DICT[self.device]["isxWRLx432"]:
            dataCom.setText(cliCom.text())
            dataCom.setEnabled(False)
        else:
            dataCom.setEnabled(True)

    def updateResetButton(self, sensorStopButton):
        if DEVICE_DEMO_DICT[self.device]["isxWRLx432"]:
            sensorStopButton.setHidden(True) # TODO change to false once sending sensorStop is implemented
        else:
            sensorStopButton.setHidden(True)

    def stopSensor(self):
        self.parser.sendLine("sensorStop 0")

    def selectFile(self, filename):
        try:
            current_dir = os.getcwd()
            configDirectory = current_dir
            path = self.cachedData.getCachedCfgPath()
            if path != "":
                configDirectory = path
        except:
            configDirectory = ""

        fname = QFileDialog.getOpenFileName(caption="Open .cfg File", dir=configDirectory, filter="cfg(*.cfg)")
        filename.setText(str(fname[0]))
        return fname[0]

    def parseCfg(self, fname):
        if (self.replay):
            self.cfg = self.data['cfg']
        else:
            with open(fname, "r") as cfg_file:
                self.cfg = cfg_file.readlines()
                self.parser.cfg = self.cfg
                self.parser.demo = self.demo
                self.parser.device = self.device
        for line in self.cfg:
            args = line.split()
            if len(args) > 0:
                # trackingCfg
                if args[0] == "trackingCfg":
                    if len(args) < 5:
                        log.error("trackingCfg had fewer arguments than expected")
                    else:
                        with suppress(AttributeError):
                            self.demoClassDict[self.demo].parseTrackingCfg(args)
                elif args[0] == "SceneryParam" or args[0] == "boundaryBox":
                    if len(args) < 7:
                        log.error(
                            "SceneryParam/boundaryBox had fewer arguments than expected"
                        )
                    else:
                        with suppress(AttributeError):
                            self.demoClassDict[self.demo].parseBoundaryBox(args)
                elif args[0] == "frameCfg":
                    if len(args) < 4:
                        log.error("frameCfg had fewer arguments than expected")
                    else:
                        self.frameTime = float(args[5]) / 2
                        self.demoClassDict[self.demo].frameTime = self.frameTime
                elif args[0] == "sensorPosition":
                    # sensorPosition for x843 family has 3 args
                    if DEVICE_DEMO_DICT[self.device]["isxWRx843"] and len(args) < 4:
                        log.error("sensorPosition had fewer arguments than expected")
                    elif DEVICE_DEMO_DICT[self.device]["isxWRLx432"] and len(args) < 6:
                        log.error("sensorPosition had fewer arguments than expected")
                    elif DEVICE_DEMO_DICT[self.device]["isxWRLx844"] and self.demo not in BUSINESS_DEMOS["Industrial"]:
                        self.demoClassDict[self.demo].parseSensorPositionCfg(args)
                    else:
                        with suppress(AttributeError):
                            self.demoClassDict[self.demo].parseSensorPosition(
                                args, DEVICE_DEMO_DICT[self.device]["isxWRx843"]
                            )
                
                # Only used for Small Obstacle Detection
                elif args[0] == "occStateMach":
                    numZones = int(args[1])
                # Only used for Small Obstacle Detection
                elif args[0] == "zoneDef":
                    if len(args) < 8:
                        log.error("zoneDef had fewer arguments than expected")
                    else:
                        with suppress(AttributeError):
                            self.demoClassDict[self.demo].parseBoundaryBox(args)
                elif args[0] == "mpdBoundaryBox":
                    if len(args) < 8:
                        log.error("mpdBoundaryBox had fewer arguments than expected")
                    else:
                        with suppress(AttributeError):
                            self.demoClassDict[self.demo].parseBoundaryBox(args)
                elif args[0] == "chirpComnCfg":
                    if len(args) < 8:
                        log.error("chirpComnCfg had fewer arguments than expected")
                    else:
                        with suppress(AttributeError):
                            self.demoClassDict[self.demo].parseChirpComnCfg(args)
                elif args[0] == "chirpTimingCfg":
                    if len(args) < 6:
                        log.error("chirpTimingCfg had fewer arguments than expected")
                    else:
                        with suppress(AttributeError):
                            self.demoClassDict[self.demo].parseChirpTimingCfg(args)
                # TODO This is specifically guiMonitor for 60Lo, this parsing will break the gui when an SDK 3 config is sent
                elif args[0] == "guiMonitor":
                    if DEVICE_DEMO_DICT[self.device]["isxWRLx432"]:
                        if len(args) < 12:
                            log.error("guiMonitor had fewer arguments than expected")
                        else:
                            with suppress(AttributeError):
                                self.demoClassDict[self.demo].parseGuiMonitor(args)
                elif args[0] == "rangeSensingCfg":
                    if len(args) < 12:
                        log.error("rangeSensingCfg had fewer arguments than expected")
                    else:
                        with suppress(AttributeError):
                            self.demoClassDict[self.demo].parseRangeSensingCfg(args)
                elif args[0] == "presenceDetectCfg":
                    with suppress(AttributeError):
                        self.demoClassDict[self.demo].parsePresenceDetectCfg(args)
                elif args[0] == "sigProcChainCfg2":
                    with suppress(AttributeError):
                        self.demoClassDict[self.demo].parseSigProcChainCfg2(args)
                elif args[0] == "mpdBoundaryArc":
                    if len(args) < 8:
                        log.error("mpdBoundaryArc had fewer arguments than expected")
                    else:
                        with suppress(AttributeError):
                            self.demoClassDict[self.demo].parseBoundaryBox(args)
                elif args[0] == "measureRangeBiasAndRxChanPhase":
                    with suppress(AttributeError):
                        self.demoClassDict[self.demo].parseRangePhaseCfg(args)
                elif args[0] == "clutterRemoval":
                    with suppress(AttributeError):
                        self.demoClassDict[self.demo].parseClutterRemovalCfg(args)
                elif args[0] == "sigProcChainCfg":
                    with suppress(AttributeError):
                        self.demoClassDict[self.demo].parseSigProcChainCfg(args)
                elif args[0] == "channelCfg":
                    with suppress(AttributeError):
                        self.demoClassDict[self.demo].parseChannelCfg(args)
                elif args[0] == "SOSOClassifierCfg":
                    with suppress(AttributeError):
                        self.demoClassDict[self.demo].parseSOSOCfg(args)
                elif args[0] == "occupancyBox":
                    with suppress(AttributeError):
                        self.demoClassDict[self.demo].parseOccCfg(args)
                elif args[0] == "intruderDetAdvCfg":
                    with suppress(AttributeError):
                        self.demoClassDict[self.demo].parseDetAdvCfg(args)
                elif args[0] == "cuboidDef":
                    with suppress(AttributeError):
                        self.demoClassDict[self.demo].parseCuboidDef(args)
                elif args[0] == "famitigCfg":
                    with suppress(AttributeError):
                        self.demoClassDict[self.demo].parseFamitigCfg(args)
        # Initialize 1D plot values based on cfg file
        with suppress(AttributeError):
            self.demoClassDict[self.demo].setRangeValues()

    def selectCfg(self, filename):
        try:
            file = self.selectFile(filename)
            self.cachedData.setCachedCfgPath(file)  # cache the file and demo used
            self.parseCfg(file)
        except Exception as e:
            log.error(e)
            log.error(
                "Parsing .cfg file failed. Did you select a valid configuration file?"
            )

        log.debug("Demo Changed to " + self.demo)
        if self.demo == DEMO_CALIBRATION:
            self.demoClassDict[self.demo].checkCalibrationParams()

    def sendCfg(self):
        try:
            if self.demo != "Replay":
                self.parser.sendCfg(self.cfg)
                sys.stdout.flush()
                self.parseTimer.start(int(self.frameTime))  # need this line
        except Exception as e:
            log.error(e)
            log.error("Parsing .cfg file failed. Did you select the right file?")

    def updateGraph(self, outputDict):
        self.demoClassDict[self.demo].updateGraph(outputDict)

    def connectCom(self, cliCom, dataCom, connectStatus, BaudRatesManager):
        if self.demo == DEMO_GESTURE or self.demo == DEMO_MULTI_PASS_DETECTION:
            self.frameTime = 25 # Gesture demo runs at 35ms frame time
        # init threads and timers
        self.uart_thread = parseUartThread(self.parser, self.demo)

        self.uart_thread.fin.connect(self.updateGraph)
        self.parseTimer = QTimer()
        self.parseTimer.setSingleShot(False)
        self.parseTimer.timeout.connect(self.parseData)
        try:
            if os.name == "nt":
                uart = "COM" + cliCom.text()
                data = "COM" + dataCom.text()
            else:
                uart = cliCom.text()
                data = dataCom.text()
            if DEVICE_DEMO_DICT[self.device]["isxWRx843"] or DEVICE_DEMO_DICT[self.device]["isxWRLx844"]:  # If using x843 device
                self.parser.connectComPorts(uart, data, BaudRatesManager.getInputBaudRate(), BaudRatesManager.getOutputBaudRate())
            else:  # If not x843 device then defer to x432 device
                self.parser.connectComPort(uart, BaudRatesManager.getSingleBaudRate())
            connectStatus.setText("Connected")
        except Exception as e:
            log.error(e)
            connectStatus.setText("Unable to Connect")
            return -1

        return 0

    def startApp(self):
        if (self.replay and self.playing is False):
            self.replayTimer = QTimer()
            self.replayTimer.setSingleShot(True)
            self.replayTimer.timeout.connect(self.replayData)
            self.playing = True
            self.replayTimer.start(100) # arbitrary value to start plotting
        elif (self.replay and self.playing is True):
            self.playing = False
        else :
            self.parseTimer.start(int(self.frameTime))  # need this line, this is for normal plotting

    def loadForReplay(self, state):
        if (state):
            self.cachedData.setCachedRecord = "True"
            with open(self.replayFile[0], 'r') as fp:
                self.data = json.load(fp)
            self.parseCfg("")
            self.sl.setMinimum(0)
            self.sl.setMaximum(len(self.data['data']) - 1)
            self.sl.setValue(0)
            self.sl.setTickInterval(5)
            # TODO need to load correct demo from file
        else:
            self.cachedData.setCachedRecord = "False"


    def replayData(self):
        if (self.playing) :
            outputDict = self.data['data'][self.replayFrameNum]['frameData']
            self.updateGraph(outputDict)
            self.replayFrameNum += 1
            self.sl.setValue(self.replayFrameNum)
            if (self.replayFrameNum < len(self.data['data'])) :
                self.replayTimer.start(self.data['data'][self.replayFrameNum]['timestamp'] - self.data['data'][self.replayFrameNum-1]['timestamp'])

    def sliderValueChange(self):
        self.replayFrameNum = self.sl.value()

    def parseData(self):
        self.uart_thread.start(priority=QThread.HighestPriority)

    def boardReset(self):
        try:
            paths = json.load(open("..\common\External_Resources\path.json", 'r'))
            xds_reset_path = paths.get("xds_reset_path")
            reset_cmd = "xds110reset --action toggle"
            try:
                subprocess.run(reset_cmd, cwd= xds_reset_path, shell = True)
            except:
                log.error("Unable to reset the device. Check your xds reset path in <Industrial_Visualizer\misc\path.json>")
        except:
            log.warning("Unable to read path.json")

    def gracefulReset(self):
        self.parseTimer.stop()
        self.uart_thread.stop()
        if self.parser.cliCom is not None:
            self.parser.cliCom.close()
        if self.parser.dataCom is not None:
            self.parser.dataCom.close()
        for demo in self.demoClassDict.values():
            if hasattr(demo, "plot_3d_thread"):
                demo.plot_3d_thread.stop()
            if hasattr(demo, "plot_3d"):
                demo.removeAllBoundBoxes()
            if hasattr(demo, "power_report"):
                demo.resetPowerNumbers()
        self.boardReset()


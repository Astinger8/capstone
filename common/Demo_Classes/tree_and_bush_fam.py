import time

import numpy as np
import pyqtgraph.opengl as gl
from PySide2.QtCore import Qt, QThread
from PySide2.QtWidgets import QPushButton, QGroupBox, QGridLayout, QLabel, QWidget, QVBoxLayout, QCheckBox

from Demo_Classes.people_tracking import PeopleTracking
from graph_utilities import get_trackColors
from gl_text import GLTextItem
from gui_threads import updateQTTargetThread3D
from pyqtgraph.opengl import GLLinePlotItem

import logging

log = logging.getLogger(__name__)

MAX_CLUSTERS = 20
RADIUS = 1.2

FAM_STATE_UNKNOWN = 0
FAM_STATE_P_HUMAN = 1
FAM_STATE_C_HUMAN = 2


class Plot3DTabContainer(QWidget):
    """Tab-page wrapper around plot_3d that is still recognized as plot_3d itself.

    PeopleTracking.updateGraph() gates point cloud/track plotting on
    `self.tabs.currentWidget() == self.plot_3d`. Wrapping plot_3d in a plain QWidget so
    the FA Mitigation panes can sit below it in the same tab would make that check always
    fail, since the tab's current widget becomes the wrapper, not plot_3d. Overriding
    equality against the wrapped widget keeps that check (and any similar ones in the
    PeopleTracking hierarchy) working without having to modify that shared base class.
    """

    def __init__(self, plot3d):
        super().__init__()
        self._plot3d = plot3d

    def __eq__(self, other):
        return other is self or other is self._plot3d

    def __hash__(self):
        return id(self)


class TreeAndBushFAM(PeopleTracking):
    def __init__(self):
        PeopleTracking.__init__(self)

        self.maxClusters = MAX_CLUSTERS
        self.centroids = None
        self.centroidStates = None
        self.centroidScatter = None
        self.centroidColorMap = get_trackColors(self.maxClusters)
        self.centroidLabels = []
        self.numCordonedAreas = 0

        self.falseAlarmCount = 0
        self.plantTestEnabled = False
        self.flag_enter_reset = False
        self.glWidget = None
        self.previousCentroids = None
        self.famitigCfg = None

    def setupGUI(self, gridLayout, demoTabs, device, menuBar=None, app=None):
        PeopleTracking.setupGUI(self, gridLayout, demoTabs, device, menuBar, app)

        # Wrap self.plot_3d in a container so the FA Mitigation panels can sit below it inside
        # the same '3D Plot' tab, without altering PeopleTracking.setupGUI's default behavior.
        tabIndex = demoTabs.indexOf(self.plot_3d)
        tabLabel = demoTabs.tabText(tabIndex)
        demoTabs.removeTab(tabIndex)

        self.plot3DContainer = Plot3DTabContainer(self.plot_3d)
        containerLayout = QVBoxLayout()
        self.plot_3d.setMinimumSize(400, 400)
        # demoTabs.removeTab() above explicitly hides self.plot_3d; reparenting it into the
        # new container's layout does not clear that hidden flag, so it must be shown again.
        self.plot_3d.show()
        containerLayout.addWidget(self.plot_3d, stretch=1)

        self.initFAMPlotItems(self.plot_3d)
        self.buildFAMPanes()
        containerLayout.addWidget(self.faMitigationDataPane)
        containerLayout.addWidget(self.faMitigationSettingsPane)

        self.plot3DContainer.setLayout(containerLayout)
        demoTabs.insertTab(tabIndex, self.plot3DContainer, tabLabel)

        # insertTab can leave the tab that was current before the swap (Range Plot) selected;
        # force '3D Plot' to be the default tab for this demo.
        demoTabs.setCurrentWidget(self.plot3DContainer)

    def buildFAMPanes(self):
        self.faMitigationDataPane = QGroupBox("False Alarm Mitigation Info")
        self.faMitigationDataPaneGrid = QGridLayout()
        self.numCentroidsDisplay = QLabel('Centroids: 0')
        self.numCordonedAreasDisplay = QLabel('Cordoned Areas: 0')

        self.famPlantTestEnable = QCheckBox("Plant test")
        self.famPlantTestEnable.stateChanged.connect(self.famPlantTestEnableStateChanged)
        self.famFalseAlarmsLabel = QLabel('False Alarms: ')
        self.famFalseAlarmsLabel.setEnabled(False)
        self.famResetAlarmsButton = QPushButton("Reset")
        self.famResetAlarmsButton.setEnabled(False)
        self.famResetAlarmsButton.clicked.connect(self.onResetAlarmsPushed)
        self.famFinalOutput = QLabel("")
        self.famFinalOutput.setAlignment(Qt.AlignCenter)
        self.famFinalOutput.setStyleSheet('background-color: rgb(70, 72, 79); color: white; font-size: 18px;')
        self.faMitigationDataPaneGrid.addWidget(self.famFinalOutput, 1, 1, 1, 2)
        self.faMitigationDataPaneGrid.addWidget(self.numCentroidsDisplay, 2, 1)
        self.faMitigationDataPaneGrid.addWidget(self.numCordonedAreasDisplay, 2, 2)
        self.faMitigationDataPaneGrid.addWidget(self.famPlantTestEnable, 3, 1)
        self.faMitigationDataPaneGrid.addWidget(self.famFalseAlarmsLabel, 4, 1)
        self.faMitigationDataPaneGrid.addWidget(self.famResetAlarmsButton, 4, 2)

        self.faMitigationDataPane.setLayout(self.faMitigationDataPaneGrid)

        self.faMitigationSettingsPane = QGroupBox("Settings")
        self.faMitigationSettingsPaneGrid = QGridLayout()
        self.showCentroidLabelsCheckbox = QCheckBox("Show Centroid Labels")
        self.showCentroidsCheckbox = QCheckBox("Show Centroids")
        self.showCentroidsCheckbox.setChecked(True)
        self.showCordonedAreasCheckbox = QCheckBox("Show Cordoned Areas")
        self.showCordonedAreasCheckbox.setChecked(True)

        self.faMitigationSettingsPaneGrid.addWidget(self.showCentroidsCheckbox, 1, 1)
        self.faMitigationSettingsPaneGrid.addWidget(self.showCentroidLabelsCheckbox, 1, 2)
        self.faMitigationSettingsPaneGrid.addWidget(self.showCordonedAreasCheckbox, 2, 1)

        self.faMitigationSettingsPane.setLayout(self.faMitigationSettingsPaneGrid)

    def initFAMPlotItems(self, plot3d):
        self.famEllipsoids = []
        self.cordonedMeshes = []
        self.centroidLabels = []

        for i in range(self.maxClusters):
            ellipsoid = GLLinePlotItem()
            ellipsoid.setVisible(False)
            plot3d.addItem(ellipsoid)
            self.famEllipsoids.append(ellipsoid)

            mesh = gl.GLMeshItem()
            mesh.setVisible(False)
            plot3d.addItem(mesh)
            self.cordonedMeshes.append(mesh)

            textItem = GLTextItem()
            textItem.setGLViewWidget(plot3d)
            plot3d.addItem(textItem)
            textItem.setVisible(False)
            self.centroidLabels.append(textItem)

    def updateGraph(self, outputDict):
        PeopleTracking.updateGraph(self, outputDict)

        self.plotStart = int(round(time.time() * 1000))

        if ('famCentroids' in outputDict or 'famStates' in outputDict
                or 'famOutput' in outputDict or 'famNumFalseAlarms' in outputDict):
            famDataDict = self.updateFamData(outputDict, self.snapTo2D)

            self.plot_3d_fam_thread = updateQTTargetThread3D(None, None, self.scatter, self.plot_3d, 0,
                                        None, "", trackColorMap=self.centroidColorMap, famDataToPlot=famDataDict)

            self.plot_3d_fam_thread.start(priority=QThread.HighestPriority)

    def updateFamData(self, outputDict, snapTo2d):
        self.famOutput = None
        self.famNumFalseAlarms = None
        famDataToPlot = {}

        for label in self.centroidLabels:
            label.setVisible(False)

        if ('famCentroids' in outputDict):
            self.centroids = np.array(outputDict['famCentroids'])

        if ('famStates' in outputDict):
            self.centroidStates = np.array(outputDict['famStates'])

        if ('famOutput' in outputDict):
            self.famOutput = outputDict['famOutput']

        if ('famNumFalseAlarms' in outputDict):
            self.famNumFalseAlarms = outputDict['famNumFalseAlarms']

        if self.centroids is not None and self.centroidStates is not None:
            self.updateCentroids(snapTo2d)

        if self.famOutput is not None:
            self.updateFamFinalOutput(self.famOutput)

        if self.famNumFalseAlarms is not None:
            self.updateFalseAlarmCount(self.famNumFalseAlarms)

        if (self.showCentroidLabelsCheckbox.isChecked() and self.centroids is not None):
            self.updateCentroidLabels()

        famDataToPlot = {"centroids":self.centroids, "states":self.centroidStates,
                                      "ellipsoids":self.famEllipsoids, "radius":RADIUS,
                                      "cordonedMeshes":self.cordonedMeshes, "showCentroids":self.showCentroidsCheckbox.isChecked(),
                                      "showCentroidLabels":self.showCentroidLabelsCheckbox.isChecked(), "showCordonedAreas":self.showCordonedAreasCheckbox.isChecked()}

        if (self.centroids is not None):
            self.numCentroidsDisplay.setText('Centroids: '+ str(len(self.centroids)-self.numCordonedAreas))

        if (self.numCordonedAreas is not None):
            self.numCordonedAreasDisplay.setText('Cordoned Areas: '+str(self.numCordonedAreas))

        return famDataToPlot

    def famPlantTestEnableStateChanged(self, state):
        self.plantTestEnabled = bool(state)
        if state:
            self.famFalseAlarmsLabel.setEnabled(True)
            self.famResetAlarmsButton.setEnabled(True)
            self.resetAlarmCount()
            self.flag_enter_reset = False
        else:
            self.famFalseAlarmsLabel.setEnabled(False)
            self.famResetAlarmsButton.setEnabled(False)
            self.famFalseAlarmsLabel.setText('False Alarms: ')
            self.flag_enter_reset = False

    def onResetAlarmsPushed(self):
        self.resetAlarmCount()

    def updateCentroids(self, snapTo2D):
        if self.centroids is None or len(self.centroids) == 0:
            return

        # Filter out cordoned zones and invalid centroids
        centroidMask = (self.centroids[:, 1] != 0)
        self.centroids = self.centroids[centroidMask]
        self.centroidStates = self.centroidStates[centroidMask]

        self.numCordonedAreas = np.count_nonzero(self.centroidStates == 112)

        if snapTo2D:
            self.centroids[:,2] = 0

    def updateCentroidLabels(self):
        for i in range(len(self.centroids)):
            if (self.centroidStates[i] == FAM_STATE_UNKNOWN):
                text = 'unknown'
                color = Qt.black
            elif (self.centroidStates[i] == FAM_STATE_P_HUMAN):
                color = Qt.cyan
                text = 'p human'
            elif (self.centroidStates[i] == FAM_STATE_C_HUMAN):
                color = Qt.green
                text = 'c human'
            else:
                color = Qt.black
                text = ''

            self.centroidLabels[i].setText(text)
            self.centroidLabels[i].setColor(color)
            self.centroidLabels[i].setX(float(self.centroids[i,0]+0.5))
            self.centroidLabels[i].setY(float(self.centroids[i,1]))
            self.centroidLabels[i].setZ(float(self.centroids[i,2]-0.5))
            self.centroidLabels[i].setVisible(True)

    def updateFamFinalOutput(self, famOutput):
        if famOutput == 2:
            self.famFinalOutput.setText('Human')
            self.famFinalOutput.setStyleSheet('background-color: rgb(0, 255, 0); color: white; font-size: 18px;')  # Green
        elif famOutput == 1:
            self.famFinalOutput.setText('P Human')
            self.famFinalOutput.setStyleSheet('background-color: rgb(0, 255, 255); color: white; font-size: 18px;')  # Cyan
        else:
            self.famFinalOutput.setText('Non-Human')
            self.famFinalOutput.setStyleSheet('background-color: rgb(255, 0, 0); color: white; font-size: 18px;')  # Red

    def updateFalseAlarmCount(self, falseAlarmCnt):
        # Initialize baseline on first plant test activation
        if self.plantTestEnabled and not self.flag_enter_reset:
            self.falseAlarmCount = falseAlarmCnt
            self.flag_enter_reset = True

        # Display relative count if in plant test mode
        if falseAlarmCnt > 0 and self.flag_enter_reset:
            relativeCount = falseAlarmCnt - self.falseAlarmCount
            self.famFalseAlarmsLabel.setText(f'False Alarms: {relativeCount}')
        elif not self.plantTestEnabled:
            # Show absolute count when not in plant test mode
            self.famFalseAlarmsLabel.setText(f'False Alarms: {falseAlarmCnt}')

    def resetAlarmCount(self):
        self.famFalseAlarmsLabel.setText('False Alarms: 0')

    def parseFamitigCfg(self, args):
        self.famitigCfg = args
        self.famitigEn = True if int(args[1]) == 1 else False
        self.famitigMode = int(args[2])
        self.t1Threshold = float(args[3])
        self.t2Threshold = float(args[4])
        self.tcordonThreshold = float(args[5])
        self.cordonAreaDist = float(args[6])
        self.numFalseAlarmsBeforeCordon = float(args[7])

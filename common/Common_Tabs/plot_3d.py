import numpy as np
import pyqtgraph as pg
import pyqtgraph.opengl as gl
from PySide2.QtCore import QEvent, QTimer
from PySide2.QtGui import QPixmap
from PySide2.QtGui import QImage

from graph_utilities import eulerRot, getBoxArcs, getBoxArcs2D ,getBoxLines, getSquareLines

# Different methods to color the points 
COLOR_MODE_SNR = 'SNR'
COLOR_MODE_HEIGHT = 'Height'
COLOR_MODE_DOPPLER = 'Doppler'
COLOR_MODE_TRACK = 'Associated Track'

class _DPIAwareGLViewWidget(gl.GLViewWidget):
    """GLViewWidget with two protections:
    1. Suppresses paintGL during rapid resize (splitter drag) to prevent QGLWidget
       barcode artifacts and context corruption; repaints once 100ms after drag stops.
    2. Forces repaint when moved to a different DPI screen."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._resizeTimer = QTimer(self)
        self._resizeTimer.setSingleShot(True)
        self._resizeTimer.setInterval(100)
        self._resizeTimer.timeout.connect(self._recoverAfterResize)

    def resizeEvent(self, event):
        self._resizeTimer.start()
        super().resizeEvent(event)

    def paintGL(self):
        if self._resizeTimer.isActive():
            return
        super().paintGL()

    def _recoverAfterResize(self):
        try:
            self.makeCurrent()
            self.doneCurrent()
        except Exception:
            pass
        self.update()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.ScreenChangeInternal:
            QTimer.singleShot(50, self.update)


class Plot3D():
    def __init__(self, demo=None):
        # Create plot
        self.plot_3d = _DPIAwareGLViewWidget()
        # Sets background to a pastel grey
        self.plot_3d.setBackgroundColor(70, 72, 79)
        # Create the background grid
        gz = gl.GLGridItem()
        self.plot_3d.addItem(gz)

        # Create scatter plot for point cloud
        self.scatter = gl.GLScatterPlotItem(size=5)
        self.scatter.setData(pos=np.zeros((1,3)))
        self.plot_3d.addItem(self.scatter)
        self.boundaryBoxList = []

        # Create scatter plot for clusters
        self.scatterClusters = gl.GLScatterPlotItem(size=10)
        self.scatterClusters.setData(pos=np.zeros((1,3)))
        self.plot_3d.addItem(self.scatterClusters)

        # demo specific
        self.demo = demo
        # Sensor position
        self.xOffset = 0
        self.yOffset = 0
        self.sensorHeight = 0
        self.az_tilt = 0
        self.elev_tilt = 0
    
        # Create box to represent EVM
        evmSizeX = 0.0625
        evmSizeZ = 0.125
        verts = np.empty((2,3,3))
        verts[0,0,:] = [-evmSizeX, 0, evmSizeZ]
        verts[0,1,:] = [-evmSizeX,0,-evmSizeZ]
        verts[0,2,:] = [evmSizeX,0,-evmSizeZ]
        verts[1,0,:] = [-evmSizeX, 0, evmSizeZ]
        verts[1,1,:] = [evmSizeX, 0, evmSizeZ]
        verts[1,2,:] = [evmSizeX, 0, -evmSizeZ]
        self.evmBox = gl.GLMeshItem(vertexes=verts,smooth=False,drawEdges=True,edgeColor=pg.glColor('r'),drawFaces=False)
        self.plot_3d.addItem(self.evmBox)

        # Initialize other elements
        self.boundaryBoxViz = []
        self.coordStr = []
        self.classifierStr = []
        self.ellipsoids = []
        self.plotComplete = 1

        self.zRange = [-3, 3]

        # Persistent point cloud
        self.previousClouds = []
        if self.demo == None:
            self.numPersistentFrames = int(3)
        elif self.demo == "LPD":
            self.numPersistentFrames = int(20)
            self.plot_3d.pan(0, 1, 0)
        self.previousCloudsLocalCoords = []

        self.mpdZoneType = None
        self.snapTo2D = None
        self.modeSwitchLabel = None

    def updatePointCloud(self, outputDict):
        pointCloudLocalCoords = [] # Needed in case no points detected
        if ('pointCloud' in outputDict and 'numDetectedPoints' in outputDict):
            pointCloud = outputDict['pointCloud']
            pointCloudLocalCoords = np.array(outputDict['pointCloud']) # hold this for camera transform
            pointCloud = np.asarray(pointCloud)

            # Rotate point cloud and tracks to account for elevation and azimuth tilt
            if (self.elev_tilt != 0 or self.az_tilt != 0):
                for i in range(outputDict['numDetectedPoints']):
                    rotX, rotY, rotZ = eulerRot (pointCloud[i,0], pointCloud[i,1], pointCloud[i,2], self.elev_tilt, self.az_tilt)
                    pointCloud[i,0] = rotX
                    pointCloud[i,1] = rotY
                    pointCloud[i,2] = rotZ

            # Shift points to account for sensor height
            if (self.sensorHeight != 0):
                pointCloud[:,2] = pointCloud[:,2] + self.sensorHeight

            if self.demo == "LPD":
                outputDict['pointCloud'] = self.filterPointCloud(outputDict['pointCloud'])
            # Add current point cloud to the cumulative cloud if it's not empty
            self.previousClouds.append(outputDict['pointCloud'])
            self.previousCloudsLocalCoords.append(pointCloudLocalCoords)
        else:
            # if there is no point cloud, append an empty array
            self.previousClouds.append([])
            self.previousCloudsLocalCoords.append([])

        # If we have more point clouds than needed, stated by numPersistentFrames, delete the oldest ones 
        while(len(self.previousClouds) > self.numPersistentFrames):
            self.previousClouds.pop(0)
            self.previousCloudsLocalCoords.pop(0)

        return pointCloudLocalCoords
            
    # Add a boundary box to the boundary boxes tab
    def addBoundBox(self, name, minX=0, maxX=0, minY=0, maxY=0, minZ=0, maxZ=0, color='b'):
        newBox = gl.GLLinePlotItem()
        newBox.setVisible(True)
        self.plot_3d.addItem(newBox)     

        if ('mpdBoundaryArc' in name):
            try:
                if(self.snapTo2D.checkState() == 0):
                    boxLines = getBoxArcs(minX,minY,minZ,maxX,maxY,maxZ)
                elif(self.snapTo2D.checkState() == 2):
                    boxLines = getBoxArcs2D(minX,minY,0,maxX,maxY,0)
                
                
                boxColor = pg.glColor('b')
                newBox.setData(pos=boxLines,color=boxColor,width=2,antialias=True,mode='lines')

                # TODO add point boundary back into visualizer

                boundaryBoxItem = {
                    'plot': newBox,
                    'name': name,
                    'boxLines': boxLines,
                    'minX': float(minX),
                    'maxX': float(maxX),
                    'minY': float(minY),
                    'maxY': float(maxY),
                    'minZ': float(minZ),
                    'maxZ': float(maxZ)
                }   

                self.boundaryBoxViz.append(boundaryBoxItem) 
                self.plot_3d.addItem(newBox)
                self.boundaryBoxList.append(newBox)
            except:
                # You get here if you enter an invalid number
                # When you enter a minus sign for a negative value, you will end up here before you type the full number
                pass
        else:
            try:
                if(self.snapTo2D.checkState() == 0):
                    boxLines = getBoxLines(minX,minY,minZ,maxX,maxY,maxZ)
                elif(self.snapTo2D.checkState() == 2):
                    boxLines = getSquareLines(minX,minY,0,maxX,maxY,0)
 
                boxColor = pg.glColor(color)
                newBox.setData(pos=boxLines,color=boxColor,width=2,antialias=True,mode='lines')

                # TODO add point boundary back into visualizer

                boundaryBoxItem = {
                    'plot': newBox,
                    'name': name,
                    'boxLines': boxLines,
                    'minX': float(minX),
                    'maxX': float(maxX),
                    'minY': float(minY),
                    'maxY': float(maxY),
                    'minZ': float(minZ),
                    'maxZ': float(maxZ)
                }   

                self.boundaryBoxViz.append(boundaryBoxItem) 
                self.plot_3d.addItem(newBox)
                self.boundaryBoxList.append(newBox)
            except:
                # You get here if you enter an invalid number
                # When you enter a minus sign for a negative value, you will end up here before you type the full number
                pass

    def removeAllBoundBoxes(self):
        for item in self.boundaryBoxList:
            item.setVisible(False)
        if(self.snapTo2D is not None):
            self.snapTo2D.setEnabled(1)
        if(self.modeSwitchLabel is not None):
            self.modeSwitchLabel.setText('Two Pass Mode Disabled')
            self.modeSwitchLabel.setStyleSheet("background-color: lightgrey; border: 1px solid black;")
        self.boundaryBoxList.clear()

    def changeBoundaryBoxColor(self, box, color):
        box['plot'].setData(pos=box['boxLines'], color=pg.glColor(color),width=2,antialias=True,mode='lines')

    def changeBoundaryBoxBold(self, box, bold, strip):
        if bold:
            box['plot'].setData(width=8)
        else:
            box['plot'].setData(width=2)
        
        if strip:
            box['plot'].setData(mode='line_strip')
        else:
            box['plot'].setData(mode='lines')


    def parseTrackingCfg(self, args):
        self.maxTracks = int(args[4])

    def parseBoundaryBox(self, args):
        self.snapTo2D.setEnabled(0)

        if (args[0] == 'SceneryParam' or args[0] == 'boundaryBox'):
            leftX = float(args[1])
            rightX = float(args[2])
            nearY = float(args[3])
            farY = float(args[4])
            bottomZ = float(args[5])
            topZ = float(args[6])
                        
            self.addBoundBox('trackerBounds', leftX, rightX, nearY, farY, bottomZ, topZ)
        elif (args[0] == 'zoneDef'):
            zoneIdx = int(args[1])
            minX = float(args[2])
            maxX = float(args[3])
            minY = float(args[4])
            maxY = float(args[5])
            # Offset by 3 so it is in center of screen
            minZ = float(args[6]) + self.sensorHeight
            maxZ = float(args[7]) + self.sensorHeight

            name = 'occZone' + str(zoneIdx)
            self.addBoundBox(name, minX, maxX, minY, maxY, minZ, maxZ)
        elif (args[0] == 'mpdBoundaryBox'):
            zoneIdx = int(args[1])
            minX = float(args[2])
            maxX = float(args[3])
            minY = float(args[4])
            maxY = float(args[5])
            minZ = float(args[6])
            maxZ = float(args[7])
            name = 'mpdBoundaryBox' + str(zoneIdx)
            self.addBoundBox(name, minX, maxX, minY, maxY, minZ, maxZ)
        elif (args[0] == 'mpdBoundaryArc'):
            zoneIdx = int(args[1])
            minR = float(args[2])
            maxR = float(args[3])
            minTheta = float(args[4])
            maxTheta = float(args[5])
            minZ = float(args[6])
            maxZ = float(args[7])
            name = 'mpdBoundaryArc' + str(zoneIdx)
            self.addBoundBox(name, minR, maxR, minTheta, maxTheta, minZ, maxZ)

        # TODO print out somewhere these boundary boxes

    def parseSensorPosition(self, args, is_x843):
        #print("parsing sensor position")
        if (is_x843):
            self.sensorHeight = float(args[1])
            self.az_tilt = float(args[2])
            self.elev_tilt = float(args[3])
        else:
            self.xOffset = float(args[1])
            self.yOffset = float(args[2])
            self.sensorHeight = float(args[3])
            self.az_tilt = float(args[4])
            self.elev_tilt = float(args[5])

        self.evmBox.resetTransform()
        if self.demo == "LPD": # TODO REMOVE THIS AFTER FIXING DEMO IMAGE TO PARSE ELEV CORRECTLY
            self.elev_tilt = -1 * self.elev_tilt
            self.az_tilt = -1 * self.az_tilt
        self.evmBox.rotate(-1 * self.elev_tilt, 1, 0, 0)
        self.evmBox.rotate(-1 * self.az_tilt, 0, 0, 1)
        self.evmBox.translate(0, 0, self.sensorHeight)

        # TODO update text showing sensor position text?
    def switchView(self, idx, maxRange, center_z=0):
        d = maxRange * 1.5
        views = {
            0: dict(azimuth=270, elevation=90,  distance=maxRange * 1.5),  # X-Y top-down
            1: dict(azimuth=0,   elevation=0,   distance=d),               # Y-Z side
            2: dict(azimuth=-90, elevation=0,   distance=d),               # X-Z front
            3: dict(azimuth=-60, elevation=20,  distance=maxRange * 1.1),  # 3D isometric
        }
        self.plot_3d.opts['center'] = pg.Vector(0, maxRange / 2, center_z)
        self.plot_3d.setCameraPosition(**views[idx])

    def filterPointCloud(self, pointCloud):
        # filter the point cloud to only include points within the bounds of the boundary boxes
        newPointCloud = []
        #print("Empty new point cloud? : " + str(newPointCloud))
        for point in pointCloud:
            for boxNum in range(len(self.boundaryBoxViz)):
                # check if the point is within the bounds of the box
                if (self.boundaryBoxViz[boxNum]['minX'] <= point[0] and point[0] <= self.boundaryBoxViz[boxNum]['maxX'] and
                    self.boundaryBoxViz[boxNum]['minY'] <= point[1] and point[1] <= self.boundaryBoxViz[boxNum]['maxY'] and
                    self.boundaryBoxViz[boxNum]['minZ'] <= point[2] and point[2] <= self.boundaryBoxViz[boxNum]['maxZ']):
                    newPointCloud.append(point)
                    # TODO STORE WHICH BOX THIS PIN IS INSIDE OF AND USE IT TO COLOR POINT TO THE BOX AND PROB LINES
                    break # if within a box then move on to next point
        return np.asarray(newPointCloud)

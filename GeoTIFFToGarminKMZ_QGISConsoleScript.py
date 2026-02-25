import os, math, re, sys, subprocess, logging, shutil, time, numpy
from optparse import OptionParser
from osgeo import gdal, osr, ogr
import os.path
from pathlib import Path
startTime = time.time()


"""
##########################################################################
User variable assignment
"""

inImage         = 'C:\\Temp\\YourImage.tif'            #The input image, e.g 'C:\\Temp\\Test.tif', this must be an 8 bit 4 band tif
order           = 50                                   #KML draw order (of the highest resolution layer), likely not relevant for much...
border          = 0                                    #Size of crop border in pixels, you can refer to the outputs for looking for a white border. This is not relevant if the image is already in WGS 84.
tile_size       = 1000                                 #Tile size of the jpg tiles. Max is 1024?
quality         = 92                                   #JPEG quality (10-100)
verbose         = False                                #Verbose logging
compressOptions = 'COMPRESS=ZSTD|PREDICTOR=1|NUM_THREADS=ALL_CPUS|BIGTIFF=IF_NEEDED|TILED=YES|ZSTD_LEVEL=1'

useTileSelector = False                                 #To limit excess tiles you can use a polygon to select only the relevant areas to produce tiles for
tileSelector    = 'C:/Temp/YourRelevantArea.gpkg'      #A polygon to select the area where tiles will be created, e.g 'C:/Temp/RelevantArea.gpkg'


"""
##########################################################################
Setting up some variables
"""

#This means that the user needs only give the image path
extension = inImage.split(".")
extension = '.' + extension[-1]
originalName = inImage.split("\\")
originalName = originalName[-1]
originalName = originalName[:-1 * len(extension)]
directory = str(Path(inImage).parent.absolute()) + '\\'
processDirectory = directory + originalName + 'Processing\\'

#Let's make sure that the file exists
if not os.path.exists(inImage):
    print("Bro this doesn't exist")
    fixItUpBro
if not os.path.exists(processDirectory): os.mkdir(processDirectory)

#Set up names for variables
destinationKmlPath  = processDirectory + originalName + '.kml'
wgsName             = originalName + '_ReProjected'
firstLayerName      = originalName + '_Layer'

#Get the pixel size of the input raster for bumping up the res
img = gdal.Open(inImage)
gt = img.GetGeoTransform()
pixelSizeX = gt[1]
pixelSizeY = -gt[5]
prj=img.GetProjection()
srs=osr.SpatialReference(wkt=prj)

#If the image is smaller than the tiles then it doesn't make sense to tile
if img.RasterXSize - border < tile_size or img.RasterYSize - border < tile_size:
    print("The image is too small/the tile size is too big/the border size is too wide")
    fixItUpBro


if useTileSelector:
    try:
        #Bring in the tile selector as geometry
        processing.run("native:reprojectlayer", {'INPUT':tileSelector,'TARGET_CRS':QgsCoordinateReferenceSystem('EPSG:4326'),
        'OPERATION':'+proj=pipeline +step +inv +proj=utm +zone=55 +south +ellps=GRS80 +step +proj=unitconvert +xy_in=rad +xy_out=deg',
        'OUTPUT':processDirectory + 'SelectorProjected.gpkg'})
    except:
        print("The tile selector probably already exists?")
    
    #Convert the polygon to a feature variable for later comparison
    selectorVector = QgsVectorLayer(processDirectory + 'SelectorProjected.gpkg')
    listOfFids = selectorVector.aggregate(QgsAggregateCalculator.ArrayAggregate, 'fid')[0]
    print('Selector polygon is ' + str(int((os.path.getsize(processDirectory + 'SelectorProjected.gpkg')/1000))) + 'KB')


"""
##########################################################################
Prepping of image for the tiling part
"""

#The kmz requires the tiles to be in WGS 84 (EPSG 4326)
if srs.GetAttrValue('AUTHORITY',1) != '4326':
    
    print("The image needs to be reprojected into WGS 84")
    #We need to reproject into WGS84
    processing.run("gdal:warpreproject", {'INPUT':inImage,
    'SOURCE_CRS':None,'TARGET_CRS':QgsCoordinateReferenceSystem('EPSG:4326'),'RESAMPLING':2,'NODATA':None,
    'TARGET_RESOLUTION':None,'OPTIONS':compressOptions,'DATA_TYPE':0,'TARGET_EXTENT':None,'TARGET_EXTENT_CRS':None,'MULTITHREADING':True,
    'EXTRA':'-srcnodata 255 -dstnodata 255 -nosrcalpha',
    'OUTPUT':processDirectory + wgsName + '.tif'})

    imageForAlphaRemoval = processDirectory + wgsName + '.tif'

    #If the border value is set then we need to crop the transformed image
    if border > 0 :
        #First get the crop boundary
        img = gdal.Open(processDirectory + wgsName + '.tif')
        gt = img.GetGeoTransform()
        pixelSizeX = gt[1]
        pixelSizeY = -gt[5]
        minx = gt[0]
        maxy = gt[3]
        maxx = (minx + gt[1] * img.RasterXSize) - (pixelSizeX * border)
        miny = (maxy + gt[5] * img.RasterYSize) + (pixelSizeX * border)
        minx = minx + (pixelSizeX * border)
        maxy = maxy - (pixelSizeY * border)
        cropParameter = str(minx) + ' ' + str(miny) + ' ' + str(maxx) + ' ' + str(maxy)
        print ('Crop dimensions are ' + cropParameter)  
        
        #Then use the crop boundary to clip the raster
        processing.run("gdal:warpreproject", {'INPUT':processDirectory + wgsName + '.tif',
        'SOURCE_CRS':None,'TARGET_CRS':None,'RESAMPLING':0,'NODATA':255,
        'TARGET_RESOLUTION':None,'OPTIONS':compressOptions,'DATA_TYPE':0,'TARGET_EXTENT':None,'TARGET_EXTENT_CRS':None,'MULTITHREADING':True,
        'EXTRA':'-srcnodata 255 -dstnodata 255 -nosrcalpha -r nearest -overwrite -te ' + cropParameter,
        'OUTPUT':processDirectory + wgsName + '_Cropped.tif'})
        
        imageForAlphaRemoval = processDirectory + wgsName + '_Cropped.tif'

else:
    imageForAlphaRemoval = inImage
    print("The image appears to already be in WGS 84")

#Remove the alpha band
processing.run("gdal:translate", {'INPUT':imageForAlphaRemoval,'TARGET_CRS':QgsCoordinateReferenceSystem('EPSG:4326'),'NODATA':None,
'COPY_SUBDATASETS':False,'OPTIONS':compressOptions,
'EXTRA':'-a_nodata none -b 1 -b 2 -b 3','DATA_TYPE':0,'OUTPUT':processDirectory + firstLayerName + '1' + '.tif'})

firstLayerPath = processDirectory + firstLayerName + '1' + '.tif'


"""
##########################################################################
This function creates the individual tiles, and is called from further below
"""

#Create a jpeg of the given area and return the bounds.
def create_tile(source, filename, offset, size, quality=75):
  
    #Create an instance of a raster
    mem_drv = gdal.GetDriverByName('MEM')
    mem_ds = mem_drv.Create('', size[0], size[1], source.RasterCount)
    bands = list(range(1, source.RasterCount+1))
    
    #Read in the raster with the crop numbers as below
    data = source.ReadRaster(offset[0], offset[1], size[0], size[1], size[0], size[1], band_list=bands)
    
    #Write the raster info to the variable
    mem_ds.WriteRaster(0, 0, size[0], size[1], data, band_list=bands)

    #Use the variable to render to jpg
    jpeg_drv = gdal.GetDriverByName('JPEG')
    jpeg_ds = jpeg_drv.CreateCopy(filename, mem_ds, strict=0, options=["QUALITY={0}".format(quality)])

    #Project and transform
    t = source.GetGeoTransform()
    if t[2]!=0 or t[4]!=0: raise Exception("Source projection not compatible")
    def transform(xxx_todo_changeme):
        (x, y) = xxx_todo_changeme
        return ( t[0] + x*t[1] + y*t[2], t[3] + x*t[4] + y*t[5] )
    nw = transform(offset)
    se = transform([ offset[0] + size[0], offset[1] + size[1] ])
    
    #Corners of the tile
    result = {
        'north': nw[1],
        'east': se[0],
        'south': se[1],
        'west': nw[0],
    }
    
    #Wipe the variables and return
    jpeg_ds = None
    mem_ds = None
    return result
    
"""
##########################################################################
The while loop creates the kml and runs through the tiles with for-loops
"""

#Start writing to a .kml file
bob = open(destinationKmlPath, 'w')
bob.write("""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2" xmlns:kml="http://www.opengis.net/kml/2.2" xmlns:atom="http://www.w3.org/2005/Atom">
  <Folder>
    <name>%s</name>
""" % originalName)

#Get the size of the raster so we know when to stop tiling
img = gdal.Open(firstLayerPath)
img_size = [img.RasterXSize, img.RasterYSize]


#Variables used in the while loop
currentLayerNumber = 0
currentTileNumber = 0
numberOfRejectedTiles = 0
timeToLeave = False


#Start up a while loop where it continues to tile to a lower resolution until the tile size exceeds the current image size
while not timeToLeave: 
    
    #This is used for naming the files based on what layer we're up to
    currentLayerNumber = currentLayerNumber + 1
    nextLayerNumber = currentLayerNumber + 1
    currentLayerName = firstLayerName + str(currentLayerNumber)
    currentLayerPath = processDirectory + currentLayerName + '.tif'
    nextLayerName = firstLayerName + str(nextLayerNumber)
    nextLayerPath = processDirectory + nextLayerName + '.tif'
    print("Processing layer " + str(currentLayerNumber) + ", dimensions of the layer are " + str(img.RasterXSize) + "," + str(img.RasterYSize))
    
    #Bring in the image and get its size
    img = gdal.Open(currentLayerPath)
    img_size = [img.RasterXSize, img.RasterYSize]
    if verbose: logging.debug('Image size: %s' % img_size)
    
    #Get the current pixel size
    gt = img.GetGeoTransform()
    pixelSizeX = gt[1]
    pixelSizeY = -gt[5]
    
    #Once the tile size is too big compared with the original image then the pyramid has reached its limit
    if img.RasterXSize < tile_size * 2 or img.RasterYSize < tile_size * 2: 
        currentMinLod = -1
        timeToLeave = True
    else:
        currentMinLod = 50/((pixelSizeX+pixelSizeY)/2)
    
    #Ensure that the lod numbers allowing viewing close up
    if currentLayerNumber == 1:
        currentMaxLod = -1
    else:
        currentMaxLod = 100/((pixelSizeX+pixelSizeY)/2)


    #You could adjust this if you wanted to do something fancy
    base = currentLayerName
    path = '.' #os.path.relpath(processDirectory, os.path.dirname(destinationKmlPath))

    #Use the dimensions of the image and the tile size to determine the integer number of tiles required
    tileAmountX = math.ceil((img_size[0]/tile_size)-0.00001)
    tileAmountY = math.ceil((img_size[1]/tile_size)-0.00001)


    """
    ##########################################################################
    The for-loops that go through
    """
    
    #Two for-loops that run through the grid of tiles
    for t_y in range(tileAmountY):
        for t_x in range(tileAmountX):
            
            tile = "%d,%d" % (t_y, t_x)
            if verbose: logging.debug(tile)
            
            #For the given tile determine the corner to start
            src_corner = (t_x * tile_size, t_y * tile_size)                
            src_size = [tile_size, tile_size]
            
            
            #If it is the case that the tile will extend beyond the full image, then scale the tile down until it does fit
            while src_corner[0] + src_size[0] > img_size[0]: 
                src_size[0] = src_size[0] -1
            while src_corner[1] + src_size[1] > img_size[1]: 
                src_size[1] = src_size[1] -1
            
            #Call the create_tile function
            outfile = "%s_%d_%d.jpg" % (base, t_x, t_y)
            bounds = create_tile(img, "%s/%s" % (processDirectory, outfile), src_corner, src_size, quality)
            
            
            #Check to see if the tile is within the relevant area
            if useTileSelector:
                selectTheTile = False
            
                boundsGeom = QgsGeometry.fromWkt('POLYGON((' + str(bounds['west']) + ' ' + str(bounds['north']) + ', ' + str(bounds['west']) + ' ' + str(bounds['south']) + ', ' + str(bounds['east']) + ' ' + str(bounds['south']) + ', ' + str(bounds['east']) + ' ' + str(bounds['north']) + ', ' + str(bounds['west']) + ' ' + str(bounds['north']) + '))')
                for fid in listOfFids:
                    if (boundsGeom.overlaps(selectorVector.getGeometry(fid))):
                        selectTheTile = True
                    elif (boundsGeom.within(selectorVector.getGeometry(fid))):
                        selectTheTile = True
                    elif (selectorVector.getGeometry(fid).within(boundsGeom)):
                        selectTheTile = True
            else:
                selectTheTile = True

            
            if selectTheTile:
                
                #A fenix 7 won't allow more than 500 tiles
                currentTileNumber = currentTileNumber + 1
                if currentTileNumber == 501:
                    print("Your garmin device may not support greater than 500 tiles...")
            
                #Write to the .kml
                bob.write("""    <GroundOverlay>
                <name>%s</name>
                <color>ffffffff</color>
                <drawOrder>%d</drawOrder>
                <Icon>
                    <href>%s/%s</href>
                    <viewBoundScale>0.75</viewBoundScale>
                </Icon>
                <LatLonBox>
        """ % (outfile, order, path, outfile))
            
                bob.write("""        <north>%(north)s</north>
                    <south>%(south)s</south>
                    <east>%(east)s</east>
                    <west>%(west)s</west>
                    <rotation>0</rotation>
        """ % bounds)
        
                bob.write("""        </LatLonBox>
                <Region>
                <Lod>
                <minLodPixels>%s</minLodPixels>
                <maxLodPixels>%s</maxLodPixels>
                <minFadeExtent>0</minFadeExtent>
                <maxFadeExtent>0</maxFadeExtent>
                </Lod>
                </Region>
        """ % (currentMinLod,currentMaxLod))
                bob.write("""</GroundOverlay>
        """);
            
            else:
                numberOfRejectedTiles = numberOfRejectedTiles + 1
    
    #Reduce the image to a lower resolution for the next layer
    processing.run("gdal:warpreproject", {'INPUT':currentLayerPath,
    'SOURCE_CRS':None,'TARGET_CRS':None,'RESAMPLING':2,'NODATA':255,
    'TARGET_RESOLUTION':None,'OPTIONS':compressOptions,'DATA_TYPE':0,'TARGET_EXTENT':None,'TARGET_EXTENT_CRS':None,'MULTITHREADING':True,
    'EXTRA':'-tr ' + str(pixelSizeX*4) + ' ' + str(pixelSizeY*4),
    'OUTPUT':nextLayerPath})
    
    #Now reduce the 'order' by one, and use the next image to see whether to continue the while loop
    order = order - 1
    if order < 1: 
        print("Bump up your original draw order number, we've run out of numbers")
        fixItUpBro
    img = gdal.Open(nextLayerPath)
    

#Finish writing the kml
bob.write("""  </Folder>
</kml>
""")
bob.close()
img = None

"""
##########################################################################
Converting the kml to kmz
"""

#Don't know what these do honestly
def htc(m):
    return chr(int(m.group(1),16))
def urldecode(url):
    try:
        rex=re.compile('%([0-9a-hA-H][0-9a-hA-H])',re.M)
        return rex.sub(htc,url)
    except BaseException as e:
        print(e)


#Out path
destinationKmzPath = processDirectory + originalName + '.kmz'
base = os.path.dirname(processDirectory)

#Create the output zip file
import zipfile
zip = zipfile.ZipFile(destinationKmzPath, 'w', zipfile.ZIP_DEFLATED)

#Read the source xml
from xml.dom.minidom import parse
kml = parse(destinationKmlPath)
nodes = kml.getElementsByTagName('href')

#This runs through each of the images and puts them in the zip
for node in nodes:
    
    href = node.firstChild
    img = urldecode(href.nodeValue).replace('file:///', '')
    
    if not os.path.exists(img): img = processDirectory + '/' + img
    if not os.path.exists(img): print("The image doesn't exist, god knows why")
    
    #Add the image into the zip
    filename = 'files/%s' % os.path.basename(img)
    if verbose: logging.debug("Storing %s as %s" % (img, filename))
    zip.write(img, filename, zipfile.ZIP_STORED)

    # modify the xml to point to the correct image
    href.nodeValue = filename

#Finishing up and copying the file to the original directory
if verbose: logging.debug("Storing KML as doc.kml")
zip.writestr('doc.kml', kml.toxml("UTF-8"));
zip.close()
shutil.copy(destinationKmzPath, directory)


#Final messages
if verbose: logging.info("Finished")
print("All done")
endTime = time.time()
totalTime = endTime - startTime
box = QMessageBox()
box.setIcon(QMessageBox.Question)
box.setWindowTitle("Yeah we're done here")
box.setText("Yeah all done\n\nThis took " + str(int(totalTime)) + " seconds\n\nThere were " + str(currentTileNumber) + " tiles created and " + str(numberOfRejectedTiles) + " rejected\n\nHave a look at " + directory + originalName + '.kmz')
box.setStandardButtons(QMessageBox.Yes)
buttonY = box.button(QMessageBox.Yes)
buttonY.setText('Yeah nice')
box.exec_()

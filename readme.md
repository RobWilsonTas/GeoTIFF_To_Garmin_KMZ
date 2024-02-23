GeoTIFF To Garmin KMZ
=======================

This uses QGIS to take in a .tif and output a multi-resolution layered kmz that is compatible with Garmin's watches (and perhaps GPS units too)

I was originally looking into IMG files, but it seems that they're vector only? Whereas a kmz allows vector and raster



Because of the reprojection into WGS, the resulting image will likely end up with some thin white borders

There is the ability within the script to crop the image

Credit goes to old mate at https://github.com/tf198/gdal2custommap for slapping together some code a decade ago that helped with this

GeoTIFFToGarminKMZ.py
----------
Run this in QGIS's python console

Check the user variables at the start and make sure that it's set up how you want it

Any issues let me know




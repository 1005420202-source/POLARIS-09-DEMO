"""Earth Engine products exposed in the browser, with the desktop display scales."""
import urllib.request

from polaris31_legends import MAP_LEGENDS, WORLDCOVER_STYLE


PRODUCTS = {
    'precipitation_chirps': ('Precipitación CHIRPS (mm)', 'UCSB-CHG/CHIRPS/DAILY', 'precipitation', 'sum', 5566),
    'gpm_imerg': ('Precipitación GPM IMERG (mm)', 'NASA/GPM_L3/IMERG_V07', 'precipitation', 'half_hour_sum', 11132),
    'smap_soil_moisture': ('Humedad superficial SMAP (m³/m³)', 'NASA/SMAP/SPL4SMGP/008', 'sm_surface', 'mean', 9000),
    'gldas_soil_moisture': ('Humedad GLDAS 0–10 cm (kg/m²)', 'NASA/GLDAS/V021/NOAH/G025/T3H', 'SoilMoi0_10cm_inst', 'mean', 27830),
    'ndvi_modis': ('NDVI MODIS', 'MODIS/061/MOD13Q1', 'NDVI', 'scaled_mean', 250),
    'evi_modis': ('EVI MODIS', 'MODIS/061/MOD13Q1', 'EVI', 'scaled_mean', 250),
    'nasadem_elevation': ('Elevación NASADEM (m)', 'NASA/NASADEM_HGT/001', 'elevation', 'image', 30),
    'nasadem_slope': ('Pendiente NASADEM (°)', 'NASA/NASADEM_HGT/001', 'elevation', 'slope', 30),
    'esa_worldcover': ('Cobertura ESA WorldCover 2021', 'ESA/WorldCover/v200', 'Map', 'classes', 10),
    'sentinel2_ndwi': ('NDWI Sentinel-2', 'COPERNICUS/S2_SR_HARMONIZED', None, 'ndwi', 20),
    'landsat_ndvi': ('NDVI Landsat 8/9', 'LANDSAT/LC08/C02/T1_L2', None, 'landsat_ndvi', 30),
    'landsat_ndmi': ('NDMI Landsat 8/9', 'LANDSAT/LC08/C02/T1_L2', None, 'landsat_ndmi', 30),
    'jrc_occurrence': ('Ocurrencia histórica de agua superficial (%)', 'JRC/GSW1_4/GlobalSurfaceWater', 'occurrence', 'image', 30),
}


def analyze(ee, geometry, start, end, key, folder):
    label, dataset, band, method, scale = PRODUCTS[key]
    region = ee.Geometry(geometry)
    if method in ('image', 'slope'):
        dem = ee.Image(dataset).select(band)
        image = ee.Terrain.slope(dem) if method == 'slope' else dem
    elif method == 'classes':
        image = ee.ImageCollection(dataset).first().select(band)
    else:
        collection = ee.ImageCollection(dataset)
        if method.startswith('landsat'):
            collection = collection.merge(ee.ImageCollection('LANDSAT/LC09/C02/T1_L2'))
        collection = collection.filterBounds(region).filterDate(start, end)
        if collection.size().getInfo() == 0:
            raise ValueError('No hay escenas de este producto para el área y periodo elegidos.')
        if method == 'ndwi':
            def index(scene):
                scl = scene.select('SCL')
                clear = scl.remap([4,5,6,7], [1,1,1,1], 0)
                return scene.updateMask(clear).normalizedDifference(['B3','B8'])
            image = collection.map(index).median()
        elif method.startswith('landsat'):
            pair = ['SR_B5', 'SR_B4' if method == 'landsat_ndvi' else 'SR_B6']
            def index(scene):
                clear = scene.select('QA_PIXEL').bitwiseAnd(63).eq(0)
                rgb = scene.select(pair).multiply(.0000275).add(-.2).updateMask(clear)
                return rgb.normalizedDifference(pair)
            image = collection.map(index).median()
        else:
            collection = collection.select(band)
            image = collection.sum() if method in ('sum','half_hour_sum') else collection.mean()
            if method == 'half_hour_sum':
                image = image.multiply(.5)
            if method == 'scaled_mean':
                image = image.multiply(.0001)
    image = image.rename('value')
    stats = image.reduceRegion(
        reducer=ee.Reducer.frequencyHistogram() if method == 'classes' else ee.Reducer.mean(),
        geometry=region, scale=scale, maxPixels=30_000_000, tileScale=4,
    ).get('value').getInfo()
    if stats is None:
        raise ValueError('El producto no tiene píxeles válidos en esta área.')
    legend = MAP_LEGENDS[key]
    if method == 'classes':
        codes = list(WORLDCOVER_STYLE)
        image = image.remap([int(c) for c in codes], list(range(len(codes))))
        vis = {'min':0, 'max':len(codes)-1, 'palette':[WORLDCOVER_STYLE[c][1][1:] for c in codes]}
        total = sum(stats.values())
        result = {'pixel_count_by_class':stats, 'area_by_class_estimate':[
            {'code':c,'class_name':WORLDCOVER_STYLE[c][0],
             'area_ha':float(stats[c])/100,'percentage':float(stats[c])/total*100}
            for c in codes if total and c in stats]}
    else:
        vis = legend
        metric = {'precipitation_chirps':'mean_accumulated_mm',
                  'smap_soil_moisture':'mean_surface_soil_moisture_m3_m3'}.get(key,'mean_value')
        result = {metric:stats}
    path = folder / (key + '.png')
    url = image.clip(region).getThumbURL({'region':region, 'dimensions':760, 'format':'png', **vis})
    with urllib.request.urlopen(url, timeout=45) as response:
        png = response.read(8 * 1024 * 1024)
    path.write_bytes(png)
    artifact = {'key':key,'title':label,'path':str(path),'legend':legend,
                'scale_note':f'{dataset} · {scale} m'}
    return {key:result}, [artifact], region.area(1).divide(1e6).getInfo()

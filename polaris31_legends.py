"""Especificaciones cartográficas de los mapas POLARIS09 v31."""

WORLDCOVER_STYLE = {
    '10': ('Árboles / bosque', '#006400'),
    '20': ('Matorral', '#ffbb22'),
    '30': ('Pastizal', '#ffff4c'),
    '40': ('Cultivos', '#f096ff'),
    '50': ('Área construida', '#fa0000'),
    '60': ('Suelo desnudo / vegetación escasa', '#b4b4b4'),
    '70': ('Nieve / hielo', '#f0f0f0'),
    '80': ('Agua', '#0064c8'),
    '90': ('Humedal herbáceo', '#0096a0'),
    '95': ('Manglar', '#00cf75'),
    '100': ('Musgo / líquenes', '#fae6a0'),
}

MAP_LEGENDS = {
    'esa_worldcover': {'classes': WORLDCOVER_STYLE},
    'evi_modis': {'min': 0, 'max': 0.8, 'palette': ['a50026', 'fdae61', 'ffffbf', 'a6d96a', '006837']},
    'gldas_soil_moisture': {'min': 0, 'max': 10, 'palette': ['8c510a', 'dfc27d', 'f6e8c3', '80cdc1', '018571']},
    'gpm_imerg': {'min': 0, 'max': 500, 'palette': ['fff7bc', 'fec44f', 'd95f0e', '7f0000']},
    'jrc_occurrence': {'min': 0, 'max': 100, 'palette': ['ffffff', 'b3d9ff', '3182bd', '08306b']},
    'landsat_ndmi': {'min': -0.5, 'max': 0.6, 'palette': ['a6611a', 'dfc27d', 'f5f5f5', '80cdc1', '018571']},
    'landsat_ndvi': {'min': -0.2, 'max': 0.85, 'palette': ['8c510a', 'dfc27d', 'f6e8c3', '80cdc1', '01665e']},
    'lst_temperature': {'min': 0, 'max': 35, 'palette': ['313695', '74add1', 'ffffbf', 'f46d43', 'a50026']},
    'nasadem_elevation': {'min': 0, 'max': 5000, 'palette': ['173f5f', '3caea3', 'f6d55c', 'ed553b', 'ffffff']},
    'nasadem_slope': {'min': 0, 'max': 45, 'palette': ['1a9850', 'fee08b', 'f46d43', 'a50026']},
    'ndvi_modis': {'min': 0, 'max': 0.9, 'palette': ['8c510a', 'd8b365', 'f6e8c3', 'c7eae5', '5ab4ac', '01665e']},
    'ndwi_hydro': {'min': -0.5, 'max': 0.8, 'palette': ['8c510a', 'f6e8c3', '80cdc1', '01665e']},
    'precipitation_chirps': {'min': 0, 'max': 500, 'palette': ['fff7bc', 'fec44f', 'd95f0e', '7f0000']},
    'sentinel2_ndwi': {'min': -0.5, 'max': 0.7, 'palette': ['8c510a', 'dfc27d', 'f6e8c3', '80cdc1', '018571']},
    'smap_soil_moisture': {'min': 0.05, 'max': 0.5, 'palette': ['8c510a', 'dfc27d', 'f6e8c3', '80cdc1', '018571']},
    'worldcover_water_wetland': {'classes': WORLDCOVER_STYLE},
}

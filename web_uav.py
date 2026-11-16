"""RGB moisture proxy from the desktop score and thresholds."""
import numpy as np
from PIL import Image


def load_pixels(source, geometry):
    if source.suffix.lower() in ('.tif','.tiff'):
        import rasterio
        from rasterio.enums import Resampling
        from rasterio.errors import WindowError
        from rasterio.features import geometry_mask, geometry_window
        from rasterio.transform import Affine
        from rasterio.warp import transform_geom

        try:
            with rasterio.open(source,driver='GTiff') as dataset:
                if dataset.crs and geometry is not None:
                    if dataset.count < 3:
                        raise ValueError('Para el análisis RGB, sube un GeoTIFF con al menos tres bandas de color.')
                    projected = transform_geom('EPSG:4326',dataset.crs,geometry)
                    window = geometry_window(dataset,[projected])
                    scale = min(1,1400/max(window.height,window.width))
                    rows, cols = max(1,round(window.height*scale)), max(1,round(window.width*scale))
                    bands = dataset.read([1,2,3],window=window,out_shape=(3,rows,cols),
                                         masked=True,out_dtype='float32',resampling=Resampling.bilinear)
                    grid = dataset.window_transform(window)*Affine.scale(window.width/cols,window.height/rows)
                    inside = geometry_mask([projected],out_shape=(rows,cols),transform=grid,invert=True)
                    pixels = np.moveaxis(bands.filled(np.nan),0,-1)
                    return pixels, np.isfinite(pixels).all(axis=2)&inside, 'GeoTIFF recortado al polígono'
                if dataset.crs:
                    raise ValueError('Dibuja o carga un polígono para recortar el GeoTIFF.')
        except WindowError:
            raise ValueError('El GeoTIFF no se superpone con el polígono seleccionado.') from None
        except rasterio.errors.RasterioIOError:
            pass

    try:
        with Image.open(source) as im:
            if im.width * im.height > 40_000_000:
                raise ValueError('Reduce la imagen a 40 megapíxeles o menos.')
            im.thumbnail((1400,1400))
            pixels = np.asarray(im.convert('RGB'), dtype=np.float32)
    except (OSError, Image.DecompressionBombError):
        raise ValueError('El archivo no contiene una imagen JPG, PNG o TIFF compatible.') from None
    return pixels, np.ones(pixels.shape[:2],dtype=bool), 'Imagen sin georreferencia verificada'


def classify(source, folder, geometry=None):
    from skimage.filters import threshold_otsu
    from matplotlib.figure import Figure
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch

    pixels, valid, origin = load_pixels(source,geometry)
    total = int(valid.sum())
    if not total:
        raise ValueError('No hay píxeles RGB válidos dentro del polígono.')
    lo, hi = np.percentile(pixels[valid], [2,98], axis=0)
    normalized = np.clip((np.where(valid[:,:,None],pixels,lo)-lo)/np.maximum(hi-lo,1e-6),0,1)
    r,g,b = np.moveaxis(normalized,-1,0)
    darkness = 1 - (r+g+b)/3
    contrast = (g-b)/(g+b+1e-6)
    vegetation = np.clip((2*g-r-b+.15)/.9,0,1)
    score = np.clip(.62*darkness+.23*(1-np.clip(contrast,0,1))+.15*vegetation,0,1)
    threshold = float(threshold_otsu(score[valid])) if total>100 else .55
    classes = np.where(score>=max(.68,threshold+.12), 2,
                       np.where(score>=max(.48,threshold-.05),1,0))
    labels = ['Humedad aparente baja','Humedad aparente media','Humedad aparente alta']
    palette = ['#d9e2ec','#f4c95d','#168aad']
    summary = {}
    for i, label in enumerate(labels):
        n = int(((classes==i)&valid).sum())
        summary[label] = {'pixels':n,'percentage':round(n/total*100,2)}
    figure = Figure(figsize=(9,5), layout='constrained')
    original, result = figure.subplots(1,2)
    display = np.where(valid[:,:,None],pixels,0)/255 if origin.startswith('Imagen') else normalized
    original.imshow(np.dstack((display,valid.astype(float))))
    original.set_title('Imagen RGB')
    result.imshow(np.ma.masked_where(~valid,classes),cmap=ListedColormap(palette),vmin=0,vmax=2)
    result.set_title('Humedad aparente')
    for axis in (original,result):
        axis.axis('off')
    figure.legend(handles=[Patch(color=c,label=l) for c,l in zip(palette,labels)],
                  loc='lower center',ncol=3,fontsize=8)
    path = folder / 'uav.png'
    figure.savefig(path, dpi=130)
    return {'images':[{'file':'Imagen UAV','map':str(path)}], 'input_count':1,'origen':origin,
            'summary':summary,'total_valid_pixels':total,
            'interpretation_limit':'Clasificación RGB de humedad aparente; no es una medición de campo.'}, path

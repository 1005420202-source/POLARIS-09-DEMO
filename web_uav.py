"""RGB moisture proxy from the desktop score and thresholds."""
import numpy as np
from PIL import Image


def classify(source, folder):
    from skimage.filters import threshold_otsu
    from matplotlib.figure import Figure
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch

    try:
        with Image.open(source) as im:
            if im.width * im.height > 40_000_000:
                raise ValueError('Reduce la imagen a 40 megapíxeles o menos.')
            im.thumbnail((1400,1400))
            pixels = np.asarray(im.convert('RGB'), dtype=np.float32)
    except (OSError, Image.DecompressionBombError):
        raise ValueError('El archivo no contiene una imagen JPG, PNG o TIFF compatible.') from None
    lo, hi = np.percentile(pixels, [2,98], axis=(0,1))
    r,g,b = np.moveaxis(np.clip((pixels-lo)/np.maximum(hi-lo,1e-6),0,1),-1,0)
    darkness = 1 - (r+g+b)/3
    contrast = (g-b)/(g+b+1e-6)
    vegetation = np.clip((2*g-r-b+.15)/.9,0,1)
    score = np.clip(.62*darkness+.23*(1-np.clip(contrast,0,1))+.15*vegetation,0,1)
    threshold = float(threshold_otsu(score.ravel())) if score.size>100 else .55
    classes = np.where(score>=max(.68,threshold+.12), 2,
                       np.where(score>=max(.48,threshold-.05),1,0))
    labels = ['Humedad aparente baja','Humedad aparente media','Humedad aparente alta']
    palette = ['#d9e2ec','#f4c95d','#168aad']
    summary = {}
    for i, label in enumerate(labels):
        n = int((classes==i).sum())
        summary[label] = {'pixels':n,'percentage':round(n/classes.size*100,2)}
    figure = Figure(figsize=(9,5), layout='constrained')
    original, result = figure.subplots(1,2)
    original.imshow(pixels/255)
    original.set_title('Imagen RGB')
    result.imshow(classes,cmap=ListedColormap(palette),vmin=0,vmax=2)
    result.set_title('Humedad aparente')
    for axis in (original,result):
        axis.axis('off')
    figure.legend(handles=[Patch(color=c,label=l) for c,l in zip(palette,labels)],
                  loc='lower center',ncol=3,fontsize=8)
    path = folder / 'uav.png'
    figure.savefig(path, dpi=130)
    return {'images':[{'file':'Imagen UAV','map':str(path)}], 'input_count':1,
            'summary':summary,'total_valid_pixels':int(classes.size),
            'interpretation_limit':'Clasificación RGB de humedad aparente; no es una medición de campo.'}, path

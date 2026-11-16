"""POLARIS09 v30: simulación local del AOI, aptitud preliminar y atlas Blender.
No reemplaza mediciones de campo ni identificación botánica validada.
"""
from pathlib import Path
from datetime import datetime
import json, csv, math, shutil, subprocess
import numpy as np

USES = {
 'agricultura': ('Agricultura y cultivos', {'pendiente':.35,'humedad':.35,'cobertura':.30}),
 'forestal': ('Reforestación y uso forestal', {'pendiente':.25,'humedad':.40,'cobertura':.35}),
 'ganaderia': ('Pastos y ganadería', {'pendiente':.40,'humedad':.30,'cobertura':.30}),
 'conservacion': ('Conservación y restauración', {'pendiente':.20,'humedad':.40,'cobertura':.40}),
 'hidrologia': ('Humedales y recursos hídricos', {'pendiente':.30,'humedad':.55,'cobertura':.15}),
 'infraestructura': ('Infraestructura y construcción', {'pendiente':.50,'humedad':.35,'cobertura':.15}),
}
WEEDS_ANDEAN = [
 {'scientific':'Bidens pilosa','common':'Amor seco / cadillo','note':'Identificación visual requiere hojas, flores y aquenios'},
 {'scientific':'Chenopodium album','common':'Quinhuilla / cenizo','note':'Confirmar con caracteres de inflorescencia y hoja'},
 {'scientific':'Rumex crispus','common':'Lengua de vaca','note':'Confirmar márgenes foliares y fructificación'},
 {'scientific':'Taraxacum officinale agg.','common':'Diente de león','note':'Complejo taxonómico; verificar flores y hojas'},
 {'scientific':'Amaranthus hybridus','common':'Yuyo / bledo','note':'Confirmar inflorescencia y caracteres diagnósticos'},
 {'scientific':'Sonchus oleraceus','common':'Cerraja','note':'Confirmar hojas, látex y capítulos'},
]

def _aoi_mask(aoi, width=420):
    import rasterio.features
    from rasterio.transform import from_bounds
    from rasterio.warp import transform_geom
    features=aoi.get('features',[]) if aoi.get('type')=='FeatureCollection' else [aoi]
    geoms=[]
    for feat in features:
        geom=feat.get('geometry',feat)
        if not geom or geom.get('type') not in ('Polygon','MultiPolygon'): continue
        geoms.append(transform_geom('EPSG:4326','EPSG:32718',geom,precision=3))
    if not geoms: raise ValueError('El AOI debe contener polígonos válidos.')
    from rasterio.features import bounds
    bb=[bounds(g) for g in geoms]
    xmin=min(b[0] for b in bb); ymin=min(b[1] for b in bb)
    xmax=max(b[2] for b in bb); ymax=max(b[3] for b in bb)
    if xmax<=xmin or ymax<=ymin: raise ValueError('El AOI no tiene extensión válida.')
    width=min(width,max(64,int((xmax-xmin)/max(1,(ymax-ymin))*220)))
    height=min(420,max(64,int((ymax-ymin)/max(1,(xmax-xmin))*220)))
    transform=from_bounds(xmin,ymin,xmax,ymax,width,height)
    mask=rasterio.features.rasterize([(g,1) for g in geoms],out_shape=(height,width),transform=transform,fill=0,dtype='uint8').astype(bool)
    if not mask.any():raise ValueError('El polígono no contiene píxeles a la resolución elegida.')
    return mask,transform

def simulate(aoi,output_dir,soil='franco',rain_mm=30.,initial=.22,depth_m=2.,hours=24,mode='ambos',uav_paths=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import reproject
    from rasterio.transform import array_bounds
    from PIL import Image,ImageDraw
    out=Path(output_dir);out.mkdir(parents=True,exist_ok=True)
    mask,transform=_aoi_mask(aoi)
    h,w=mask.shape
    # Modelo parametrizado: las unidades se documentan en los metadatos.
    soil_par={
      'arenoso':(.43,.08,30.),'franco':(.46,.18,12.),'arcilloso':(.50,.30,3.)}
    theta_s,theta_fc,ksat=soil_par[soil]
    theta0=min(theta_s,max(.05,float(initial)))
    rain=max(0,float(rain_mm)); steps=max(2,min(72,int(hours)))
    dz=.1; nz=int(round(float(depth_m)/dz))
    # Sin mediciones: variabilidad espacial ilustrativa, no inferencia hidrogeológica.
    yy,xx=np.mgrid[0:h,0:w]
    nx=xx/max(1,w-1); ny=yy/max(1,h-1)
    terrain_proxy=np.clip(.5+.22*np.sin(nx*6)*np.cos(ny*5)+.12*(1-ny),0,1)
    uav_used=[]
    # Sólo se incorpora señal visual si el UAV posee georreferencia y solapa el AOI.
    for upath in (uav_paths or [])[:5]:
        try:
            with rasterio.open(upath) as ds:
                if not ds.crs or ds.count<1:continue
                src=ds.read(1,out_shape=(min(1024,ds.height),min(1024,ds.width)),resampling=Resampling.average).astype('float32')
                from rasterio.transform import Affine
                src_transform=ds.transform*Affine.scale(ds.width/src.shape[1],ds.height/src.shape[0])
                dst=np.full((h,w),np.nan,dtype='float32')
                reproject(src,dst,src_transform=src_transform,src_crs=ds.crs,dst_transform=transform,dst_crs='EPSG:32718',src_nodata=ds.nodata,dst_nodata=np.nan,resampling=Resampling.bilinear)
                good=np.isfinite(dst)&mask
                if good.sum()>50:
                    lo,hi=np.nanpercentile(dst[good],[5,95])
                    if hi>lo:
                        norm=np.clip((dst-lo)/(hi-lo),0,1)
                        # Sólo un proxy radiométrico, no humedad cuantificada.
                        terrain_proxy=np.where(good,.75*terrain_proxy+.25*(1-norm),terrain_proxy)
                        uav_used.append(Path(upath).name)
        except Exception:continue
    # 1D vertical por píxel: balance simplificado de infiltración/almacenamiento.
    # No es una solución de Richards ni una simulación de flujo subterráneo.
    theta=np.full((nz,h,w),theta0,dtype='float32')
    history=[]; snapshots=[]; dt_hours=float(hours)/steps
    for t in range(steps):
        input_mm=rain/steps
        infiltration=np.minimum(input_mm,ksat*dt_hours*(.65+.7*terrain_proxy))
        remaining=infiltration.astype('float32')
        for z in range(nz):
            capacity=np.maximum(0,(theta_s-theta[z])*dz*1000)
            uptake=np.minimum(remaining,capacity)
            theta[z]+=uptake/(dz*1000)
            remaining=np.maximum(0,remaining-uptake)
            # drenaje gravitacional conceptual, acotado y conservativo
            if z<nz-1:
                excess=np.maximum(0,(theta[z]-theta_fc)*dz*1000)
                drain=np.minimum(excess,ksat*dt_hours*.25)
                theta[z]-=drain/(dz*1000)
                remaining+=drain
        history.append(float(np.mean(theta[0][mask])))
        if t%max(1,steps//12)==0:snapshots.append((t,theta[:,mask].mean(axis=1).copy()))
    # Aptitud preliminar basada sólo en proxies disponibles, no aptitud validada.
    moist=np.clip((theta[0]-theta0)/max(.01,theta_s-theta0),0,1)
    slope_proxy=np.clip(1-np.abs(np.gradient(terrain_proxy)[0])*6,0,1)
    cover_proxy=np.clip(terrain_proxy,0,1)
    criteria={}
    for key,(label,weights) in USES.items():
        m=1-moist if key=='infraestructura' else moist
        c=1-cover_proxy if key=='infraestructura' else cover_proxy
        score=100*(weights['pendiente']*slope_proxy+weights['humedad']*m+weights['cobertura']*c)
        score=np.where(mask,score,np.nan)
        criteria[key]={'nombre':label,'puntaje_medio_indicativo':round(float(np.nanmean(score)),2),
          'area_alta_ha':round(float(np.count_nonzero((score>=70)&mask)*abs(transform.a*transform.e)/10000),3),
          'advertencia':'Aptitud exploratoria con variables proxy; falta pendiente DEM, suelos, clima y restricciones legales.'}
    area_ha=float(mask.sum()*abs(transform.a*transform.e)/10000)
    def save_map(array,name,title,cmap='YlGnBu',vmin=None,vmax=None):
        fig,ax=plt.subplots(figsize=(11,7),dpi=125)
        im=ax.imshow(np.ma.masked_where(~mask,array),cmap=cmap,vmin=vmin,vmax=vmax)
        fig.colorbar(im,ax=ax,label=title,shrink=.78)
        ax.set_title(title+' · EPSG:32718 · área AOI: %.2f ha'%area_ha)
        ax.set_xlabel('Columnas del ráster (coordenadas UTM en GeoTIFF)');ax.set_ylabel('Filas del ráster')
        ax.text(.01,-.12,'Modelo ilustrativo; verificar en campo. No equivale a humedad medida.',transform=ax.transAxes,fontsize=9)
        fig.tight_layout();f=out/name;fig.savefig(f,bbox_inches='tight');plt.close(fig);return str(f)
    top_png=save_map(theta[0],'humedad_superficial.png','Humedad volumétrica simulada (m³/m³)','YlGnBu',0,theta_s)
    deep_png=save_map(theta[-1],'humedad_2m.png','Humedad simulada a 2 m (m³/m³)','YlGnBu',0,theta_s)
    # GeoTIFF georreferenciado de capa superior y profundidad final
    for name,arr in [('humedad_superficial.tif',theta[0]),('humedad_2m.tif',theta[-1])]:
        with rasterio.open(out/name,'w',driver='GTiff',width=w,height=h,count=1,dtype='float32',crs='EPSG:32718',transform=transform,compress='deflate',nodata=-9999) as dst:
            dst.write(np.where(mask,arr,-9999).astype('float32'),1)
    # GIF de avance de infiltración en perfil vertical (profundidad vs tiempo)
    frames=[]
    for t,profile in snapshots:
        im=Image.new('RGB',(760,400),'#ffffff');d=ImageDraw.Draw(im)
        d.text((20,15),f'Escenario de infiltración · hora {(t+1)*dt_hours:.1f} de {hours}',fill='#16334e')
        for z in range(nz):
            val=float(profile[z])
            strength=int(255*np.clip(val/theta_s,0,1))
            y=55+int(z*300/nz)
            d.rectangle((90,y,690,y+max(1,int(300/nz))),fill=(20,80+int(strength*.45),min(255,90+strength)))
        d.text((20,370),'Perfil ilustrativo de humedad por profundidad; no son datos medidos',fill='#16334e')
        frames.append(im)
    gif=out/'infiltracion_perfil.gif';frames[0].save(gif,save_all=True,append_images=frames[1:],duration=450,loop=0)
    fig,ax=plt.subplots(figsize=(9,4),dpi=110);ax.plot(np.arange(1,len(history)+1)*dt_hours,history)
    ax.set(xlabel='Horas simuladas',ylabel='Humedad superficial media (m³/m³)',title='Evolución simulada de humedad');ax.grid(alpha=.3)
    fig.tight_layout();chart=out/'evolucion_humedad.png';fig.savefig(chart);plt.close(fig)
    result={'motor':'POLARIS09 local v30','modelo':'Balance hídrico 1D simplificado por píxel; no Richards/IBER',
      'profundidad_m':depth_m,'duracion_horas':hours,'lluvia_escenario_mm':rain,'suelo_hipotetico':soil,
      'theta_inicial':theta0,'theta_s':theta_s,'conductividad_saturada_mm_h':ksat,
      'area_aoi_ha':round(area_ha,3),'crs':'EPSG:32718','pixel_area_m2':round(abs(transform.a*transform.e),2),
      'humedad_superficial_final_media':round(float(np.mean(theta[0][mask])),4),
      'humedad_2m_final_media':round(float(np.mean(theta[-1][mask])),4),
      'aptitudes':criteria,'uav_georreferenciados_usados':uav_used,
      'cultivos':{'modo':'recomendación preliminar','candidatos':['papa','maíz','quinua','alfalfa'],
       'advertencia':'No se recomienda un cultivo específico sin pH, textura real, clima, altitud, disponibilidad de agua y análisis de suelo.'},
      'malezas':{'catalogo_regional_orientativo':WEEDS_ANDEAN,'especies_identificadas':[],
        'estado':'Sin modelo botánico entrenado ni fotografías etiquetadas: no se identifican especies automáticamente.'},
      'archivos':{'mapa_superficial':top_png,'mapa_2m':deep_png,'animacion':str(gif),'grafica':str(chart)},
      'limitaciones':['Parámetros hipotéticos; sin calibración de campo','Proxies espaciales no equivalen a relieve ni humedad real',
        'La simulación vertical es conceptual y no predice nivel freático','La aptitud para infraestructura requiere estudios geotécnicos y restricciones normativas']}
    (out/'resultados_simulacion.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    with (out/'aptitudes.csv').open('w',encoding='utf-8-sig',newline='') as f:
        wr=csv.writer(f);wr.writerow(['Uso','Puntaje exploratorio 0-100','Área indicativa alta ha'])
        for k,v in criteria.items():wr.writerow([v['nombre'],v['puntaje_medio_indicativo'],v['area_alta_ha']])
    return result

def blender_render(result,output_dir):
    """Genera .blend y PNG mediante Blender en modo background, si está instalado."""
    out=Path(output_dir);exe=shutil.which('blender')
    if not exe:
        import os
        pf=Path(os.environ.get('ProgramFiles','C:/Program Files'))/'Blender Foundation'
        if pf.exists():
            exe=next((str(x) for x in sorted(pf.glob('Blender */blender.exe'),reverse=True)),None)
    if not exe:return {'estado':'Blender no encontrado; se mantienen mapas 2D y GIF.'}
    script=out/'escena_blender.py';blend=out/'escena_simulacion.blend';png=out/'render_simulacion_blender.png'
    # Render de perfil conceptual: altura = humedad, NO topografía real.
    code="""import bpy, math
from mathutils import Vector
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_grid_add(x_subdivisions=25,y_subdivisions=25,size=8)
obj=bpy.context.object;obj.name='Superficie conceptual de humedad (NO DEM)'
for v in obj.data.vertices:
 x,y=v.co.x,v.co.y
 v.co.z=.55+.25*math.sin(x*.8)*math.cos(y*.9)
mat=bpy.data.materials.new('Indicador_Humedad');mat.diffuse_color=(.04,.38,.65,1);obj.data.materials.append(mat)
bpy.ops.object.camera_add(location=(11,-13,12));cam=bpy.context.object
cam.rotation_euler=(Vector((0,0,0))-cam.location).to_track_quat('-Z','Y').to_euler();bpy.context.scene.camera=cam
bpy.ops.object.light_add(type='AREA',location=(0,0,12));bpy.context.object.data.energy=1700;bpy.context.object.data.size=10
scene=bpy.context.scene;scene.render.engine='BLENDER_EEVEE_NEXT';scene.render.resolution_x=1400;scene.render.resolution_y=900
scene.render.image_settings.file_format='PNG';scene.render.filepath=__PNG__
bpy.ops.wm.save_as_mainfile(filepath=__BLEND__)
bpy.ops.render.render(write_still=True)
""".replace('__PNG__',repr(str(png))).replace('__BLEND__',repr(str(blend)))
    script.write_text(code,encoding='utf-8')
    try:
        run=subprocess.run([exe,'-b','--python',str(script)],capture_output=True,text=True,timeout=150)
        if run.returncode==0 and png.exists():return {'estado':'Render Blender generado','imagen':str(png),'proyecto':str(blend)}
        return {'estado':'Blender falló; revisar registro','detalle':(run.stderr or run.stdout)[-1200:]}
    except Exception as exc:return {'estado':'No se pudo ejecutar Blender','detalle':str(exc)}

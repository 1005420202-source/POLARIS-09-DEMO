"""Native browser entry points for POLARIS09's processing engines."""
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile
import json
import math
import os
import re
import secrets
import shutil
import time
from threading import Lock

from flask import Flask, abort, jsonify, request, send_file, send_from_directory, session
from werkzeug.exceptions import HTTPException

from polaris31_assistant import ask, decision_context, decision_guide

app = Flask(__name__)
app.secret_key = os.environ.get('POLARIS_SESSION_KEY') or secrets.token_hex(32)
app.config.update(MAX_CONTENT_LENGTH=18 * 1024 * 1024, SESSION_COOKIE_SAMESITE='Lax',
                  SESSION_COOKIE_SECURE=os.environ.get('POLARIS_HTTPS') == '1',
                  PERMANENT_SESSION_LIFETIME=timedelta(hours=4))
ROOT = Path(os.environ.get('POLARIS_WORK', '/tmp/polaris09'))
LOCK = Lock()
LAST_CLEANUP = 0


@app.errorhandler(HTTPException)
def http_error(error):
    return jsonify(error=error.description), error.code


@app.errorhandler(ValueError)
def invalid(error):
    return jsonify(error=str(error)), 400


@app.get('/')
def home():
    return send_file(Path(__file__).with_name('index.html'))


@app.get('/web.js')
def javascript():
    return send_file(Path(__file__).with_name('web.js'))


def workspace():
    sid = session.get('sid')
    if not sid:
        abort(409, 'Abre o recarga la portada primero.')
    return ROOT / sid


@app.get('/api/status')
def status():
    from web_gis import PRODUCTS
    session.setdefault('sid', secrets.token_hex(24))
    session.permanent = True
    return jsonify(gee=bool(os.environ.get('POLARIS_GEE_PROJECT')),
                   ai=bool(os.environ.get('OPENAI_API_KEY') or os.environ.get('POLARIS_OLLAMA_URL')),
                   blender=bool(shutil.which('blender')),
                   products={key: product[0] for key,product in PRODUCTS.items()})


def payload():
    data = request.get_json()
    if not isinstance(data, dict):
        abort(400, 'Se esperaba un objeto JSON.')
    return data


def read_results():
    file = workspace() / 'results.json'
    return json.loads(file.read_text(encoding='utf-8')) if file.is_file() else {}


def save_results(data):
    folder = workspace()
    folder.mkdir(parents=True, exist_ok=True)
    pending = folder / 'results.tmp'
    pending.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    pending.replace(folder / 'results.json')


def results_for_geometry(geometry):
    state = read_results()
    signature = json.loads(json.dumps(geometry))
    if state.get('geometry') != signature:
        for key in ('results','gis_key','period','artifacts','aoi','selected_processes','simulacion_aoi','ai_history'):
            state.pop(key, None)
        state['geometry'] = signature
    return state


def run_job(function):
    if not LOCK.acquire(blocking=False):
        abort(429, 'Hay un análisis en ejecución. Vuelve a intentarlo cuando termine.')
    try:
        global LAST_CLEANUP
        now = time.time()
        ROOT.mkdir(parents=True, exist_ok=True)
        if now - LAST_CLEANUP > 900:
            for old in ROOT.iterdir():
                if old.is_dir() and re.fullmatch(r'[a-f0-9]{48}', old.name) and now - old.stat().st_mtime > 14400:
                    shutil.rmtree(old)
            LAST_CLEANUP = now
        path = workspace()
        path.mkdir(exist_ok=True)
        os.utime(path)
        return function()
    except (ValueError, HTTPException):
        raise
    except Exception:
        app.logger.exception('Error al ejecutar el proceso')
        abort(502, 'El proceso no pudo terminar. Revisa los datos de entrada o la conexión del servidor.')
    finally:
        LOCK.release()


def geo(value):
    from shapely.geometry import shape, mapping
    from shapely.ops import unary_union
    if not isinstance(value, dict):
        raise ValueError('Dibuja o carga un polígono primero.')
    features = value.get('features', []) if value.get('type') == 'FeatureCollection' else [value]
    if not isinstance(features, list) or not 1 <= len(features) <= 40:
        raise ValueError('Usa entre 1 y 40 polígonos.')
    polygons = []
    count = 0
    for feature in features:
        if not isinstance(feature,dict):
            raise ValueError('El área debe contener polígonos WGS84.')
        geometry = feature.get('geometry') if feature.get('type') == 'Feature' else feature
        if not isinstance(geometry,dict) or geometry.get('type') not in ('Polygon', 'MultiPolygon'):
            raise ValueError('El área debe contener polígonos WGS84.')
        try:
            part = shape(geometry)
        except (ValueError, TypeError, IndexError, KeyError):
            raise ValueError('Las coordenadas del polígono no son válidas.') from None
        if part.is_empty or not part.is_valid or part.has_z:
            raise ValueError('Corrige el polígono antes de ejecutar el análisis.')
        pieces = list(part.geoms) if part.geom_type == 'MultiPolygon' else [part]
        count += sum(len(p.exterior.coords) + sum(len(r.coords) for r in p.interiors) for p in pieces)
        polygons.append(part)
    if count > 3000:
        raise ValueError('Simplifica el área a 3000 vértices como máximo.')
    merged = unary_union(polygons)
    west, south, east, north = merged.bounds
    if not (-180 <= west < east <= 180 and -85 <= south < north <= 85
            and east-west <= 1 and north-south <= 1):
        raise ValueError('Usa un área WGS84 con extensión máxima de un grado por lado.')
    return mapping(merged)


@app.post('/api/shape')
def load_shape():
    from io import BytesIO
    from pyproj import CRS, Transformer
    import shapefile
    from shapely.geometry import mapping, shape
    from shapely.ops import transform

    upload = request.files.get('file')
    if not upload:
        raise ValueError('Selecciona un Shapefile ZIP.')
    try:
        with ZipFile(upload.stream) as archive:
            members = [item for item in archive.infolist()
                       if not item.is_dir() and Path(item.filename).suffix.lower() in ('.shp','.shx','.prj')]
            if len(members) > 3 or sum(item.file_size for item in members) > 12 * 1024 * 1024:
                raise ValueError('El ZIP debe contener un solo Shapefile de hasta 12 MB descomprimido.')
            files = {Path(item.filename).suffix.lower():archive.read(item) for item in members}
        if '.shp' not in files or '.prj' not in files:
            raise ValueError('Incluye los archivos .shp y .prj en el ZIP.')
        projection = Transformer.from_crs(CRS.from_wkt(files['.prj'].decode('utf-8-sig')),
                                          'EPSG:4326', always_xy=True)
        options = {'shx':BytesIO(files['.shx'])} if '.shx' in files else {}
        with shapefile.Reader(shp=BytesIO(files['.shp']), **options) as reader:
            geometries = []
            for record in reader.iterShapes():
                if len(geometries) >= 40 or len(record.points) > 3000:
                    raise ValueError('Simplifica el Shapefile a 40 registros y 3000 vértices.')
                polygon = transform(projection.transform,shape(record.__geo_interface__))
                geometries.append({'type':'Feature','geometry':mapping(polygon),'properties':{}})
    except (BadZipFile, OSError, UnicodeError, shapefile.ShapefileException):
        raise ValueError('No se pudo leer el Shapefile ZIP.') from None
    except Exception as error:
        if isinstance(error,ValueError):
            raise
        raise ValueError('Revisa que el Shapefile contenga polígonos y una proyección válida.') from None
    return jsonify(geometry=geo({'type':'FeatureCollection','features':geometries}))


def asset(folder, name):
    return '/api/file/' + folder.name + '/' + name


def new_run():
    folder = workspace() / secrets.token_hex(12)
    folder.mkdir()
    return folder


@app.get('/api/file/<run>/<path:name>')
def download(run, name):
    if not re.fullmatch(r'[a-f0-9]{24}', run) or not re.fullmatch(r'[\w.-]+\.(png|jpg|pdf|gif|tif|csv|blend)', name):
        abort(404)
    return send_from_directory(workspace() / run, name, as_attachment=name.endswith(('.pdf','.tif','.csv','.blend')))


@app.post('/api/project')
def gee_connection():
    def execute():
        ee = earth_engine()
        return jsonify(ok=ee.Number(1).getInfo() == 1)
    return run_job(execute)


def earth_engine():
    project = os.environ.get('POLARIS_GEE_PROJECT')
    if not project:
        abort(503, 'Falta configurar el proyecto de Earth Engine en el servidor.')
    import ee
    import google.auth
    credentials, _ = google.auth.default(scopes=ee.oauth.SCOPES)
    ee.Initialize(credentials=credentials, project=project)
    return ee


@app.post('/api/gis')
def gis():
    data = payload()
    geometry = geo(data.get('aoi'))
    try:
        start, end = (date.fromisoformat(data.get(k, '')) for k in ('start','end'))
    except (ValueError, TypeError):
        raise ValueError('Selecciona las fechas de inicio y fin.') from None
    if not 0 < (end-start).days <= 366:
        raise ValueError('El periodo debe tener entre 1 y 366 días.')
    module = data.get('module')
    from web_gis import PRODUCTS, analyze
    if not isinstance(module,str) or module not in PRODUCTS:
        raise ValueError('Selecciona un proceso GIS.')

    def execute():
        folder = new_run()
        result, maps, area = analyze(earth_engine(), geometry, str(start), str(end), module, folder)
        state = results_for_geometry(geometry)
        run_key = [geometry, str(start), str(end)]
        run_key = json.loads(json.dumps(run_key))
        if state.get('gis_key') != run_key:
            state.update(gis_key=run_key, results={}, artifacts={'maps':[]}, selected_processes=[])
        state.update(period={'start': str(start), 'end': str(end)}, aoi={'area_km2':area},
                     project_id=os.environ['POLARIS_GEE_PROJECT'])
        state['results'].update(result)
        files = state['artifacts']['maps']
        files[:] = [item for item in files if item['key'] not in {m['key'] for m in maps}]
        files.extend(maps)
        state['selected_processes'] = list(state['results'])
        save_results(state)
        return jsonify(result=result, maps=[dict(m, path=asset(folder,Path(m['path']).name)) for m in maps])
    return run_job(execute)


@app.post('/api/simulation')
def simulation():
    from polaris30_engine import simulate
    data = payload()
    geometry = geo(data.get('aoi'))
    soil = data.get('soil')
    try:
        rain, initial = (float(data.get(k, '')) for k in ('rain','initial'))
    except (ValueError, TypeError):
        raise ValueError('Indica lluvia y humedad inicial válidas.') from None
    if soil not in ('franco','arenoso','arcilloso') or not 0 <= rain <= 500 or not .05 <= initial <= .50:
        raise ValueError('Lluvia de 0 a 500 mm y humedad inicial de 0,05 a 0,50 m³/m³.')

    def execute():
        folder = new_run()
        result = simulate(geometry,folder,soil=soil,rain_mm=rain,initial=initial)
        state = results_for_geometry(geometry)
        state['simulacion_aoi'] = result
        state.setdefault('aoi', {})['area_km2'] = result['area_aoi_ha'] / 100
        save_results(state)
        return jsonify(result={k:result[k] for k in ('area_aoi_ha','modelo',
                        'humedad_superficial_final_media','aptitudes')},
                       maps=[{'title':'Humedad superficial simulada', 'path':asset(folder,Path(result['archivos']['mapa_superficial']).name)}])
    return run_job(execute)


@app.post('/api/uav')
def uav():
    from web_uav import classify
    upload = request.files.get('image')
    if not upload or Path(upload.filename or '').suffix.lower() not in ('.png','.jpg','.jpeg','.tif','.tiff'):
        raise ValueError('Sube una imagen JPG, PNG o TIFF de hasta 18 MB.')

    def execute():
        folder = new_run()
        with TemporaryDirectory() as tmp:
            raw = Path(tmp) / ('uav' + Path(upload.filename).suffix.lower())
            upload.save(raw)
            result, preview = classify(raw, folder)
        state = read_results()
        state['drone_moisture'] = result
        save_results(state)
        return jsonify(result=result['summary'], maps=[{'title':'Humedad aparente UAV',
                       'path':asset(folder,preview.name)}])
    return run_job(execute)


@app.post('/api/aquafinder')
def aquafinder():
    data = payload()

    def number(key, limit=100):
        try:
            value = float(data.get(key,''))
        except (ValueError,TypeError):
            raise ValueError(f'Introduce un valor para {key}.') from None
        if not 0 <= value <= limit:
            raise ValueError(f'{key} debe estar entre 0 y {limit}.')
        return value

    def execute():
        state = results_for_geometry(geo(data.get('aoi')))
        gis = state.setdefault('results',{})
        slope = gis.get('nasadem_slope',{}).get('mean_value')
        precip = gis.get('precipitation_chirps',{}).get('mean_accumulated_mm')
        ndvi = gis.get('ndvi_modis',{}).get('mean_value')
        slope_pct = math.tan(math.radians(min(slope,89.9)))*100 if slope is not None else number('Pendiente',500)
        slope_score = next(score for upper,score in ((5,100),(10,85),(20,65),(30,40),(math.inf,20)) if slope_pct <= upper)
        recharge = min(100,max(0,precip/8)) if precip is not None else number('Recarga')
        vegetation = min(100,max(0,(ndvi+.2)/.9*100)) if ndvi is not None else number('Vegetación')
        indicators = [
            ('Geología',number('Geología'),.25,'Entrada manual'),
            ('Lineamientos',number('Lineamientos'),.20,'Entrada manual'),
            ('Pendiente',slope_score,.10,'NASADEM' if slope is not None else 'Entrada manual'),
            ('Recarga',recharge,.15,'CHIRPS' if precip is not None else 'Entrada manual'),
            ('Vegetación',vegetation,.10,'MODIS NDVI' if ndvi is not None else 'Entrada manual'),
            ('Drenaje',number('Drenaje'),.10,'Entrada manual'),
        ]
        if data.get('Geofísica') not in (None,''):
            indicators.append(('Geofísica ERT/SEV',number('Geofísica'),.10,'Entrada manual'))
        total = sum(weight for _,_,weight,_ in indicators)
        score = sum(value*weight for _,value,weight,_ in indicators)/total
        result = {'score':round(score,2),'category':'Potencial preliminar '+('alto' if score>=70 else 'medio' if score>=45 else 'bajo'),
                  'indicators':[{'name':name,'score':round(value,2),'weight':weight,'source':source}
                                for name,value,weight,source in indicators],
                  'validation_warning':'Índice experimental. Requiere verificación hidrogeológica y geofísica de campo.'}
        gis['aquafinder_groundwater'] = result
        save_results(state)
        return jsonify(result=result)
    return run_job(execute)


@app.post('/api/assistant')
def assistant():
    question = request.form.get('question','').strip() or '¿Qué acciones debo priorizar?'
    if len(question)>1500:
        raise ValueError('La consulta admite hasta 1500 caracteres.')
    photo = request.files.get('image')
    if photo and Path(photo.filename or '').suffix.lower() not in ('.png','.jpg','.jpeg','.webp'):
        raise ValueError('Para la consulta visual usa JPG, PNG o WEBP.')

    def execute():
        state = read_results()
        context = decision_context(state, state.get('drone_moisture'))
        mode = 'online' if os.environ.get('OPENAI_API_KEY') else 'local'
        if os.environ.get('OPENAI_API_KEY') or os.environ.get('POLARIS_OLLAMA_URL'):
            with TemporaryDirectory() as tmp:
                path = None
                if photo:
                    path = Path(tmp) / ('foto' + Path(photo.filename).suffix.lower())
                    photo.save(path)
                answer = ask(question, path, context, mode, os.environ.get('POLARIS_OLLAMA_MODEL','llava'), decision=True)
        else:
            answer = {'provider':'Guía local por reglas','answer':decision_guide(context)}
        state['ai_history'] = [{'pregunta':question,**answer}]
        save_results(state)
        return jsonify(answer)
    return run_job(execute)


@app.post('/api/report')
def report():
    from polaris31_report import generate_brief_report
    def execute():
        state = read_results()
        if not state:
            raise ValueError('Ejecuta primero un análisis o una consulta.')
        folder = new_run()
        file = generate_brief_report(state, folder, include_blender=False)
        return jsonify(path=asset(folder,file.name))
    return run_job(execute)


@app.post('/api/blender')
def render():
    from polaris31_blender import render_terrain
    from web_gis import terrain_sample

    geometry = geo(payload().get('aoi'))

    def execute():
        pixels, width, height, scale = terrain_sample(earth_engine(),geometry)
        folder = new_run()
        jpg, pdf = render_terrain(pixels,width,height,scale,folder)
        return jsonify(
            maps=[{'title':'Relieve 3D NASADEM en Blender','path':asset(folder,jpg.name)}],
            downloads=[{'label':'Descargar JPG','path':asset(folder,jpg.name)},
                       {'label':'Descargar PDF del relieve 3D','path':asset(folder,pdf.name)}],
        )
    return run_job(execute)

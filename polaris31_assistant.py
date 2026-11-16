"""Asistente híbrido de POLARIS09. No identifica taxones sin modelo visual."""
from __future__ import annotations
import os, json, base64, mimetypes, urllib.request, urllib.error
from pathlib import Path

SYSTEM = ("Eres el asistente técnico POLARIS09, especializado en interpretación visual preliminar de terrenos "
          "andinos, malezas y suelos. Responde en español sencillo. Diferencia observación visible de inferencia. "
          "No diagnostiques especies, humedad real, composición de suelo o aptitud definitiva sin evidencias. "
          "Si la especie no es reconocible, solicita fotografía de hojas, flores y tallo. "
          "Cuando haya datos GIS aportados, usa solo los datos suministrados. No inventes medidas ni porcentajes. "
          "Da recomendaciones condicionadas y breves.")

DECISION_SYSTEM = SYSTEM + (
    " Para ayudar a decidir, responde con: evidencia disponible, acciones en orden de prioridad "
    "(qué hacer y por qué), y datos por verificar antes de decidir. Cita el producto y el periodo "
    "cuando correspondan. Distingue estimaciones satelitales, indicios UAV y escenarios simulados "
    "de mediciones de campo. Si faltan datos decisivos, indica cómo obtenerlos."
)


def decision_context(results, drone=None):
    """Extrae indicadores; excluye rutas, geometrías y archivos del proyecto."""
    results = results or {}
    context = {
        'periodo_GIS': results.get('period'),
        'area_km2': (results.get('aoi') or {}).get('area_km2'),
        'GIS': {},
    }
    for source, metrics in (results.get('results') or {}).items():
        if source == 'aquafinder_groundwater':
            fields = ('score', 'category', 'validation_warning')
            context['GIS'][source] = {k: metrics[k] for k in fields if k in metrics}
        else:
            indicators = {
                k: v for k, v in metrics.items()
                if type(v) in (int, float, bool) or k in ('interpretation', 'resolution')
            }
            for field in ('pixel_count_by_class', 'surface_water', 'surface_temperature',
                          'precipitation_hydro', 'nearby_water_bodies', 'nearby_wetlands'):
                values = metrics.get(field)
                if isinstance(values, dict):
                    indicators[field] = {k: v for k, v in values.items() if type(v) in (int, float)}
            context['GIS'][source] = indicators
    issues = results.get('module_errors') or []
    if issues:
        context['modulos_con_incidencias'] = sorted({str(item.get('module', '')) for item in issues})
    simulation = results.get('simulacion_aoi')
    if simulation:
        fields = ('modelo', 'lluvia_escenario_mm', 'suelo_hipotetico', 'theta_inicial',
                  'humedad_superficial_final_media', 'humedad_2m_final_media', 'aptitudes')
        context['simulacion_AOI'] = {k: simulation[k] for k in fields if k in simulation}
    if drone:
        fields = ('input_count', 'summary', 'total_valid_pixels', 'interpretation_limit')
        context['UAV'] = {k: drone[k] for k in fields if k in drone}
    return context


def decision_guide(context):
    actions = []
    gis = context.get('GIS', {})
    if context.get('modulos_con_incidencias'):
        modules = ', '.join(context['modulos_con_incidencias'])
        actions.append(f'Revisa y repite los módulos con incidencias ({modules}) si son necesarios para la decisión.')
    drone = context.get('UAV')
    if drone:
        high = drone.get('summary', {}).get('Humedad aparente alta', {}).get('percentage')
        evidence = f' ({high} % de píxeles válidos)' if high is not None else ''
        actions.append(f'Contrasta en campo zonas marcadas con humedad aparente alta{evidence} y zonas de referencia. Revisa sombras y mide humedad a la profundidad de interés antes de ajustar el riego.')
    if any(gis.values()):
        actions.append('Comprueba el periodo, la cobertura y la resolución de las capas GIS. Contrasta sus patrones con observaciones de la parcela antes de delimitar zonas de manejo.')
    if 'aquafinder_groundwater' in gis or 'hydro_potential' in gis:
        actions.append('Usa los candidatos hidrogeológicos para priorizar una visita técnica y verificación geofísica antes de elegir un punto de estudio.')
    if context.get('simulacion_AOI'):
        actions.append('Repite la simulación con lluvia y humedad inicial observadas. Compara escenarios antes de elegir una intervención.')
    if not actions:
        actions.append('Define la decisión y el área de estudio. Ejecuta un análisis relacionado o reúne observaciones de campo para comparar opciones.')
    return '\n'.join(f'{i}. {action}' for i, action in enumerate(actions, 1))


def _post(url, payload, headers=None, timeout=75):
    data=json.dumps(payload,ensure_ascii=False).encode('utf-8')
    req=urllib.request.Request(url,data=data,headers={'Content-Type':'application/json',**(headers or {})},method='POST')
    with urllib.request.urlopen(req,timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _encoded_image(path):
    p=Path(path)
    if not p.is_file(): raise FileNotFoundError(p)
    if p.stat().st_size>12*1024*1024 and p.suffix.lower() not in ('.tif','.tiff','.img','.jp2'): raise ValueError('La imagen supera 12 MB; exporta una vista previa JPG o PNG.')
    # GeoTIFF multipágina o 16 bits: generar vista previa RGB; no inferir bandas NIR.
    if p.suffix.lower() in ('.tif','.tiff','.img','.jp2'):
        from PIL import Image, ImageOps
        from io import BytesIO
        with Image.open(p) as im:
            im.thumbnail((1400,1400))
            if im.mode not in ('RGB','RGBA'):
                im=ImageOps.autocontrast(im.convert('RGB'))
            buf=BytesIO(); im.convert('RGB').save(buf,format='JPEG',quality=85)
            return 'image/jpeg',base64.b64encode(buf.getvalue()).decode('ascii')
    mime=mimetypes.guess_type(p.name)[0] or 'image/jpeg'
    if mime not in ('image/jpeg','image/png','image/webp'): raise ValueError('Para IA usa JPG, PNG, WEBP o TIFF convertible.')
    return mime,base64.b64encode(p.read_bytes()).decode('ascii')


def ask(question, image_path=None, context=None, mode='auto', model='llava', api_key=None, decision=False):
    """Prioridad: Ollama local; API online opcional; respuesta explícita sin modelo si no hay conexión."""
    question=(question or '').strip()
    if not question: raise ValueError('Escribe una consulta.')
    context=context or {}
    system = DECISION_SYSTEM if decision else SYSTEM
    msg=question+'\nDatos GIS disponibles: '+json.dumps(context,ensure_ascii=False,default=str)
    image_data=None; mime=None
    if image_path: mime,image_data=_encoded_image(image_path)
    errors=[]
    if mode in ('auto','local'):
        try:
            payload={'model':model,'stream':False,'messages':[{'role':'system','content':system},
                     {'role':'user','content':msg,'images':[image_data] if image_data else []}]}
            result=_post(os.environ.get('POLARIS_OLLAMA_URL','http://127.0.0.1:11434/api/chat'),payload,timeout=65)
            answer=result.get('message',{}).get('content','').strip()
            if not answer: raise RuntimeError('El modelo local devolvió una respuesta vacía.')
            return {'provider':'Ollama local','model':model,'answer':answer,'vision':bool(image_data)}
        except Exception as exc: errors.append('Ollama: '+str(exc)[:220])
    if mode in ('auto','online'):
        key=api_key or os.environ.get('OPENAI_API_KEY','')
        if key:
            try:
                content=[{'type':'text','text':msg}]
                if image_data: content.append({'type':'image_url','image_url':{'url':f'data:{mime};base64,{image_data}'}})
                model_online=os.environ.get('POLARIS_OPENAI_MODEL','gpt-4o-mini')
                result=_post('https://api.openai.com/v1/chat/completions',
                    {'model':model_online,'messages':[{'role':'system','content':system},{'role':'user','content':content}],
                     'max_tokens':750},headers={'Authorization':'Bearer '+key},timeout=65)
                answer=result['choices'][0]['message']['content'].strip()
                if not answer: raise RuntimeError('La API devolvió una respuesta vacía.')
                return {'provider':'API en línea','model':model_online,'answer':answer,'vision':bool(image_data)}
            except Exception as exc: errors.append('API: '+str(exc)[:220])
        else: errors.append('API: falta OPENAI_API_KEY')
    if decision:
        return {'provider':'Guía local por reglas','model':None,'vision':False,
                'answer':'Modelo de IA no disponible. Recomendaciones preliminares:\n'+decision_guide(context)}
    return {'provider':'Sin modelo de IA conectado','model':None,'vision':False,
        'answer':('No se ha podido ejecutar un modelo de visión. La fotografía no ha sido identificada. '\
                  'Para activar la IA local instala Ollama y un modelo con visión (por ejemplo llava), '\
                  'o configura OPENAI_API_KEY para el modo en línea. '\
                  'Mientras tanto puedes consultar los mapas y resultados GIS ya calculados.\n'+'; '.join(errors))}

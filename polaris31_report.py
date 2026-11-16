"""Informe ejecutivo POLARIS09: ocho páginas fijas, sin relleno ni afirmaciones inventadas."""
from __future__ import annotations
import json, html, shutil
from datetime import datetime
from pathlib import Path
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak, KeepTogether
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.units import cm
from reportlab.lib.pagesizes import A4
from reportlab.graphics.shapes import Drawing, Rect
from polaris31_legends import MAP_LEGENDS


MAP_UNITS = {
    'precipitation_chirps': 'mm', 'gpm_imerg': 'mm',
    'smap_soil_moisture': 'm³/m³', 'gldas_soil_moisture': 'kg/m²',
    'nasadem_elevation': 'm', 'nasadem_slope': '°',
    'jrc_occurrence': '%', 'lst_temperature': '°C',
}
UAV_CLASSES = {
    '0': ('Sin datos', '#000000'),
    '1': ('Humedad aparente baja', '#d9e2ec'),
    '2': ('Humedad aparente media', '#f4c95d'),
    '3': ('Humedad aparente alta', '#168aad'),
}
YLGNBU = ['ffffd9', 'edf8b1', 'c7e9b4', '7fcdbb', '41b6c4',
          '1d91c0', '225ea8', '253494', '081d58']


def _flatten(v, limit=350):
    if isinstance(v,(dict,list)): return json.dumps(v,ensure_ascii=False,default=str)[:limit]
    return str(v)[:limit] if v is not None else 'Sin datos'


def generate_brief_report(data, folder, include_blender=True):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    path=folder/('POLARIS09_Informe_Breve_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.pdf')
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name='PT',parent=styles['Title'],fontSize=21,leading=25,textColor=colors.HexColor('#123B58'),spaceAfter=18))
    styles.add(ParagraphStyle(name='PH',parent=styles['Heading2'],fontSize=13,textColor=colors.HexColor('#0D6680'),spaceAfter=9))
    styles.add(ParagraphStyle(name='PB',parent=styles['BodyText'],fontSize=9,leading=13,spaceAfter=8))
    styles.add(ParagraphStyle(name='PN',parent=styles['BodyText'],fontSize=8,leading=11,textColor=colors.HexColor('#586879'),spaceAfter=8))
    story=[]
    def txt(x):return html.escape(_flatten(x,700))
    def h(title):story.append(Paragraph(txt(title),styles['PH']))
    def para(text):story.append(Paragraph(txt(text),styles['PB']))
    def table(rows):
        if not rows:return
        prepared=[[Paragraph(txt(x),styles['PN']) for x in row] for row in rows[:19]]
        widths=[(17.1/len(prepared[0]))*cm]*len(prepared[0])
        t=Table(prepared,colWidths=widths,repeatRows=1,hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#dcebf1')),
            ('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.3,colors.HexColor('#cad4df')),
            ('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6)]))
        story.append(t);story.append(Spacer(1,10))
    def picture(p, caption, max_height=10.3):
        if not p:return False
        p=Path(p)
        if not p.exists() or p.suffix.lower() not in ('.png','.jpg','.jpeg'):return False
        try:
            from PIL import Image as PILImage
            with PILImage.open(p) as im:w,ht=im.size
            factor=min(16.8*cm/w,max_height*cm/ht)
            story.append(KeepTogether([Image(str(p),width=w*factor,height=ht*factor),
                                       Paragraph(txt(caption),styles['PN'])]))
            return True
        except Exception:return False
    def map_legend(artifact):
        key=artifact.get('key')
        legend=artifact.get('legend') or MAP_LEGENDS.get(key,{})
        classes=legend.get('classes')
        if classes:
            heading=artifact.get('legend_title','Leyenda ESA WorldCover')
            story.append(Paragraph(f'<b>{txt(heading)}</b>',styles['PN']))
            entries=[]
            for code,(label,color) in classes.items():
                swatch=Drawing(10,10)
                swatch.add(Rect(0,0,9,9,fillColor=colors.HexColor(color),
                                strokeColor=colors.HexColor('#8b99a6'),strokeWidth=.35))
                entries.append([swatch,Paragraph(f'{txt(code)} · {txt(label)}',styles['PN'])])
            cells=[entries[i]+(entries[i+1] if i+1<len(entries) else ['', ''])
                   for i in range(0,len(entries),2)]
            grid=Table(cells,colWidths=[15,215,15,215],hAlign='LEFT')
            grid.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),
                                      ('TOPPADDING',(0,0),(-1,-1),0),
                                      ('BOTTOMPADDING',(0,0),(-1,-1),1),
                                      ('LEFTPADDING',(0,0),(-1,-1),0)]))
            story.append(grid)
        elif legend.get('palette') and 'min' in legend and 'max' in legend:
            palette=legend['palette']
            swatch=Drawing(150,12)
            width=150/len(palette)
            for i,color in enumerate(palette):
                swatch.add(Rect(i*width,1,width,10,fillColor=colors.HexColor('#'+color.lstrip('#')),
                                strokeColor=colors.HexColor('#8b99a6'),strokeWidth=.25))
            story.append(swatch)
            unit=artifact.get('unit',MAP_UNITS.get(key,'adimensional'))
            heading=artifact.get('legend_title','Escala de visualización')
            para(f"Leyenda · {heading}: {legend['min']} a {legend['max']} ({unit}), de izquierda a derecha.")
        else:
            para('Leyenda · Consulte la escala o las clases impresas en el mapa exportado.')
    def page(title,subtitle):
        if story:story.append(PageBreak())
        story.append(Paragraph(txt(title),styles['PT']))
        para(subtitle)
    aoi=data.get('aoi',{});res=data.get('results',{});art=data.get('artifacts',{})
    maps=[x for x in art.get('maps',[]) if isinstance(x,dict) and x.get('path') and Path(x['path']).is_file()]
    sim=data.get('simulacion_aoi') or {};drone=data.get('drone_moisture') or {};hydro=data.get('hydro_prospecting') or {}
    wc=res.get('esa_worldcover') or {}; rows=wc.get('area_by_class_estimate') or []
    page('POLARIS09 · Informe territorial','Resumen técnico de ocho páginas. Se incluyen únicamente los resultados disponibles de la ejecución.')
    table([['Dato','Valor'],['Proyecto',data.get('project_id','No registrado')],['Periodo',_flatten(data.get('period'))],
           ['Sistema de trabajo','WGS 84 / UTM 18S (EPSG:32718)'],['Archivos de área',_flatten(aoi.get('sources'))],
           ['Procesos ejecutados',', '.join(data.get('selected_processes',[]))]])
    para('El análisis no sustituye una inspección de campo. Los modelos de humedad y aptitud no constituyen mediciones directas.')
    page('1. Resultados principales','Lectura rápida de las evidencias disponibles.')
    table([['Componente','Resultado o estado'],['Cobertura del suelo',f'{len(rows)} clases reportadas' if rows else 'No calculada'],
           ['Simulación 0–2 m',f"{sim.get('area_aoi_ha','Sin dato')} ha" if sim else 'No ejecutada'],
           ['Fotografías UAV',f"{len(drone.get('images',[]))} imágenes analizadas" if drone else 'No analizadas'],
           ['Prospección hídrica','Ejecutada' if hydro else 'No ejecutada'],
           ['Asistente de IA',f"{len(data.get('ai_history',[]))} consultas" if data.get('ai_history') else 'Sin consultas guardadas']])
    para('Las recomendaciones dependen de la calidad, fecha y resolución de las capas utilizadas.')
    page('2. Área de estudio y cartografía','La leyenda indica las clases o el rango de visualización de la capa. Sistema de trabajo UTM 18S.')
    if maps:
        for artifact in maps:
            original=artifact['path']
            if picture(original, f"{artifact.get('title','Mapa')} · {artifact.get('scale_note','')}", 8):
                map_legend(artifact)
                try:
                    from polaris31_blender import render_map
                    rendered=render_map(original,folder/'Renders_Blender') if include_blender else {}
                    picture(rendered.get('imagen'),'Lámina de Blender del mismo mapa 2D. Usa la leyenda anterior.', 6)
                except Exception:pass
                break
    else:para('No se exportó ningún mapa cartográfico en esta ejecución.')
    table([['Indicador espacial','Dato'],['Superficie AOI',aoi.get('area_km2','No disponible')],['Fuentes',_flatten(aoi.get('sources'))]])
    page('3. Uso y cobertura del suelo','Áreas estimadas por clase; WorldCover 2021 no representa necesariamente el uso actual.')
    if rows:
        table([['Código','Cobertura','Hectáreas','%']]+[
            [r.get('code',''),r.get('class_name'),f"{r['area_ha']:.2f}",f"{r['percentage']:.2f}"]
            for r in rows[:13]])
        for key in ('legend','render_3d'):
            if picture(wc.get('report_assets',{}).get(key),'Superficie estimada por clase de cobertura (ha).', 8):break
    else:para('No hay clasificación WorldCover para el área. No se asignan superficies sin análisis.')
    page('4. Agua superficial y humedales','Se distinguen observaciones cartográficas de hipótesis de humedad subterránea.')
    if hydro:
        table([['Indicador','Resultado']]+[[k,_flatten(v,140)] for k,v in list(hydro.items()) if isinstance(v,(str,int,float))][:10])
    elif res.get('aquafinder_groundwater'):
        aqua=res['aquafinder_groundwater']
        table([['Indicador','Resultado'],['Índice AQUAFINDER (0–100)',aqua.get('score')],
               ['Clasificación',aqua.get('category')]])
        para(aqua.get('validation_warning',''))
    else:para('No se ejecutó la prospección hídrica en este proyecto.')
    para('La presencia de agua superficial o vegetación húmeda no confirma un acuífero ni su profundidad.')
    page('5. Imágenes de dron y asistente IA','Interpretación visual preliminar de la cobertura, suelo, vegetación y posibles malezas.')
    imgs=drone.get('images',[])
    if imgs:
        first=imgs[0]
        caption='Leyenda UAV · La barra indica la frecuencia de clasificación como indicio de humedad alta (0–100 %).'
        if picture(first.get('probability_map'),caption,8):
            map_legend({'legend':{'palette':YLGNBU,'min':0,'max':100},
                        'unit':'%','legend_title':'Frecuencia de clasificación alta'})
        else:
            if not picture(first.get('render_3d'),caption,8):
                if picture(first.get('map'),'Clasificación UAV RGB · Indicios visuales de humedad.',8):
                    map_legend({'legend':{'classes':UAV_CLASSES},'legend_title':'Leyenda UAV · Clasificación RGB'})
        table([['Archivo','Estado']]+[[x.get('file') or x.get('name') or 'Imagen', 'Analizada'] for x in imgs[:7]])
    else:para('No hay imágenes UAV analizadas para esta ejecución.')
    history=data.get('ai_history',[])
    if history:
        para('Última consulta: '+_flatten(history[-1].get('pregunta')))
        para('Respuesta ('+_flatten(history[-1].get('provider'))+'): '+_flatten(history[-1].get('answer'),600))
    else:para('No se registraron consultas del asistente para este informe.')
    page('6. Simulación y aptitud territorial','Simulación conceptual desde la superficie hasta 2 m. Parámetros de suelo hipotéticos.')
    if sim:
        table([['Uso propuesto','Puntaje indicativo','Área alta (ha)']]+[[v.get('nombre'),v.get('puntaje_medio_indicativo'),v.get('area_alta_ha')] for v in sim.get('aptitudes',{}).values()])
        caption='Leyenda de simulación · La barra indica humedad volumétrica superficial simulada (m³/m³).'
        if picture(sim.get('archivos',{}).get('mapa_superficial'),caption,8):
            if sim.get('theta_s') is not None:
                map_legend({'legend':{'palette':YLGNBU,'min':0,'max':sim['theta_s']},
                            'unit':'m³/m³','legend_title':'Humedad superficial simulada'})
        else:
            picture(sim.get('blender',{}).get('imagen'),
                    'Ilustración conceptual de Blender · Superficie esquemática sin escala de humedad medida.',8)
    else:para('La simulación AOI todavía no se ha ejecutado.')
    page('7. Recomendaciones y limitaciones','Prioridades para decisiones de campo y próximos pasos.')
    if sim and sim.get('aptitudes'):
        ranking=sorted(sim['aptitudes'].values(),key=lambda x:float(x.get('puntaje_medio_indicativo') or 0),reverse=True)
        para('Mayor puntaje exploratorio: '+', '.join(_flatten(x.get('nombre')) for x in ranking[:3])+'. No es una autorización de uso del terreno.')
    para('1. Verificar en campo la cobertura y las zonas aparentemente húmedas antes de intervenir.')
    para('2. Tomar muestras y medir humedad a varias profundidades para calibrar el modelo.')
    para('3. Confirmar especies de malezas mediante rasgos botánicos y, si es necesario, especialista.')
    para('4. Para infraestructura, incorporar geotecnia, estabilidad, drenaje y normativa aplicable.')
    para('5. Para cultivos, contrastar clima, pendiente, suelo, agua disponible y calendario agrícola.')
    para('Limitaciones: sin validación de campo; los modelos son exploratorios; las fechas y resoluciones difieren entre fuentes. No se atribuye certeza estadística no calculada.')
    def footer(canvas,doc):
        canvas.saveState();canvas.setFont('Helvetica',8);canvas.setFillColor(colors.HexColor('#64748b'))
        canvas.drawString(2*cm,1.2*cm,'POLARIS09 v31 · Informe ejecutivo')
        canvas.drawRightString(19*cm,1.2*cm,f'Página {doc.page} / 8')
        canvas.restoreState()
    doc=SimpleDocTemplate(str(path),pagesize=A4,leftMargin=2*cm,rightMargin=2*cm,
                          topMargin=2.0*cm,bottomMargin=2.0*cm,title='POLARIS09 · Informe breve')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return path

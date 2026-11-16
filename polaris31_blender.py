"""Render Blender de láminas cartográficas 2D; no crea relieve inexistente."""
from pathlib import Path
import os, shutil, subprocess, json


def find_blender():
    executable=shutil.which('blender')
    if executable:return executable
    if os.name=='nt':
        roots=[Path(os.environ.get('PROGRAMFILES',r'C:\Program Files'))/'Blender Foundation',
               Path(os.environ.get('LOCALAPPDATA',''))/'Programs'/'Blender Foundation']
        candidates=[p for root in roots if root.exists() for p in root.glob('**/blender.exe')]
        if candidates:return str(sorted(candidates,reverse=True)[0])
    return None


def render_map(image_path, output_dir, timeout=120):
    """Renderiza imagen cartográfica sobre plano en escena Blender; no simula elevación."""
    image_path=Path(image_path);output_dir=Path(output_dir)
    if not image_path.exists():return {'estado':'Imagen cartográfica inexistente'}
    exe=find_blender()
    if not exe:return {'estado':'Blender no encontrado','imagen':None}
    output_dir.mkdir(parents=True,exist_ok=True)
    target=output_dir/(image_path.stem+'_blender.png')
    project=output_dir/(image_path.stem+'_blender.blend')
    script=output_dir/(image_path.stem+'_blender.py')
    # Argumentos a Blender tras -- evitan interpolar rutas de usuario en código Python.
    script.write_text('''import bpy, sys
from mathutils import Vector
args=sys.argv[sys.argv.index('--')+1:]
source,target,project=args
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
img=bpy.data.images.load(source,check_existing=False)
img.pack()
ratio=img.size[0]/max(img.size[1],1)
bpy.ops.mesh.primitive_plane_add(size=2,location=(0,0,0))
plane=bpy.context.object;plane.name='Mapa georreferenciado (visualización 2D)';plane.scale=(ratio,1,1)
mat=bpy.data.materials.new('Textura del mapa');mat.use_nodes=True
nodes=mat.node_tree.nodes;links=mat.node_tree.links
tex=nodes.new('ShaderNodeTexImage');tex.image=img
shader=nodes.new('ShaderNodeEmission')
links.new(tex.outputs['Color'],shader.inputs['Color'])
links.new(shader.outputs['Emission'],nodes.get('Material Output').inputs['Surface'])
plane.data.materials.append(mat)
bpy.ops.object.camera_add(location=(0,-2.8,4.8));cam=bpy.context.object
cam.rotation_euler=(Vector((0,0,0))-cam.location).to_track_quat('-Z','Y').to_euler()
cam.data.type='ORTHO';cam.data.ortho_scale=max(4.8,ratio*3.3);bpy.context.scene.camera=cam
scene=bpy.context.scene
scene.render.engine='CYCLES'
scene.cycles.device='CPU'
scene.cycles.samples=1
scene.cycles.use_denoising=False
scene.cycles.max_bounces=0
scene.render.threads_mode='FIXED'
scene.render.threads=1
scene.view_settings.view_transform='Standard'
scene.render.resolution_x=720;scene.render.resolution_y=500;scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG';scene.render.filepath=target
bpy.ops.wm.save_as_mainfile(filepath=project)
bpy.ops.render.render(write_still=True)
''',encoding='utf-8')
    try:
        proc=subprocess.run([exe,'-b','--factory-startup','-t','1','--python-exit-code','2',
                             '--python',str(script),'--',str(image_path.resolve()),
                             str(target.resolve()),str(project.resolve())],capture_output=True,text=True,timeout=timeout)
        if proc.returncode!=0 or not target.is_file() or not target.stat().st_size:
            details='\n'.join((proc.stderr[-900:],proc.stdout[-900:])).strip()
            return {'estado':'Falló Blender','detalle':f'Salida {proc.returncode}: {details}'}
        return {'estado':'Render Blender completado','imagen':str(target),'proyecto':str(project)}
    except (OSError,subprocess.TimeoutExpired) as exc:return {'estado':'Blender no completó el render','detalle':str(exc)}


TERRAIN_SCRIPT = '''
import bpy, json, sys
from mathutils import Vector

mesh_path, texture_path, target = sys.argv[sys.argv.index('--')+1:]
with open(mesh_path, encoding='utf-8') as source:
    data = json.load(source)
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
mesh = bpy.data.meshes.new('Malla de elevación NASADEM')
mesh.from_pydata(data['vertices'], [], data['faces'])
mesh.update()
surface = bpy.data.objects.new('Relieve NASADEM',mesh)
bpy.context.collection.objects.link(surface)
uv = mesh.uv_layers.new(name='Retícula NASADEM')
for polygon in mesh.polygons:
    for index in polygon.loop_indices:
        uv.data[index].uv = data['uv'][mesh.loops[index].vertex_index]

material = bpy.data.materials.new('Elevación NASADEM')
material.use_nodes = True
nodes, links = material.node_tree.nodes, material.node_tree.links
texture = nodes.new('ShaderNodeTexImage')
texture.image = bpy.data.images.load(texture_path)
texture.interpolation = 'Closest'
emission = nodes.new('ShaderNodeEmission')
links.new(texture.outputs['Color'],emission.inputs['Color'])
links.new(emission.outputs['Emission'],nodes.get('Material Output').inputs['Surface'])
mesh.materials.append(material)

scene = bpy.context.scene
scene.world.color = (0.18,0.23,0.27)
target_center = Vector((0,0,data['height']/2))
bpy.ops.object.camera_add(location=target_center+Vector((2.8,-3.6,3.1)))
camera = bpy.context.object
camera.rotation_euler = (target_center-camera.location).to_track_quat('-Z','Y').to_euler()
camera.data.type = 'ORTHO'
camera.data.ortho_scale = 3.8
scene.camera = camera
scene.render.engine = 'CYCLES'
scene.cycles.device = 'CPU'
scene.cycles.samples = 3
scene.cycles.use_denoising = False
scene.cycles.max_bounces = 0
scene.render.threads_mode = 'FIXED'
scene.render.threads = 1
scene.view_settings.view_transform = 'Standard'
scene.render.resolution_x = 900
scene.render.resolution_y = 620
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.render.filepath = target
bpy.ops.render.render(write_still=True)
'''


def terrain_mesh(pixels, width, height):
    import numpy as np
    values = np.asarray(pixels,dtype=float)
    if values.ndim != 2 or min(values.shape) < 2:
        raise ValueError('Dibuja un área más grande para obtener relieve NASADEM.')
    valid = np.isfinite(values) & (values != -9999)
    if not valid.any():
        raise ValueError('El área no tiene elevaciones válidas en NASADEM.')
    rows, cols = values.shape
    low, high = float(values[valid].min()), float(values[valid].max())
    unit = 2/max(width,height,2*(high-low))
    vertices, coords, faces = [], [], []
    for row in range(rows):
        for col in range(cols):
            z = 2*(float(values[row,col])-low)*unit if valid[row,col] else 0
            vertices.append([(col/(cols-1)-.5)*width*unit,
                             (.5-row/(rows-1))*height*unit,z])
            coords.append([(col+.5)/cols,1-(row+.5)/rows])
    for row in range(rows-1):
        for col in range(cols-1):
            corners = [(row,col),(row+1,col),(row+1,col+1),(row,col+1)]
            face = [r*cols+c for r,c in corners if valid[r,c]]
            if len(face)>=3:
                faces.append(face)
    if not faces:
        raise ValueError('Dibuja un área que incluya más píxeles contiguos para crear el relieve.')
    return {'vertices':vertices,'uv':coords,'faces':faces,'height':2*(high-low)*unit}, low, high


def render_terrain(pixels, width, height, scale, folder, timeout=150):
    from web_gis import paint_sample
    from polaris31_legends import MAP_LEGENDS

    exe = find_blender()
    if not exe:
        raise RuntimeError('Blender no está instalado.')
    mesh, low, high = terrain_mesh(pixels,width,height)
    configuration, texture = folder/'terrain.json', folder/'elevacion.png'
    target, script = folder/'escena.png', folder/'terrain_blender.py'
    legend = MAP_LEGENDS['nasadem_elevation']
    configuration.write_text(json.dumps(mesh),encoding='utf-8')
    paint_sample(pixels,legend,texture,False,upscale=False)
    script.write_text(TERRAIN_SCRIPT,encoding='utf-8')
    proc = subprocess.run([exe,'-b','--factory-startup','-t','1','--python-exit-code','2',
                           '--python',str(script),'--',str(configuration),str(texture),str(target)],
                          capture_output=True,text=True,timeout=timeout)
    if proc.returncode or not target.is_file() or not target.stat().st_size:
        raise RuntimeError(f'Render Blender terminó con código {proc.returncode}: {proc.stderr[-1000:]}')
    return export_terrain(target,folder,legend,scale,low,high)


def export_terrain(target, folder, legend, scale, low, high):
    from PIL import Image, ImageDraw, ImageColor, ImageFont
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    with Image.open(target) as raw:
        scene = raw.convert('RGB')
    margin = 36
    sheet = Image.new('RGB',(scene.width+2*margin,scene.height+215),'white')
    draw = ImageDraw.Draw(sheet)

    def font(name,size):
        try:
            return ImageFont.truetype(name,size)
        except OSError:
            return ImageFont.load_default()

    title = font('DejaVuSans-Bold.ttf',34)
    body = font('DejaVuSans.ttf',18)
    draw.text((margin,23),'POLARIS09 | Relieve 3D NASADEM',font=title,fill='#163c53')
    sheet.paste(scene,(margin,78))
    y = 78+scene.height+18
    draw.text((margin,y),f'Elevación muestreada: {low:,.0f} a {high:,.0f} m · Muestreo: ~{scale} m · Escala vertical: 2×',
              font=body,fill='#22394a')
    stops = [ImageColor.getrgb('#'+color) for color in legend['palette']]
    pixels = 390
    for x in range(pixels):
        value = x*(len(stops)-1)/(pixels-1)
        index = min(int(value),len(stops)-2)
        weight = value-index
        color = tuple(round(a+(b-a)*weight) for a,b in zip(stops[index],stops[index+1]))
        draw.line((margin+x,y+39,margin+x,y+58),fill=color)
    draw.rectangle((margin,y+39,margin+pixels-1,y+58),outline='#7891a0')
    draw.text((margin,y+65),f"{legend['min']} m",font=body,fill='#22394a')
    draw.text((margin+pixels,y+65),f"{legend['max']} m",font=body,fill='#22394a',anchor='ra')
    draw.text((margin+pixels+18,y+37),'Elevación sobre el nivel del mar',font=body,fill='#22394a')
    jpg = folder/'POLARIS09_Relieve_NASADEM_3D.jpg'
    sheet.save(jpg,quality=92,optimize=True)
    pdf = folder/'POLARIS09_Relieve_NASADEM_3D.pdf'
    page = canvas.Canvas(str(pdf),pagesize=A4)
    page.setTitle('POLARIS09 · Relieve 3D NASADEM')
    page_w,page_h = A4
    img_w = page_w-36
    img_h = img_w*sheet.height/sheet.width
    page.drawImage(str(jpg),18,page_h-img_h-24,width=img_w,height=img_h)
    page.setFont('Helvetica',9)
    page.drawString(38,page_h-img_h-30,'Fuente: NASADEM HGT v001 · Google Earth Engine · Modelo renderizado en Blender')
    page.save()
    return jpg, pdf

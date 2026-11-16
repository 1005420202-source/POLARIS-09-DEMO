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

import argparse
import json
import os
import sys
import traceback
import importlib
import addon_utils
import bpy # API de Blender Python, disponible dentro del entorno ejecutor de Blender


# Creación del archivo JSON estructurado que Django espera como resultado del análisis de Blender
def write_report(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as report_file:
        json.dump(payload, report_file)


# Habilitación de addons necesarios para importación/exportación de formatos
# 3D en Blender, con manejo de versiones y compatibilidad
def enable_addon(module_name):
    if module_name in bpy.context.preferences.addons:
        return True
    try:
        addon_utils.enable(module_name, default_set=True, persistent=True)
    except Exception:
        try:
            bpy.ops.preferences.addon_enable(module=module_name)
        except Exception:
            pass

    if module_name in bpy.context.preferences.addons:
        return True

    try:
        module = importlib.import_module(module_name)
        if hasattr(module, 'register'):
            module.register()
    except Exception:
        return False

    return module_name in bpy.context.preferences.addons


# Obtención dinámica del operador de exportación glTF/GLB según la versión
# de la API activa en la instancia de Blender
def get_gltf_operator():
    if hasattr(bpy.ops.export_scene, 'gltf'):
        return bpy.ops.export_scene.gltf
    return None


# Función auxiliar para obtener las propiedades disponibles de un operador de Blender
def get_operator_props(operator):
    try:
        return set(operator.get_rna_type().properties.keys())
    except Exception:
        return set()


# Función de importación de archivos wavefront OBJ, con manejo de opciones
# según la versión del addon disponible
def import_obj(filepath):
    # Importación de archivos OBJ con Blender 4.0+, que utiliza un importador nativo optimizado en C++
    if hasattr(bpy.ops.wm, 'obj_import'):
        operator = bpy.ops.wm.obj_import
    else:
        # Respaldo para Blender 3.x, con base en el script de Python tradicional
        enable_addon('io_scene_obj')
        operator = bpy.ops.import_scene.obj

    props = get_operator_props(operator)
    kwargs = {'filepath': filepath}
    # Inyección condicional de parámetros para preservar texturas, materiales y jerarquías
    if 'use_image_search' in props:
        kwargs['use_image_search'] = True
    if 'import_mtl' in props:
        kwargs['import_mtl'] = True
    if 'use_materials' in props:
        kwargs['use_materials'] = True
    if 'use_split_objects' in props:
        kwargs['use_split_objects'] = True
    if 'use_split_groups' in props:
        kwargs['use_split_groups'] = True

    operator(**kwargs)


# Inyección de parámetros de optimización para asegurar que las mallas web mantengan
# colores, normales, tangentes y coordenadas UV en el visualizador Three.js
def apply_gltf_export_options(props, kwargs):
    if 'export_materials' in props:
        kwargs['export_materials'] = 'EXPORT'
    if 'export_colors' in props:
        kwargs['export_colors'] = True
    if 'export_texcoords' in props:
        kwargs['export_texcoords'] = True
    if 'export_normals' in props:
        kwargs['export_normals'] = True
    if 'export_tangents' in props:
        kwargs['export_tangents'] = True
    return kwargs


# Función de exportación a GLB para entornos web, con manejo de compatibilidad
# entre versiones de Blender y addons
def export_glb(output_path):
    enable_addon('io_scene_gltf2')
    operator = get_gltf_operator()
    if not operator:
        raise RuntimeError('No se encontró el exportador glTF en Blender.')

    candidate_path = f"{os.path.splitext(output_path)[0]}.glb"
    os.makedirs(os.path.dirname(candidate_path), exist_ok=True)

    last_error = None
    props = get_operator_props(operator)
    attempts = []
    if 'export_format' in props:
        attempts.append({'export_format': 'GLB'})
    attempts.append({})

    for extra_kwargs in attempts:
        kwargs = {'filepath': candidate_path, **extra_kwargs}
        if 'export_apply' in props:
            kwargs['export_apply'] = True
        kwargs = apply_gltf_export_options(props, kwargs)
        try:
            result = operator(**kwargs)
            if isinstance(result, set) and 'FINISHED' not in result:
                raise RuntimeError(f"Exportación cancelada: {result}")
            if not os.path.exists(candidate_path):
                raise RuntimeError('El exportador no generó el archivo esperado.')
            export_format = extra_kwargs.get('export_format', 'AUTO')
            return candidate_path, export_format
        except Exception as exc:
            last_error = exc
            continue

    if last_error:
        raise RuntimeError(f"No se pudo exportar el modelo a GLB. Detalle: {last_error}") from last_error
    raise RuntimeError('No se pudo exportar el modelo a GLB.')


# Función principal de importación de modelos 3D, con soporte para múltiples formatos
def import_model(input_path):
    extension = os.path.splitext(input_path)[1].lower()

    # Caso nativo: carga directa de la BD de bloques de Blender
    if extension == '.blend':
        bpy.ops.wm.open_mainfile(filepath=input_path)
        return

    # Limpieza de la escena
    bpy.ops.wm.read_factory_settings(use_empty=True)

    # Evaluación y enrutamiento según formato de manufactura/modelado
    if extension == '.obj':
        import_obj(input_path)
    elif extension in {'.glb', '.gltf'}:
        if not hasattr(bpy.ops.import_scene, 'gltf'):
            enable_addon('io_scene_gltf2')
        bpy.ops.import_scene.gltf(filepath=input_path)
    elif extension == '.stl':
        if hasattr(bpy.ops.wm, 'stl_import'):
            bpy.ops.wm.stl_import(filepath=input_path)
        else:
            enable_addon('io_mesh_stl')
            bpy.ops.import_mesh.stl(filepath=input_path)
    else:
        raise ValueError(f"Unsupported extension: {extension}")


# Función de conteo de submallados
# definidos como el número de objetos de tipo MESH en la escena
def count_mesh_objects():
    return len([obj for obj in bpy.context.scene.objects if obj.type == 'MESH'])


# Función principal con la lógica secuencial del pipeline

def main(args):
    import_model(args.input)
    submesh_count = count_mesh_objects()

    exported = False
    output_path = None
    export_format = None

    # Verificación de cumplimiento del límite de submallados
    if args.max_submeshes <= 0 or submesh_count <= args.max_submeshes:
        output_path, export_format = export_glb(args.output)
        exported = True

    # Generación del reporte final de sincronización de datos con Django
    write_report(args.report, {
        'submesh_count': submesh_count,
        'exported': exported,
        'output_path': output_path if exported else None,
        'export_format': export_format if exported else None,
    })


if __name__ == '__main__':
    # Recuperación y parseo de argumentos de línea de comandos (CLI)
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--max-submeshes', type=int, default=10)

    # Pase de argumentos a Blender de '--' para evitar conflictos con sus propios parámetros de ejecución
    parsed_args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])

    try:
        main(parsed_args)
    except Exception as exc:
        # Captura de excepciones imprevistas
        write_report(parsed_args.report, {
            'submesh_count': 0,
            'exported': False,
            'error': str(exc),
            'traceback': traceback.format_exc(),
        })
        raise

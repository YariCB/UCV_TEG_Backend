import argparse
import json
import os
import sys
import traceback
import importlib
import addon_utils
import bpy


def write_report(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as report_file:
        json.dump(payload, report_file)


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


def get_gltf_operator():
    if hasattr(bpy.ops.export_scene, 'gltf'):
        return bpy.ops.export_scene.gltf
    return None


def get_operator_props(operator):
    try:
        return set(operator.get_rna_type().properties.keys())
    except Exception:
        return set()


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


def import_model(input_path):
    extension = os.path.splitext(input_path)[1].lower()

    if extension == '.blend':
        bpy.ops.wm.open_mainfile(filepath=input_path)
        return

    bpy.ops.wm.read_factory_settings(use_empty=True)

    if extension == '.obj':
        if hasattr(bpy.ops.wm, 'obj_import'):
            bpy.ops.wm.obj_import(filepath=input_path)
        else:
            enable_addon('io_scene_obj')
            bpy.ops.import_scene.obj(filepath=input_path)
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


def count_mesh_objects():
    return len([obj for obj in bpy.context.scene.objects if obj.type == 'MESH'])


def main(args):
    import_model(args.input)
    submesh_count = count_mesh_objects()

    exported = False
    output_path = None
    export_format = None
    if args.max_submeshes <= 0 or submesh_count <= args.max_submeshes:
        output_path, export_format = export_glb(args.output)
        exported = True

    write_report(args.report, {
        'submesh_count': submesh_count,
        'exported': exported,
        'output_path': output_path if exported else None,
        'export_format': export_format if exported else None,
    })


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--max-submeshes', type=int, default=10)
    parsed_args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])

    try:
        main(parsed_args)
    except Exception as exc:
        write_report(parsed_args.report, {
            'submesh_count': 0,
            'exported': False,
            'error': str(exc),
            'traceback': traceback.format_exc(),
        })
        raise

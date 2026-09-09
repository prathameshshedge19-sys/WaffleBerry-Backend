"""Isolated native portrait child. No settings, database or storage credentials.

Linux x86-64 only; Landlock limits filesystem access and seccomp denies network
and process-control syscalls BEFORE native imports. Failure is terminal. The
parent enforces wall time and cleanup; deployment additionally caps the cgroup.
"""
from __future__ import annotations

import base64
import ctypes
import errno
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import sys
import time

MODEL_SHA256 = '64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff'


def confine(extra_read=()):
    if sys.platform != 'linux' or platform.machine() != 'x86_64':
        raise RuntimeError('visual_native_isolation_unavailable')
    import resource
    # Writable confinement must never accidentally grant a shared/broad root.
    work = Path.cwd()
    info = work.stat()
    if (not work.name.startswith('l19-native-') or info.st_uid != os.getuid()
            or info.st_mode & 0o077):
        raise RuntimeError('visual_native_isolation_unavailable')
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (110, 110))
    resource.setrlimit(resource.RLIMIT_FSIZE, (4 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024,) * 2)
    os.umask(0o077)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise RuntimeError('visual_native_isolation_unavailable')
    # ABI >=3: execute/write/read/remove/create/refer/truncate all mediated.
    abi = libc.syscall(444, 0, 0, 1)
    if abi < 3:
        raise RuntimeError('visual_native_isolation_unavailable')
    class Rules(ctypes.Structure):
        _fields_ = [('access', ctypes.c_uint64)]
    class Beneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [('access', ctypes.c_uint64), ('fd', ctypes.c_int32)]
    handled = (1 << 15) - 1
    rules = Rules(handled)
    fd = libc.syscall(444, ctypes.byref(rules), ctypes.sizeof(rules), 0)
    if fd < 0:
        raise RuntimeError('visual_native_isolation_unavailable')
    try:
        reads = [Path(sys.prefix) / 'lib', Path(sys.base_prefix) / 'lib',
                 Path('/usr/lib'), Path('/lib'), Path(__file__).resolve().parents[1],
                 Path('/etc/ld.so.cache'), Path('/dev/urandom'), Path('/dev/null'),
                 Path('/proc/self/status'), Path('/proc/self/stat'), Path('/proc/cmdline'),
                 Path('/proc/cpuinfo'), Path('/sys/devices/system/cpu'),
                 *(Path(p) for p in extra_read if p)]
        for path, writable in [(p, False) for p in reads] + [(Path.cwd(), True)]:
            if not path.exists():
                continue
            access = (4 | 8) if path.is_dir() else 4  # READ_FILE/READ_DIR
            if path == Path('/dev/null'):
                access |= 2
            if writable:
                access |= 2 | 16 | 32 | 128 | 256 | 4096 | 8192 | 16384
            path_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = Beneath(access, path_fd)
                if libc.syscall(445, fd, 1, ctypes.byref(rule), 0) != 0:
                    raise RuntimeError('visual_native_isolation_unavailable')
            finally:
                os.close(path_fd)
        if libc.syscall(446, fd, 0) != 0:
            raise RuntimeError('visual_native_isolation_unavailable')
    finally:
        os.close(fd)
    sec = ctypes.CDLL('libseccomp.so.2', use_errno=True)
    sec.seccomp_init.argtypes = [ctypes.c_uint32]
    sec.seccomp_init.restype = ctypes.c_void_p
    sec.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    sec.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    sec.seccomp_load.argtypes = [ctypes.c_void_p]
    sec.seccomp_release.argtypes = [ctypes.c_void_p]
    ctx = sec.seccomp_init(0x7fff0000)
    if not ctx:
        raise RuntimeError('visual_native_isolation_unavailable')
    try:
        denied = ('socket socketpair connect bind listen accept accept4 sendto sendmsg sendmmsg '
                  'recvfrom recvmsg recvmmsg ptrace process_vm_readv process_vm_writev '
                  'mount umount2 unshare setns execve execveat fork vfork '
                  'io_uring_setup bpf keyctl add_key request_key').split()
        for name in denied:
            number = sec.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and sec.seccomp_rule_add(ctx, 0x50000 | errno.EPERM, number, 0) != 0:
                raise RuntimeError('visual_native_isolation_unavailable')
        if sec.seccomp_load(ctx) != 0:
            raise RuntimeError('visual_native_isolation_unavailable')
    finally:
        sec.seccomp_release(ctx)


def build_rig(png, landmarks, request_digest, *, landmark_grid=False):
    """Landmark-positioned restrained deformation; no recognition/expressions.

    A fixed image grid preserves the full approved crop. Source-measured eye/lip
    patches position smooth local displacements; no new teeth/background pixels.
    The bounds intentionally limit eyelid motion rather than claiming full blink
    closure, which would collapse eyelid triangles in this topology.
    """
    from PIL import Image
    from app.services.visual_provider import (
        VisualAssetBundle, VisualBundleError, _encode_png, _json_bytes, validate_bundle,
    )
    if len(landmarks) < 468 or any(not math.isfinite(v) for p in landmarks for v in p):
        raise ValueError('visual_needs_recrop')
    points = landmarks
    if any(not 0 <= v <= 1 for p in points for v in p):
        raise ValueError('visual_needs_recrop')
    eye_left = (33, 133, 159, 145)
    eye_right = (362, 263, 386, 374)
    mouth = (61, 291, 13, 14)
    # Reject severe pose/occlusion: both eye spans and mouth must be usable.
    if any(abs(points[a][0] - points[b][0]) < .035 for a,b in ((33,133),(362,263),(61,291))):
        raise ValueError('visual_needs_recrop')
    face_height = abs(points[152][1] - points[10][1])
    if not .2 <= face_height <= .95:
        raise ValueError('visual_needs_recrop')
    centers = {}
    patches = {}
    for name, indices in [('mouth', mouth), ('blink_left', eye_left), ('blink_right', eye_right)]:
        selected = [points[i] for i in indices]
        cx = sum(p[0] for p in selected) / len(selected)
        cy = sum(p[1] for p in selected) / len(selected)
        span = max(p[0] for p in selected) - min(p[0] for p in selected)
        rx = max(.055, span * .8)
        ry = .055 if name != 'mouth' else min(.12, face_height * .2)
        if not (rx < cx < 1-rx and ry < cy < 1-ry):
            raise ValueError('visual_needs_recrop')
        centers[name] = (cx, cy, rx, ry)
        patches[name] = [cx-rx, cy-ry, 2*rx, 2*ry]
    # Keep the old grid where usable. If it misses eye/lip controls, place rows
    # through measured features instead of asking the owner to shift the photo.
    def axis(index):
        anchors=sorted({round(c[index]+delta*c[index+2]/3,9)
                        for c in centers.values() for delta in (-1,0,1)})
        # Minimum spacing preserves triangle area under bounded deformation.
        fixed=[]
        for value in anchors:
            if not fixed or value-fixed[-1]>=.012: fixed.append(value)
        return sorted(fixed+[i/12 for i in range(13)
                             if all(abs(i/12-a)>=.012 for a in fixed)])
    xs,ys=(axis(0),axis(1)) if landmark_grid else ([i/21 for i in range(22)],)*2
    vertices = [[x,y] for y in ys for x in xs]
    triangles = []
    columns=len(xs)
    for y in range(len(ys)-1):
        for x in range(columns-1):
            a = y*columns+x
            triangles.extend(([a,a+1,a+columns+1], [a,a+columns+1,a+columns]))
    movements = {name: [0.] * len(vertices) for name in centers}
    for i, (vx,vy) in enumerate(vertices):
        active = []
        for name, (cx,cy,rx,ry) in centers.items():
            dx,dy = (vx-cx)/rx, (vy-cy)/ry
            if abs(dx)<1 and abs(dy)<1 and 0<vx<1 and 0<vy<1:
                weight = math.cos(dx*math.pi/2)**2 * math.cos(dy*math.pi/2)**2
                active.append((name, weight))
        if len(active)>1:
            raise ValueError('visual_needs_recrop')
        if active:
            name, weight = active[0]
            amplitude = min(.006, face_height*.01) if name == 'mouth' else .003
            if name != 'mouth':
                # Upper and lower source eyelid regions move toward their
                # measured center, not a translated rectangular eye overlay.
                amplitude *= (centers[name][1]-vy)/centers[name][3]
            movements[name][i] = round(amplitude*weight, 9)
    if any(max(map(abs, values)) < .0002 for values in movements.values()):
        if not landmark_grid:
            return build_rig(png,landmarks,request_digest,landmark_grid=True)
        raise ValueError('visual_needs_recrop')
    with Image.open(io.BytesIO(png)) as image:
        image.load()
        if image.size != (512,512):
            raise ValueError('visual_image_dimensions')
        atlas = _encode_png(image)
        with image.resize((256,256), Image.Resampling.LANCZOS) as small:
            poster = _encode_png(small)
            if len(poster) > 100 * 1024:
                # Bounded thumbnail compression only; atlas retains approved
                # source pixels. Convert back to RGB for the fixed PNG schema.
                with small.quantize(colors=256, dither=Image.Dither.NONE) as reduced:
                    with reduced.convert('RGB') as rgb:
                        poster = _encode_png(rgb)
    rig = dict(recipe_version='portrait_2d_v1', topology_version=1, fake_only=False,
        request_identity_sha256=request_digest, atlas_sha256=hashlib.sha256(atlas).hexdigest(),
        poster_sha256=hashlib.sha256(poster).hexdigest(), atlas_size=[512,512], crop_rect=[0,0,512,512],
        patches=patches, vertices=vertices, uvs=vertices, triangles=triangles, deformations=movements)
    bundle = VisualAssetBundle(dict(poster=poster, texture_atlas=atlas, rig=_json_bytes(rig)))
    validate_bundle(bundle)
    return bundle


def auto_frame(png, landmarks, padding=2.0):
    """Frame the sole detected face with headroom; never choose among people."""
    from PIL import Image
    if len(landmarks) < 468 or any(not math.isfinite(v) or not 0 <= v <= 1 for p in landmarks for v in p):
        raise ValueError('visual_needs_recrop')
    xs, ys = [p[0] for p in landmarks], [p[1] for p in landmarks]
    height = max(ys)-min(ys)
    if height < .045:
        raise ValueError('visual_needs_recrop')
    edge = min(1., max(height * padding, (max(xs)-min(xs)) * padding))
    x = min(max(0., (min(xs)+max(xs)-edge)/2), 1-edge)
    y = min(max(0., (min(ys)+max(ys)-edge)/2 + height*.08), 1-edge)
    with Image.open(io.BytesIO(png)) as image:
        if image.size != (1024, 1024):
            raise ValueError('visual_image_dimensions')
        with image.resize((512,512), Image.Resampling.LANCZOS,
                          box=(x*1024,y*1024,(x+edge)*1024,(y+edge)*1024)) as framed:
            output=io.BytesIO(); framed.save(output,format='PNG')
    return output.getvalue(), [[(px-x)/edge,(py-y)/edge] for px,py in landmarks]


def detect_points(detector, image, mp, np, *, auto_fit):
    """Whole-photo detection, then bounded overlapping views for a distant face.

    Views are used only when the whole image found none. Distinct detections
    remain ambiguous; never silently select a person from a group.
    """
    result=detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB,data=np.array(image.convert('RGB'))))
    faces=[[[p.x,p.y] for p in face] for face in result.face_landmarks]
    if faces or not auto_fit: return faces
    from PIL import Image
    unique=[]
    for x,y in ((0,0),(.4,0),(0,.4),(.4,.4)):
        with image.resize((1024,1024),Image.Resampling.LANCZOS,
                          box=(x*image.width,y*image.height,(x+.6)*image.width,(y+.6)*image.height)) as view:
            result=detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB,data=np.array(view.convert('RGB'))))
        for face in result.face_landmarks:
            points=[[x+p.x*.6,y+p.y*.6] for p in face]
            bounds=(min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points))
            def same(previous):
                a=previous[0];intersection=max(0,min(a[2],bounds[2])-max(a[0],bounds[0]))*max(0,min(a[3],bounds[3])-max(a[1],bounds[1]))
                union=(a[2]-a[0])*(a[3]-a[1])+(bounds[2]-bounds[0])*(bounds[3]-bounds[1])-intersection
                return union>0 and intersection/union>.4
            if not any(same(previous) for previous in unique): unique.append((bounds,points))
            if len(unique)>1: return [item[1] for item in unique]
    return [item[1] for item in unique]


def main():
    import resource
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, 9, 0, 0, 0) != 0 or os.getppid() != int(sys.argv[3]):
        raise RuntimeError('visual_native_isolation_unavailable')
    # Trusted parent supplies the pinned provisioned path, never an API path.
    model_path = Path(sys.argv[1]).resolve(strict=True)
    model = model_path.read_bytes()
    if hashlib.sha256(model).hexdigest() != MODEL_SHA256:
        raise ValueError('visual_model_mismatch')
    payload = json.loads(sys.stdin.buffer.read(3*1024*1024 + 1))
    png = base64.b64decode(payload['png'], validate=True)
    auto_fit = payload.get('auto_fit', False)
    if type(auto_fit) is not bool:
        raise ValueError('visual_request_invalid')
    if len(png)>2*1024*1024 or len(payload['request_digest']) != 64:
        raise ValueError('visual_request_invalid')
    confine([sys.argv[2]] if sys.argv[2] else [])
    # Imports occur only after confinement, including telemetry initialization.
    import mediapipe as mp
    import numpy as np
    from PIL import Image
    if mp.__version__ != '1.0.1':
        raise ValueError('visual_model_mismatch')
    before = time.monotonic()
    opts = mp.tasks.vision.FaceLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_buffer=model, delegate=mp.tasks.BaseOptions.Delegate.CPU),
        num_faces=2, output_face_blendshapes=False, output_facial_transformation_matrixes=False)
    with mp.tasks.vision.FaceLandmarker.create_from_options(opts) as detector:
        with Image.open(io.BytesIO(png)) as image:
            if image.size != ((1024,1024) if auto_fit else (512,512)):
                raise ValueError('visual_image_dimensions')
            faces = detect_points(detector,image,mp,np,auto_fit=True)
    if len(faces) != 1:
        raise ValueError('visual_needs_recrop')
    points = faces[0]
    if not auto_fit:
        try:
            bundle=build_rig(png,points,payload['request_digest'])
        except ValueError as error:
            if str(error)!='visual_needs_recrop': raise
            # A detected single face inside the owner's frame can still be too
            # distant for the rig. Frame it automatically inside that selected
            # region; never reset to, or select a person outside, the original.
            with Image.open(io.BytesIO(png)) as selected:
                with selected.resize((1024,1024),Image.Resampling.LANCZOS) as enlarged:
                    output=io.BytesIO();enlarged.save(output,format='PNG');png=output.getvalue()
            auto_fit=True
    if auto_fit:
        # Fixed-grid alignment can make a usable face miss an eye/lip control.
        # Try bounded deterministic framing alternatives, retaining full geometry
        # validation rather than lowering the usable-motion threshold.
        for padding in (2.0, 1.8, 1.6, 2.2):
            framed, adjusted = auto_frame(png, points, padding)
            try:
                bundle = build_rig(framed, adjusted, payload['request_digest'])
                break
            except ValueError as error:
                if str(error) != 'visual_needs_recrop':
                    raise
        else:
            raise ValueError('visual_needs_recrop')
    print(json.dumps({'assets':{k:base64.b64encode(v).decode() for k,v in bundle.assets.items()},
        'seconds':time.monotonic()-before,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    try:
        main()
    except Exception as exc:
        code = str(exc) if str(exc) in {'visual_needs_recrop','visual_model_mismatch',
            'visual_image_dimensions','visual_native_isolation_unavailable'} else 'visual_native_failed'
        print(json.dumps({'error':code}))

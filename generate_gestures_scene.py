# # """
# # generate_gestures.py  --  synthetic foot-gesture clip generator (Blender 5.x)
# #
# # Gestures match the recorded reference:
# #   TAP   : toe lifts UP, heel planted        -> pitch foot up about the ankle
# #   SWIPE : heel planted, toe pivots outward  -> yaw foot about world-vertical
# #   MOVE  : ONE foot relocates, other stays   -> abduct that leg at the hip
# #
# # Robustness choices (these fix the recurring pain):
# #  * Motions are defined in WORLD space (e.g. "rotate about vertical") and
# #    converted to each bone's local frame, so we never hand-guess bone axes.
# #  * left/right LABELS come from projecting the toe into the camera and reading
# #    which way it moved ON SCREEN -- so labels always match your detector's view.
# #  * each clip = REST (calibration) -> eased gesture -> SETTLE, framed on the feet.
# #
# # Run:
# #   blender --background --python generate_gestures.py -- --fbx pete.fbx --out ./synth --n 60
# # Then run YOUR detector on the mp4s; label each window by the clip's .json.
# # """
# #
# # import bpy, sys, os, math, random
# # from mathutils import Vector, Quaternion, Matrix
# #
# # # ----------------------------- args -----------------------------------------
# # argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
# # import argparse, json
# # ap = argparse.ArgumentParser()
# # ap.add_argument("--fbx", required=True)
# # ap.add_argument("--out", default="./synth")
# # ap.add_argument("--n", type=int, default=60)
# # ap.add_argument("--fps", type=int, default=30)
# # ap.add_argument("--res", default="848x478")
# # ap.add_argument("--scene", choices=["industrial", "plain", "hdri"], default="industrial")
# # ap.add_argument("--hdri-dir", default="", help="folder of Poly Haven .hdr/.exr (enables hdri scene)")
# # ap.add_argument("--floor-tex-dir", default="", help="folder with a Poly Haven floor PBR set")
# # ap.add_argument("--seed", type=int, default=0)
# # args = ap.parse_args(argv)
# # random.seed(args.seed)
# # os.makedirs(args.out, exist_ok=True)
# # RES_W, RES_H = (int(x) for x in args.res.split("x"))
# # PFX = "mixamorig1:"
# # WORLD_UP = Vector((0, 0, 1))
# # GESTURES = ["tap", "swipe", "move"]     # left/right decided by measured direction
# #
# #
# # # ----------------------------- helpers --------------------------------------
# # def clean_scene():
# #     bpy.ops.wm.read_factory_settings(use_empty=True)
# #
# #
# # def import_rig(path):
# #     bpy.ops.import_scene.fbx(filepath=path, automatic_bone_orientation=True)
# #     arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
# #     if arm.animation_data:
# #         arm.animation_data_clear()
# #     return arm
# #
# #
# # def bw(arm, name):
# #     """World position of a bone head."""
# #     return arm.matrix_world @ arm.pose.bones[PFX + name].head
# #
# #
# # def rest_world_3x3(arm, name):
# #     pb = arm.pose.bones[PFX + name]
# #     return (arm.matrix_world @ pb.matrix).to_3x3()
# #
# #
# # def make_mat(name, color, rough=0.8, metal=0.0):
# #     m = bpy.data.materials.new(name)
# #     try:
# #         m.use_nodes = True
# #         bsdf = m.node_tree.nodes.get("Principled BSDF")
# #         if bsdf:
# #             bsdf.inputs["Base Color"].default_value = (*color, 1.0)
# #             if "Roughness" in bsdf.inputs:
# #                 bsdf.inputs["Roughness"].default_value = rough
# #             if "Metallic" in bsdf.inputs:
# #                 bsdf.inputs["Metallic"].default_value = metal
# #     except Exception:
# #         try:
# #             m.diffuse_color = (*color, 1.0)
# #         except Exception:
# #             pass
# #     return m
# #
# #
# # def _box(size, loc, rot, mat):
# #     bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
# #     o = bpy.context.active_object
# #     o.scale = size; o.rotation_euler = rot
# #     o.data.materials.append(mat)
# #     return o
# #
# #
# # def _cyl(r, h, loc, rot, mat):
# #     bpy.ops.mesh.primitive_cylinder_add(radius=r, depth=h, location=loc)
# #     o = bpy.context.active_object
# #     o.rotation_euler = rot
# #     o.data.materials.append(mat)
# #     return o
# #
# #
# # def add_lights(specs):
# #     """specs: list of (location, energy). Returns light objects with base values."""
# #     lights = []
# #     for loc, e in specs:
# #         l = bpy.data.lights.new("L", "AREA"); l.energy = e; l.size = 4
# #         o = bpy.data.objects.new("L", l); o.location = loc
# #         bpy.context.collection.objects.link(o)
# #         o["e0"] = e
# #         o["lx"], o["ly"], o["lz"] = loc
# #         lights.append(o)
# #     return lights
# #
# #
# # def randomize_lights(lights):
# #     """Per-clip lighting variation (energy, position, slight color temperature)."""
# #     for o in lights:
# #         o.data.energy = o["e0"] * random.uniform(0.65, 1.4)
# #         o.location = (o["lx"] + random.uniform(-0.6, 0.6),
# #                       o["ly"] + random.uniform(-0.6, 0.6),
# #                       o["lz"] + random.uniform(-0.3, 0.3))
# #         t = random.uniform(0.0, 1.0)                      # warm..cool tint
# #         o.data.color = (1.0, 0.93 + 0.07 * t, 0.85 + 0.15 * t)
# #
# #
# # def build_plain():
# #     bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))
# #     bpy.context.active_object.data.materials.append(
# #         make_mat("floor", (0.62, 0.5, 0.4), rough=0.9))
# #     return add_lights([((3, -3, 5), 1600), ((-3, -2, 3), 600)])
# #
# #
# # def build_industrial():
# #     """Concrete floor, metal walls, scattered props -- all BEHIND/around the
# #     avatar (camera is in front at -Y), feet area kept clear."""
# #     concrete = make_mat("concrete", (0.34, 0.34, 0.36), rough=0.95)
# #     wall = make_mat("wall", (0.40, 0.42, 0.45), rough=0.7, metal=0.4)
# #     steel = make_mat("steel", (0.55, 0.56, 0.58), rough=0.4, metal=0.9)
# #     crate = make_mat("crate", (0.45, 0.33, 0.18), rough=0.8)
# #     barrel_b = make_mat("barrel_b", (0.15, 0.30, 0.55), rough=0.5, metal=0.3)
# #     barrel_r = make_mat("barrel_r", (0.55, 0.18, 0.15), rough=0.5, metal=0.3)
# #     hazard = make_mat("hazard", (0.85, 0.7, 0.05), rough=0.7)
# #
# #     bpy.ops.mesh.primitive_plane_add(size=24, location=(0, 0, 0))
# #     bpy.context.active_object.data.materials.append(concrete)
# #
# #     # room: back wall (+Y) and side walls (the camera at -Y sees these behind)
# #     _box((12, 0.2, 6), (0, 4.0, 1.5), (0, 0, 0), wall)
# #     _box((0.2, 12, 6), (4.0, 0, 1.5), (0, 0, 0), wall)
# #     _box((0.2, 12, 6), (-4.0, 0, 1.5), (0, 0, 0), wall)
# #
# #     # props behind / to the sides (clear zone ~1.3m around the feet)
# #     _box((0.8, 0.8, 0.8), (1.9, 2.6, 0.4), (0, 0, 0.3), crate)
# #     _box((0.8, 0.8, 0.8), (1.9, 2.6, 1.2), (0, 0, 0.25), crate)
# #     _box((0.9, 0.9, 0.5), (-2.0, 2.2, 0.25), (0, 0, -0.2), crate)
# #     _cyl(0.32, 0.95, (-1.5, 3.0, 0.48), (0, 0, 0), barrel_b)
# #     _cyl(0.32, 0.95, (-0.9, 3.2, 0.48), (0, 0, 0), barrel_r)
# #     _cyl(0.12, 5.0, (3.4, 0.0, 2.2), (math.radians(90), 0, 0), steel)   # wall pipe
# #     _cyl(0.12, 5.0, (3.7, 0.0, 1.6), (math.radians(90), 0, 0), steel)
# #     _box((1.2, 0.12, 0.12), (1.0, 1.6, 0.02), (0, 0, 0.5), hazard)      # floor stripe
# #     _box((1.2, 0.12, 0.12), (-1.2, 1.7, 0.02), (0, 0, -0.4), hazard)
# #
# #     return add_lights([((2.5, -1.0, 3.0), 1400), ((-2.5, 0.5, 3.0), 1000),
# #                        ((0.0, 2.5, 3.2), 800)])
# #
# #
# # def list_hdris(d):
# #     import glob
# #     return sorted(glob.glob(os.path.join(d, "*.hdr")) +
# #                   glob.glob(os.path.join(d, "*.exr")))
# #
# #
# # def set_world_hdri(path, rot_z):
# #     """Poly Haven HDRI as the world: lighting + photographic backdrop in one.
# #     Random Z rotation per clip varies which part sits behind the feet."""
# #     w = bpy.context.scene.world or bpy.data.worlds.new("W")
# #     bpy.context.scene.world = w
# #     try:
# #         w.use_nodes = True
# #     except Exception:
# #         pass
# #     nt = w.node_tree
# #     nt.nodes.clear()
# #     out = nt.nodes.new("ShaderNodeOutputWorld")
# #     bg = nt.nodes.new("ShaderNodeBackground")
# #     env = nt.nodes.new("ShaderNodeTexEnvironment")
# #     env.image = bpy.data.images.load(path, check_existing=True)
# #     mp = nt.nodes.new("ShaderNodeMapping")
# #     mp.inputs["Rotation"].default_value[2] = rot_z
# #     tc = nt.nodes.new("ShaderNodeTexCoord")
# #     nt.links.new(tc.outputs["Generated"], mp.inputs["Vector"])
# #     nt.links.new(mp.outputs["Vector"], env.inputs["Vector"])
# #     nt.links.new(env.outputs["Color"], bg.inputs["Color"])
# #     nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
# #
# #
# # def _find_map(d, *keys):
# #     import glob
# #     files = [f for ext in ("png", "jpg", "jpeg", "exr", "tif")
# #              for f in glob.glob(os.path.join(d, "*." + ext))]
# #     for f in files:
# #         n = os.path.basename(f).lower()
# #         if any(k in n for k in keys):
# #             return f
# #     return None
# #
# #
# # def build_floor_textured(tex_dir, tiling=8.0):
# #     """Floor from a Poly Haven PBR set (auto-detects diff/rough/normal maps).
# #     Falls back to flat concrete if no diffuse map is found."""
# #     bpy.ops.mesh.primitive_plane_add(size=24, location=(0, 0, 0))
# #     floor = bpy.context.active_object
# #     diff = _find_map(tex_dir, "diff", "albedo", "_col", "color")
# #     rough = _find_map(tex_dir, "rough", "_arm")
# #     norm = _find_map(tex_dir, "nor_gl", "normal", "_nor")
# #     if not diff:
# #         floor.data.materials.append(make_mat("concrete", (0.34, 0.34, 0.36), 0.95))
# #         return floor
# #     m = bpy.data.materials.new("floor_pbr")
# #     m.use_nodes = True
# #     nt = m.node_tree; nt.nodes.clear()
# #     out = nt.nodes.new("ShaderNodeOutputMaterial")
# #     bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
# #     tc = nt.nodes.new("ShaderNodeTexCoord")
# #     mp = nt.nodes.new("ShaderNodeMapping")
# #     mp.inputs["Scale"].default_value = (tiling, tiling, tiling)
# #     nt.links.new(tc.outputs["UV"], mp.inputs["Vector"])
# #
# #     def tex(path, non_color):
# #         t = nt.nodes.new("ShaderNodeTexImage")
# #         t.image = bpy.data.images.load(path, check_existing=True)
# #         if non_color:
# #             try:
# #                 t.image.colorspace_settings.name = "Non-Color"
# #             except Exception:
# #                 pass
# #         nt.links.new(mp.outputs["Vector"], t.inputs["Vector"])
# #         return t
# #
# #     nt.links.new(tex(diff, False).outputs["Color"], bsdf.inputs["Base Color"])
# #     if rough:
# #         nt.links.new(tex(rough, True).outputs["Color"], bsdf.inputs["Roughness"])
# #     if norm:
# #         nmap = nt.nodes.new("ShaderNodeNormalMap")
# #         nt.links.new(tex(norm, True).outputs["Color"], nmap.inputs["Color"])
# #         nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
# #     nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
# #     floor.data.materials.append(m)
# #     return floor
# #
# #
# # def setup_world(scene_kind="industrial", hdri_dir="", floor_tex_dir=""):
# #     if scene_kind == "hdri":
# #         hdris = list_hdris(hdri_dir)
# #         if not hdris:
# #             raise RuntimeError("no .hdr/.exr files in --hdri-dir: " + hdri_dir)
# #         if floor_tex_dir:
# #             build_floor_textured(floor_tex_dir)
# #         else:
# #             bpy.ops.mesh.primitive_plane_add(size=24, location=(0, 0, 0))
# #             bpy.context.active_object.data.materials.append(
# #                 make_mat("concrete", (0.34, 0.34, 0.36), 0.95))
# #         return add_lights([((2.5, -1.0, 3.0), 250)]), hdris
# #     if scene_kind == "industrial":
# #         return build_industrial(), []
# #     return build_plain(), []
# #
# #
# # def make_camera():
# #     cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
# #     bpy.context.collection.objects.link(cam)
# #     bpy.context.scene.camera = cam
# #     return cam
# #
# #
# # def aim_camera(cam, arm, foot_len):
# #     """Frame the feet from a randomized front-ish view, at the REST pose."""
# #     bpy.context.scene.frame_set(0)
# #     bpy.context.view_layer.update()
# #     feet_mid = (bw(arm, "LeftFoot") + bw(arm, "RightFoot")) * 0.5
# #     target = Vector((feet_mid.x, feet_mid.y, foot_len * 1.2))
# #     az = math.radians(random.uniform(-22, 22))
# #     el = math.radians(random.uniform(8, 20))
# #     dist = foot_len * random.uniform(7.0, 9.5)
# #     cam.location = target + Vector((dist * math.sin(az) * math.cos(el),
# #                                     -dist * math.cos(az) * math.cos(el),
# #                                     dist * math.sin(el)))
# #     cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
# #     cam.data.lens = random.uniform(35, 50)
# #
# #
# # def setup_render(fps):
# #     sc = bpy.context.scene
# #     sc.render.resolution_x = RES_W
# #     sc.render.resolution_y = RES_H
# #     sc.render.fps = fps
# #     sc.render.image_settings.file_format = "PNG"     # 5.x: encode to mp4 via ffmpeg
# #     for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
# #         try:
# #             sc.render.engine = eng; break
# #         except Exception:
# #             continue
# #
# #
# # # ---- world-intent rotation (axis-agnostic) ---------------------------------
# # def local_axis(rest3x3, world_axis):
# #     v = rest3x3.inverted() @ Vector(world_axis)
# #     v.normalize()
# #     return v
# #
# #
# # def quat_key(pb, axis_local, angle, frame):
# #     pb.rotation_mode = "QUATERNION"
# #     pb.rotation_quaternion = Quaternion(axis_local, angle)
# #     pb.keyframe_insert("rotation_quaternion", frame=frame)
# #
# #
# # def rest_key(pb, frame):
# #     pb.rotation_mode = "QUATERNION"
# #     pb.rotation_quaternion = (1, 0, 0, 0)
# #     pb.location = (0, 0, 0)
# #     pb.keyframe_insert("rotation_quaternion", frame=frame)
# #     pb.keyframe_insert("location", frame=frame)
# #
# #
# # def arms_down(arm, frame):
# #     """Lower arms from the T-pose (off-frame, but keeps the stance natural)."""
# #     for side, sgn in (("Left", 1), ("Right", -1)):
# #         try:
# #             ab = arm.pose.bones[PFX + side + "Arm"]
# #             ab.rotation_mode = "XYZ"
# #             ab.rotation_euler = (0, 0, sgn * math.radians(68))
# #             ab.keyframe_insert("rotation_euler", frame=frame)
# #         except KeyError:
# #             pass
# #
# #
# # def foot_forward(arm, side):
# #     f = bw(arm, side + "Toe_End") - bw(arm, side + "Foot")
# #     f.z = 0
# #     return f.normalized() if f.length > 1e-6 else Vector((0, -1, 0))
# #
# #
# # # ----------------------------- animation --------------------------------------
# # def outward_sign(arm, side, bone, ax_local, measure_bone, outward, test_deg=15):
# #     """Pick the rotation sign that moves `measure_bone` OUTWARD (away from body).
# #     Used for swipe (foot yaw -> toe out) and move (hip abduction -> foot out)."""
# #     pb = arm.pose.bones[PFX + side + bone]
# #     pb.rotation_mode = "QUATERNION"
# #     base = bw(arm, side + measure_bone)
# #     best, bestd = 1, -1e18
# #     for sgn in (1, -1):
# #         pb.rotation_quaternion = Quaternion(ax_local, math.radians(test_deg) * sgn)
# #         bpy.context.view_layer.update()
# #         disp = bw(arm, side + measure_bone) - base
# #         if disp.x * outward.x + disp.y * outward.y > bestd:
# #             bestd, best = disp.x * outward.x + disp.y * outward.y, sgn
# #     pb.rotation_quaternion = (1, 0, 0, 0); bpy.context.view_layer.update()
# #     return best
# # def animate_clip(arm, gesture, foot_len, rest, side=None):
# #     fps = args.fps
# #     rest_n = int(fps * 1.5)
# #     move_n = int(fps * random.uniform(0.40, 0.70))
# #     settle_n = int(fps * 1.0)
# #     f0, f1, f2 = rest_n, rest_n + move_n, rest_n + move_n + settle_n
# #
# #     if side is None:
# #         side = random.choice(["Left", "Right"])
# #     foot_pb = arm.pose.bones[PFX + side + "Foot"]
# #     upleg_pb = arm.pose.bones[PFX + side + "UpLeg"]
# #     R = rest[side]
# #
# #     # rest frames (calibration) on all driven bones + arms + root
# #     for pb in (foot_pb, upleg_pb):
# #         rest_key(pb, 0); rest_key(pb, f0)
# #     arms_down(arm, 0)
# #     arm.location = (0, 0, 0); arm.keyframe_insert("location", frame=0)
# #
# #     params = dict(gesture=gesture, foot=side, fps=fps, move_frames=move_n)
# #
# #     if gesture == "tap":
# #         amp = math.radians(random.uniform(16, 28))
# #         # Pivot about the HEEL (floor point just behind the ankle) so the heel
# #         # stays planted and the toe lifts UP -- nothing dips under the floor.
# #         A, fwd = R["A"], R["fwd"]
# #         P = Vector((A.x, A.y, 0.0)) - fwd * (0.30 * foot_len)   # heel-ish, on floor
# #         axis = fwd.cross(WORLD_UP).normalized()                 # rotates toe UP
# #         R4 = Matrix.Rotation(amp, 4, axis)
# #         Wpivot = Matrix.Translation(P) @ R4 @ Matrix.Translation(-P)
# #         desired = arm.matrix_world.inverted() @ (Wpivot @ R["M0"])  # object space
# #         bpy.context.scene.frame_set(0); bpy.context.view_layer.update()
# #         foot_pb.rotation_mode = "QUATERNION"
# #         foot_pb.matrix = desired                                # back-solves loc+rot
# #         foot_pb.keyframe_insert("location", frame=f1)
# #         foot_pb.keyframe_insert("rotation_quaternion", frame=f1)
# #         rest_key(foot_pb, f2)                                   # toe returns
# #         params["amp_deg"] = round(math.degrees(amp), 1)
# #
# #     elif gesture == "swipe":
# #         amp = math.radians(random.uniform(18, 35)) * R["swipe_sign"]   # toe out
# #         quat_key(foot_pb, R["swipe_ax"], amp, f1)
# #         rest_key(foot_pb, f2)                                          # OUT then BACK
# #         params["amp_deg"] = round(math.degrees(amp), 1)
# #
# #     else:  # move : abduct THIS foot OUTWARD (move_left=Left foot, move_right=Right)
# #         amp = math.radians(random.uniform(16, 26)) * R["out_sign"]
# #         quat_key(upleg_pb, R["upleg_ax"], amp, f1)
# #         rest_key(upleg_pb, f2)                                  # steps OUT then BACK
# #         params["amp_deg"] = round(math.degrees(amp), 1)
# #
# #     return f1, f2, side, params
# #
# #
# #
# #
# # # ----------------------------- render ---------------------------------------
# # def render_clip(path, fps):
# #     import shutil, subprocess
# #     stem = path[:-4] if path.endswith(".mp4") else path
# #     framedir = stem + "_frames"; os.makedirs(framedir, exist_ok=True)
# #     bpy.context.scene.render.filepath = os.path.join(framedir, "f_####")
# #     bpy.ops.render.render(animation=True)
# #     ff = shutil.which("ffmpeg")
# #     if ff:
# #         subprocess.run([ff, "-y", "-framerate", str(fps), "-start_number", "0",
# #                         "-i", os.path.join(framedir, "f_%04d.png"),
# #                         "-c:v", "libx264", "-pix_fmt", "yuv420p", path], check=False)
# #         shutil.rmtree(framedir, ignore_errors=True)
# #     else:
# #         print("  ffmpeg not on PATH -- left PNG frames in", framedir)
# #
# #
# # # ----------------------------- main -----------------------------------------
# # def main():
# #     clean_scene()
# #     arm = import_rig(args.fbx)
# #     scene_kind = "hdri" if args.hdri_dir else args.scene
# #     lights, hdris = setup_world(scene_kind, args.hdri_dir, args.floor_tex_dir)
# #     cam = make_camera()
# #     setup_render(args.fps)
# #
# #     try:
# #         ed = bpy.context.preferences.edit
# #         ed.keyframe_new_interpolation_type = "BEZIER"
# #         ed.keyframe_new_handle_type = "AUTO_CLAMPED"
# #     except Exception as e:
# #         print("keyframe defaults:", e)
# #
# #     bpy.context.scene.frame_set(0); bpy.context.view_layer.update()
# #     foot_len = (bw(arm, "LeftToe_End") - bw(arm, "LeftFoot")).length or 0.2
# #     rest = {}
# #     for sd in ("Left", "Right"):
# #         fp = arm.pose.bones[PFX + sd + "Foot"]
# #         rest[sd] = dict(A=bw(arm, sd + "Foot"),
# #                         fwd=foot_forward(arm, sd),
# #                         M0=(arm.matrix_world @ fp.matrix).copy(),
# #                         foot3=rest_world_3x3(arm, sd + "Foot"),
# #                         upleg3=rest_world_3x3(arm, sd + "UpLeg"))
# #     print("foot length:", round(foot_len, 4))
# #
# #     # outward hip-abduction axis + sign per foot (for the move gesture)
# #     hips = bw(arm, "Hips")
# #     for sd in ("Left", "Right"):
# #         A = rest[sd]["A"]
# #         outward = Vector((A.x - hips.x, A.y - hips.y, 0.0))
# #         outward = outward.normalized() if outward.length > 1e-6 else Vector((1, 0, 0))
# #         rest[sd]["swipe_ax"] = local_axis(rest[sd]["foot3"], WORLD_UP)
# #         rest[sd]["swipe_sign"] = outward_sign(arm, sd, "Foot",
# #                                               rest[sd]["swipe_ax"], "Toe_End", outward)
# #         rest[sd]["upleg_ax"] = local_axis(rest[sd]["upleg3"], rest[sd]["fwd"])
# #         rest[sd]["out_sign"] = outward_sign(arm, sd, "UpLeg",
# #                                             rest[sd]["upleg_ax"], "Foot", outward)
# #
# #     SPECS = [("tap", None), ("swipe", "Left"), ("swipe", "Right"),
# #              ("move", "Left"), ("move", "Right")]
# #     counts = {}
# #     for i in range(args.n):
# #         gesture, side_spec = SPECS[i % len(SPECS)]
# #         if arm.animation_data:
# #             arm.animation_data_clear()
# #         arm.location = (0, 0, 0)
# #         randomize_lights(lights)
# #         if hdris:
# #             set_world_hdri(random.choice(hdris), random.uniform(0, 2 * math.pi))
# #
# #         f1, f2, side, params = animate_clip(arm, gesture, foot_len, rest, side_spec)
# #         bpy.context.scene.frame_start = 0
# #         bpy.context.scene.frame_end = f2
# #         aim_camera(cam, arm, foot_len)
# #
# #         if gesture == "tap":
# #             label = "tap"
# #         else:                                  # swipe/move are foot-mapped
# #             label = f"{gesture}_" + ("left" if side == "Left" else "right")
# #         params["label"] = label
# #
# #         idx = counts.get(label, 0); counts[label] = idx + 1
# #         stem = os.path.join(args.out, f"{label}_{idx:04d}")
# #         with open(stem + ".json", "w") as f:
# #             json.dump(params, f, indent=2)
# #         render_clip(stem + ".mp4", args.fps)
# #         print(f"[{i+1}/{args.n}] {label} ({side}) -> {stem}.mp4")
# #
# #     print("done:", counts)
# #
# #
# # if __name__ == "__main__":
# #     main()
# """
# generate_gestures.py  --  synthetic foot-gesture clip generator (Blender 5.x)
#
# Gestures match the recorded reference:
#   TAP   : toe lifts UP, heel planted        -> pitch foot up about the ankle
#   SWIPE : heel planted, toe pivots outward  -> yaw foot about world-vertical
#   MOVE  : ONE foot relocates, other stays   -> abduct that leg at the hip
#
# Robustness choices (these fix the recurring pain):
#  * Motions are defined in WORLD space (e.g. "rotate about vertical") and
#    converted to each bone's local frame, so we never hand-guess bone axes.
#  * left/right LABELS come from projecting the toe into the camera and reading
#    which way it moved ON SCREEN -- so labels always match your detector's view.
#  * each clip = REST (calibration) -> eased gesture -> SETTLE, framed on the feet.
#
# Run:
#   blender --background --python generate_gestures.py -- --fbx pete.fbx --out ./synth --n 60
# Then run YOUR detector on the mp4s; label each window by the clip's .json.
# """
#
# import bpy, sys, os, math, random
# from mathutils import Vector, Quaternion, Matrix
#
# # ----------------------------- args -----------------------------------------
# argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
# import argparse, json
# ap = argparse.ArgumentParser()
# ap.add_argument("--fbx", required=True)
# ap.add_argument("--out", default="./synth")
# ap.add_argument("--n", type=int, default=60)
# ap.add_argument("--fps", type=int, default=30)
# ap.add_argument("--res", default="848x478")
# ap.add_argument("--scene", choices=["industrial", "plain", "hdri"], default="industrial")
# ap.add_argument("--hdri-dir", default="", help="folder of Poly Haven .hdr/.exr (enables hdri scene)")
# ap.add_argument("--floor-tex-dir", default="", help="folder with a Poly Haven floor PBR set")
# ap.add_argument("--idle", type=int, default=0, help="extra no-gesture clips for false-fire testing")
# ap.add_argument("--seed", type=int, default=0)
# args = ap.parse_args(argv)
# random.seed(args.seed)
# os.makedirs(args.out, exist_ok=True)
# RES_W, RES_H = (int(x) for x in args.res.split("x"))
# PFX = "mixamorig:"
# WORLD_UP = Vector((0, 0, 1))
#
#
# # ----------------------------- helpers --------------------------------------
# def clean_scene():
#     bpy.ops.wm.read_factory_settings(use_empty=True)
#
#
# def import_rig(path):
#     bpy.ops.import_scene.fbx(filepath=path, automatic_bone_orientation=True)
#     arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
#     if arm.animation_data:
#         arm.animation_data_clear()
#     return arm
#
#
# def bw(arm, name):
#     """World position of a bone head."""
#     return arm.matrix_world @ arm.pose.bones[PFX + name].head
#
#
# def rest_world_3x3(arm, name):
#     pb = arm.pose.bones[PFX + name]
#     return (arm.matrix_world @ pb.matrix).to_3x3()
#
#
# def make_mat(name, color, rough=0.8, metal=0.0):
#     m = bpy.data.materials.new(name)
#     try:
#         m.use_nodes = True
#         bsdf = m.node_tree.nodes.get("Principled BSDF")
#         if bsdf:
#             bsdf.inputs["Base Color"].default_value = (*color, 1.0)
#             if "Roughness" in bsdf.inputs:
#                 bsdf.inputs["Roughness"].default_value = rough
#             if "Metallic" in bsdf.inputs:
#                 bsdf.inputs["Metallic"].default_value = metal
#     except Exception:
#         try:
#             m.diffuse_color = (*color, 1.0)
#         except Exception:
#             pass
#     return m
#
#
# def _box(size, loc, rot, mat):
#     bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
#     o = bpy.context.active_object
#     o.scale = size; o.rotation_euler = rot
#     o.data.materials.append(mat)
#     return o
#
#
# def _cyl(r, h, loc, rot, mat):
#     bpy.ops.mesh.primitive_cylinder_add(radius=r, depth=h, location=loc)
#     o = bpy.context.active_object
#     o.rotation_euler = rot
#     o.data.materials.append(mat)
#     return o
#
#
# def add_lights(specs):
#     """specs: list of (location, energy). Returns light objects with base values."""
#     lights = []
#     for loc, e in specs:
#         l = bpy.data.lights.new("L", "AREA"); l.energy = e; l.size = 4
#         o = bpy.data.objects.new("L", l); o.location = loc
#         bpy.context.collection.objects.link(o)
#         o["e0"] = e
#         o["lx"], o["ly"], o["lz"] = loc
#         lights.append(o)
#     return lights
#
#
# def randomize_lights(lights):
#     """Per-clip lighting variation (energy, position, slight color temperature)."""
#     for o in lights:
#         o.data.energy = o["e0"] * random.uniform(0.65, 1.4)
#         o.location = (o["lx"] + random.uniform(-0.6, 0.6),
#                       o["ly"] + random.uniform(-0.6, 0.6),
#                       o["lz"] + random.uniform(-0.3, 0.3))
#         t = random.uniform(0.0, 1.0)                      # warm..cool tint
#         o.data.color = (1.0, 0.93 + 0.07 * t, 0.85 + 0.15 * t)
#
#
# def build_plain():
#     bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))
#     bpy.context.active_object.data.materials.append(
#         make_mat("floor", (0.62, 0.5, 0.4), rough=0.9))
#     return add_lights([((3, -3, 5), 1600), ((-3, -2, 3), 600)])
#
#
# def build_industrial():
#     """Concrete floor, metal walls, scattered props -- all BEHIND/around the
#     avatar (camera is in front at -Y), feet area kept clear."""
#     concrete = make_mat("concrete", (0.34, 0.34, 0.36), rough=0.95)
#     wall = make_mat("wall", (0.40, 0.42, 0.45), rough=0.7, metal=0.4)
#     steel = make_mat("steel", (0.55, 0.56, 0.58), rough=0.4, metal=0.9)
#     crate = make_mat("crate", (0.45, 0.33, 0.18), rough=0.8)
#     barrel_b = make_mat("barrel_b", (0.15, 0.30, 0.55), rough=0.5, metal=0.3)
#     barrel_r = make_mat("barrel_r", (0.55, 0.18, 0.15), rough=0.5, metal=0.3)
#     hazard = make_mat("hazard", (0.85, 0.7, 0.05), rough=0.7)
#
#     bpy.ops.mesh.primitive_plane_add(size=24, location=(0, 0, 0))
#     bpy.context.active_object.data.materials.append(concrete)
#
#     # room: back wall (+Y) and side walls (the camera at -Y sees these behind)
#     _box((12, 0.2, 6), (0, 4.0, 1.5), (0, 0, 0), wall)
#     _box((0.2, 12, 6), (4.0, 0, 1.5), (0, 0, 0), wall)
#     _box((0.2, 12, 6), (-4.0, 0, 1.5), (0, 0, 0), wall)
#
#     # props behind / to the sides (clear zone ~1.3m around the feet)
#     _box((0.8, 0.8, 0.8), (1.9, 2.6, 0.4), (0, 0, 0.3), crate)
#     _box((0.8, 0.8, 0.8), (1.9, 2.6, 1.2), (0, 0, 0.25), crate)
#     _box((0.9, 0.9, 0.5), (-2.0, 2.2, 0.25), (0, 0, -0.2), crate)
#     _cyl(0.32, 0.95, (-1.5, 3.0, 0.48), (0, 0, 0), barrel_b)
#     _cyl(0.32, 0.95, (-0.9, 3.2, 0.48), (0, 0, 0), barrel_r)
#     _cyl(0.12, 5.0, (3.4, 0.0, 2.2), (math.radians(90), 0, 0), steel)   # wall pipe
#     _cyl(0.12, 5.0, (3.7, 0.0, 1.6), (math.radians(90), 0, 0), steel)
#     _box((1.2, 0.12, 0.12), (1.0, 1.6, 0.02), (0, 0, 0.5), hazard)      # floor stripe
#     _box((1.2, 0.12, 0.12), (-1.2, 1.7, 0.02), (0, 0, -0.4), hazard)
#
#     return add_lights([((2.5, -1.0, 3.0), 1400), ((-2.5, 0.5, 3.0), 1000),
#                        ((0.0, 2.5, 3.2), 800)])
#
#
# def list_hdris(d):
#     import glob
#     return sorted(glob.glob(os.path.join(d, "*.hdr")) +
#                   glob.glob(os.path.join(d, "*.exr")))
#
#
# def set_world_hdri(path, rot_z):
#     """Poly Haven HDRI as the world: lighting + photographic backdrop in one.
#     Random Z rotation per clip varies which part sits behind the feet."""
#     w = bpy.context.scene.world or bpy.data.worlds.new("W")
#     bpy.context.scene.world = w
#     try:
#         w.use_nodes = True
#     except Exception:
#         pass
#     nt = w.node_tree
#     nt.nodes.clear()
#     out = nt.nodes.new("ShaderNodeOutputWorld")
#     bg = nt.nodes.new("ShaderNodeBackground")
#     env = nt.nodes.new("ShaderNodeTexEnvironment")
#     env.image = bpy.data.images.load(path, check_existing=True)
#     mp = nt.nodes.new("ShaderNodeMapping")
#     mp.inputs["Rotation"].default_value[2] = rot_z
#     tc = nt.nodes.new("ShaderNodeTexCoord")
#     nt.links.new(tc.outputs["Generated"], mp.inputs["Vector"])
#     nt.links.new(mp.outputs["Vector"], env.inputs["Vector"])
#     nt.links.new(env.outputs["Color"], bg.inputs["Color"])
#     nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
#
#
# def _find_map(d, *keys):
#     import glob
#     files = [f for ext in ("png", "jpg", "jpeg", "exr", "tif")
#              for f in glob.glob(os.path.join(d, "*." + ext))]
#     for f in files:
#         n = os.path.basename(f).lower()
#         if any(k in n for k in keys):
#             return f
#     return None
#
#
# def build_floor_textured(tex_dir, tiling=8.0):
#     """Floor from a Poly Haven PBR set (auto-detects diff/rough/normal maps).
#     Falls back to flat concrete if no diffuse map is found."""
#     bpy.ops.mesh.primitive_plane_add(size=24, location=(0, 0, 0))
#     floor = bpy.context.active_object
#     diff = _find_map(tex_dir, "diff", "albedo", "_col", "color")
#     rough = _find_map(tex_dir, "rough", "_arm")
#     norm = _find_map(tex_dir, "nor_gl", "normal", "_nor")
#     if not diff:
#         floor.data.materials.append(make_mat("concrete", (0.34, 0.34, 0.36), 0.95))
#         return floor
#     m = bpy.data.materials.new("floor_pbr")
#     m.use_nodes = True
#     nt = m.node_tree; nt.nodes.clear()
#     out = nt.nodes.new("ShaderNodeOutputMaterial")
#     bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
#     tc = nt.nodes.new("ShaderNodeTexCoord")
#     mp = nt.nodes.new("ShaderNodeMapping")
#     mp.inputs["Scale"].default_value = (tiling, tiling, tiling)
#     nt.links.new(tc.outputs["UV"], mp.inputs["Vector"])
#
#     def tex(path, non_color):
#         t = nt.nodes.new("ShaderNodeTexImage")
#         t.image = bpy.data.images.load(path, check_existing=True)
#         if non_color:
#             try:
#                 t.image.colorspace_settings.name = "Non-Color"
#             except Exception:
#                 pass
#         nt.links.new(mp.outputs["Vector"], t.inputs["Vector"])
#         return t
#
#     nt.links.new(tex(diff, False).outputs["Color"], bsdf.inputs["Base Color"])
#     if rough:
#         nt.links.new(tex(rough, True).outputs["Color"], bsdf.inputs["Roughness"])
#     if norm:
#         nmap = nt.nodes.new("ShaderNodeNormalMap")
#         nt.links.new(tex(norm, True).outputs["Color"], nmap.inputs["Color"])
#         nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
#     nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
#     floor.data.materials.append(m)
#     return floor
#
#
# def setup_world(scene_kind="industrial", hdri_dir="", floor_tex_dir=""):
#     if scene_kind == "hdri":
#         hdris = list_hdris(hdri_dir)
#         if not hdris:
#             raise RuntimeError("no .hdr/.exr files in --hdri-dir: " + hdri_dir)
#         if floor_tex_dir:
#             build_floor_textured(floor_tex_dir)
#         else:
#             bpy.ops.mesh.primitive_plane_add(size=24, location=(0, 0, 0))
#             bpy.context.active_object.data.materials.append(
#                 make_mat("concrete", (0.34, 0.34, 0.36), 0.95))
#         return add_lights([((2.5, -1.0, 3.0), 250)]), hdris
#     if scene_kind == "industrial":
#         return build_industrial(), []
#     return build_plain(), []
#
#
# def make_camera():
#     cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
#     bpy.context.collection.objects.link(cam)
#     bpy.context.scene.camera = cam
#     return cam
#
#
# def aim_camera(cam, arm, foot_len):
#     """Frame the feet from a randomized front-ish view, at the REST pose."""
#     bpy.context.scene.frame_set(0)
#     bpy.context.view_layer.update()
#     feet_mid = (bw(arm, "LeftFoot") + bw(arm, "RightFoot")) * 0.5
#     target = Vector((feet_mid.x, feet_mid.y, foot_len * 1.2))
#     az = math.radians(random.uniform(-22, 22))
#     el = math.radians(random.uniform(8, 20))
#     dist = foot_len * random.uniform(7.0, 9.5)
#     cam.location = target + Vector((dist * math.sin(az) * math.cos(el),
#                                     -dist * math.cos(az) * math.cos(el),
#                                     dist * math.sin(el)))
#     cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
#     cam.data.lens = random.uniform(35, 50)
#
#
# def setup_render(fps):
#     sc = bpy.context.scene
#     sc.render.resolution_x = RES_W
#     sc.render.resolution_y = RES_H
#     sc.render.fps = fps
#     sc.render.image_settings.file_format = "PNG"     # 5.x: encode to mp4 via ffmpeg
#     for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
#         try:
#             sc.render.engine = eng; break
#         except Exception:
#             continue
#
#
# # ---- world-intent rotation (axis-agnostic) ---------------------------------
# def local_axis(rest3x3, world_axis):
#     v = rest3x3.inverted() @ Vector(world_axis)
#     v.normalize()
#     return v
#
#
# def quat_key(pb, axis_local, angle, frame):
#     pb.rotation_mode = "QUATERNION"
#     pb.rotation_quaternion = Quaternion(axis_local, angle)
#     pb.keyframe_insert("rotation_quaternion", frame=frame)
#
#
# def rest_key(pb, frame):
#     pb.rotation_mode = "QUATERNION"
#     pb.rotation_quaternion = (1, 0, 0, 0)
#     pb.location = (0, 0, 0)
#     pb.keyframe_insert("rotation_quaternion", frame=frame)
#     pb.keyframe_insert("location", frame=frame)
#
#
# def arms_down(arm, frame):
#     """Lower arms from the T-pose (off-frame, but keeps the stance natural)."""
#     for side, sgn in (("Left", 1), ("Right", -1)):
#         try:
#             ab = arm.pose.bones[PFX + side + "Arm"]
#             ab.rotation_mode = "XYZ"
#             ab.rotation_euler = (0, 0, sgn * math.radians(68))
#             ab.keyframe_insert("rotation_euler", frame=frame)
#         except KeyError:
#             pass
#
#
# def foot_forward(arm, side):
#     f = bw(arm, side + "Toe_End") - bw(arm, side + "Foot")
#     f.z = 0
#     return f.normalized() if f.length > 1e-6 else Vector((0, -1, 0))
#
#
# def animate_idle(arm, foot_len, rest):
#     """A 'no gesture' clip: stand with small sway + micro foot motion, all kept
#     WELL BELOW the gesture thresholds. The recognizer should fire nothing here;
#     any fire is a false positive. (A frozen statue would be too easy a test.)"""
#     fps = args.fps
#     f0 = int(fps * 1.5)
#     total = f0 + int(fps * 2.0)
#     side = random.choice(["Left", "Right"])
#     R = rest[side]
#     foot_pb = arm.pose.bones[PFX + side + "Foot"]
#
#     for s in ("Left", "Right"):
#         for b in ("Foot", "UpLeg"):
#             pb = arm.pose.bones[PFX + s + b]
#             rest_key(pb, 0); rest_key(pb, f0)
#     arms_down(arm, 0)
#     arm.location = (0, 0, 0)
#     arm.keyframe_insert("location", frame=0)
#     arm.keyframe_insert("location", frame=f0)
#
#     for k in range(1, 5):                       # gentle sway after calibration
#         fr = f0 + int((total - f0) * k / 4)
#         arm.location = (random.uniform(-0.10, 0.10) * foot_len,
#                         random.uniform(-0.06, 0.06) * foot_len, 0.0)
#         arm.keyframe_insert("location", frame=fr)
#         quat_key(foot_pb, R["swipe_ax"], math.radians(random.uniform(-3, 3)), fr)
#     arm.location = (0, 0, 0); arm.keyframe_insert("location", frame=total)
#     rest_key(foot_pb, total)
#     return total, side, dict(label="idle", foot=side, fps=fps)
#
#
# # ----------------------------- animation --------------------------------------
# def outward_sign(arm, side, bone, ax_local, measure_bone, outward, test_deg=15):
#     """Pick the rotation sign that moves `measure_bone` OUTWARD (away from body).
#     Used for swipe (foot yaw -> toe out) and move (hip abduction -> foot out)."""
#     pb = arm.pose.bones[PFX + side + bone]
#     pb.rotation_mode = "QUATERNION"
#     base = bw(arm, side + measure_bone)
#     best, bestd = 1, -1e18
#     for sgn in (1, -1):
#         pb.rotation_quaternion = Quaternion(ax_local, math.radians(test_deg) * sgn)
#         bpy.context.view_layer.update()
#         disp = bw(arm, side + measure_bone) - base
#         if disp.x * outward.x + disp.y * outward.y > bestd:
#             bestd, best = disp.x * outward.x + disp.y * outward.y, sgn
#     pb.rotation_quaternion = (1, 0, 0, 0); bpy.context.view_layer.update()
#     return best
# def animate_clip(arm, label, foot_len, rest):
#     fps = args.fps
#     rest_n = int(fps * 1.5)
#     move_n = int(fps * random.uniform(0.40, 0.70))
#     settle_n = int(fps * 1.0)
#     f0, f1, f2 = rest_n, rest_n + move_n, rest_n + move_n + settle_n
#
#     if label.endswith("_left"):
#         side = "Left"
#     elif label.endswith("_right"):
#         side = "Right"
#     else:                                   # tap / move_forward / move_backward
#         side = random.choice(["Left", "Right"])
#     foot_pb = arm.pose.bones[PFX + side + "Foot"]
#     upleg_pb = arm.pose.bones[PFX + side + "UpLeg"]
#     R = rest[side]
#
#     # rest frames (calibration) on all driven bones + arms + root
#     for pb in (foot_pb, upleg_pb):
#         rest_key(pb, 0); rest_key(pb, f0)
#     arms_down(arm, 0)
#     arm.location = (0, 0, 0); arm.keyframe_insert("location", frame=0)
#
#     params = dict(label=label, foot=side, fps=fps, move_frames=move_n)
#
#     if label == "tap":
#         amp = math.radians(random.uniform(16, 28))
#         # Pivot about the HEEL (floor point just behind the ankle) so the heel
#         # stays planted and the toe lifts UP -- nothing dips under the floor.
#         A, fwd = R["A"], R["fwd"]
#         P = Vector((A.x, A.y, 0.0)) - fwd * (0.30 * foot_len)
#         axis = fwd.cross(WORLD_UP).normalized()                # rotates toe UP
#         R4 = Matrix.Rotation(amp, 4, axis)
#         Wpivot = Matrix.Translation(P) @ R4 @ Matrix.Translation(-P)
#         desired = arm.matrix_world.inverted() @ (Wpivot @ R["M0"])
#         bpy.context.scene.frame_set(0); bpy.context.view_layer.update()
#         foot_pb.rotation_mode = "QUATERNION"
#         foot_pb.matrix = desired
#         foot_pb.keyframe_insert("location", frame=f1)
#         foot_pb.keyframe_insert("rotation_quaternion", frame=f1)
#         rest_key(foot_pb, f2)                                  # toe returns
#         params["amp_deg"] = round(math.degrees(amp), 1)
#
#     elif label.startswith("swipe"):                            # foot-mapped, toe OUT
#         amp = math.radians(random.uniform(18, 35)) * R["swipe_sign"]
#         quat_key(foot_pb, R["swipe_ax"], amp, f1)
#         rest_key(foot_pb, f2)
#         params["amp_deg"] = round(math.degrees(amp), 1)
#
#     elif label in ("move_left", "move_right"):                 # abduct foot OUTWARD
#         amp = math.radians(random.uniform(16, 26)) * R["out_sign"]
#         quat_key(upleg_pb, R["upleg_ax"], amp, f1)
#         rest_key(upleg_pb, f2)
#         params["amp_deg"] = round(math.degrees(amp), 1)
#
#     else:  # move_forward / move_backward : swing the leg fwd/back at the hip
#         d = 1 if label == "move_forward" else -1
#         amp = math.radians(random.uniform(14, 24)) * R["fwd_sign"] * d
#         quat_key(upleg_pb, R["flex_ax"], amp, f1)
#         rest_key(upleg_pb, f2)
#         params["amp_deg"] = round(math.degrees(amp), 1)
#
#     return f1, f2, side, params
#
#
#
#
# # ----------------------------- render ---------------------------------------
# def render_clip(path, fps):
#     import shutil, subprocess
#     stem = path[:-4] if path.endswith(".mp4") else path
#     framedir = stem + "_frames"; os.makedirs(framedir, exist_ok=True)
#     bpy.context.scene.render.filepath = os.path.join(framedir, "f_####")
#     bpy.ops.render.render(animation=True)
#     ff = shutil.which("ffmpeg")
#     if ff:
#         subprocess.run([ff, "-y", "-framerate", str(fps), "-start_number", "0",
#                         "-i", os.path.join(framedir, "f_%04d.png"),
#                         "-c:v", "libx264", "-pix_fmt", "yuv420p", path], check=False)
#         shutil.rmtree(framedir, ignore_errors=True)
#     else:
#         print("  ffmpeg not on PATH -- left PNG frames in", framedir)
#
#
# # ----------------------------- main -----------------------------------------
# def main():
#     clean_scene()
#     arm = import_rig(args.fbx)
#     scene_kind = "hdri" if args.hdri_dir else args.scene
#     lights, hdris = setup_world(scene_kind, args.hdri_dir, args.floor_tex_dir)
#     cam = make_camera()
#     setup_render(args.fps)
#
#     try:
#         ed = bpy.context.preferences.edit
#         ed.keyframe_new_interpolation_type = "BEZIER"
#         ed.keyframe_new_handle_type = "AUTO_CLAMPED"
#     except Exception as e:
#         print("keyframe defaults:", e)
#
#     bpy.context.scene.frame_set(0); bpy.context.view_layer.update()
#     foot_len = (bw(arm, "LeftToe_End") - bw(arm, "LeftFoot")).length or 0.2
#     rest = {}
#     for sd in ("Left", "Right"):
#         fp = arm.pose.bones[PFX + sd + "Foot"]
#         rest[sd] = dict(A=bw(arm, sd + "Foot"),
#                         fwd=foot_forward(arm, sd),
#                         M0=(arm.matrix_world @ fp.matrix).copy(),
#                         foot3=rest_world_3x3(arm, sd + "Foot"),
#                         upleg3=rest_world_3x3(arm, sd + "UpLeg"))
#     print("foot length:", round(foot_len, 4))
#
#     # outward hip-abduction axis + sign per foot (for the move gesture)
#     hips = bw(arm, "Hips")
#     for sd in ("Left", "Right"):
#         A = rest[sd]["A"]
#         outward = Vector((A.x - hips.x, A.y - hips.y, 0.0))
#         outward = outward.normalized() if outward.length > 1e-6 else Vector((1, 0, 0))
#         rest[sd]["swipe_ax"] = local_axis(rest[sd]["foot3"], WORLD_UP)
#         rest[sd]["swipe_sign"] = outward_sign(arm, sd, "Foot",
#                                               rest[sd]["swipe_ax"], "Toe_End", outward)
#         rest[sd]["upleg_ax"] = local_axis(rest[sd]["upleg3"], rest[sd]["fwd"])
#         rest[sd]["out_sign"] = outward_sign(arm, sd, "UpLeg",
#                                             rest[sd]["upleg_ax"], "Foot", outward)
#         # hip flexion/extension (forward/back leg swing) -- rotate about lateral
#         flex_ax = local_axis(rest[sd]["upleg3"], WORLD_UP.cross(rest[sd]["fwd"]))
#         rest[sd]["flex_ax"] = flex_ax
#         rest[sd]["fwd_sign"] = outward_sign(arm, sd, "UpLeg",
#                                             flex_ax, "Foot", rest[sd]["fwd"])
#
#     GESTURES = ["tap", "swipe_left", "swipe_right", "move_left", "move_right",
#                 "move_forward", "move_backward"]
#     counts = {}
#     for i in range(args.n):
#         label = GESTURES[i % len(GESTURES)]
#         if arm.animation_data:
#             arm.animation_data_clear()
#         arm.location = (0, 0, 0)
#         randomize_lights(lights)
#         if hdris:
#             set_world_hdri(random.choice(hdris), random.uniform(0, 2 * math.pi))
#
#         f1, f2, side, params = animate_clip(arm, label, foot_len, rest)
#         bpy.context.scene.frame_start = 0
#         bpy.context.scene.frame_end = f2
#         aim_camera(cam, arm, foot_len)
#
#         idx = counts.get(label, 0); counts[label] = idx + 1
#         stem = os.path.join(args.out, f"{label}_{idx:04d}")
#         with open(stem + ".json", "w") as f:
#             json.dump(params, f, indent=2)
#         render_clip(stem + ".mp4", args.fps)
#         print(f"[{i+1}/{args.n}] {label} ({side}) -> {stem}.mp4")
#
#     for j in range(args.idle):
#         if arm.animation_data:
#             arm.animation_data_clear()
#         arm.location = (0, 0, 0)
#         randomize_lights(lights)
#         if hdris:
#             set_world_hdri(random.choice(hdris), random.uniform(0, 2 * math.pi))
#         total, side, params = animate_idle(arm, foot_len, rest)
#         bpy.context.scene.frame_start = 0
#         bpy.context.scene.frame_end = total
#         aim_camera(cam, arm, foot_len)
#         idx = counts.get("idle", 0); counts["idle"] = idx + 1
#         stem = os.path.join(args.out, f"idle_{idx:04d}")
#         with open(stem + ".json", "w") as f:
#             json.dump(params, f, indent=2)
#         render_clip(stem + ".mp4", args.fps)
#         print(f"[idle {j+1}/{args.idle}] -> {stem}.mp4")
#
#     print("done:", counts)
#
#
# if __name__ == "__main__":
#     main()

"""
generate_gestures.py  --  synthetic foot-gesture clip generator (Blender 5.x)

Gestures match the recorded reference:
  TAP   : toe lifts UP, heel planted        -> pitch foot up about the ankle
  SWIPE : heel planted, toe pivots outward  -> yaw foot about world-vertical
  MOVE  : ONE foot relocates, other stays   -> abduct that leg at the hip

Robustness choices (these fix the recurring pain):
 * Motions are defined in WORLD space (e.g. "rotate about vertical") and
   converted to each bone's local frame, so we never hand-guess bone axes.
 * left/right LABELS come from projecting the toe into the camera and reading
   which way it moved ON SCREEN -- so labels always match your detector's view.
 * each clip = REST (calibration) -> eased gesture -> SETTLE, framed on the feet.

Run:
  blender --background --python generate_gestures.py -- --fbx pete.fbx --out ./synth --n 60
Then run YOUR detector on the mp4s; label each window by the clip's .json.
"""

import bpy, sys, os, math, random
from mathutils import Vector, Quaternion, Matrix

# ----------------------------- args -----------------------------------------
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
import argparse, json
ap = argparse.ArgumentParser()
ap.add_argument("--fbx", required=True)
ap.add_argument("--out", default="./synth")
ap.add_argument("--n", type=int, default=60)
ap.add_argument("--fps", type=int, default=30)
ap.add_argument("--res", default="848x478")
ap.add_argument("--scene", choices=["industrial", "plain", "hdri"], default="industrial")
ap.add_argument("--hdri-dir", default="", help="folder of Poly Haven .hdr/.exr (enables hdri scene)")
ap.add_argument("--floor-tex-dir", default="", help="folder with a Poly Haven floor PBR set")
ap.add_argument("--idle", type=int, default=0, help="extra no-gesture clips for false-fire testing")
ap.add_argument("--seed", type=int, default=0)
args = ap.parse_args(argv)
random.seed(args.seed)
os.makedirs(args.out, exist_ok=True)
RES_W, RES_H = (int(x) for x in args.res.split("x"))
PFX = "mixamorig1:"
WORLD_UP = Vector((0, 0, 1))


# ----------------------------- helpers --------------------------------------
def clean_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_rig(path):
    bpy.ops.import_scene.fbx(filepath=path, automatic_bone_orientation=True)
    arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    if arm.animation_data:
        arm.animation_data_clear()
    return arm


def bw(arm, name):
    """World position of a bone head."""
    return arm.matrix_world @ arm.pose.bones[PFX + name].head


def rest_world_3x3(arm, name):
    pb = arm.pose.bones[PFX + name]
    return (arm.matrix_world @ pb.matrix).to_3x3()


def make_mat(name, color, rough=0.8, metal=0.0):
    m = bpy.data.materials.new(name)
    try:
        m.use_nodes = True
        bsdf = m.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (*color, 1.0)
            if "Roughness" in bsdf.inputs:
                bsdf.inputs["Roughness"].default_value = rough
            if "Metallic" in bsdf.inputs:
                bsdf.inputs["Metallic"].default_value = metal
    except Exception:
        try:
            m.diffuse_color = (*color, 1.0)
        except Exception:
            pass
    return m


def _box(size, loc, rot, mat):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    o = bpy.context.active_object
    o.scale = size; o.rotation_euler = rot
    o.data.materials.append(mat)
    return o


def _cyl(r, h, loc, rot, mat):
    bpy.ops.mesh.primitive_cylinder_add(radius=r, depth=h, location=loc)
    o = bpy.context.active_object
    o.rotation_euler = rot
    o.data.materials.append(mat)
    return o


def add_lights(specs):
    """specs: list of (location, energy). Returns light objects with base values."""
    lights = []
    for loc, e in specs:
        l = bpy.data.lights.new("L", "AREA"); l.energy = e; l.size = 4
        o = bpy.data.objects.new("L", l); o.location = loc
        bpy.context.collection.objects.link(o)
        o["e0"] = e
        o["lx"], o["ly"], o["lz"] = loc
        lights.append(o)
    return lights


def randomize_lights(lights):
    """Per-clip lighting variation (energy, position, slight color temperature)."""
    for o in lights:
        o.data.energy = o["e0"] * random.uniform(0.65, 1.4)
        o.location = (o["lx"] + random.uniform(-0.6, 0.6),
                      o["ly"] + random.uniform(-0.6, 0.6),
                      o["lz"] + random.uniform(-0.3, 0.3))
        t = random.uniform(0.0, 1.0)                      # warm..cool tint
        o.data.color = (1.0, 0.93 + 0.07 * t, 0.85 + 0.15 * t)


def build_plain():
    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))
    bpy.context.active_object.data.materials.append(
        make_mat("floor", (0.62, 0.5, 0.4), rough=0.9))
    return add_lights([((3, -3, 5), 1600), ((-3, -2, 3), 600)])


def build_industrial():
    """Concrete floor, metal walls, scattered props -- all BEHIND/around the
    avatar (camera is in front at -Y), feet area kept clear."""
    concrete = make_mat("concrete", (0.34, 0.34, 0.36), rough=0.95)
    wall = make_mat("wall", (0.40, 0.42, 0.45), rough=0.7, metal=0.4)
    steel = make_mat("steel", (0.55, 0.56, 0.58), rough=0.4, metal=0.9)
    crate = make_mat("crate", (0.45, 0.33, 0.18), rough=0.8)
    barrel_b = make_mat("barrel_b", (0.15, 0.30, 0.55), rough=0.5, metal=0.3)
    barrel_r = make_mat("barrel_r", (0.55, 0.18, 0.15), rough=0.5, metal=0.3)
    hazard = make_mat("hazard", (0.85, 0.7, 0.05), rough=0.7)

    bpy.ops.mesh.primitive_plane_add(size=24, location=(0, 0, 0))
    bpy.context.active_object.data.materials.append(concrete)

    # room: back wall (+Y) and side walls (the camera at -Y sees these behind)
    _box((12, 0.2, 6), (0, 4.0, 1.5), (0, 0, 0), wall)
    _box((0.2, 12, 6), (4.0, 0, 1.5), (0, 0, 0), wall)
    _box((0.2, 12, 6), (-4.0, 0, 1.5), (0, 0, 0), wall)

    # props behind / to the sides (clear zone ~1.3m around the feet)
    _box((0.8, 0.8, 0.8), (1.9, 2.6, 0.4), (0, 0, 0.3), crate)
    _box((0.8, 0.8, 0.8), (1.9, 2.6, 1.2), (0, 0, 0.25), crate)
    _box((0.9, 0.9, 0.5), (-2.0, 2.2, 0.25), (0, 0, -0.2), crate)
    _cyl(0.32, 0.95, (-1.5, 3.0, 0.48), (0, 0, 0), barrel_b)
    _cyl(0.32, 0.95, (-0.9, 3.2, 0.48), (0, 0, 0), barrel_r)
    _cyl(0.12, 5.0, (3.4, 0.0, 2.2), (math.radians(90), 0, 0), steel)   # wall pipe
    _cyl(0.12, 5.0, (3.7, 0.0, 1.6), (math.radians(90), 0, 0), steel)
    _box((1.2, 0.12, 0.12), (1.0, 1.6, 0.02), (0, 0, 0.5), hazard)      # floor stripe
    _box((1.2, 0.12, 0.12), (-1.2, 1.7, 0.02), (0, 0, -0.4), hazard)

    return add_lights([((2.5, -1.0, 3.0), 1400), ((-2.5, 0.5, 3.0), 1000),
                       ((0.0, 2.5, 3.2), 800)])


def list_hdris(d):
    import glob
    return sorted(glob.glob(os.path.join(d, "*.hdr")) +
                  glob.glob(os.path.join(d, "*.exr")))


def set_world_hdri(path, rot_z):
    """Poly Haven HDRI as the world: lighting + photographic backdrop in one.
    Random Z rotation per clip varies which part sits behind the feet."""
    w = bpy.context.scene.world or bpy.data.worlds.new("W")
    bpy.context.scene.world = w
    try:
        w.use_nodes = True
    except Exception:
        pass
    nt = w.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    env = nt.nodes.new("ShaderNodeTexEnvironment")
    env.image = bpy.data.images.load(path, check_existing=True)
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Rotation"].default_value[2] = rot_z
    tc = nt.nodes.new("ShaderNodeTexCoord")
    nt.links.new(tc.outputs["Generated"], mp.inputs["Vector"])
    nt.links.new(mp.outputs["Vector"], env.inputs["Vector"])
    nt.links.new(env.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])


def _find_map(d, *keys):
    import glob
    files = [f for ext in ("png", "jpg", "jpeg", "exr", "tif")
             for f in glob.glob(os.path.join(d, "*." + ext))]
    for f in files:
        n = os.path.basename(f).lower()
        if any(k in n for k in keys):
            return f
    return None


def build_floor_textured(tex_dir, tiling=8.0):
    """Floor from a Poly Haven PBR set (auto-detects diff/rough/normal maps).
    Falls back to flat concrete if no diffuse map is found."""
    bpy.ops.mesh.primitive_plane_add(size=24, location=(0, 0, 0))
    floor = bpy.context.active_object
    diff = _find_map(tex_dir, "diff", "albedo", "_col", "color")
    rough = _find_map(tex_dir, "rough", "_arm")
    norm = _find_map(tex_dir, "nor_gl", "normal", "_nor")
    if not diff:
        floor.data.materials.append(make_mat("concrete", (0.34, 0.34, 0.36), 0.95))
        return floor
    m = bpy.data.materials.new("floor_pbr")
    m.use_nodes = True
    nt = m.node_tree; nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (tiling, tiling, tiling)
    nt.links.new(tc.outputs["UV"], mp.inputs["Vector"])

    def tex(path, non_color):
        t = nt.nodes.new("ShaderNodeTexImage")
        t.image = bpy.data.images.load(path, check_existing=True)
        if non_color:
            try:
                t.image.colorspace_settings.name = "Non-Color"
            except Exception:
                pass
        nt.links.new(mp.outputs["Vector"], t.inputs["Vector"])
        return t

    nt.links.new(tex(diff, False).outputs["Color"], bsdf.inputs["Base Color"])
    if rough:
        nt.links.new(tex(rough, True).outputs["Color"], bsdf.inputs["Roughness"])
    if norm:
        nmap = nt.nodes.new("ShaderNodeNormalMap")
        nt.links.new(tex(norm, True).outputs["Color"], nmap.inputs["Color"])
        nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    floor.data.materials.append(m)
    return floor


def setup_world(scene_kind="industrial", hdri_dir="", floor_tex_dir=""):
    if scene_kind == "hdri":
        hdris = list_hdris(hdri_dir)
        if not hdris:
            raise RuntimeError("no .hdr/.exr files in --hdri-dir: " + hdri_dir)
        if floor_tex_dir:
            build_floor_textured(floor_tex_dir)
        else:
            bpy.ops.mesh.primitive_plane_add(size=24, location=(0, 0, 0))
            bpy.context.active_object.data.materials.append(
                make_mat("concrete", (0.34, 0.34, 0.36), 0.95))
        return add_lights([((2.5, -1.0, 3.0), 250)]), hdris
    if scene_kind == "industrial":
        return build_industrial(), []
    return build_plain(), []


def make_camera():
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    bpy.context.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    return cam


def aim_camera(cam, arm, foot_len):
    """Frame the feet from a randomized front-ish view, at the REST pose."""
    bpy.context.scene.frame_set(0)
    bpy.context.view_layer.update()
    feet_mid = (bw(arm, "LeftFoot") + bw(arm, "RightFoot")) * 0.5
    target = Vector((feet_mid.x, feet_mid.y, foot_len * 1.2))
    az = math.radians(random.uniform(-22, 22))
    el = math.radians(random.uniform(8, 20))
    dist = foot_len * random.uniform(7.0, 9.5)
    cam.location = target + Vector((dist * math.sin(az) * math.cos(el),
                                    -dist * math.cos(az) * math.cos(el),
                                    dist * math.sin(el)))
    cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam.data.lens = random.uniform(35, 50)


def setup_render(fps):
    sc = bpy.context.scene
    sc.render.resolution_x = RES_W
    sc.render.resolution_y = RES_H
    sc.render.fps = fps
    sc.render.image_settings.file_format = "PNG"     # 5.x: encode to mp4 via ffmpeg
    for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
        try:
            sc.render.engine = eng; break
        except Exception:
            continue


# ---- world-intent rotation (axis-agnostic) ---------------------------------
def local_axis(rest3x3, world_axis):
    v = rest3x3.inverted() @ Vector(world_axis)
    v.normalize()
    return v


def quat_key(pb, axis_local, angle, frame):
    pb.rotation_mode = "QUATERNION"
    pb.rotation_quaternion = Quaternion(axis_local, angle)
    pb.keyframe_insert("rotation_quaternion", frame=frame)


def rest_key(pb, frame):
    pb.rotation_mode = "QUATERNION"
    pb.rotation_quaternion = (1, 0, 0, 0)
    pb.location = (0, 0, 0)
    pb.keyframe_insert("rotation_quaternion", frame=frame)
    pb.keyframe_insert("location", frame=frame)


def keep_foot_flat(arm, foot_pb, M0_rest, frame):
    """After the hip rotates the leg, restore the foot's REST world ORIENTATION
    (keeping its new position). The foot then relocates RIGIDLY -- toe and ankle
    translate together -- so the recognizer reads a clean base translation (move)
    instead of toe articulation, which a hip 'arc' would otherwise produce and
    which reads as swipe/tap."""
    bpy.context.scene.frame_set(frame)
    bpy.context.view_layer.update()
    cur = arm.matrix_world @ foot_pb.matrix
    desired = Matrix.Translation(cur.translation) @ M0_rest.to_3x3().to_4x4()
    foot_pb.rotation_mode = "QUATERNION"
    foot_pb.matrix = arm.matrix_world.inverted() @ desired
    foot_pb.keyframe_insert("location", frame=frame)
    foot_pb.keyframe_insert("rotation_quaternion", frame=frame)


def arms_down(arm, frame):
    """Lower arms from the T-pose (off-frame, but keeps the stance natural)."""
    for side, sgn in (("Left", 1), ("Right", -1)):
        try:
            ab = arm.pose.bones[PFX + side + "Arm"]
            ab.rotation_mode = "XYZ"
            ab.rotation_euler = (0, 0, sgn * math.radians(68))
            ab.keyframe_insert("rotation_euler", frame=frame)
        except KeyError:
            pass


def foot_forward(arm, side):
    f = bw(arm, side + "Toe_End") - bw(arm, side + "Foot")
    f.z = 0
    return f.normalized() if f.length > 1e-6 else Vector((0, -1, 0))


def animate_idle(arm, foot_len, rest):
    """A 'no gesture' clip: stand with small sway + micro foot motion, all kept
    WELL BELOW the gesture thresholds. The recognizer should fire nothing here;
    any fire is a false positive. (A frozen statue would be too easy a test.)"""
    fps = args.fps
    f0 = int(fps * 1.5)
    total = f0 + int(fps * 2.0)
    side = random.choice(["Left", "Right"])
    R = rest[side]
    foot_pb = arm.pose.bones[PFX + side + "Foot"]

    for s in ("Left", "Right"):
        for b in ("Foot", "UpLeg"):
            pb = arm.pose.bones[PFX + s + b]
            rest_key(pb, 0); rest_key(pb, f0)
    arms_down(arm, 0)
    arm.location = (0, 0, 0)
    arm.keyframe_insert("location", frame=0)
    arm.keyframe_insert("location", frame=f0)

    for k in range(1, 5):                       # gentle sway after calibration
        fr = f0 + int((total - f0) * k / 4)
        arm.location = (random.uniform(-0.10, 0.10) * foot_len,
                        random.uniform(-0.06, 0.06) * foot_len, 0.0)
        arm.keyframe_insert("location", frame=fr)
        quat_key(foot_pb, R["swipe_ax"], math.radians(random.uniform(-3, 3)), fr)
    arm.location = (0, 0, 0); arm.keyframe_insert("location", frame=total)
    rest_key(foot_pb, total)
    return total, side, dict(label="idle", foot=side, fps=fps)


# ----------------------------- animation --------------------------------------
def outward_sign(arm, side, bone, ax_local, measure_bone, outward, test_deg=15):
    """Pick the rotation sign that moves `measure_bone` OUTWARD (away from body).
    Used for swipe (foot yaw -> toe out) and move (hip abduction -> foot out)."""
    pb = arm.pose.bones[PFX + side + bone]
    pb.rotation_mode = "QUATERNION"
    base = bw(arm, side + measure_bone)
    best, bestd = 1, -1e18
    for sgn in (1, -1):
        pb.rotation_quaternion = Quaternion(ax_local, math.radians(test_deg) * sgn)
        bpy.context.view_layer.update()
        disp = bw(arm, side + measure_bone) - base
        if disp.x * outward.x + disp.y * outward.y > bestd:
            bestd, best = disp.x * outward.x + disp.y * outward.y, sgn
    pb.rotation_quaternion = (1, 0, 0, 0); bpy.context.view_layer.update()
    return best
def animate_clip(arm, label, foot_len, rest):
    fps = args.fps
    rest_n = int(fps * 1.5)
    move_n = int(fps * random.uniform(0.40, 0.70))
    settle_n = int(fps * 1.0)
    f0, f1, f2 = rest_n, rest_n + move_n, rest_n + move_n + settle_n

    if label.endswith("_left"):
        side = "Left"
    elif label.endswith("_right"):
        side = "Right"
    else:                                   # tap / move_forward / move_backward
        side = random.choice(["Left", "Right"])
    foot_pb = arm.pose.bones[PFX + side + "Foot"]
    upleg_pb = arm.pose.bones[PFX + side + "UpLeg"]
    R = rest[side]

    # rest frames (calibration) on all driven bones + arms + root
    for pb in (foot_pb, upleg_pb):
        rest_key(pb, 0); rest_key(pb, f0)
    arms_down(arm, 0)
    arm.location = (0, 0, 0); arm.keyframe_insert("location", frame=0)

    params = dict(label=label, foot=side, fps=fps, move_frames=move_n)

    if label == "tap":
        amp = math.radians(random.uniform(16, 28))
        # Pivot about the HEEL (floor point just behind the ankle) so the heel
        # stays planted and the toe lifts UP -- nothing dips under the floor.
        A, fwd = R["A"], R["fwd"]
        P = Vector((A.x, A.y, 0.0)) - fwd * (0.30 * foot_len)
        axis = fwd.cross(WORLD_UP).normalized()                # rotates toe UP
        R4 = Matrix.Rotation(amp, 4, axis)
        Wpivot = Matrix.Translation(P) @ R4 @ Matrix.Translation(-P)
        desired = arm.matrix_world.inverted() @ (Wpivot @ R["M0"])
        bpy.context.scene.frame_set(0); bpy.context.view_layer.update()
        foot_pb.rotation_mode = "QUATERNION"
        foot_pb.matrix = desired
        foot_pb.keyframe_insert("location", frame=f1)
        foot_pb.keyframe_insert("rotation_quaternion", frame=f1)
        rest_key(foot_pb, f2)                                  # toe returns
        params["amp_deg"] = round(math.degrees(amp), 1)

    elif label.startswith("swipe"):                            # foot-mapped, toe OUT
        amp = math.radians(random.uniform(18, 35)) * R["swipe_sign"]
        quat_key(foot_pb, R["swipe_ax"], amp, f1)
        rest_key(foot_pb, f2)
        params["amp_deg"] = round(math.degrees(amp), 1)

    elif label in ("move_left", "move_right"):                 # abduct foot OUTWARD
        amp = math.radians(random.uniform(16, 26)) * R["out_sign"]
        quat_key(upleg_pb, R["upleg_ax"], amp, f1)
        keep_foot_flat(arm, foot_pb, R["M0"], f1)              # rigid, not an arc
        rest_key(upleg_pb, f2); rest_key(foot_pb, f2)
        params["amp_deg"] = round(math.degrees(amp), 1)

    else:  # move_forward / move_backward : swing the leg fwd/back at the hip
        d = 1 if label == "move_forward" else -1
        amp = math.radians(random.uniform(14, 24)) * R["fwd_sign"] * d
        quat_key(upleg_pb, R["flex_ax"], amp, f1)
        keep_foot_flat(arm, foot_pb, R["M0"], f1)              # rigid, not an arc
        rest_key(upleg_pb, f2); rest_key(foot_pb, f2)
        params["amp_deg"] = round(math.degrees(amp), 1)

    return f1, f2, side, params




# ----------------------------- render ---------------------------------------
def render_clip(path, fps):
    import shutil, subprocess
    stem = path[:-4] if path.endswith(".mp4") else path
    framedir = stem + "_frames"; os.makedirs(framedir, exist_ok=True)
    bpy.context.scene.render.filepath = os.path.join(framedir, "f_####")
    bpy.ops.render.render(animation=True)
    ff = shutil.which("ffmpeg")
    if ff:
        subprocess.run([ff, "-y", "-framerate", str(fps), "-start_number", "0",
                        "-i", os.path.join(framedir, "f_%04d.png"),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", path], check=False)
        shutil.rmtree(framedir, ignore_errors=True)
    else:
        print("  ffmpeg not on PATH -- left PNG frames in", framedir)


# ----------------------------- main -----------------------------------------
def main():
    clean_scene()
    arm = import_rig(args.fbx)
    scene_kind = "hdri" if args.hdri_dir else args.scene
    lights, hdris = setup_world(scene_kind, args.hdri_dir, args.floor_tex_dir)
    cam = make_camera()
    setup_render(args.fps)

    try:
        ed = bpy.context.preferences.edit
        ed.keyframe_new_interpolation_type = "BEZIER"
        ed.keyframe_new_handle_type = "AUTO_CLAMPED"
    except Exception as e:
        print("keyframe defaults:", e)

    bpy.context.scene.frame_set(0); bpy.context.view_layer.update()
    foot_len = (bw(arm, "LeftToe_End") - bw(arm, "LeftFoot")).length or 0.2
    rest = {}
    for sd in ("Left", "Right"):
        fp = arm.pose.bones[PFX + sd + "Foot"]
        rest[sd] = dict(A=bw(arm, sd + "Foot"),
                        fwd=foot_forward(arm, sd),
                        M0=(arm.matrix_world @ fp.matrix).copy(),
                        foot3=rest_world_3x3(arm, sd + "Foot"),
                        upleg3=rest_world_3x3(arm, sd + "UpLeg"))
    print("foot length:", round(foot_len, 4))

    # outward hip-abduction axis + sign per foot (for the move gesture)
    hips = bw(arm, "Hips")
    for sd in ("Left", "Right"):
        A = rest[sd]["A"]
        outward = Vector((A.x - hips.x, A.y - hips.y, 0.0))
        outward = outward.normalized() if outward.length > 1e-6 else Vector((1, 0, 0))
        rest[sd]["swipe_ax"] = local_axis(rest[sd]["foot3"], WORLD_UP)
        rest[sd]["swipe_sign"] = outward_sign(arm, sd, "Foot",
                                              rest[sd]["swipe_ax"], "Toe_End", outward)
        rest[sd]["upleg_ax"] = local_axis(rest[sd]["upleg3"], rest[sd]["fwd"])
        rest[sd]["out_sign"] = outward_sign(arm, sd, "UpLeg",
                                            rest[sd]["upleg_ax"], "Foot", outward)
        # hip flexion/extension (forward/back leg swing) -- rotate about lateral
        flex_ax = local_axis(rest[sd]["upleg3"], WORLD_UP.cross(rest[sd]["fwd"]))
        rest[sd]["flex_ax"] = flex_ax
        rest[sd]["fwd_sign"] = outward_sign(arm, sd, "UpLeg",
                                            flex_ax, "Foot", rest[sd]["fwd"])

    GESTURES = ["tap", "swipe_left", "swipe_right", "move_left", "move_right",
                "move_forward", "move_backward"]
    counts = {}
    for i in range(args.n):
        label = GESTURES[i % len(GESTURES)]
        if arm.animation_data:
            arm.animation_data_clear()
        arm.location = (0, 0, 0)
        randomize_lights(lights)
        if hdris:
            set_world_hdri(random.choice(hdris), random.uniform(0, 2 * math.pi))

        f1, f2, side, params = animate_clip(arm, label, foot_len, rest)
        bpy.context.scene.frame_start = 0
        bpy.context.scene.frame_end = f2
        aim_camera(cam, arm, foot_len)

        idx = counts.get(label, 0); counts[label] = idx + 1
        stem = os.path.join(args.out, f"{label}_{idx:04d}")
        with open(stem + ".json", "w") as f:
            json.dump(params, f, indent=2)
        render_clip(stem + ".mp4", args.fps)
        print(f"[{i+1}/{args.n}] {label} ({side}) -> {stem}.mp4")

    for j in range(args.idle):
        if arm.animation_data:
            arm.animation_data_clear()
        arm.location = (0, 0, 0)
        randomize_lights(lights)
        if hdris:
            set_world_hdri(random.choice(hdris), random.uniform(0, 2 * math.pi))
        total, side, params = animate_idle(arm, foot_len, rest)
        bpy.context.scene.frame_start = 0
        bpy.context.scene.frame_end = total
        aim_camera(cam, arm, foot_len)
        idx = counts.get("idle", 0); counts["idle"] = idx + 1
        stem = os.path.join(args.out, f"idle_{idx:04d}")
        with open(stem + ".json", "w") as f:
            json.dump(params, f, indent=2)
        render_clip(stem + ".mp4", args.fps)
        print(f"[idle {j+1}/{args.idle}] -> {stem}.mp4")

    print("done:", counts)


if __name__ == "__main__":
    main()
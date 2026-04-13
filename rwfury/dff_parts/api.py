from __future__ import annotations

from ..generic_mesh import GenericMesh
from ..rwbinary import RwBinaryWriter
from .mesh_export import build_generic_mesh_from_indices, expand_bin_mesh_indices
from .models import DffFrame, DffLight, DffLightFlags, Mesh


class DffApiMixin:
    def get_light_frame(self, light: DffLight) -> DffFrame | None:
        if 0 <= light.frame_index < len(self.frames):
            return self.frames[light.frame_index]
        return None

    def iter_lights_with_frames(self):
        for light in self.lights:
            yield light, self.get_light_frame(light)

    def get_lights(self) -> list[dict]:
        """Return real RenderWare lights with resolved frame metadata."""
        return [
            {
                "light": light,
                "frame": frame,
                "frame_name": frame.name if frame else "",
                "type": light.type_name,
                "flags": light.flags_enum,
                "radius": light.radius,
                "color": light.color,
                "spot_angle_degrees": light.spot_angle_degrees,
                "affects_scene": light.affects_scene,
                "affects_world": light.affects_world,
            }
            for light, frame in self.iter_lights_with_frames()
        ]

    def add_light(self, light: DffLight) -> DffLight:
        self.lights.append(light)
        return light

    def add_ambient_light(
        self,
        color: tuple[float, float, float] = (1.0, 1.0, 1.0),
        frame_index: int = 0,
        flags: int | DffLightFlags = DffLightFlags.SCENE,
    ) -> DffLight:
        return self.add_light(DffLight.ambient(color=color, frame_index=frame_index, flags=flags))

    def add_directional_light(
        self,
        color: tuple[float, float, float] = (1.0, 1.0, 1.0),
        frame_index: int = 0,
        flags: int | DffLightFlags = DffLightFlags.SCENE,
    ) -> DffLight:
        return self.add_light(DffLight.directional(color=color, frame_index=frame_index, flags=flags))

    def add_point_light(
        self,
        radius: float,
        color: tuple[float, float, float] = (1.0, 1.0, 1.0),
        frame_index: int = 0,
        flags: int | DffLightFlags = DffLightFlags.SCENE,
    ) -> DffLight:
        return self.add_light(DffLight.point(
            radius=radius,
            color=color,
            frame_index=frame_index,
            flags=flags,
        ))

    def add_spot_light(
        self,
        radius: float,
        angle_degrees: float,
        color: tuple[float, float, float] = (1.0, 1.0, 1.0),
        frame_index: int = 0,
        soft: bool = False,
        flags: int | DffLightFlags = DffLightFlags.SCENE,
    ) -> DffLight:
        return self.add_light(DffLight.spot(
            radius=radius,
            angle_degrees=angle_degrees,
            color=color,
            frame_index=frame_index,
            soft=soft,
            flags=flags,
        ))

    def get_meshes(self) -> list[dict]:
        """Extract mesh data grouped by atomic/material.

        Returns a list of dicts, each with:
            meshes: list[Mesh]  -- one Mesh per material in the geometry
            texture: str        -- diffuse texture name of the first material
            name: str           -- frame name
        """
        results = []

        for atomic in self.atomics:
            geom = self.geometries[atomic.geometry_index]
            frame = self.frames[atomic.frame_index] if atomic.frame_index < len(self.frames) else DffFrame()

            meshes = []
            for mat_idx, material in enumerate(geom.materials):
                mat_tris = [t for t in geom.triangles if t[3] == mat_idx]
                if not mat_tris:
                    continue

                used_indices = sorted({i for a, b, c, _ in mat_tris for i in (a, b, c)})
                remap = {old: new for new, old in enumerate(used_indices)}

                positions = [geom.vertices[i] for i in used_indices]
                indices = [remap[i] for a, b, c, _ in mat_tris for i in (a, b, c)]

                texcoords = []
                for uv_layer in geom.texcoord_sets:
                    texcoords.append([uv_layer[i] for i in used_indices])

                normals = [geom.normals[i] for i in used_indices] if geom.normals else None
                colors = [geom.vertex_colors[i] for i in used_indices] if geom.vertex_colors else None

                meshes.append(Mesh(
                    positions=positions,
                    indices=indices,
                    texcoords=texcoords,
                    normals=normals,
                    vertex_colors=colors,
                ))

            results.append({
                "meshes": meshes,
                "texture": geom.materials[0].texture_name if geom.materials else "",
                "name": frame.name,
            })

        return results

    def to_generic_meshes(self) -> list[GenericMesh]:
        """Export all meshes as flat, format-agnostic GenericMesh objects.

        Each material split in each atomic produces one GenericMesh with:
        - Flat position/normal/texcoord/color arrays
        - Triangle indices (remapped to local vertex space)
        - 4x4 transform from the associated frame
        - Skinning data (if the geometry has a SkinPLG)
        """
        results: list[GenericMesh] = []

        for atomic in self.atomics:
            geom = self.geometries[atomic.geometry_index]
            frame = (self.frames[atomic.frame_index]
                     if atomic.frame_index < len(self.frames) else DffFrame())

            # Build 4x4 row-major transform from frame
            r = frame.rotation_matrix  # 9 floats (3x3 row-major)
            p = frame.position
            xform = [
                r[0], r[1], r[2], 0.0,
                r[3], r[4], r[5], 0.0,
                r[6], r[7], r[8], 0.0,
                p[0], p[1], p[2], 1.0,
            ]

            if geom.bin_mesh and geom.bin_mesh.splits:
                for split in geom.bin_mesh.splits:
                    mesh = build_generic_mesh_from_indices(
                        geom=geom,
                        frame=frame,
                        transform=xform,
                        material_index=split.material_index,
                        source_indices=expand_bin_mesh_indices(
                            split.indices, geom.bin_mesh.flags
                        ),
                    )
                    if mesh:
                        results.append(mesh)
                continue

            for mat_idx, _material in enumerate(geom.materials):
                source_indices = [
                    i for a, b, c, tri_mat in geom.triangles
                    if tri_mat == mat_idx
                    for i in (a, b, c)
                ]
                mesh = build_generic_mesh_from_indices(
                    geom=geom,
                    frame=frame,
                    transform=xform,
                    material_index=mat_idx,
                    source_indices=source_indices,
                )
                if mesh:
                    results.append(mesh)

        return results

    def to_file(self, path: str):
        """Write this DFF back to a binary file."""
        with RwBinaryWriter.to_file(path) as writer:
            self._write(writer)

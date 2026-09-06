"""Stateless camera projection math for the 3D wireframe view."""


def project_point(x, y, z, *, target, cos_yaw, sin_yaw,
                  cos_pitch, sin_pitch, canvas_width, canvas_height,
                  pan_x, pan_y, scale):
    """Project one world-space point using an explicit camera snapshot."""
    tx, ty, tz = target
    x -= tx
    y -= ty
    z -= tz

    # Yaw around vertical Z.
    x1 = cos_yaw * x - sin_yaw * y
    y1 = sin_yaw * x + cos_yaw * y
    z1 = z

    # Pitch around X.
    y2 = cos_pitch * y1 - sin_pitch * z1
    z2 = sin_pitch * y1 + cos_pitch * z1

    sx = canvas_width * 0.5 + pan_x + x1 * scale
    sy = canvas_height * 0.5 + pan_y - z2 * scale
    return sx, sy, y2


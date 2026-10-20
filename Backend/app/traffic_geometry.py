"""Local metric approximation for inspection, not proof of road/travel direction."""
from math import atan2, cos, degrees, hypot, radians


def summarize_geometry(points, latitude, longitude):
    if not points:
        raise ValueError("missing_segment")
    east_scale = 111320 * cos(radians(latitude))
    north_scale = 111320

    def xy(point):
        return ((point["longitude"] - longitude) * east_scale,
                (point["latitude"] - latitude) * north_scale)

    best = None
    segments = zip(points, points[1:]) if len(points) > 1 else [(points[0], points[0])]
    for start, end in segments:
        ax, ay = xy(start)
        bx, by = xy(end)
        dx, dy = bx - ax, by - ay
        squared_length = dx * dx + dy * dy
        t = max(0, min(1, -(ax * dx + ay * dy) / squared_length)) if squared_length else 0
        x, y = ax + t * dx, ay + t * dy
        distance = hypot(x, y)
        if best is None or distance < best[0]:
            best = (distance, x, y, dx, dy)
    distance, x, y, dx, dy = best
    bearing = round((degrees(atan2(dx, dy)) + 360) % 360, 1) if dx or dy else None
    return {
        "point_count": len(points), "start": points[0], "end": points[-1],
        "nearest_point": {"latitude": latitude + y / north_scale,
                          "longitude": longitude + x / east_scale},
        "distance_to_query_point_m": round(distance, 1),
        "polyline_bearing_deg": bearing,
        "bearing_basis": "coordinate_order_near_query_point_not_verified_travel_direction",
        "travel_direction_verified": False,
    }

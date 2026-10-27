"""Generate app/static/atlas.ico (a small node graph on an indigo tile) with the standard library only."""
import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "app" / "static" / "atlas.ico"
BG = (91, 110, 245)
NODES = [(0.30, 0.32, 0.11), (0.70, 0.30, 0.09), (0.50, 0.70, 0.12), (0.22, 0.72, 0.07)]
EDGES = [(0, 1), (0, 2), (1, 2), (2, 3)]


def seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def sample(x, y):
    """RGBA at unit coords."""
    r = 0.2  # corner radius
    cx, cy = min(max(x, r), 1 - r), min(max(y, r), 1 - r)
    if math.hypot(x - cx, y - cy) > r:
        return (0, 0, 0, 0)
    for nx, ny, nr in NODES:
        if math.hypot(x - nx, y - ny) <= nr:
            return (255, 255, 255, 255)
    for a, b in EDGES:
        ax, ay, _ = NODES[a]
        bx, by, _ = NODES[b]
        if seg_dist(x, y, ax, ay, bx, by) <= 0.035:
            return (225, 230, 255, 255)
    return (*BG, 255)


def png(size: int) -> bytes:
    ss = 4
    rows = []
    for j in range(size):
        row = bytearray([0])
        for i in range(size):
            acc = [0, 0, 0, 0]
            for sj in range(ss):
                for si in range(ss):
                    c = sample((i + (si + .5) / ss) / size, (j + (sj + .5) / ss) / size)
                    for k in range(3):
                        acc[k] += c[k] * c[3]
                    acc[3] += c[3]
            a = acc[3] / (ss * ss)
            row += bytes([int(acc[0] / acc[3]) if acc[3] else 0, int(acc[1] / acc[3]) if acc[3] else 0,
                          int(acc[2] / acc[3]) if acc[3] else 0, int(a)])
        rows.append(bytes(row))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def main():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [png(s) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, data in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    OUT.write_bytes(header + entries + b"".join(images))
    print("wrote", OUT)


if __name__ == "__main__":
    main()

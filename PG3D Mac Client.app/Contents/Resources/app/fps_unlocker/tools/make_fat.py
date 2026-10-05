"""Join single-architecture Mach-O files into one universal (fat) file.

Usage: make_fat.py OUTPUT INPUT...   (used by build_mouse_fix.sh)
"""
import struct
import sys

CPU = {0x01000007: "x86_64", 0x0100000C: "arm64"}


def main(out, inputs):
    slices = []
    for path in inputs:
        data = open(path, "rb").read()
        magic, cputype, cpusub = struct.unpack("<III", data[:12])
        if magic != 0xFEEDFACF or cputype not in CPU:
            raise SystemExit(f"{path}: not a 64-bit x86_64/arm64 Mach-O")
        slices.append((cputype, cpusub, data))
    align = 14  # 16 KiB, as lipo uses for arm64
    offset = 1 << align
    header = struct.pack(">II", 0xCAFEBABE, len(slices))
    body = b""
    placed = []
    for cputype, cpusub, data in slices:
        placed.append((cputype, cpusub, offset, len(data)))
        body += data + b"\0" * ((-len(data)) % (1 << align))
        offset += len(data) + ((-len(data)) % (1 << align))
    for cputype, cpusub, off, size in placed:
        header += struct.pack(">IIIII", cputype, cpusub, off, size, align)
    header += b"\0" * ((1 << align) - len(header))
    open(out, "wb").write(header + body)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])

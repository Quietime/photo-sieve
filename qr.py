"""Minimal QR Code encoder for the phone pairing link: byte mode, error correction level M,
versions 1-10 (up to 213 bytes). Follows ISO/IEC 18004; no third-party dependency.

matrix(text) returns a list of rows of booleans (True = dark module), without the quiet zone.
"""
from __future__ import annotations

# Per version (1-10), level M: (error correction codewords per block, [(block count, data codewords per block), ...])
_BLOCKS_M = {
    1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]), 5: (24, [(2, 43)]),
    6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]), 9: (22, [(3, 36), (2, 37)]),
    10: (26, [(4, 43), (1, 44)]),
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42],
          9: [6, 26, 46], 10: [6, 28, 50]}

# GF(256) with the QR polynomial x^8 + x^4 + x^3 + x^2 + 1.
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x; _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100: _x ^= 0x11D
for _i in range(255, 512): _EXP[_i] = _EXP[_i - 255]


def _mul(a, b):
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _rs_remainder(data, degree):
    """Reed-Solomon error correction codewords for one block."""
    generator = [1]
    for i in range(degree):
        nxt = [0] * (len(generator) + 1)
        for j, c in enumerate(generator):
            nxt[j] ^= c; nxt[j + 1] ^= _mul(c, _EXP[i])
        generator = nxt
    remainder = [0] * degree
    for byte in data:
        factor = byte ^ remainder[0]
        remainder = remainder[1:] + [0]
        for j in range(degree):
            remainder[j] ^= _mul(generator[j + 1], factor)
    return remainder


def _capacity(version):
    _, groups = _BLOCKS_M[version]
    return sum(count * size for count, size in groups)


def _codewords(payload, version):
    bits = []
    def put(value, length): bits.extend((value >> (length - 1 - i)) & 1 for i in range(length))
    put(0b0100, 4)                                   # byte mode
    put(len(payload), 8 if version < 10 else 16)
    for byte in payload: put(byte, 8)
    capacity = _capacity(version) * 8
    put(0, min(4, capacity - len(bits)))              # terminator
    while len(bits) % 8: bits.append(0)
    data = [int(''.join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    while len(data) < capacity // 8: data.append(pad[(len(data) - len(bits) // 8) % 2])
    ec_len, groups = _BLOCKS_M[version]
    blocks, offset = [], 0
    for count, size in groups:
        for _ in range(count):
            block = data[offset:offset + size]; offset += size
            blocks.append((block, _rs_remainder(block, ec_len)))
    out = []
    for i in range(max(len(b) for b, _ in blocks)):
        out.extend(b[i] for b, _ in blocks if i < len(b))
    for i in range(ec_len):
        out.extend(e[i] for _, e in blocks)
    return out


def _bch(value, poly, length):
    v = value << (poly.bit_length() - 1)
    while v.bit_length() >= poly.bit_length():
        v ^= poly << (v.bit_length() - poly.bit_length())
    return (value << (poly.bit_length() - 1)) | v


_MASKS = [
    lambda r, c: (r + c) % 2 == 0, lambda r, c: r % 2 == 0, lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0, lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0, lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _base(version):
    size = 17 + 4 * version
    grid = [[False] * size for _ in range(size)]
    fixed = [[False] * size for _ in range(size)]
    def set_(r, c, dark):
        grid[r][c] = dark; fixed[r][c] = True
    for r0, c0 in ((0, 0), (0, size - 7), (size - 7, 0)):
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = r0 + r, c0 + c
                if 0 <= rr < size and 0 <= cc < size:
                    ring = max(abs(r - 3), abs(c - 3))
                    set_(rr, cc, ring != 2 and ring != 4 and 0 <= r <= 6 and 0 <= c <= 6)
    for i in range(8, size - 8):
        set_(6, i, i % 2 == 0); set_(i, 6, i % 2 == 0)
    centres = _ALIGN[version]
    last = len(centres) - 1
    for i, r in enumerate(centres):
        for j, c in enumerate(centres):
            if (i, j) in ((0, 0), (0, last), (last, 0)): continue   # those corners hold finder patterns
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    set_(r + dr, c + dc, max(abs(dr), abs(dc)) != 1)
    set_(size - 8, 8, True)                          # the dark module
    for i in range(9):                               # reserve format areas
        if not fixed[8][i]: set_(8, i, False)
        if not fixed[i][8]: set_(i, 8, False)
    for i in range(8):
        set_(8, size - 1 - i, False); set_(size - 1 - i, 8, False)
    if version >= 7:
        info = _bch(version, 0x1F25, 12)
        for i in range(18):
            bit = (info >> i) & 1
            set_(size - 11 + i % 3, i // 3, bool(bit)); set_(i // 3, size - 11 + i % 3, bool(bit))
    return grid, fixed


def _place(grid, fixed, codewords):
    size = len(grid); bits = [(b >> (7 - i)) & 1 for b in codewords for i in range(8)]; k = 0
    col = size - 1; upward = True
    while col > 0:
        if col == 6: col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for c in (col, col - 1):
                if not fixed[r][c]:
                    grid[r][c] = bool(bits[k]) if k < len(bits) else False; k += 1
        upward = not upward; col -= 2


def _format(grid, mask):
    size = len(grid)
    info = _bch((0b00 << 3) | mask, 0x537, 10) ^ 0x5412   # level M = 00
    bit = lambda i: bool((info >> i) & 1)
    for i in range(6): grid[i][8] = bit(i)              # first copy, around the top-left finder
    grid[7][8] = bit(6); grid[8][8] = bit(7); grid[8][7] = bit(8)
    for i in range(9, 15): grid[8][14 - i] = bit(i)
    for i in range(8): grid[8][size - 1 - i] = bit(i)    # second copy, split by the other finders
    for i in range(8, 15): grid[size - 15 + i][8] = bit(i)
    grid[size - 8][8] = True


def _penalty(grid):
    size = len(grid); score = 0
    for lines in (grid, [list(col) for col in zip(*grid)]):
        for line in lines:
            run, prev = 0, None
            for v in line:
                if v == prev: run += 1
                else:
                    if run >= 5: score += run - 2
                    run, prev = 1, v
            if run >= 5: score += run - 2
            text = ''.join('1' if v else '0' for v in line)
            score += 40 * (text.count('10111010000') + text.count('00001011101'))
    for r in range(size - 1):
        for c in range(size - 1):
            v = grid[r][c]
            if v == grid[r][c + 1] == grid[r + 1][c] == grid[r + 1][c + 1]: score += 3
    dark = sum(map(sum, grid)); score += abs(dark * 20 - size * size * 10) // (size * size) * 10
    return score


def matrix(text):
    payload = text.encode('utf-8')
    version = next((v for v in range(1, 11) if _capacity(v) >= len(payload) + (2 if v < 10 else 3)), None)
    if version is None: raise ValueError('text too long for a version 1-10 QR code')
    codewords = _codewords(payload, version)
    best = None
    for mask, rule in enumerate(_MASKS):
        grid, fixed = _base(version)
        _place(grid, fixed, codewords)
        for r in range(len(grid)):
            for c in range(len(grid)):
                if not fixed[r][c] and rule(r, c): grid[r][c] = not grid[r][c]
        _format(grid, mask)
        score = _penalty(grid)
        if best is None or score < best[0]: best = (score, grid)
    return best[1]

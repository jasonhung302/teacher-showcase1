"""純 Python QR Code 產生器（不需安裝額外套件）。

支援：Byte 模式、錯誤更正等級 M（約 15% 容錯）、版本 1–10（最多約 210 個英數字元，網址綽綽有餘）。
輸出：SVG 字串（網頁顯示、可無限放大印刷）或 PNG bytes（下載用）。
參考 ISO/IEC 18004 規格實作。
"""
import io

# 版本 → (每區塊 EC 碼字數, [(區塊數, 每區塊資料碼字數), ...])，錯誤更正等級 M
_EC_M = {
    1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]),
    5: (24, [(2, 43)]), 6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]),
    9: (22, [(3, 36), (2, 37)]), 10: (26, [(4, 43), (1, 44)]),
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
          7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
_EC_LEVEL_BITS_M = 0


# ---------------------------------------------------------------- Reed-Solomon (GF(256), 0x11D)
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gf_mul(a, b):
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(degree):
    g = [1]
    for i in range(degree):
        nxt = [0] * (len(g) + 1)
        for j, coef in enumerate(g):
            nxt[j] ^= coef
            nxt[j + 1] ^= _gf_mul(coef, _EXP[i])
        g = nxt
    return g


def _rs_remainder(data, degree):
    gen = _rs_generator(degree)
    rem = [0] * degree
    for b in data:
        factor = b ^ rem[0]
        rem = rem[1:] + [0]
        for i in range(degree):
            rem[i] ^= _gf_mul(gen[i + 1], factor)
    return rem


# ---------------------------------------------------------------- 編碼
def _data_capacity(version):
    _ec, groups = _EC_M[version]
    return sum(n * k for n, k in groups)


def _encode_codewords(data, version):
    count_bits = 8 if version <= 9 else 16
    bits = []

    def put(value, n):
        bits.extend((value >> i) & 1 for i in range(n - 1, -1, -1))

    put(0b0100, 4)                 # Byte 模式
    put(len(data), count_bits)
    for b in data:
        put(b, 8)
    cap_bits = _data_capacity(version) * 8
    put(0, min(4, cap_bits - len(bits)))          # 結束符
    put(0, (-len(bits)) % 8)                      # 補齊位元組
    codewords = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(codewords) < _data_capacity(version):
        codewords.append(pad)
        pad ^= 0xEC ^ 0x11

    # 分區塊、計算 EC、交錯排列
    ec_len, groups = _EC_M[version]
    blocks, pos = [], 0
    for n, k in groups:
        for _ in range(n):
            blk = codewords[pos:pos + k]
            pos += k
            blocks.append((blk, _rs_remainder(blk, ec_len)))
    out = []
    for i in range(max(len(b[0]) for b in blocks)):
        for d, _e in blocks:
            if i < len(d):
                out.append(d[i])
    for i in range(ec_len):
        for _d, e in blocks:
            out.append(e[i])
    return out


# ---------------------------------------------------------------- 矩陣
class _Matrix:
    def __init__(self, version):
        self.v = version
        self.size = version * 4 + 17
        self.mod = [[False] * self.size for _ in range(self.size)]
        self.func = [[False] * self.size for _ in range(self.size)]

    def set_func(self, x, y, dark):
        self.mod[y][x] = dark
        self.func[y][x] = True

    def draw_function_patterns(self):
        s = self.size
        for i in range(s):                          # 定時圖樣
            self.set_func(6, i, i % 2 == 0)
            self.set_func(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (s - 4, 3), (3, s - 4)):   # 定位圖樣
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < s and 0 <= y < s:
                        d = max(abs(dx), abs(dy))
                        self.set_func(x, y, d not in (2, 4))
        pos = _ALIGN[self.v]                        # 校正圖樣
        last = len(pos) - 1
        for i, ax in enumerate(pos):
            for j, ay in enumerate(pos):
                if (i == 0 and j == 0) or (i == 0 and j == last) or (i == last and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_func(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(0)                         # 先佔位
        if self.v >= 7:
            rem = self.v
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.v << 12 | rem
            for i in range(18):
                bit = (bits >> i) & 1 == 1
                a, b = s - 11 + i % 3, i // 3
                self.set_func(a, b, bit)
                self.set_func(b, a, bit)

    def draw_format(self, mask):
        s = self.size
        data = _EC_LEVEL_BITS_M << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412

        def bit(i):
            return (bits >> i) & 1 == 1
        for i in range(6):
            self.set_func(8, i, bit(i))
        self.set_func(8, 7, bit(6))
        self.set_func(8, 8, bit(7))
        self.set_func(7, 8, bit(8))
        for i in range(9, 15):
            self.set_func(14 - i, 8, bit(i))
        for i in range(8):
            self.set_func(s - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set_func(8, s - 15 + i, bit(i))
        self.set_func(8, s - 8, True)               # 固定暗點

    def place_data(self, codewords):
        s = self.size
        bits = [(cw >> (7 - i)) & 1 for cw in codewords for i in range(8)]
        i = 0
        right = s - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(s):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = s - 1 - vert if upward else vert
                    if not self.func[y][x]:
                        self.mod[y][x] = i < len(bits) and bits[i] == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask):
        f = [lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0,
             lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
             lambda x, y: x * y % 2 + x * y % 3 == 0, lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
             lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0][mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.func[y][x] and f(x, y):
                    self.mod[y][x] = not self.mod[y][x]

    def penalty(self):
        s, m, score = self.size, self.mod, 0
        for line_getter in (lambda i, j: m[i][j], lambda i, j: m[j][i]):
            for i in range(s):
                run, prev = 0, None
                seq = [line_getter(i, j) for j in range(s)]
                for c in seq:
                    if c == prev:
                        run += 1
                    else:
                        if run >= 5:
                            score += run - 2
                        run, prev = 1, c
                if run >= 5:
                    score += run - 2
                pattern = [True, False, True, True, True, False, True]
                padded = [False] * 4 + seq + [False] * 4
                for j in range(len(padded) - 10):
                    if padded[j + 2:j + 9] == pattern and (
                            not any(padded[j - 2 + 2:j + 2]) or not any(padded[j + 9:j + 13])):
                        score += 40
        for y in range(s - 1):
            for x in range(s - 1):
                c = m[y][x]
                if c == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    score += 3
        dark = sum(sum(r) for r in m)
        total = s * s
        score += (abs(dark * 20 - total * 10) + total - 1) // total * 10
        return score


def make_matrix(text):
    data = text.encode("utf-8")
    for version in range(1, 11):
        count_bits = 8 if version <= 9 else 16
        if 4 + count_bits + len(data) * 8 <= _data_capacity(version) * 8:
            break
    else:
        raise ValueError("內容太長，無法產生 QR Code")
    codewords = _encode_codewords(data, version)
    best, best_score = None, None
    for mask in range(8):
        mtx = _Matrix(version)
        mtx.draw_function_patterns()
        mtx.place_data(codewords)
        mtx.apply_mask(mask)
        mtx.draw_format(mask)
        sc = mtx.penalty()
        if best_score is None or sc < best_score:
            best, best_score = mtx, sc
    return best.mod


def to_svg(text, border=4, dark="#1c2733", light="#ffffff"):
    m = make_matrix(text)
    n = len(m) + border * 2
    path = "".join(f"M{x + border},{y + border}h1v1h-1z"
                   for y, row in enumerate(m) for x, c in enumerate(row) if c)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges">'
            f'<rect width="100%" height="100%" fill="{light}"/><path d="{path}" fill="{dark}"/></svg>')


def to_png(text, scale=12, border=4):
    from PIL import Image
    m = make_matrix(text)
    n = len(m) + border * 2
    img = Image.new("L", (n, n), 255)
    px = img.load()
    for y, row in enumerate(m):
        for x, c in enumerate(row):
            if c:
                px[x + border, y + border] = 0
    img = img.resize((n * scale, n * scale), Image.NEAREST)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()

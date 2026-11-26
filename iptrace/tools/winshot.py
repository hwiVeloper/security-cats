"""창 캡처 (Windows, 추가 패키지 없음). 문서용 화면 이미지를 만들 때 쓴다."""
import ctypes
import struct
import zlib


def capture_png(win, path):
    import ctypes.wintypes as wt
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    win.update()
    hwnd = user32.GetParent(win.winfo_id())
    frame, wrect = wt.RECT(), wt.RECT()
    ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(frame), ctypes.sizeof(frame))
    user32.GetWindowRect(hwnd, ctypes.byref(wrect))
    w, h = wrect.right - wrect.left, wrect.bottom - wrect.top
    hdc = user32.GetWindowDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mdc, bmp)
    user32.PrintWindow(hwnd, mdc, 2)

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
                    ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16),
                    ("biCompression", ctypes.c_uint32), ("biSizeImage", ctypes.c_uint32),
                    ("biXPelsPerMeter", ctypes.c_int32), ("biYPelsPerMeter", ctypes.c_int32),
                    ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]
    bih = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bih), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(hwnd, hdc)

    ox, oy = frame.left - wrect.left, frame.top - wrect.top
    cw, ch = frame.right - frame.left, frame.bottom - frame.top
    raw, rows = buf.raw, []
    for y in range(oy, oy + ch):
        line = raw[(y * w + ox) * 4:(y * w + ox + cw) * 4]
        rgb = bytearray(cw * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = line[2::4], line[1::4], line[0::4]
        rows.append(b"\x00" + bytes(rgb))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", cw, ch, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))

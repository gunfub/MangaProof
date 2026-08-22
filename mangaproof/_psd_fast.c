/* MangaProof psd 解码加速（纯 C，经 ctypes 加载）。

 * mp_delta_decode_8 / mp_delta_decode_16：ZIP_WITH_PREDICTION 的
   delta 解码（psd-tools 原实现为逐像素纯 Python 循环，50MB 通道
   约 1s，本实现 200× 加速）。

   行为与 psd-tools 原实现严格一致，由 mangaproof/psd_accel.py
   运行时补丁进 psd_tools.compression，编译产物缺失时自动回退。

   构建：python scripts/build_accel.py（gcc / clang / cl，无需 Python.h）
*/

#include <stdint.h>

/* 8-bit delta 解码（逐行预测，行为与 psd-tools._delta_decode 一致）。
   out 需预分配 w*h 字节。 */
void mp_delta_decode_8(const unsigned char *src, long long w, long long h,
                       unsigned char *out) {
    long long y, x, base, pos;
    if (w <= 0 || h <= 0) {
        return;
    }
    for (y = 0; y < h; y++) {
        base = y * w;
        out[base] = src[base];
        for (x = 1; x < w; x++) {
            pos = base + x;
            out[pos] = (unsigned char)((src[pos] + out[pos - 1]) & 0xFF);
        }
    }
}

/* 16-bit delta 解码。输入为大端 packed uint16（psd-tools
   be_array_from_bytes 语义）；输出与 psd-tools 一致：按主机序
   （小端）读入数值做 delta，随后 byteswap 后 tobytes —— 即输出
   为大端 packed uint16。out 需预分配 w*h*2 字节。 */
void mp_delta_decode_16(const unsigned char *src, long long w, long long h,
                        unsigned char *out) {
    long long y, x, pos;
    unsigned int value;
    unsigned short *dst = (unsigned short *)out;
    if (w <= 0 || h <= 0) {
        return;
    }
    for (y = 0; y < h; y++) {
        pos = y * w;
        value = ((unsigned int)src[pos * 2] << 8) | src[pos * 2 + 1];
        dst[pos] = (unsigned short)value;
        for (x = 1; x < w; x++) {
            pos = y * w + x;
            value = ((((unsigned int)src[pos * 2] << 8) | src[pos * 2 + 1])
                     + dst[pos - 1]) & 0xFFFF;
            dst[pos] = (unsigned short)value;
        }
    }
    /* 大端写出：主机序（小端）数值 → byteswap → 大端字节流 */
    for (pos = 0; pos < w * h; pos++) {
        unsigned short v = dst[pos];
        out[pos * 2] = (unsigned char)(v >> 8);
        out[pos * 2 + 1] = (unsigned char)(v & 0xFF);
    }
}

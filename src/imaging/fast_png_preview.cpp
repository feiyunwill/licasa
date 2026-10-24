#include "imaging/fast_png_preview.h"
#include "imaging/image_decode_contract.h"

#include <QColorSpace>
#include <QDebug>
#include <QElapsedTimer>
#include <QFile>
#include <QtEndian>

#include <png.h>

#include <cstring>
#include <limits>

namespace Licasa {
namespace {
constexpr quint32 kMaximumDimension = 20000;

quint32 bigEndian32(const uchar* bytes)
{
    return (quint32(bytes[0]) << 24) | (quint32(bytes[1]) << 16) | (quint32(bytes[2]) << 8) |
           quint32(bytes[3]);
}

QSize eligibleSize(QFile& file, quint64 maximumPixels)
{
#if Q_BYTE_ORDER != Q_LITTLE_ENDIAN
    Q_UNUSED(file);
    Q_UNUSED(maximumPixels);
    return {};
#else
    // The second chunk must be IDAT. Color profiles, gamma, animation, and
    // other ancillary metadata keep their established Qt decoder path.
    uchar header[41] = {};
    if (!file.seek(0) ||
        file.read(reinterpret_cast<char*>(header), sizeof(header)) != qint64(sizeof(header)) ||
        png_sig_cmp(header, 0, 8) != 0 || bigEndian32(header + 8) != 13 ||
        std::memcmp(header + 12, "IHDR", 4) != 0 || std::memcmp(header + 37, "IDAT", 4) != 0 ||
        header[24] != 8 ||
        (header[25] != PNG_COLOR_TYPE_RGB && header[25] != PNG_COLOR_TYPE_RGBA) ||
        header[26] != PNG_COMPRESSION_TYPE_BASE || header[27] != PNG_FILTER_TYPE_BASE ||
        header[28] != PNG_INTERLACE_NONE) {
        return {};
    }
    const quint32 width = bigEndian32(header + 16);
    const quint32 height = bigEndian32(header + 20);
    if (width == 0 || height == 0 || width > kMaximumDimension || height > kMaximumDimension ||
        !ImageDecodeContract::boundedPngFullBase(QSize(int(width), int(height)), maximumPixels)) {
        return {};
    }
    return QSize(int(width), int(height));
#endif
}

void readFromQFile(png_structp png, png_bytep bytes, png_size_t length)
{
    auto* file = static_cast<QFile*>(png_get_io_ptr(png));
    if (!file || length > png_size_t(std::numeric_limits<qint64>::max()) ||
        file->read(reinterpret_cast<char*>(bytes), qint64(length)) != qint64(length)) {
        png_error(png, "PNG read failed");
    }
}

void ignorePngWarning(png_structp, png_const_charp) {}
void ignorePngError(png_structp png, png_const_charp) { longjmp(png_jmpbuf(png), 1); }
} // namespace

bool fastPngBaseEligible(const QString& path, quint64 maximumPixels)
{
    if (qEnvironmentVariableIsSet("LICASA_DISABLE_FAST_PNG_PREVIEW")) {
        return false;
    }
    QFile file(path);
    return file.open(QIODevice::ReadOnly) && eligibleSize(file, maximumPixels).isValid();
}

static FastPngPreview readFastPng(const QString& path, const QSize& previewSize,
                                  quint64 maximumPixels, qsizetype maximumPreviewBytes,
                                  const std::atomic_bool* cancelled, bool retainFullBase)
{
    FastPngPreview result;
    if (maximumPreviewBytes <= 0 || !previewSize.isValid() || previewSize.isEmpty() ||
        (cancelled && cancelled->load(std::memory_order_relaxed))) {
        return result;
    }
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return result;
    }
    const QSize sourceSize = eligibleSize(file, maximumPixels);
    if (!sourceSize.isValid() ||
        quint64(previewSize.width()) * quint64(previewSize.height()) >
            quint64(maximumPreviewBytes) / 4 ||
        !file.seek(0)) {
        return result;
    }

    const bool tracing = qEnvironmentVariableIsSet("LICASA_TRACE_FAST_PNG");
    QElapsedTimer timer;
    if (tracing) {
        timer.start();
    }
    png_structp png =
        png_create_read_struct(PNG_LIBPNG_VER_STRING, nullptr, ignorePngError, ignorePngWarning);
    if (!png) {
        return result;
    }
    png_infop info = png_create_info_struct(png);
    if (!info) {
        png_destroy_read_struct(&png, nullptr, nullptr);
        return result;
    }
    if (setjmp(png_jmpbuf(png))) {
        png_destroy_read_struct(&png, &info, nullptr);
        return {};
    }
    png_set_user_limits(png, kMaximumDimension, kMaximumDimension);
    png_set_chunk_malloc_max(png, 8 * 1024 * 1024);
    png_set_read_fn(png, &file, readFromQFile);
    png_read_info(png, info);
    png_uint_32 width = 0, height = 0;
    int bitDepth = 0, colorType = 0, interlace = 0, compression = 0, filter = 0;
    png_get_IHDR(png, info, &width, &height, &bitDepth, &colorType, &interlace, &compression,
                 &filter);
    if (width != png_uint_32(sourceSize.width()) || height != png_uint_32(sourceSize.height()) ||
        bitDepth != 8 || (colorType != PNG_COLOR_TYPE_RGB && colorType != PNG_COLOR_TYPE_RGBA) ||
        interlace != PNG_INTERLACE_NONE) {
        png_destroy_read_struct(&png, &info, nullptr);
        return {};
    }

    // QImage::Format_ARGB32 is BGRA in memory on little-endian targets.
    png_set_bgr(png);
    if (colorType == PNG_COLOR_TYPE_RGB) {
        png_set_filler(png, 0xff, PNG_FILLER_AFTER);
    }
    png_read_update_info(png, info);
    if (png_get_rowbytes(png, info) != png_size_t(width) * 4) {
        png_destroy_read_struct(&png, &info, nullptr);
        return {};
    }
    const QImage::Format imageFormat =
        colorType == PNG_COLOR_TYPE_RGB ? QImage::Format_RGB32 : QImage::Format_ARGB32;
    if (retainFullBase) {
        result.fullBase = QImage(sourceSize, imageFormat);
    }
    QImage row;
    if (!retainFullBase) {
        row = QImage(int(width), 1, imageFormat);
    }
    result.preview = QImage(previewSize, imageFormat);
    if ((retainFullBase && result.fullBase.isNull()) || (!retainFullBase && row.isNull()) ||
        result.preview.isNull() ||
        result.fullBase.sizeInBytes() >
            qsizetype(ImageDecodeContract::maximumDecodedBaseCacheBytes) ||
        result.preview.sizeInBytes() > maximumPreviewBytes) {
        png_destroy_read_struct(&png, &info, nullptr);
        return {};
    }

    int previewY = 0;
    for (png_uint_32 y = 0; y < height; ++y) {
        if (cancelled && cancelled->load(std::memory_order_relaxed)) {
            png_destroy_read_struct(&png, &info, nullptr);
            return {};
        }
        uchar* decodedRow = retainFullBase ? result.fullBase.scanLine(int(y)) : row.scanLine(0);
        png_read_row(png, decodedRow, nullptr);
        while (previewY < previewSize.height() &&
               quint64(previewY) * height / quint64(previewSize.height()) == y) {
            const auto* source = reinterpret_cast<const QRgb*>(decodedRow);
            auto* output = reinterpret_cast<QRgb*>(result.preview.scanLine(previewY));
            for (int x = 0; x < previewSize.width(); ++x) {
                const int sourceX = int(quint64(x) * width / quint64(previewSize.width()));
                output[x] = source[sourceX];
            }
            ++previewY;
        }
    }
    png_read_end(png, info);
    png_destroy_read_struct(&png, &info, nullptr);
    if (previewY != previewSize.height() ||
        (cancelled && cancelled->load(std::memory_order_relaxed))) {
        return {};
    }
    const QColorSpace displaySpace(QColorSpace::SRgb);
    if (retainFullBase) {
        result.fullBase.setColorSpace(displaySpace);
    }
    result.preview.setColorSpace(displaySpace);
    if (tracing) {
        qInfo().noquote() << "[Licasa] Fast PNG preview:" << "source=" << sourceSize
                          << "output=" << previewSize
                          << "elapsed_ms=" << double(timer.nsecsElapsed()) / 1000000.0;
    }
    return result;
}

FastPngPreview readFastPngPreview(const QString& path, const QSize& previewSize,
                                  quint64 maximumPixels, qsizetype maximumPreviewBytes,
                                  const std::atomic_bool* cancelled)
{
    return readFastPng(path, previewSize, maximumPixels, maximumPreviewBytes, cancelled, true);
}

QImage readFastPngNearbyPreview(const QString& path, const QSize& previewSize,
                                quint64 maximumPixels, qsizetype maximumPreviewBytes,
                                const std::atomic_bool* cancelled)
{
    return readFastPng(path, previewSize, maximumPixels, maximumPreviewBytes, cancelled, false)
        .preview;
}

} // namespace Licasa

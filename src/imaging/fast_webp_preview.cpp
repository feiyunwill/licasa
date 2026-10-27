#include "imaging/fast_webp_preview.h"
#include "imaging/parallel_smooth_scale.h"

#include <QColorSpace>
#include <QFile>
#include <QtEndian>

#include <webp/decode.h>

#include <cstring>
#include <limits>
#include <utility>

namespace Licasa {
QImage readFastWebpPreview(const QString& path, const QSize& target, quint64 maximumPixels,
                           qsizetype maximumPreviewBytes, const std::atomic_bool* cancelled,
                           QImage* retainedFullBase, qsizetype maximumRetainedBytes)
{
    if (retainedFullBase) {
        *retainedFullBase = {};
    }
    if (!target.isValid() || target.isEmpty() || target.width() > 8192 || target.height() > 8192 ||
        quint64(target.width()) * target.height() > 2'000'000 || maximumPreviewBytes <= 0 ||
        (cancelled && cancelled->load(std::memory_order_relaxed))) {
        return {};
    }
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly) || file.size() < 20 || file.size() > 128 * 1024 * 1024 ||
        quint64(file.size()) > std::numeric_limits<size_t>::max()) {
        return {};
    }
    const uchar* const bytes = file.map(0, file.size());
    if (!bytes || std::memcmp(bytes, "RIFF", 4) != 0 || std::memcmp(bytes + 8, "WEBP", 4) != 0 ||
        (std::memcmp(bytes + 12, "VP8 ", 4) != 0 && std::memcmp(bytes + 12, "VP8L", 4) != 0) ||
        quint64(qFromLittleEndian<quint32>(bytes + 4)) + 8 != quint64(file.size())) {
        return {};
    }
    WebPDecoderConfig config;
    if (!WebPInitDecoderConfig(&config) ||
        WebPGetFeatures(bytes, size_t(file.size()), &config.input) != VP8_STATUS_OK ||
        config.input.has_animation || config.input.width <= 0 || config.input.height <= 0 ||
        quint64(config.input.width) * config.input.height > maximumPixels ||
        target.width() > config.input.width || target.height() > config.input.height) {
        return {};
    }
    const quint64 sourceBytes = quint64(config.input.width) * config.input.height * 4;
    const bool retainBase = retainedFullBase && maximumRetainedBytes > 0 &&
                            file.size() >= 16 * 1024 * 1024 && sourceBytes >= 100 * 1024 * 1024 &&
                            sourceBytes <= quint64(maximumRetainedBytes);
    QImage image(retainBase ? QSize(config.input.width, config.input.height) : target,
                 QImage::Format_RGBA8888);
    if (image.isNull() || (!retainBase && image.sizeInBytes() > maximumPreviewBytes) ||
        (cancelled && cancelled->load(std::memory_order_relaxed))) {
        return {};
    }
    if (!retainBase) {
        config.options.use_scaling = 1;
        config.options.scaled_width = target.width();
        config.options.scaled_height = target.height();
    }
    config.options.use_threads = 1;
    config.output.colorspace = MODE_RGBA;
    config.output.is_external_memory = 1;
    config.output.u.RGBA.rgba = image.bits();
    config.output.u.RGBA.stride = int(image.bytesPerLine());
    config.output.u.RGBA.size = size_t(image.sizeInBytes());
    const VP8StatusCode status = WebPDecode(bytes, size_t(file.size()), &config);
    WebPFreeDecBuffer(&config.output);
    if (status != VP8_STATUS_OK || (cancelled && cancelled->load(std::memory_order_relaxed))) {
        return {};
    }
    image.setColorSpace(QColorSpace::SRgb);
    if (retainBase) {
        QImage preview = parallelSmoothScale(image, target);
        if (preview.isNull() || preview.sizeInBytes() > maximumPreviewBytes ||
            (cancelled && cancelled->load(std::memory_order_relaxed))) {
            return {};
        }
        *retainedFullBase = std::move(image);
        return preview;
    }
    return image;
}
} // namespace Licasa

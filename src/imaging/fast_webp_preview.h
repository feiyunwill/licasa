#pragma once

#include <QImage>
#include <QSize>

#include <atomic>

namespace Licasa {
// Decode a bounded preview for simple, static WebP files. For costly large
// files, optionally retain the full raster so promotion avoids a second decode.
// Extended files keep Qt's reader for metadata and animation.
QImage readFastWebpPreview(const QString& path, const QSize& target, quint64 maximumPixels,
                           qsizetype maximumPreviewBytes, const std::atomic_bool* cancelled,
                           QImage* retainedFullBase = nullptr, qsizetype maximumRetainedBytes = 0);
} // namespace Licasa

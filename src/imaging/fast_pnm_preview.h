#pragma once

#include <QImage>
#include <QSize>
#include <QString>

#include <atomic>

class QIODevice;

namespace Licasa {

// Stream one plain P6/maxval-255 raster through an area filter. The caller
// supplies an aspect-fitted target and its existing source admission limit.
// Unsupported inputs, exceeded budgets, read failures and cancellation return
// an empty image so the caller can retain its ordinary Qt decoder path.
QImage readFastPnmPreview(const QString& path, const QSize& target, quint64 maximumSourcePixels,
                          qsizetype maximumOutputBytes, const std::atomic_bool* cancelled);

// The device must be finite, seekable and open for reading. It remains open;
// its position is unspecified on return. This overload also permits precise
// short-read and cancellation coverage without changing the file decoder.
QImage readFastPnmPreview(QIODevice& device, const QSize& target, quint64 maximumSourcePixels,
                          qsizetype maximumOutputBytes, const std::atomic_bool* cancelled);

} // namespace Licasa

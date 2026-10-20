#pragma once

#include <QImage>
#include <QSize>
#include <QString>

#include <atomic>

namespace Licasa {

// Return a bounded preview for selected stripped grayscale and RGB TIFFs.
// Other TIFF layouts stay on the ordinary Qt decoder path.
QImage readFastTiffPreview(const QString& path, const QSize& requestedSize,
                           quint64 maximumOutputPixels, qsizetype maximumOutputBytes,
                           std::atomic_bool* cancelled);

} // namespace Licasa

#pragma once

#include "imaging/image_decode_contract.h"

#include <QSize>
#include <QString>

#include <algorithm>
#include <cmath>

namespace Licasa::PreviewDecodeSupport {

inline bool isJpegSuffix(const QString& suffix)
{
    return suffix == QStringLiteral("jpg") || suffix == QStringLiteral("jpeg");
}

inline bool isTiffSuffix(const QString& suffix)
{
    return suffix == QStringLiteral("tif") || suffix == QStringLiteral("tiff");
}

inline bool isRawSuffix(const QString& suffix)
{
    return suffix == QStringLiteral("dng") || suffix == QStringLiteral("raw") ||
           suffix == QStringLiteral("cr2") || suffix == QStringLiteral("cr3") ||
           suffix == QStringLiteral("nef") || suffix == QStringLiteral("nrw") ||
           suffix == QStringLiteral("arw") || suffix == QStringLiteral("srw") ||
           suffix == QStringLiteral("rw2") || suffix == QStringLiteral("pef") ||
           suffix == QStringLiteral("orf") || suffix == QStringLiteral("raf");
}

inline QSize boundedPreviewSize(const QSize& requestedSize, quint64 maximumPixels)
{
    if (!requestedSize.isValid() || requestedSize.width() <= 0 || requestedSize.height() <= 0) {
        return {};
    }
    if (ImageDecodeContract::allows(requestedSize, maximumPixels)) {
        return requestedSize;
    }

    const long double requestedPixels =
        static_cast<long double>(requestedSize.width()) * requestedSize.height();
    const long double scale = std::sqrt(static_cast<long double>(maximumPixels) / requestedPixels);
    QSize boundedSize(std::max(1, static_cast<int>(std::floor(requestedSize.width() * scale))),
                      std::max(1, static_cast<int>(std::floor(requestedSize.height() * scale))));

    // Floating-point rounding should already leave the area at or below the
    // budget. Keep the final adjustment integer-only so that invariant is exact.
    while (!ImageDecodeContract::allows(boundedSize, maximumPixels)) {
        if (boundedSize.width() >= boundedSize.height()) {
            boundedSize.rwidth() -= 1;
        } else {
            boundedSize.rheight() -= 1;
        }
    }
    return boundedSize;
}

} // namespace Licasa::PreviewDecodeSupport

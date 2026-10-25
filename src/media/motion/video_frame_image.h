#pragma once

#include <QImage>
#include <QTransform>
#include <QVideoFrame>

namespace Licasa::Detail {

// Qt 6.4 converts presentation transforms in toImage(). Qt 6.8 converts
// surface transforms there, leaving presentation transforms to the caller.
// Container rotation is a fallback only when Qt did not expose that metadata.
inline QImage videoFramePresentationImage(const QVideoFrame& frame, int fallbackRotationDegrees)
{
    QImage image = frame.toImage();
    if (image.isNull()) {
        return image;
    }
#if QT_VERSION >= QT_VERSION_CHECK(6, 7, 0)
    const auto format = frame.surfaceFormat();
    int rotationDegrees = static_cast<int>(frame.rotation());
    if (rotationDegrees == 0 && format.rotation() == QtVideo::Rotation::None &&
        !format.isMirrored()) {
        rotationDegrees = fallbackRotationDegrees;
    }
#else
    const int rotationDegrees =
        frame.rotationAngle() == QVideoFrame::Rotation0 ? fallbackRotationDegrees : 0;
#endif
    if (rotationDegrees != 0) {
        image = image.transformed(QTransform().rotate(rotationDegrees), Qt::SmoothTransformation);
    }
#if QT_VERSION >= QT_VERSION_CHECK(6, 7, 0)
    if (frame.mirrored()) {
        image = image.mirrored(true, false);
    }
#endif
    return image;
}

} // namespace Licasa::Detail

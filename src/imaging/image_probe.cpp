#include "imaging/image_probe.h"
#include "imaging/image_decode_contract.h"
#include "imaging/image_resource_policy.h"
#ifdef LICASA_HAVE_FAST_PNG_PREVIEW
#include "imaging/fast_png_preview.h"
#endif
#include <QDateTime>
#include <QDebug>
#include <QElapsedTimer>
#include <QFileInfo>
#include <QImageReader>

namespace Licasa {
ImageProbe::ImageProbe(ImageResourcePolicy& resourcePolicy, QObject* parent)
    : QObject(parent), resourcePolicy_(resourcePolicy)
{}

QVariantMap ImageProbe::inspect(const QUrl& url) const
{
    const bool trace = qEnvironmentVariableIsSet("LICASA_TRACE_IMAGE_PROBE");
    QElapsedTimer elapsed;
    if (trace) {
        elapsed.start();
    }
    if (!url.isValid() || !url.isLocalFile()) {
        return {};
    }

    const QFileInfo fileInfo(url.toLocalFile());
    if (!fileInfo.exists() || !fileInfo.isFile()) {
        return {};
    }

    const quint64 budget = resourcePolicy_.maximumImagePixels();
    const qint64 modified = fileInfo.lastModified().toMSecsSinceEpoch();
    if (lastProbePath_ == fileInfo.absoluteFilePath() && lastProbeBytes_ == fileInfo.size() &&
        lastProbeModified_ == modified && lastProbeBudget_ == budget) {
        if (trace) {
            qInfo().noquote() << "[Licasa] Image metadata cache hit:"
                              << elapsed.nsecsElapsed() / 1000000.0 << "ms";
        }
        return lastProbe_;
    }
    lastProbePath_ = fileInfo.absoluteFilePath();
    lastProbeBytes_ = fileInfo.size();
    lastProbeModified_ = modified;
    lastProbeBudget_ = budget;
    lastProbe_ = {{QStringLiteral("fileSize"), fileInfo.size()}};

    QImageReader reader(fileInfo.absoluteFilePath());
    ImageDecodeContract::configure(reader, budget);
    reader.setAutoTransform(true);
    if (!reader.canRead()) {
        if (trace) {
            qInfo().noquote() << "[Licasa] Image metadata probe (unreadable):"
                              << elapsed.nsecsElapsed() / 1000000.0 << "ms";
        }
        return lastProbe_;
    }

    QSize size = reader.size();
    if (!size.isValid()) {
        if (trace) {
            qInfo().noquote() << "[Licasa] Image metadata probe (unknown dimensions):"
                              << elapsed.nsecsElapsed() / 1000000.0 << "ms";
        }
        return lastProbe_;
    }

    const auto transformation = reader.transformation();
    if (transformation.testFlag(QImageIOHandler::TransformationRotate90) ||
        transformation.testFlag(QImageIOHandler::TransformationRotate270)) {
        size.transpose();
    }
    const QSize sensor =
        reader.device()->property(ImageDecodeContract::sensorSizeProperty).toSize();
    const bool plainPng =
        fileInfo.suffix().compare(QStringLiteral("png"), Qt::CaseInsensitive) == 0 &&
        reader.transformation() == QImageIOHandler::TransformationNone &&
        (!reader.supportsAnimation() || reader.imageCount() == 1);
    bool reusablePngBase = plainPng && ImageDecodeContract::reusablePngFullBase(
                                           reader.size(), reader.imageFormat(), budget);
#ifdef LICASA_HAVE_FAST_PNG_PREVIEW
    if (plainPng && !reusablePngBase &&
        ImageDecodeContract::boundedPngFullBase(reader.size(), budget)) {
        reusablePngBase = fastPngBaseEligible(fileInfo.absoluteFilePath(), budget);
    }
#endif
    lastProbe_ = {
        {QStringLiteral("fileSize"), fileInfo.size()},
        {QStringLiteral("size"), size},
        {QStringLiteral("withinBudget"),
         resourcePolicy_.allows(size) && (!sensor.isValid() || resourcePolicy_.allows(sensor))},
        {QStringLiteral("animated"), reader.supportsAnimation() && reader.imageCount() != 1},
        {QStringLiteral("previewFirst"),
         reader.device()->property(ImageDecodeContract::previewFirstProperty).toBool()},
        {QStringLiteral("previewSize"),
         reader.device()->property(ImageDecodeContract::embeddedPreviewSizeProperty).toSize()},
        {QStringLiteral("reusablePngBase"), reusablePngBase}};
    if (trace) {
        qInfo().noquote() << "[Licasa] Image metadata probe:" << elapsed.nsecsElapsed() / 1000000.0
                          << "ms";
    }
    return lastProbe_;
}

} // namespace Licasa

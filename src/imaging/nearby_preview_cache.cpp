#include "imaging/nearby_preview_cache.h"
#include "imaging/image_resource_policy.h"
#include "imaging/preview_decode_support.h"
#ifdef LICASA_HAVE_FAST_PNG_PREVIEW
#include "imaging/fast_png_preview.h"
#endif
#ifdef LICASA_HAVE_FAST_WEBP_PREVIEW
#include "imaging/fast_webp_preview.h"
#endif
#ifdef LICASA_HAVE_FAST_TIFF_PREVIEW
#include "imaging/fast_tiff_preview.h"
#endif

#include <QElapsedTimer>
#include <QFile>
#include <QFileInfo>
#include <QImageReader>
#include <QMutexLocker>
#include <QThreadPool>

#include <algorithm>
#include <utility>

namespace Licasa {
using namespace PreviewDecodeSupport;
namespace {
bool isHeifSuffix(const QString& suffix)
{
    return suffix == QStringLiteral("heic") || suffix == QStringLiteral("heif") ||
           suffix == QStringLiteral("hif");
}

bool isJp2Suffix(const QString& suffix)
{
    return suffix == QStringLiteral("jp2") || suffix == QStringLiteral("j2k") ||
           suffix == QStringLiteral("j2c") || suffix == QStringLiteral("jpc");
}

bool hasEmbeddedPreviewCodec(const QString& suffix)
{
    return isHeifSuffix(suffix) || suffix == QStringLiteral("jxl");
}

QSize admittedNativeJpegSize(const QSize& source, quint64 maximumPixels)
{
    if (!source.isValid() || source.isEmpty()) {
        return {};
    }
    for (const quint64 divider : {quint64(1), quint64(2), quint64(4), quint64(8)}) {
        const QSize nativeSize(int((quint64(source.width()) + divider - 1) / divider),
                               int((quint64(source.height()) + divider - 1) / divider));
        if (ImageDecodeContract::allows(nativeSize, maximumPixels)) {
            return nativeSize;
        }
    }
    return {};
}
} // namespace

NearbyPreviewCache::FileStamp NearbyPreviewCache::fileStamp(const QString& path)
{
    const QFileInfo info(path);
    if (!info.isFile()) {
        return {};
    }
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return {};
    }
    return {info.canonicalFilePath(), info.size(), info.lastModified(), info.metadataChangeTime(),
            externalFileIdentity(file)};
}

bool NearbyPreviewCache::sameFileStamp(const FileStamp& left, const FileStamp& right)
{
    return left.isValid() && right.isValid() && left.canonicalPath == right.canonicalPath &&
           left.size == right.size && left.modified == right.modified &&
           left.changed == right.changed && left.nativeIdentity == right.nativeIdentity;
}

bool NearbyPreviewCache::coversRequestedSize(const QSize& prepared, const QSize& requested)
{
    return prepared.isValid() && !prepared.isEmpty() && requested.isValid() &&
           !requested.isEmpty() && prepared.width() >= requested.width() &&
           prepared.height() >= requested.height();
}

void NearbyPreviewCache::cancelActive()
{
    if (activeCancellation) {
        activeCancellation->store(true, std::memory_order_relaxed);
        activeFinished.wakeAll();
    }
}

void NearbyPreviewCache::cancelActiveHeavyCodec()
{
    QMutexLocker lock(&mutex);
    const QString suffix = QFileInfo(activePath).suffix().toLower();
    if (isHeifSuffix(suffix) || isJp2Suffix(suffix) || suffix == QStringLiteral("avif") ||
        suffix == QStringLiteral("webp")) {
        cancelActive();
    }
}

void NearbyPreviewCache::clear()
{
    QMutexLocker lock(&mutex);
    cancelActive();
    desired.clear();
    activePath.clear();
    activeRequestedSize = {};
    activeFinished.wakeAll();
    entries.clear();
    attempted.clear();
    bytes = 0;
}

QImage NearbyPreviewCache::lookup(const QString& path)
{
    QMutexLocker lock(&mutex);
    auto it = entries.find(path);
    if (it == entries.end()) {
        return {};
    }
    if (!sameFileStamp(it->stamp, fileStamp(path))) {
        bytes -= it->image.sizeInBytes();
        entries.erase(it);
        attempted.remove(path);
        return {};
    }
    ++hitCount;
    return it->image;
}

void NearbyPreviewCache::waitForActivePng(const QString& path,
                                          const std::atomic_bool* selectedCancelled)
{
    QMutexLocker lock(&mutex);
    if (activePath != path || !activeCancellation ||
        activeCancellation->load(std::memory_order_relaxed)) {
        return;
    }
    // The preview-only PNG reader may already be halfway through the same
    // file. Wait on this worker instead of starting a duplicate full read.
    // The selected request can still fall back if that work stalls.
    QElapsedTimer timer;
    timer.start();
    while (activePath == path && activeCancellation &&
           !activeCancellation->load(std::memory_order_relaxed) &&
           !(selectedCancelled && selectedCancelled->load(std::memory_order_relaxed))) {
        const int remaining = 700 - int(timer.elapsed());
        if (remaining <= 0) {
            break;
        }
        activeFinished.wait(&mutex, std::min(remaining, 25));
    }
}

bool NearbyPreviewCache::setWindow(const QList<QUrl>& urls, const QSize& requested, int memoryMiB)
{
    QStringList paths;
    paths.reserve(std::min<qsizetype>(urls.size(), 10));
    for (const QUrl& url : urls) {
        if (url.isLocalFile() && !paths.contains(url.toLocalFile())) {
            paths.append(url.toLocalFile());
        }
        if (paths.size() == 10) {
            break;
        }
    }
    const qsizetype limit =
        std::min<qsizetype>(96, std::max(0, memoryMiB) / 8) * qsizetype(1024 * 1024);
    // Ten slots at this size cannot retain more than the cache limit.
    const qsizetype perSlot = limit / 10;
    QMutexLocker lock(&mutex);
    if (desired == paths && requestedSize == requested && byteLimit == limit) {
        const bool startWorker = !workerRunning && !paths.isEmpty();
        workerRunning = workerRunning || startWorker;
        return startWorker;
    }
    if (!activePath.isEmpty()) {
        const int activeIndex = paths.indexOf(activePath);
        if (activeIndex < 0 || !coversRequestedSize(activeRequestedSize, requested)) {
            cancelActive();
        } else {
            // A direction change can move the active decode behind a new
            // nearest neighbor. Let the worker restart with that neighbor.
            for (int index = 0; index < activeIndex; ++index) {
                const auto entry = entries.constFind(paths.at(index));
                if (entry == entries.cend() || entry->image.sizeInBytes() > perSlot ||
                    !coversRequestedSize(entry->preparedFor, requested)) {
                    cancelActive();
                    break;
                }
            }
        }
    }
    desired = std::move(paths);
    requestedSize = requested;
    byteLimit = limit;
    slotLimit = perSlot;
    attempted.clear();
    for (auto it = entries.begin(); it != entries.end();) {
        if (!desired.contains(it.key()) || it->image.sizeInBytes() > slotLimit ||
            !coversRequestedSize(it->preparedFor, requested)) {
            bytes -= it->image.sizeInBytes();
            it = entries.erase(it);
        } else {
            ++it;
        }
    }
    const bool startWorker = !workerRunning && !desired.isEmpty();
    workerRunning = workerRunning || startWorker;
    return startWorker;
}

void NearbyPreviewCache::run(ImageResourcePolicy& policy, QThreadPool& pool)
{
    QString path;
    QSize target;
    qsizetype maxBytes = 0;
    qsizetype cacheBudgetBytes = 0;
    int priority = -1;
    auto cancellation = std::make_shared<std::atomic_bool>(false);
    {
        QMutexLocker lock(&mutex);
        for (int index = 0; index < desired.size(); ++index) {
            const QString& candidate = desired.at(index);
            if (!entries.contains(candidate) && !attempted.contains(candidate)) {
                path = candidate;
                priority = index;
                break;
            }
        }
        if (path.isEmpty()) {
            workerRunning = false;
            activeCancellation.reset();
            activePath.clear();
            activeRequestedSize = {};
            activeFinished.wakeAll();
            return;
        }
        attempted.insert(path);
        target = requestedSize;
        maxBytes = slotLimit;
        cacheBudgetBytes = byteLimit;
        activeCancellation = cancellation;
        activePath = path;
        activeRequestedSize = target;
    }

    QImage decoded;
    const FileStamp before = fileStamp(path);
    const QString suffix = QFileInfo(path).suffix().toLower();
    const bool raw = isRawSuffix(suffix);
    const bool embeddedCodec = hasEmbeddedPreviewCodec(suffix);
    const bool nativeStillCodec =
        suffix == QStringLiteral("avif") || suffix == QStringLiteral("webp");
    const bool prefetchCandidate =
        isJpegSuffix(suffix) || isJp2Suffix(suffix) || suffix == QStringLiteral("png") ||
        suffix == QStringLiteral("webp") || suffix == QStringLiteral("bmp") ||
        isTiffSuffix(suffix) || suffix == QStringLiteral("avif") || embeddedCodec || raw;
    if (prefetchCandidate && before.isValid() && maxBytes > 0 && target.isValid()) {
#ifdef LICASA_HAVE_FAST_TIFF_PREVIEW
        if (isTiffSuffix(suffix)) {
            decoded = readFastTiffPreview(path, target, policy.maximumImagePixels(), maxBytes,
                                          cancellation.get());
        }
#endif
        if (decoded.isNull() && !cancellation->load(std::memory_order_relaxed)) {
            const quint64 maximumPixels =
                std::min(policy.maximumImagePixels(), quint64(maxBytes) / 4);
            // AVIF and WebP decode the native raster before scaling.
            // Budget ten bytes of transient work per source pixel against
            // the nearby-cache allowance for only the three closest stills.
            const quint64 nativeStillPixels =
                nativeStillCodec && priority < 3
                    ? std::min({policy.maximumImagePixels(), quint64(cacheBudgetBytes) / 10,
                                quint64(12'000'000)})
                    : maximumPixels;
            const bool jp2Nearby = isJp2Suffix(suffix) && priority < 3;
            QImageReader reader(path);
            ImageDecodeContract::configure(
                reader, jp2Nearby ? policy.maximumImagePixels() : nativeStillPixels,
                cancellation.get(), false, false, false);
            if (jp2Nearby && reader.device()) {
                reader.device()->setProperty(ImageDecodeContract::scaledDecodePixelBudgetProperty,
                                             QVariant::fromValue(maximumPixels));
            }
            reader.setAutoTransform(true);
            if (reader.canRead()) {
                const QSize source = reader.size();
#ifdef LICASA_HAVE_FAST_PNG_PREVIEW
                // Decoding a large PNG still consumes CPU for every source
                // row. Prepare the closest three, then leave farther files
                // to the next browsing window.
                if (priority < 3 && suffix == QStringLiteral("png") && source.isValid() &&
                    !ImageDecodeContract::allows(source, maximumPixels) &&
                    fastPngBaseEligible(path, policy.maximumImagePixels())) {
                    QSize scaled = source;
                    scaled.scale(boundedPreviewSize(target, maximumPixels), Qt::KeepAspectRatio);
                    if (scaled.isValid()) {
                        decoded =
                            readFastPngNearbyPreview(path, scaled, policy.maximumImagePixels(),
                                                     maxBytes, cancellation.get());
                    }
                }
#endif
                const bool jpeg = isJpegSuffix(suffix);
                const QSize nativeJpegSize =
                    jpeg ? admittedNativeJpegSize(source, maximumPixels) : QSize();
                const bool nativeJpegScale =
                    jpeg && nativeJpegSize.isValid() &&
                    reader.supportsOption(QImageIOHandler::ScaledSize) &&
                    ImageDecodeContract::allows(source, policy.maximumImagePixels());
                const QSize embeddedRawPreview =
                    raw && reader.device()
                        ? reader.device()
                              ->property(ImageDecodeContract::embeddedPreviewSizeProperty)
                              .toSize()
                        : QSize();
                const bool scalableEmbeddedJpeg =
                    raw && reader.device() &&
                    reader.device()
                        ->property(ImageDecodeContract::scalableEmbeddedJpegProperty)
                        .toBool();
                const bool boundedRawPreview =
                    raw && (ImageDecodeContract::allows(embeddedRawPreview, maximumPixels) ||
                            scalableEmbeddedJpeg);
                const QSize bounded = boundedPreviewSize(target, maximumPixels);
                QSize scaled = raw ? embeddedRawPreview : source;
                // Small neighbors already contain all their source pixels.
                // Leave enlargement to presentation rather than allocating a
                // viewport-sized raster with no additional image information.
                if (scaled.width() > bounded.width() || scaled.height() > bounded.height()) {
                    scaled.scale(bounded, Qt::KeepAspectRatio);
                }
                const QSize embeddedStillPreview =
                    embeddedCodec && reader.device()
                        ? reader.device()
                              ->property(ImageDecodeContract::embeddedPreviewSizeProperty)
                              .toSize()
                        : QSize();
                const bool animatedEmbeddedCodec = embeddedCodec && reader.supportsAnimation();
                const bool animatedNativeStill = nativeStillCodec && reader.supportsAnimation();
                const bool boundedNativeStill =
                    nativeStillCodec && priority < 3 && !animatedNativeStill &&
                    ImageDecodeContract::allows(source, nativeStillPixels);
                const bool boundedJp2 =
                    jp2Nearby && reader.device() &&
                    reader.device()
                        ->property(ImageDecodeContract::boundedJp2ScaleProperty)
                        .toBool() &&
                    source.isValid() && scaled.isValid() &&
                    reader.supportsOption(QImageIOHandler::ScaledSize) &&
                    ImageDecodeContract::allows(source, policy.maximumImagePixels());
                const bool boundedEmbeddedStill =
                    source.isValid() && scaled.isValid() && embeddedStillPreview.isValid() &&
                    !animatedEmbeddedCodec &&
                    ImageDecodeContract::allows(embeddedStillPreview, maximumPixels) &&
                    embeddedStillPreview.width() >= scaled.width() &&
                    embeddedStillPreview.height() >= scaled.height() &&
                    qAbs(qint64(embeddedStillPreview.width()) * source.height() -
                         qint64(embeddedStillPreview.height()) * source.width()) <=
                        std::max(source.width(), source.height());
                const bool boundedHeifGrid =
                    embeddedCodec && reader.device() && scaled.isValid() &&
                    reader.device()
                        ->property(ImageDecodeContract::boundedGridPreviewProperty)
                        .toBool();
#ifdef LICASA_HAVE_FAST_WEBP_PREVIEW
                if (decoded.isNull() && suffix == QStringLiteral("webp") && boundedNativeStill &&
                    scaled.isValid() &&
                    reader.transformation() == QImageIOHandler::TransformationNone) {
                    decoded = readFastWebpPreview(path, scaled, nativeStillPixels, maxBytes,
                                                  cancellation.get());
                }
#endif
                // Only a bounded source or a codec-owned native scale may
                // run alongside the current full reader.
                if (decoded.isNull() &&
                    (boundedRawPreview || boundedEmbeddedStill || boundedHeifGrid ||
                     boundedNativeStill || boundedJp2 ||
                     (!raw && source.isValid() && !animatedEmbeddedCodec && !animatedNativeStill &&
                      (ImageDecodeContract::allows(source, maximumPixels) || nativeJpegScale)))) {
                    if (nativeJpegScale && !ImageDecodeContract::allows(source, maximumPixels)) {
                        scaled.scale(nativeJpegSize, Qt::KeepAspectRatio);
                    }
                    if (scaled.isValid() && scaled != (raw ? embeddedRawPreview : source)) {
                        reader.setScaledSize(scaled);
                    }
                    if (!cancellation->load(std::memory_order_relaxed)) {
                        decoded = reader.read();
                        if (!ImageDecodeContract::allows(decoded.size(), maximumPixels)) {
                            decoded = {};
                        }
                    }
                }
            }
        }
    }
    const FileStamp after = fileStamp(path);
    bool more = false;
    {
        QMutexLocker lock(&mutex);
        if (activePath == path) {
            activePath.clear();
            activeRequestedSize = {};
        }
        if (!cancellation->load(std::memory_order_relaxed) && desired.contains(path) &&
            !decoded.isNull() && decoded.sizeInBytes() > 0 && decoded.sizeInBytes() <= slotLimit &&
            sameFileStamp(before, after)) {
            bytes += decoded.sizeInBytes();
            entries.insert(path, {std::move(decoded), after, target});
        }
        for (const QString& candidate : desired) {
            if (!entries.contains(candidate) && !attempted.contains(candidate)) {
                more = true;
                break;
            }
        }
        if (!more) {
            workerRunning = false;
            activeCancellation.reset();
        }
        activeFinished.wakeAll();
    }
    if (more) {
        pool.start([self = shared_from_this(), &policy, &pool] { self->run(policy, pool); }, -1);
    }
}

qsizetype NearbyPreviewCache::count() const
{
    QMutexLocker lock(&mutex);
    return entries.size();
}

qsizetype NearbyPreviewCache::retainedBytes() const
{
    QMutexLocker lock(&mutex);
    return bytes;
}

quint64 NearbyPreviewCache::hits() const
{
    QMutexLocker lock(&mutex);
    return hitCount;
}

} // namespace Licasa

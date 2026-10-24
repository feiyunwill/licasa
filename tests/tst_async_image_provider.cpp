#include "app/application_settings.h"
#include "imaging/async_image_provider.h"
#include "imaging/image_resource_policy.h"
#ifdef LICASA_HAVE_FAST_TIFF_PREVIEW
#include "imaging/fast_tiff_preview.h"
#include <tiffio.h>
#endif
#ifdef LICASA_HAVE_FAST_PNG_PREVIEW
#include "imaging/fast_png_preview.h"
#include <png.h>
#endif
#ifdef LICASA_HAVE_FAST_WEBP_PREVIEW
#include "imaging/fast_webp_preview.h"
#endif

#include <QColorSpace>
#include <QFile>
#include <QImage>
#include <QImageWriter>
#include <QQuickTextureFactory>
#include <QSemaphore>
#include <QSettings>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QTest>
#include <QThread>

#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <memory>
#include <thread>
#include <vector>

#ifdef LICASA_HAVE_FAST_PNG_PREVIEW
namespace {
bool writePlainLargePng(const QString& path, bool alpha)
{
    const QByteArray encoded = QFile::encodeName(path);
    FILE* file = std::fopen(encoded.constData(), "wb");
    if (!file) {
        return false;
    }
    png_structp png = png_create_write_struct(PNG_LIBPNG_VER_STRING, nullptr, nullptr, nullptr);
    png_infop info = png ? png_create_info_struct(png) : nullptr;
    if (!info) {
        if (png) {
            png_destroy_write_struct(&png, nullptr);
        }
        std::fclose(file);
        return false;
    }
    std::vector<uchar> row(size_t(5000) * (alpha ? 4 : 3));
    if (setjmp(png_jmpbuf(png))) {
        png_destroy_write_struct(&png, &info);
        std::fclose(file);
        return false;
    }
    png_init_io(png, file);
    png_set_compression_level(png, 1);
    png_set_IHDR(png, info, 5000, 5000, 8, alpha ? PNG_COLOR_TYPE_RGBA : PNG_COLOR_TYPE_RGB,
                 PNG_INTERLACE_NONE, PNG_COMPRESSION_TYPE_BASE, PNG_FILTER_TYPE_BASE);
    png_write_info(png, info);
    const int channels = alpha ? 4 : 3;
    for (int x = 0; x < 5000; ++x) {
        row[size_t(x) * channels] = 30;
        row[size_t(x) * channels + 1] = 80;
        row[size_t(x) * channels + 2] = 140;
        if (alpha) {
            row[size_t(x) * channels + 3] = 255;
        }
    }
    for (int y = 0; y < 5000; ++y) {
        if (y == 2500) {
            row[size_t(2500) * channels] = 210;
            row[size_t(2500) * channels + 1] = 40;
            row[size_t(2500) * channels + 2] = 90;
            if (alpha) {
                row[size_t(2500) * channels + 3] = 120;
            }
        }
        png_write_row(png, row.data());
    }
    png_write_end(png, info);
    png_destroy_write_struct(&png, &info);
    return std::fclose(file) == 0;
}
} // namespace
#endif

class AsyncImageProviderTest final : public QObject {
    Q_OBJECT

  private slots:
#ifdef LICASA_HAVE_FAST_WEBP_PREVIEW
    void simpleWebpPreviewUsesBoundedBufferAndRejectsCancellation()
    {
        QTemporaryDir images;
        QVERIFY(images.isValid());
        const QString path = images.filePath(QStringLiteral("large.webp"));
        QImage source(2400, 1600, QImage::Format_RGB32);
        QVERIFY(!source.isNull());
        source.fill(QColor(35, 80, 140));
        QImageWriter writer(path, "webp");
        writer.setQuality(95);
        QVERIFY2(writer.write(source), qPrintable(writer.errorString()));

        std::atomic_bool cancelled{false};
        const QImage preview =
            Licasa::readFastWebpPreview(path, QSize(600, 400), 4'000'000, 1'000'000, &cancelled);
        QCOMPARE(preview.size(), QSize(600, 400));
        QVERIFY(preview.sizeInBytes() <= 1'000'000);
        const QColor pixel = preview.pixelColor(300, 200);
        QVERIFY(qAbs(pixel.red() - 35) <= 5);
        QVERIFY(qAbs(pixel.green() - 80) <= 5);
        QVERIFY(qAbs(pixel.blue() - 140) <= 5);
        QVERIFY(Licasa::readFastWebpPreview(path, QSize(600, 400), 3'000'000, 1'000'000, &cancelled)
                    .isNull());
        cancelled.store(true);
        QVERIFY(Licasa::readFastWebpPreview(path, QSize(600, 400), 4'000'000, 1'000'000, &cancelled)
                    .isNull());
    }
#endif
#ifdef LICASA_HAVE_FAST_TIFF_PREVIEW
    void largeGrayTiffPreviewStaysBoundedAndCancelable()
    {
        QTemporaryDir images;
        QVERIFY(images.isValid());
        const QString path = images.filePath(QStringLiteral("large-gray.tif"));
        const QByteArray encodedPath = QFile::encodeName(path);
        TIFF* tiff = TIFFOpen(encodedPath.constData(), "w");
        QVERIFY(tiff != nullptr);
        constexpr uint32_t width = 2048;
        constexpr uint32_t height = 1536;
        TIFFSetField(tiff, TIFFTAG_IMAGEWIDTH, width);
        TIFFSetField(tiff, TIFFTAG_IMAGELENGTH, height);
        TIFFSetField(tiff, TIFFTAG_BITSPERSAMPLE, 16);
        TIFFSetField(tiff, TIFFTAG_SAMPLESPERPIXEL, 1);
        TIFFSetField(tiff, TIFFTAG_PHOTOMETRIC, PHOTOMETRIC_MINISBLACK);
        TIFFSetField(tiff, TIFFTAG_COMPRESSION, COMPRESSION_NONE);
        TIFFSetField(tiff, TIFFTAG_PLANARCONFIG, PLANARCONFIG_CONTIG);
        TIFFSetField(tiff, TIFFTAG_ROWSPERSTRIP, 3);
        TIFFSetField(tiff, TIFFTAG_ORIENTATION, ORIENTATION_TOPLEFT);
        std::vector<quint16> row(width);
        for (uint32_t y = 0; y < height; ++y) {
            for (uint32_t x = 0; x < width; ++x) {
                row[x] = quint16(((x * 13 + y * 7) & 255) * 257);
            }
            QCOMPARE(TIFFWriteScanline(tiff, row.data(), y), 1);
        }
        TIFFClose(tiff);

        std::atomic_bool cancelled{false};
        const QImage preview =
            Licasa::readFastTiffPreview(path, QSize(1200, 900), 100000, 100000, &cancelled);
        QVERIFY(!preview.isNull());
        QCOMPARE(preview.format(), QImage::Format_Grayscale8);
        QVERIFY(preview.sizeInBytes() <= 100000);
        const int x = preview.width() / 2;
        const int y = preview.height() / 2;
        const int sourceX = int(quint64(x) * width / quint64(preview.width()));
        const int sourceY = int(quint64(y) * height / quint64(preview.height()));
        QCOMPARE(preview.pixelColor(x, y).red(), (sourceX * 13 + sourceY * 7) & 255);

        cancelled.store(true);
        QVERIFY(Licasa::readFastTiffPreview(path, QSize(1200, 900), 100000, 100000, &cancelled)
                    .isNull());
    }
    void deflateRgbTiffPreviewKeepsPixelsAndBudget()
    {
        QTemporaryDir images;
        QVERIFY(images.isValid());
        constexpr uint32_t width = 1024;
        constexpr uint32_t height = 768;
        for (const uint16_t samples : {uint16_t(3), uint16_t(4)}) {
            const QString path = images.filePath(QStringLiteral("rgb-%1.tif").arg(samples));
            TIFF* tiff = TIFFOpen(QFile::encodeName(path).constData(), "w");
            QVERIFY(tiff != nullptr);
            TIFFSetField(tiff, TIFFTAG_IMAGEWIDTH, width);
            TIFFSetField(tiff, TIFFTAG_IMAGELENGTH, height);
            TIFFSetField(tiff, TIFFTAG_BITSPERSAMPLE, 8);
            TIFFSetField(tiff, TIFFTAG_SAMPLESPERPIXEL, samples);
            TIFFSetField(tiff, TIFFTAG_PHOTOMETRIC, PHOTOMETRIC_RGB);
            TIFFSetField(tiff, TIFFTAG_COMPRESSION, COMPRESSION_ADOBE_DEFLATE);
            if (samples == 3) {
                TIFFSetField(tiff, TIFFTAG_PREDICTOR, 2);
            }
            TIFFSetField(tiff, TIFFTAG_PLANARCONFIG, PLANARCONFIG_CONTIG);
            TIFFSetField(tiff, TIFFTAG_ROWSPERSTRIP, 3);
            TIFFSetField(tiff, TIFFTAG_ORIENTATION, ORIENTATION_TOPLEFT);
            if (samples == 4) {
                uint16_t extra = EXTRASAMPLE_UNASSALPHA;
                TIFFSetField(tiff, TIFFTAG_EXTRASAMPLES, 1, &extra);
            }
            std::vector<uchar> row(size_t(width) * samples);
            for (uint32_t y = 0; y < height; ++y) {
                for (uint32_t x = 0; x < width; ++x) {
                    row[size_t(x) * samples] = uchar(x % 256);
                    row[size_t(x) * samples + 1] = uchar(y % 256);
                    row[size_t(x) * samples + 2] = uchar((x + y) % 256);
                    if (samples == 4) {
                        row[size_t(x) * samples + 3] = uchar((x * 3 + y) % 256);
                    }
                }
                QCOMPARE(TIFFWriteScanline(tiff, row.data(), y), 1);
            }
            TIFFClose(tiff);

            std::atomic_bool cancelled{false};
            const QImage preview =
                Licasa::readFastTiffPreview(path, QSize(320, 240), 80000, 320000, &cancelled);
            QVERIFY(!preview.isNull());
            QCOMPARE(preview.size(), QSize(320, 240));
            QVERIFY(preview.sizeInBytes() <= 320000);
            for (const QPoint point : {QPoint(0, 0), QPoint(159, 119), QPoint(319, 239)}) {
                const int sourceX = int(quint64(point.x()) * width / quint64(preview.width()));
                const int sourceY = int(quint64(point.y()) * height / quint64(preview.height()));
                const QColor color = preview.pixelColor(point);
                QCOMPARE(color.red(), sourceX % 256);
                QCOMPARE(color.green(), sourceY % 256);
                QCOMPARE(color.blue(), (sourceX + sourceY) % 256);
                QCOMPARE(color.alpha(), samples == 4 ? (sourceX * 3 + sourceY) % 256 : 255);
            }
            cancelled.store(true);
            QVERIFY(Licasa::readFastTiffPreview(path, QSize(320, 240), 80000, 320000, &cancelled)
                        .isNull());
        }
    }
#endif

    void nearbyPreviewPreparesWhileFullDecoderOwnsGate()
    {
        QTemporaryDir settings;
        QTemporaryDir images;
        QVERIFY(settings.isValid());
        QVERIFY(images.isValid());
        QSettings::setDefaultFormat(QSettings::IniFormat);
        QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, settings.path());
        Licasa::ViewerPreferences preferences;
        Licasa::ImageResourcePolicy policy(preferences);
        Licasa::AsyncImageProvider provider(policy);
        const QString path = images.filePath(QStringLiteral("neighbor.png"));
        QImage image(640, 360, QImage::Format_RGB32);
        image.fill(Qt::green);
        QVERIFY(image.save(path));
        const QUrl neighbor = QUrl::fromLocalFile(path);

        // Full development owns this gate, but a separately admitted small
        // neighbor can be ready before that long reader finishes.
        QMutexLocker fullDecodeGate(&policy.processingMutex());
        provider.prepareNearbyPreviews({neighbor}, QSize(640, 360), 384);
        QTRY_VERIFY_WITH_TIMEOUT(provider.hasNearbyPreview(neighbor), 1000);
        fullDecodeGate.unlock();
        provider.waitForPendingWork();
        QVERIFY(provider.nearbyPreviewBytes() <= 48 * 1024 * 1024);
    }

    void largeWebpNearbyPreviewRespectsDistanceAndMemory()
    {
        if (!QImageWriter::supportedImageFormats().contains("webp")) {
            QSKIP("WebP image writer unavailable");
        }
        QTemporaryDir settings;
        QTemporaryDir images;
        QVERIFY(settings.isValid());
        QVERIFY(images.isValid());
        QSettings::setDefaultFormat(QSettings::IniFormat);
        QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, settings.path());
        Licasa::ViewerPreferences preferences;
        Licasa::ImageResourcePolicy policy(preferences);
        const QString firstPath = images.filePath(QStringLiteral("neighbor-0.webp"));
        QImage source(3840, 2160, QImage::Format_RGB32);
        source.fill(QColor(30, 80, 140));
        QVERIFY(source.save(firstPath, "WEBP", 80));
        source = {};
        QList<QUrl> urls{QUrl::fromLocalFile(firstPath)};
        for (int index = 1; index < 4; ++index) {
            const QString path = images.filePath(QStringLiteral("neighbor-%1.webp").arg(index));
            QVERIFY(QFile::copy(firstPath, path));
            urls.append(QUrl::fromLocalFile(path));
        }

        const QSize target(1200, 675);
        Licasa::AsyncImageProvider minimumBudget(policy);
        minimumBudget.prepareNearbyPreviews(urls, target, 384);
        minimumBudget.waitForPendingWork();
        QVERIFY(!minimumBudget.hasNearbyPreview(urls.first()));

        Licasa::AsyncImageProvider provider(policy);
        provider.prepareNearbyPreviews(urls, target, 8192);
        provider.waitForPendingWork();
        for (int index = 0; index < 3; ++index) {
            QVERIFY(provider.hasNearbyPreview(urls.at(index)));
        }
        QVERIFY(!provider.hasNearbyPreview(urls.at(3)));
        QVERIFY(provider.nearbyPreviewBytes() <= 96 * 1024 * 1024);

        const quint64 hitsBefore = provider.nearbyPreviewHitCount();
        const QString request = QStringLiteral("1/") +
                                QString::fromLatin1(QUrl::toPercentEncoding(firstPath)) +
                                QStringLiteral("?licasa_stage=preview");
        std::unique_ptr<QQuickImageResponse> response(
            provider.requestImageResponse(request, target));
        provider.waitForPendingWork();
        QVERIFY2(response->errorString().isEmpty(), qPrintable(response->errorString()));
        std::unique_ptr<QQuickTextureFactory> texture(response->textureFactory());
        QVERIFY(texture);
        QCOMPARE(texture->textureSize(), target);
        QVERIFY(provider.nearbyPreviewHitCount() > hitsBefore);
    }

    void largePngPreviewReusesItsDecodedFullBase()
    {
        QTemporaryDir settings;
        QTemporaryDir images;
        QVERIFY(settings.isValid());
        QVERIFY(images.isValid());
        QSettings::setDefaultFormat(QSettings::IniFormat);
        QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, settings.path());
        Licasa::ViewerPreferences preferences;
        preferences.setMaximumImageMegapixels(40);
        Licasa::ImageResourcePolicy policy(preferences);
        Licasa::AsyncImageProvider provider(policy);
        const QString path = images.filePath(QStringLiteral("large.png"));
        const QUrl url = QUrl::fromLocalFile(path);
#ifdef LICASA_HAVE_FAST_PNG_PREVIEW
        QVERIFY(writePlainLargePng(path, true));
        QVERIFY(Licasa::fastPngBaseEligible(path, policy.maximumImagePixels()));
        std::atomic_bool cancelled{false};
        const auto direct = Licasa::readFastPngPreview(
            path, QSize(1000, 1000), policy.maximumImagePixels(), 8 * 1024 * 1024, &cancelled);
        QCOMPARE(direct.fullBase.format(), QImage::Format_ARGB32);
        QCOMPARE(direct.fullBase.pixelColor(2500, 2500), QColor(210, 40, 90, 120));
        QVERIFY(direct.fullBase.colorSpace() == QColorSpace(QColorSpace::SRgb));
#else
        QImage source(5000, 5000, QImage::Format_ARGB32);
        QVERIFY(!source.isNull());
        source.fill(QColor(30, 80, 140));
        source.setPixelColor(2500, 2500, QColor(210, 40, 90, 120));
        QVERIFY(source.save(path));
        source = {};
#endif

        const QString base =
            QStringLiteral("1/") + QString::fromLatin1(QUrl::toPercentEncoding(path));
        QVERIFY(!provider.hasDecodedFullBase(url));
        std::unique_ptr<QQuickImageResponse> preview(provider.requestImageResponse(
            base + QStringLiteral("?licasa_stage=preview"), QSize(1200, 1200)));
        provider.waitForPendingWork();
        QVERIFY(preview->errorString().isEmpty());
        std::unique_ptr<QQuickTextureFactory> previewTexture(preview->textureFactory());
        QVERIFY(previewTexture != nullptr);
        QCOMPARE(previewTexture->image().size(), QSize(1200, 1200));
        QVERIFY(provider.hasDecodedFullBase(url));
        const quint64 hitsBeforeFull = provider.decodedBaseCacheHitCount();

        std::unique_ptr<QQuickImageResponse> full(
            provider.requestImageResponse(base + QStringLiteral("?licasa_stage=full"), QSize()));
        provider.waitForPendingWork();
        QVERIFY(full->errorString().isEmpty());
        std::unique_ptr<QQuickTextureFactory> fullTexture(full->textureFactory());
        QVERIFY(fullTexture != nullptr);
        QCOMPARE(fullTexture->image().size(), QSize(5000, 5000));
        const QColor displayed = fullTexture->image().pixelColor(2500, 2500);
        QCOMPARE(displayed.red(), 210);
        QCOMPARE(displayed.green(), 40);
        QVERIFY(std::abs(displayed.blue() - 90) <= 1);
        QCOMPARE(displayed.alpha(), 120);
        QVERIFY(provider.decodedBaseCacheHitCount() > hitsBeforeFull);
        provider.releasePictureResources();
        provider.waitForPendingWork();
        QVERIFY(!provider.hasDecodedFullBase(url));
    }

#ifdef LICASA_HAVE_FAST_PNG_PREVIEW
    void largePngGetsBoundedNearbyPreview()
    {
        QTemporaryDir settings;
        QTemporaryDir images;
        QVERIFY(settings.isValid());
        QVERIFY(images.isValid());
        QSettings::setDefaultFormat(QSettings::IniFormat);
        QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, settings.path());
        Licasa::ViewerPreferences preferences;
        preferences.setMaximumImageMegapixels(40);
        Licasa::ImageResourcePolicy policy(preferences);
        Licasa::AsyncImageProvider provider(policy);
        const QString path = images.filePath(QStringLiteral("neighbor.png"));
        QVERIFY(writePlainLargePng(path, false));
        const QUrl url = QUrl::fromLocalFile(path);

        provider.prepareNearbyPreviews({url}, QSize(1000, 1000), 384);
        QTRY_VERIFY_WITH_TIMEOUT(provider.hasNearbyPreview(url), 5000);
        QVERIFY(provider.nearbyPreviewBytes() <= 5 * 1024 * 1024);
        provider.releasePictureResources();
        provider.waitForPendingWork();
    }

    void fastPngRgbPreviewRespectsPixelsAndCancellation()
    {
        QTemporaryDir images;
        QVERIFY(images.isValid());
        const QString path = images.filePath(QStringLiteral("rgb.png"));
        QVERIFY(writePlainLargePng(path, false));
        QVERIFY(Licasa::fastPngBaseEligible(path, 40000000));
        std::atomic_bool cancelled{false};
        const auto decoded = Licasa::readFastPngPreview(path, QSize(1000, 1000), 40000000,
                                                        8 * 1024 * 1024, &cancelled);
        QCOMPARE(decoded.preview.size(), QSize(1000, 1000));
        QCOMPARE(decoded.fullBase.size(), QSize(5000, 5000));
        QCOMPARE(decoded.preview.format(), QImage::Format_RGB32);
        QCOMPARE(decoded.fullBase.format(), QImage::Format_RGB32);
        QCOMPARE(decoded.preview.pixelColor(500, 500), QColor(210, 40, 90));
        QCOMPARE(decoded.fullBase.pixelColor(2500, 2500), QColor(210, 40, 90));
        const QImage nearby = Licasa::readFastPngNearbyPreview(path, QSize(1000, 1000), 40000000,
                                                               8 * 1024 * 1024, &cancelled);
        QCOMPARE(nearby.size(), QSize(1000, 1000));
        QCOMPARE(nearby.pixelColor(500, 500), QColor(210, 40, 90));
        cancelled.store(true);
        QVERIFY(Licasa::readFastPngNearbyPreview(path, QSize(1000, 1000), 40000000, 8 * 1024 * 1024,
                                                 &cancelled)
                    .isNull());
        QVERIFY(Licasa::readFastPngPreview(path, QSize(1000, 1000), 40000000, 8 * 1024 * 1024,
                                           &cancelled)
                    .preview.isNull());
        cancelled.store(false);
        QVERIFY(Licasa::readFastPngPreview(path, QSize(1000, 1000), 40000000, 1000, &cancelled)
                    .preview.isNull());
        QFile truncated(path);
        QVERIFY(truncated.open(QIODevice::ReadWrite));
        QVERIFY(truncated.resize(truncated.size() / 2));
        truncated.close();
        QVERIFY(Licasa::readFastPngPreview(path, QSize(1000, 1000), 40000000, 8 * 1024 * 1024,
                                           &cancelled)
                    .preview.isNull());
    }
#endif

    void selectedPreviewDoesNotWaitForCancelledFullDecodeGate()
    {
        QTemporaryDir settings;
        QTemporaryDir images;
        QVERIFY(settings.isValid());
        QVERIFY(images.isValid());
        QSettings::setDefaultFormat(QSettings::IniFormat);
        QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, settings.path());
        Licasa::ViewerPreferences preferences;
        Licasa::ImageResourcePolicy policy(preferences);
        Licasa::AsyncImageProvider provider(policy);
        const QString path = images.filePath(QStringLiteral("next.png"));
        const QString stalePath = images.filePath(QStringLiteral("previous.png"));
        QImage image(640, 360, QImage::Format_RGB32);
        image.fill(Qt::green);
        QVERIFY(image.save(path));
        image.fill(Qt::red);
        QVERIFY(image.save(stalePath));
        const QString requestId = QStringLiteral("1/") +
                                  QString::fromLatin1(QUrl::toPercentEncoding(path)) +
                                  QStringLiteral("?licasa_stage=preview");
        const QString staleRequestId = QStringLiteral("1/") +
                                       QString::fromLatin1(QUrl::toPercentEncoding(stalePath)) +
                                       QStringLiteral("?licasa_stage=preview");

        // A previous full reader can keep this gate during native cancellation.
        // The bounded selected preview must still reach its first pixels.
        provider.releasePictureResources(true, QUrl::fromLocalFile(path));
        QMutexLocker oldDecodeGate(&policy.processingMutex());
        std::unique_ptr<QQuickImageResponse> staleResponse(
            provider.requestImageResponse(staleRequestId, QSize(640, 360)));
        std::unique_ptr<QQuickImageResponse> response(
            provider.requestImageResponse(requestId, QSize(640, 360)));
        QSignalSpy finished(response.get(), &QQuickImageResponse::finished);
        const bool readyWithoutGate = finished.wait(1000);
        oldDecodeGate.unlock();
        provider.waitForPendingWork();

        QVERIFY(readyWithoutGate);
        std::unique_ptr<QQuickTextureFactory> texture(response->textureFactory());
        QVERIFY(texture != nullptr);
        QCOMPARE(texture->image().pixelColor(0, 0), QColor(Qt::green));
    }

    void navigationCancelsQueuedImageBeforeDecodingTheNextOne()
    {
        QTemporaryDir settings;
        QTemporaryDir images;
        QVERIFY(settings.isValid());
        QVERIFY(images.isValid());
        QSettings::setDefaultFormat(QSettings::IniFormat);
        QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, settings.path());
        Licasa::ViewerPreferences preferences;
        Licasa::ImageResourcePolicy policy(preferences);
        Licasa::AsyncImageProvider provider(policy);

        const QString oldPath = images.filePath(QStringLiteral("old.png"));
        const QString nextPath = images.filePath(QStringLiteral("next.png"));
        QImage image(64, 64, QImage::Format_RGB32);
        image.fill(Qt::red);
        QVERIFY(image.save(oldPath));
        image.fill(Qt::green);
        QVERIFY(image.save(nextPath));
        const auto requestId = [](const QString& path) {
            return QStringLiteral("1/") + QString::fromLatin1(QUrl::toPercentEncoding(path)) +
                   QStringLiteral("?licasa_stage=preview");
        };

        QSemaphore entered, release;
        provider.enqueueWork([&] {
            entered.release();
            release.acquire();
        });
        entered.acquire();
        std::unique_ptr<QQuickImageResponse> oldResponse(
            provider.requestImageResponse(requestId(oldPath), QSize(64, 64)));
        provider.releasePictureResources(true);
        std::unique_ptr<QQuickImageResponse> nextResponse(
            provider.requestImageResponse(requestId(nextPath), QSize(64, 64)));
        release.release();
        provider.waitForPendingWork();

        QVERIFY(oldResponse->textureFactory() == nullptr);
        std::unique_ptr<QQuickTextureFactory> nextTexture(nextResponse->textureFactory());
        QVERIFY(nextTexture != nullptr);
        QCOMPARE(nextTexture->image().pixelColor(0, 0), QColor(Qt::green));
    }

    void nearbyPreviewsStayWithinTheSelectedMemoryBudget()
    {
        QTemporaryDir settings;
        QTemporaryDir images;
        QVERIFY(settings.isValid());
        QVERIFY(images.isValid());
        QSettings::setDefaultFormat(QSettings::IniFormat);
        QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, settings.path());
        Licasa::ViewerPreferences preferences;
        Licasa::ImageResourcePolicy policy(preferences);
        Licasa::AsyncImageProvider provider(policy);
        QList<QUrl> firstWindow;
        QList<QUrl> secondWindow;
        for (int index = 0; index < 11; ++index) {
            const QString path = images.filePath(QStringLiteral("photo-%1.png").arg(index));
            QImage image(800, 800, QImage::Format_ARGB32);
            image.fill(QColor::fromRgb(index * 12, 30, 90));
            QVERIFY(image.save(path));
            const QUrl url = QUrl::fromLocalFile(path);
            if (index < 10) {
                firstWindow.append(url);
            }
            if (index > 0) {
                secondWindow.append(url);
            }
        }

        provider.prepareNearbyPreviews(firstWindow, QSize(1200, 1200), 384);
        provider.waitForPendingWork();
        QCOMPARE(provider.nearbyPreviewCount(), 10);
        QVERIFY(provider.nearbyPreviewBytes() <= 48 * 1024 * 1024);
        QVERIFY(provider.hasNearbyPreview(firstWindow.first()));

        const QString originalPath = firstWindow.first().toLocalFile();
        const QString replacementPath = images.filePath(QStringLiteral("replacement.png"));
        QVERIFY(QFile::copy(originalPath, replacementPath));
        QVERIFY(QFile::remove(originalPath));
        QVERIFY(QFile::rename(replacementPath, originalPath));
        QVERIFY(!provider.hasNearbyPreview(firstWindow.first()));

        provider.prepareNearbyPreviews(secondWindow, QSize(1200, 1200), 384);
        provider.waitForPendingWork();
        QCOMPARE(provider.nearbyPreviewCount(), 10);
        QVERIFY(!provider.hasNearbyPreview(firstWindow.first()));
        QVERIFY(provider.hasNearbyPreview(secondWindow.last()));

        QImage changed(799, 800, QImage::Format_ARGB32);
        changed.fill(Qt::green);
        QVERIFY(changed.save(secondWindow.last().toLocalFile()));
        QVERIFY(!provider.hasNearbyPreview(secondWindow.last()));

        const QString hugePath = images.filePath(QStringLiteral("huge.png"));
        QImage huge(1600, 1600, QImage::Format_ARGB32);
        huge.fill(Qt::blue);
        QVERIFY(huge.save(hugePath));
        provider.prepareNearbyPreviews({QUrl::fromLocalFile(hugePath)}, QSize(1200, 1200), 384);
        provider.waitForPendingWork();
        QVERIFY(!provider.hasNearbyPreview(QUrl::fromLocalFile(hugePath)));
        QVERIFY(provider.nearbyPreviewBytes() <= 48 * 1024 * 1024);

        provider.prepareNearbyPreviews({secondWindow.last()}, QSize(400, 400), 384);
        provider.waitForPendingWork();
        QVERIFY(provider.hasNearbyPreview(secondWindow.last()));
        const QString requestId =
            QStringLiteral("1/") +
            QString::fromLatin1(QUrl::toPercentEncoding(secondWindow.last().toLocalFile())) +
            QStringLiteral("?licasa_stage=preview");
        const quint64 hitsBeforeResponse = provider.nearbyPreviewHitCount();
        std::unique_ptr<QQuickImageResponse> response(
            provider.requestImageResponse(requestId, QSize(1200, 1200)));
        provider.waitForPendingWork();
        QVERIFY(response->errorString().isEmpty());
        QVERIFY(provider.nearbyPreviewHitCount() > hitsBeforeResponse);

        provider.releasePictureResources();
        provider.waitForPendingWork();
        QCOMPARE(provider.nearbyPreviewCount(), 0);
    }

    void destructionDrainsQueuedWorkOnTheWorker()
    {
        QTemporaryDir settings;
        QVERIFY(settings.isValid());
        QSettings::setDefaultFormat(QSettings::IniFormat);
        QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, settings.path());
        Licasa::ViewerPreferences preferences;
        Licasa::ImageResourcePolicy policy(preferences);
        auto provider = std::make_unique<Licasa::AsyncImageProvider>(policy);
        QSemaphore entered, release;
        std::atomic_bool cleanupRan{false};
        std::atomic_bool cleanupRanOffMainThread{false};
        QThread* mainThread = QThread::currentThread();
        provider->enqueueWork([&] {
            entered.release();
            release.acquire();
        });
        entered.acquire();
        provider->enqueueWork([&] {
            cleanupRanOffMainThread = QThread::currentThread() != mainThread;
            cleanupRan = true;
        });
        // Keep cleanup queued when destruction begins, then let the active job
        // finish. Destruction must execute cleanup, not drop its closure.
        std::thread unblock([&] {
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
            release.release();
        });
        provider.reset();
        unblock.join();
        QVERIFY(cleanupRan.load());
        QVERIFY(cleanupRanOffMainThread.load());
    }
};

QTEST_MAIN(AsyncImageProviderTest)
#include "tst_async_image_provider.moc"

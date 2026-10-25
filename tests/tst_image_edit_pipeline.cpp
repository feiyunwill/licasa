#include "imaging/image_edit_pipeline.h"

#include <QColorSpace>
#include <QList>
#include <QRandomGenerator>
#include <QScopeGuard>
#include <QTest>
#include <QTransform>

#include <algorithm>
#include <cmath>

namespace {

// Scalar specification of the original color equations. Keep this independent
// of the production execution plan, including calculations for disabled stages.
QRgb referenceColor(QRgb pixel, int x, int y, const QSize& size,
                    const Licasa::ImageEditParameters& edit)
{
    if (qAlpha(pixel) == 0) {
        return pixel;
    }
    const double exposure = std::pow(2.0, edit.exposure);
    double red = qRed(pixel) * exposure;
    double green = qGreen(pixel) * exposure;
    double blue = qBlue(pixel) * exposure;
    const double luminance =
        std::clamp((0.299 * red + 0.587 * green + 0.114 * blue) / 255.0, 0.0, 1.0);
    const double shift = edit.shadows * 72.0 * std::pow(1.0 - luminance, 2.0) +
                         edit.highlights * 72.0 * std::pow(luminance, 2.0);
    const double contrast = std::max(0.0, 1.0 + edit.contrast);
    red = (red - 127.5) * contrast + 127.5 + shift;
    green = (green - 127.5) * contrast + 127.5 + shift;
    blue = (blue - 127.5) * contrast + 127.5 + shift;
    const double warmth = edit.warmth * 42.0;
    const double tint = edit.tint * 36.0;
    red += warmth + tint * 0.45;
    green -= tint;
    blue += -warmth + tint * 0.45;
    const double gray = 0.299 * red + 0.587 * green + 0.114 * blue;
    const double chroma =
        std::clamp((std::max({red, green, blue}) - std::min({red, green, blue})) / 255.0, 0.0, 1.0);
    const double saturation =
        std::max(0.0, 1.0 + edit.saturation + edit.vibrance * (1.0 - chroma) * 0.75);
    red = gray + (red - gray) * saturation;
    green = gray + (green - gray) * saturation;
    blue = gray + (blue - gray) * saturation;
    if (edit.vignette > 0.0) {
        const double centerX = std::max(1.0, (size.width() - 1) * 0.5);
        const double centerY = std::max(1.0, (size.height() - 1) * 0.5);
        const double nx = (x - centerX) / centerX;
        const double ny = (y - centerY) / centerY;
        const double radius = std::clamp(std::sqrt(nx * nx + ny * ny), 0.0, 1.5);
        const double edge = std::clamp((radius - 0.28) / 0.92, 0.0, 1.0);
        const double factor = 1.0 - edit.vignette * 0.72 * (edge * edge * (3.0 - 2.0 * edge));
        red *= factor;
        green *= factor;
        blue *= factor;
    }
    const auto channel = [](double value) {
        return int(std::clamp(std::round(value), 0.0, 255.0));
    };
    return qRgba(channel(red), channel(green), channel(blue), qAlpha(pixel));
}

QImage randomImage(const QSize& size, QRandomGenerator& random)
{
    QImage image(size, QImage::Format_ARGB32);
    for (int y = 0; y < size.height(); ++y) {
        auto* row = reinterpret_cast<QRgb*>(image.scanLine(y));
        for (int x = 0; x < size.width(); ++x) {
            row[x] = random.generate();
        }
    }
    image.setColorSpace(QColorSpace::DisplayP3);
    image.setText(QStringLiteral("caption"), QStringLiteral("preserved"));
    image.setDevicePixelRatio(1.5);
    return image;
}

} // namespace

class ImageEditPipelineTest final : public QObject {
    Q_OBJECT

  private slots:
    void colorAdjustmentsMatchScalarReference()
    {
        using Parameters = Licasa::ImageEditParameters;
        qreal Parameters::*const fields[] = {
            &Parameters::exposure, &Parameters::contrast,   &Parameters::highlights,
            &Parameters::shadows,  &Parameters::saturation, &Parameters::vibrance,
            &Parameters::warmth,   &Parameters::tint,       &Parameters::vignette};
        QList<Parameters> cases;
        for (auto field : fields) {
            for (qreal value : {-1.0, -0.01, 0.01, 1.0}) {
                Parameters edit;
                edit.*field = field == &Parameters::vignette ? std::abs(value) : value;
                cases.append(edit);
            }
        }
        QRandomGenerator random(0xc0104u);
        for (int trial = 0; trial < 64; ++trial) {
            Parameters edit;
            for (auto field : fields) {
                edit.*field = random.generateDouble() * 2.0 - 1.0;
            }
            edit.vignette = std::abs(edit.vignette);
            cases.append(edit);
            // Channel tables must agree both with RGB-dependent stages enabled
            // and with only the channel-independent controls combined.
            edit.highlights = 0.0;
            edit.shadows = 0.0;
            cases.append(edit);
            edit.saturation = 0.0;
            edit.vibrance = 0.0;
            edit.vignette = 0.0;
            cases.append(edit);
        }
        for (const QSize size :
             {QSize(1, 1), QSize(2, 2), QSize(1, 17), QSize(23, 1), QSize(37, 29)}) {
            const QImage raster = randomImage(size, random);
            for (auto format : {QImage::Format_ARGB32, QImage::Format_RGBA8888,
                                QImage::Format_ARGB32_Premultiplied, QImage::Format_RGB888}) {
                const QImage source = raster.convertToFormat(format);
                for (const Parameters& edit : cases) {
                    QImage expected = source.convertToFormat(QImage::Format_ARGB32);
                    for (int y = 0; y < size.height(); ++y) {
                        for (int x = 0; x < size.width(); ++x) {
                            expected.setPixel(
                                x, y, referenceColor(expected.pixel(x, y), x, y, size, edit));
                        }
                    }
                    QImage actual = source;
                    QVERIFY(Licasa::applyImageEdits(actual, edit));
                    QCOMPARE(actual, expected);
                    QCOMPARE(actual.colorSpace(), source.colorSpace());
                    QCOMPARE(actual.devicePixelRatio(), source.devicePixelRatio());
                    QCOMPARE(actual.text("caption"), source.text("caption"));
                    QCOMPARE(source, raster.convertToFormat(format));
                }
            }
        }
    }

    void vignetteCacheBoundaryPreservesPixels()
    {
        QRandomGenerator random(0x519u);
        Licasa::ImageEditParameters edit;
        edit.vignette = 0.63;
        edit.exposure = -0.3;
        for (int width : {65535, 65536, 65537}) {
            const QImage source = randomImage(QSize(width, 3), random);
            QImage actual = source;
            QVERIFY(Licasa::applyImageEdits(actual, edit));
            for (int y = 0; y < source.height(); ++y) {
                for (int x = 0; x < source.width(); ++x) {
                    QCOMPARE(actual.pixel(x, y),
                             referenceColor(source.pixel(x, y), x, y, source.size(), edit));
                }
            }
        }
    }

    void parallelColorAdjustmentsMatchSerialPixels()
    {
        const QByteArray previousBackend = qgetenv("LICASA_EDIT_BACKEND");
        const QByteArray previousWorkers = qgetenv("LICASA_CPU_EDIT_WORKERS");
        const auto restore = qScopeGuard([&] {
            if (previousBackend.isNull()) {
                qunsetenv("LICASA_EDIT_BACKEND");
            } else {
                qputenv("LICASA_EDIT_BACKEND", previousBackend);
            }
            if (previousWorkers.isNull()) {
                qunsetenv("LICASA_CPU_EDIT_WORKERS");
            } else {
                qputenv("LICASA_CPU_EDIT_WORKERS", previousWorkers);
            }
        });
        qputenv("LICASA_EDIT_BACKEND", "cpu");
        QRandomGenerator random(0x6123u);
        const QImage source = randomImage(QSize(2048, 2048), random);
        Licasa::ImageEditParameters edit;
        edit.exposure = 0.35;
        edit.contrast = -0.17;
        edit.vibrance = 0.41;
        edit.shadows = 0.23;
        edit.vignette = 0.38;

        qputenv("LICASA_CPU_EDIT_WORKERS", "1");
        QImage serial = source;
        QVERIFY(Licasa::applyImageEdits(serial, edit));
        qputenv("LICASA_CPU_EDIT_WORKERS", "8");
        QImage parallel = source;
        QVERIFY(Licasa::applyImageEdits(parallel, edit));
        QCOMPARE(parallel, serial);
        QCOMPARE(parallel.colorSpace(), source.colorSpace());
    }

    void backendEnvironmentOverrideAppliesWithExecutionTelemetry()
    {
        const QByteArray previous = qgetenv("LICASA_EDIT_BACKEND");
        qputenv("LICASA_EDIT_BACKEND", "cpu");

        QImage image(1024, 768, QImage::Format_ARGB32);
        image.fill(qRgb(90, 120, 150));
        Licasa::ImageEditParameters edit;
        edit.exposure = 0.5;
        Licasa::ImageEditExecution execution;
        const bool edited = Licasa::applyImageEdits(image, edit, nullptr, &execution);
        const auto requested = execution.requested;
        const auto used = execution.used;

        if (previous.isNull()) {
            qunsetenv("LICASA_EDIT_BACKEND");
        } else {
            qputenv("LICASA_EDIT_BACKEND", previous);
        }

        QVERIFY(edited);
        QCOMPARE(requested, Licasa::ImageEditBackend::Cpu);
        QCOMPARE(used, Licasa::ImageEditBackend::Cpu);
    }

    void composedGeometryMatchesQtAndPreservesMetadata()
    {
        QRandomGenerator random(0x9167u);
        for (const QSize size : {QSize(1, 1), QSize(1, 7), QSize(7, 1), QSize(32, 17)}) {
            const QImage raster = randomImage(size, random);
            for (auto format : {QImage::Format_Mono, QImage::Format_Indexed8, QImage::Format_RGB888,
                                QImage::Format_ARGB32, QImage::Format_ARGB32_Premultiplied,
                                QImage::Format_RGBA64}) {
                QImage source = raster.convertToFormat(format);
                source.setOffset(QPoint(7, 9));
                source.setDotsPerMeterX(1234);
                source.setDotsPerMeterY(4321);
                for (int turns = 0; turns < 4; ++turns) {
                    for (bool horizontal : {false, true}) {
                        for (bool vertical : {false, true}) {
                            QTransform rotation;
                            rotation.rotate(turns * 90.0);
                            QImage expected =
                                source.transformed(rotation).mirrored(horizontal, vertical);
                            if (!expected.colorSpace().isValid()) {
                                expected.setColorSpace(QColorSpace::SRgb);
                            }
                            Licasa::ImageEditParameters edit;
                            edit.quarterTurns = turns;
                            edit.flipHorizontal = horizontal;
                            edit.flipVertical = vertical;
                            for (bool shared : {false, true}) {
                                QImage actual = shared ? source : source.copy();
                                if (!shared) {
                                    actual.setOffset(source.offset());
                                }
                                QVERIFY(Licasa::applyImageEdits(actual, edit));
                                QCOMPARE(actual, expected);
                                QVERIFY2(actual.offset() == expected.offset(),
                                         qPrintable(QStringLiteral(
                                                        "size=%1x%2 format=%3 turns=%4 h=%5 v=%6 "
                                                        "shared=%7 offset=%8,%9 expected=%10,%11")
                                                        .arg(size.width())
                                                        .arg(size.height())
                                                        .arg(int(format))
                                                        .arg(turns)
                                                        .arg(horizontal)
                                                        .arg(vertical)
                                                        .arg(shared)
                                                        .arg(actual.offset().x())
                                                        .arg(actual.offset().y())
                                                        .arg(expected.offset().x())
                                                        .arg(expected.offset().y())));
                                QCOMPARE(actual.colorSpace(), expected.colorSpace());
                                QCOMPARE(actual.devicePixelRatio(), expected.devicePixelRatio());
                                QCOMPARE(actual.dotsPerMeterX(), expected.dotsPerMeterX());
                                QCOMPARE(actual.dotsPerMeterY(), expected.dotsPerMeterY());
                                QCOMPARE(actual.text("caption"), expected.text("caption"));
                            }
                        }
                    }
                }
            }
        }
    }

    void halfTurnReusesOwnedRaster()
    {
        QRandomGenerator random(0x77a1u);
        for (bool horizontal : {false, true}) {
            for (bool vertical : {false, true}) {
                QImage actual = randomImage(QSize(37, 29), random);
                actual.setColorSpace(QColorSpace::SRgb);
                const uchar* storage = actual.constBits();
                const QImage source = actual.copy();
                QTransform rotation;
                rotation.rotate(180);
                const QImage expected = source.transformed(rotation).mirrored(horizontal, vertical);
                Licasa::ImageEditParameters edit;
                edit.quarterTurns = 2;
                edit.flipHorizontal = horizontal;
                edit.flipVertical = vertical;
                QVERIFY(Licasa::applyImageEdits(actual, edit));
                QCOMPARE(actual.constBits(), storage);
                QImage expectedTagged = expected;
                if (!expectedTagged.colorSpace().isValid()) {
                    expectedTagged.setColorSpace(QColorSpace::SRgb);
                }
                QCOMPARE(actual, expectedTagged);
                QCOMPARE(actual.colorSpace(), expectedTagged.colorSpace());
                QCOMPARE(actual.offset(), expected.offset());
            }
        }
    }

    void mirrorReusesOwnedRasterAndPreservesSharedSource()
    {
        QRandomGenerator random(0x5151u);
        for (const QSize size : {QSize(1, 7), QSize(7, 1), QSize(32, 17)}) {
            for (bool horizontal : {false, true}) {
                for (bool vertical : {false, true}) {
                    QImage owned = randomImage(size, random);
                    const QImage original = owned.copy();
                    const QImage expected = original.mirrored(horizontal, vertical);
                    const uchar* storage = owned.constBits();
                    Licasa::ImageEditParameters edit;
                    edit.flipHorizontal = horizontal;
                    edit.flipVertical = vertical;
                    QVERIFY(Licasa::applyImageEdits(owned, edit));
                    QCOMPARE(owned, expected);
                    QCOMPARE(owned.constBits(), storage);
                    QCOMPARE(owned.colorSpace(), original.colorSpace());
                    QCOMPARE(owned.text("caption"), original.text("caption"));
                    QImage shared = original;
                    QVERIFY(Licasa::applyImageEdits(shared, edit));
                    QCOMPARE(shared, expected);
                    QCOMPARE(original.mirrored(horizontal, vertical), expected);
                }
            }
        }
    }
};

QTEST_GUILESS_MAIN(ImageEditPipelineTest)
#include "tst_image_edit_pipeline.moc"

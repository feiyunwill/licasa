#include "imaging/image_edit_pipeline.h"

#include <QColorSpace>
#include <QList>
#include <QRandomGenerator>
#include <QScopeGuard>
#include <QTest>
#include <QTransform>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <thread>

namespace {

QImage raster(const QSize& size)
{
    QRandomGenerator random(0x33c0104u);
    QImage image(size, QImage::Format_ARGB32);
    for (int y = 0; y < size.height(); ++y) {
        auto* row = reinterpret_cast<QRgb*>(image.scanLine(y));
        for (int x = 0; x < size.width(); ++x) {
            row[x] = random.generate();
        }
    }
    image.setColorSpace(QColorSpace::SRgb);
    image.setDevicePixelRatio(1.75);
    image.setDotsPerMeterX(1234);
    image.setDotsPerMeterY(5678);
    image.setOffset(QPoint(7, 9));
    image.setText(QStringLiteral("caption"), QStringLiteral("round three"));
    return image;
}

int roundedChannel(double value) { return int(std::clamp(std::round(value), 0.0, 255.0)); }

// These are the original scalar equations, including disabled arithmetic.
// The oracle deliberately does not use the production tables or row plans.
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
    return qRgba(roundedChannel(red), roundedChannel(green), roundedChannel(blue), qAlpha(pixel));
}

QImage referenceColorImage(const QImage& source, const Licasa::ImageEditParameters& edit)
{
    QImage result = source.convertToFormat(QImage::Format_ARGB32);
    for (int y = 0; y < result.height(); ++y) {
        auto* row = reinterpret_cast<QRgb*>(result.scanLine(y));
        for (int x = 0; x < result.width(); ++x) {
            row[x] = referenceColor(row[x], x, y, result.size(), edit);
        }
    }
    return result;
}

QImage referenceSharpen(const QImage& source, double amount)
{
    QImage result = source.copy();
    for (int y = 1; y < source.height() - 1; ++y) {
        for (int x = 1; x < source.width() - 1; ++x) {
            const QRgb center = source.pixel(x, y);
            const QRgb left = source.pixel(x - 1, y);
            const QRgb right = source.pixel(x + 1, y);
            const QRgb top = source.pixel(x, y - 1);
            const QRgb bottom = source.pixel(x, y + 1);
            const auto channel = [amount](int c, int l, int r, int t, int b) {
                return roundedChannel(c + (c * 4.0 - l - r - t - b) * (amount * 0.48));
            };
            result.setPixel(
                x, y,
                qRgba(channel(qRed(center), qRed(left), qRed(right), qRed(top), qRed(bottom)),
                      channel(qGreen(center), qGreen(left), qGreen(right), qGreen(top),
                              qGreen(bottom)),
                      channel(qBlue(center), qBlue(left), qBlue(right), qBlue(top), qBlue(bottom)),
                      qAlpha(center)));
        }
    }
    return result;
}

void compareMetadata(const QImage& actual, const QImage& expected)
{
    QCOMPARE(actual.format(), expected.format());
    QCOMPARE(actual.offset(), expected.offset());
    QCOMPARE(actual.colorSpace(), expected.colorSpace());
    QCOMPARE(actual.devicePixelRatio(), expected.devicePixelRatio());
    QCOMPARE(actual.dotsPerMeterX(), expected.dotsPerMeterX());
    QCOMPARE(actual.dotsPerMeterY(), expected.dotsPerMeterY());
    QCOMPARE(actual.text(QStringLiteral("caption")), expected.text(QStringLiteral("caption")));
}

} // namespace

class RuntimeEditRound3Test final : public QObject {
    Q_OBJECT

  private slots:
    void fullDesaturationMatchesOriginalEquations()
    {
        QList<Licasa::ImageEditParameters> cases;
        for (double vibrance : {-1.0, -0.4, 0.0, std::nextafter(0.0, 1.0), 0.4}) {
            Licasa::ImageEditParameters edit;
            edit.saturation = -1.0;
            edit.vibrance = vibrance;
            cases.append(edit);
            edit.exposure = -0.4;
            edit.contrast = 0.5;
            edit.highlights = 0.7;
            edit.shadows = -0.3;
            edit.warmth = 0.4;
            edit.tint = -0.5;
            cases.append(edit);
            edit.vignette = 0.63;
            cases.append(edit);
            edit.saturation = std::nextafter(-1.0, 0.0);
            cases.append(edit);
        }
        for (const QSize size : {QSize(1, 7), QSize(2, 2), QSize(37, 29), QSize(256, 64)}) {
            const QImage original = raster(size);
            for (auto format : {QImage::Format_ARGB32, QImage::Format_ARGB32_Premultiplied,
                                QImage::Format_RGB888, QImage::Format_RGBA64}) {
                const QImage source = original.convertToFormat(format);
                for (const auto& edit : cases) {
                    QImage actual = source;
                    Licasa::ImageEditExecution execution;
                    execution.requested = Licasa::ImageEditBackend::Cpu;
                    QVERIFY(Licasa::applyImageEdits(actual, edit, nullptr, &execution));
                    const QImage expected = referenceColorImage(source, edit);
                    QCOMPARE(actual, expected);
                    compareMetadata(actual, expected);
                    QCOMPARE(source, original.convertToFormat(format));
                }
            }
        }
    }

    void colorAndSharpenMatchIndependentTwoStageOracle()
    {
        QList<Licasa::ImageEditParameters> cases;
        Licasa::ImageEditParameters edit;
        edit.exposure = 0.4;
        edit.sharpen = 0.7;
        cases.append(edit);
        edit.contrast = 0.5; // Includes channel half boundaries.
        edit.highlights = -0.4;
        edit.shadows = 0.3;
        edit.saturation = 0.7;
        edit.vibrance = 0.5;
        edit.warmth = -0.3;
        edit.tint = 0.4;
        cases.append(edit);
        edit.saturation = -1.0;
        edit.vibrance = -0.4;
        cases.append(edit);
        edit.vignette = 0.63; // Exercises the existing unfused path.
        cases.append(edit);
        for (const QSize size : {QSize(2, 7), QSize(3, 3), QSize(3, 7), QSize(37, 29),
                                 QSize(514, 514), QSize(515, 514)}) {
            const QImage original = raster(size);
            for (auto format : {QImage::Format_ARGB32, QImage::Format_ARGB32_Premultiplied,
                                QImage::Format_RGB888, QImage::Format_RGBA64}) {
                const QImage source = original.convertToFormat(format);
                for (const auto& parameters : cases) {
                    QImage actual = source;
                    const QImage expected = referenceSharpen(
                        referenceColorImage(source, parameters), parameters.sharpen);
                    Licasa::ImageEditExecution execution;
                    execution.requested = Licasa::ImageEditBackend::Cpu;
                    QVERIFY(Licasa::applyImageEdits(actual, parameters, nullptr, &execution));
                    QCOMPARE(actual, expected);
                    compareMetadata(actual, expected);
                    QCOMPARE(source, original.convertToFormat(format));
                }
            }
        }
    }

    void combinedStagesMatchSerialBandsAndKeepSharedSource()
    {
        const QByteArray prior = qgetenv("LICASA_CPU_EDIT_WORKERS");
        const auto restore = qScopeGuard([&] {
            prior.isNull() ? qunsetenv("LICASA_CPU_EDIT_WORKERS")
                           : qputenv("LICASA_CPU_EDIT_WORKERS", prior);
        });
        const QImage source = raster(QSize(4096, 8193));
        const QImage unchanged = source.copy();
        Licasa::ImageEditParameters edit;
        edit.sharpen = 0.7;
        edit.exposure = 0.3;
        edit.shadows = -0.4;
        edit.vibrance = 0.6;
        Licasa::ImageEditExecution execution;
        execution.requested = Licasa::ImageEditBackend::Cpu;
        qputenv("LICASA_CPU_EDIT_WORKERS", "1");
        QImage serial = source;
        QVERIFY(Licasa::applyImageEdits(serial, edit, nullptr, &execution));
        for (const QByteArray& workers : {QByteArray("2"), QByteArray("8"), QByteArray("16")}) {
            qputenv("LICASA_CPU_EDIT_WORKERS", workers);
            QImage actual = source;
            QVERIFY(Licasa::applyImageEdits(actual, edit, nullptr, &execution));
            QCOMPARE(actual, serial);
            compareMetadata(actual, serial);
            QCOMPARE(source, unchanged);
        }
    }

    void quarterTurnWithSingleMirrorMatchesQtSequence()
    {
        for (const QSize size : {QSize(1, 7), QSize(7, 1), QSize(2, 3), QSize(37, 29)}) {
            const QImage original = raster(size);
            for (auto format :
                 {QImage::Format_RGB32, QImage::Format_ARGB32, QImage::Format_ARGB32_Premultiplied,
                  QImage::Format_RGBX8888, QImage::Format_RGBA8888,
                  QImage::Format_RGBA8888_Premultiplied, QImage::Format_RGB30, QImage::Format_BGR30,
                  QImage::Format_A2RGB30_Premultiplied, QImage::Format_A2BGR30_Premultiplied,
                  QImage::Format_RGB888, QImage::Format_RGBA64}) {
                for (const QColorSpace& profile : {QColorSpace(), QColorSpace(QColorSpace::SRgb),
                                                   QColorSpace(QColorSpace::DisplayP3)}) {
                    for (const QPoint offset : {QPoint(), QPoint(7, 9)}) {
                        QImage source = original.convertToFormat(format);
                        source.setColorSpace(profile);
                        source.setOffset(offset);
                        const QImage unchanged = source.copy();
                        for (int turns : {1, 3}) {
                            for (bool horizontal : {false, true}) {
                                Licasa::ImageEditParameters edit;
                                edit.quarterTurns = turns;
                                edit.flipHorizontal = horizontal;
                                edit.flipVertical = !horizontal;
                                QTransform rotation;
                                rotation.rotate(turns * 90.0);
                                QImage expected = source.transformed(rotation).mirrored(
                                    edit.flipHorizontal, edit.flipVertical);
                                if (!expected.colorSpace().isValid()) {
                                    expected.setColorSpace(QColorSpace::SRgb);
                                }
                                for (bool shared : {false, true}) {
                                    QImage actual = shared ? source : source.copy();
                                    Licasa::ImageEditExecution execution;
                                    execution.requested = Licasa::ImageEditBackend::Cpu;
                                    QVERIFY(
                                        Licasa::applyImageEdits(actual, edit, nullptr, &execution));
                                    if (actual != expected) {
                                        qWarning()
                                            << "geometry mismatch" << size << format << profile
                                            << offset << turns << horizontal << shared
                                            << actual.format() << expected.format();
                                    }
                                    QCOMPARE(actual, expected);
                                    compareMetadata(actual, expected);
                                    QCOMPARE(source, unchanged);
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    void paddedGeometryKeepsSourceBytesAndSimdTails()
    {
        constexpr int width = 37;
        constexpr int height = 29;
        constexpr int stride = 256;
        QByteArray storage(stride * height + 4, char(0x59));
        QImage source(reinterpret_cast<uchar*>(storage.data()) + 4, width, height, stride,
                      QImage::Format_ARGB32);
        QRandomGenerator random(0x331052u);
        for (int y = 0; y < height; ++y) {
            auto* row = reinterpret_cast<QRgb*>(source.scanLine(y));
            for (int x = 0; x < width; ++x) {
                row[x] = random.generate();
            }
        }
        source.setColorSpace(QColorSpace::SRgb);
        const QByteArray unchanged = storage;
        for (int turns : {1, 3}) {
            for (bool horizontal : {false, true}) {
                Licasa::ImageEditParameters edit;
                edit.quarterTurns = turns;
                edit.flipHorizontal = horizontal;
                edit.flipVertical = !horizontal;
                QTransform transform;
                transform.rotate(turns * 90.0);
                QImage expected = source.transformed(transform).mirrored(horizontal, !horizontal);
                if (!expected.colorSpace().isValid()) {
                    expected.setColorSpace(QColorSpace::SRgb);
                }
                QImage actual = source;
                Licasa::ImageEditExecution execution;
                execution.requested = Licasa::ImageEditBackend::Cpu;
                QVERIFY(Licasa::applyImageEdits(actual, edit, nullptr, &execution));
                QCOMPARE(actual, expected);
                compareMetadata(actual, expected);
                QCOMPARE(storage, unchanged);
            }
        }
    }

    void automaticSmallImageDeclinesGpuAndMatchesCpuOracle()
    {
        const QByteArray prior = qgetenv("LICASA_EDIT_BACKEND");
        const auto restore = qScopeGuard([&] {
            prior.isNull() ? qunsetenv("LICASA_EDIT_BACKEND")
                           : qputenv("LICASA_EDIT_BACKEND", prior);
        });
        qunsetenv("LICASA_EDIT_BACKEND");
        const QImage source = raster(QSize(37, 29));
        Licasa::ImageEditParameters edit;
        edit.exposure = 0.4;
        edit.shadows = 0.3;
        edit.vibrance = 0.7;
        edit.sharpen = 0.7;
        const QImage expected = referenceSharpen(referenceColorImage(source, edit), edit.sharpen);
        QImage actual = source;
        Licasa::ImageEditExecution execution;
        QVERIFY(Licasa::applyImageEdits(actual, edit, nullptr, &execution));
        QCOMPARE(execution.used, Licasa::ImageEditBackend::Cpu);
        QVERIFY(execution.detail.contains(QStringLiteral("small image")));
        QCOMPARE(actual, expected);
        compareMetadata(actual, expected);
    }

    void cancelledCombinedStagesPreserveSharedInput()
    {
        const QImage source = raster(QSize(2049, 2049));
        const QImage unchanged = source.copy();
        Licasa::ImageEditParameters edit;
        edit.exposure = 0.4;
        edit.shadows = 0.3;
        edit.vibrance = 0.7;
        edit.sharpen = 0.7;
        Licasa::ImageEditExecution execution;
        execution.requested = Licasa::ImageEditBackend::Cpu;
        std::atomic_bool cancelled{true};
        QImage actual = source;
        QVERIFY(!Licasa::applyImageEdits(actual, edit, &cancelled, &execution));
        QCOMPARE(actual.constBits(), source.constBits());
        cancelled = false;
        std::thread cancel([&] {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
            cancelled.store(true, std::memory_order_relaxed);
        });
        const bool completed = Licasa::applyImageEdits(actual, edit, &cancelled, &execution);
        cancel.join();
        QVERIFY(!completed);
        QCOMPARE(source, unchanged);
        compareMetadata(actual, source);
    }
};

QTEST_GUILESS_MAIN(RuntimeEditRound3Test)
#include "tst_runtime_edit_round3.moc"

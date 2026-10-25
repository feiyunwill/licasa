#include "imaging/image_edit_pipeline.h"

#include <QColorSpace>
#include <QList>
#include <QRandomGenerator>
#include <QScopeGuard>
#include <QTest>
#include <QTransform>

#include <algorithm>
#include <atomic>
#include <cmath>

namespace {

QImage randomRaster(const QSize& size)
{
    QRandomGenerator random(0x7256u);
    QImage image(size, QImage::Format_ARGB32);
    for (int y = 0; y < size.height(); ++y) {
        auto* row = reinterpret_cast<QRgb*>(image.scanLine(y));
        for (int x = 0; x < size.width(); ++x) {
            row[x] = random.generate();
        }
    }
    image.setDevicePixelRatio(1.75);
    image.setDotsPerMeterX(1234);
    image.setDotsPerMeterY(5678);
    image.setText(QStringLiteral("caption"), QStringLiteral("round two"));
    return image;
}

QRect referenceCrop(const QSize& size, const Licasa::ImageEditParameters& edit)
{
    const int left = int(std::floor(edit.cropX * size.width()));
    const int top = int(std::floor(edit.cropY * size.height()));
    const int right = int(std::ceil((edit.cropX + edit.cropWidth) * size.width()));
    const int bottom = int(std::ceil((edit.cropY + edit.cropHeight) * size.height()));
    return QRect(left, top, right - left, bottom - top);
}

QRgb referenceChannelControls(QRgb pixel, const Licasa::ImageEditParameters& edit)
{
    if (qAlpha(pixel) == 0) {
        return pixel;
    }
    const double exposure = std::pow(2.0, edit.exposure);
    const double contrast = std::max(0.0, 1.0 + edit.contrast);
    double red = (qRed(pixel) * exposure - 127.5) * contrast + 127.5 + 0.0;
    double green = (qGreen(pixel) * exposure - 127.5) * contrast + 127.5 + 0.0;
    double blue = (qBlue(pixel) * exposure - 127.5) * contrast + 127.5 + 0.0;
    const double warmth = edit.warmth * 42.0;
    const double tint = edit.tint * 36.0;
    red += warmth + tint * 0.45;
    green -= tint;
    blue += -warmth + tint * 0.45;
    const double gray = 0.299 * red + 0.587 * green + 0.114 * blue;
    const auto channel = [gray](double value) {
        return int(std::clamp(std::round(gray + (value - gray) * 1.0), 0.0, 255.0));
    };
    return qRgba(channel(red), channel(green), channel(blue), qAlpha(pixel));
}

} // namespace

class RuntimeEditRound2Test final : public QObject {
    Q_OBJECT

  private slots:
    void cropWithGeometryMatchesOriginalQtSequence()
    {
        for (const QSize size :
             {QSize(64, 48), QSize(37, 29), QSize(6, 8), QSize(3, 7), QSize(1, 7)}) {
            const QImage raster = randomRaster(size);
            for (auto format :
                 {QImage::Format_RGB32, QImage::Format_ARGB32, QImage::Format_ARGB32_Premultiplied,
                  QImage::Format_RGBA8888, QImage::Format_RGBA8888_Premultiplied,
                  QImage::Format_RGBX8888, QImage::Format_RGB30, QImage::Format_BGR30,
                  QImage::Format_A2RGB30_Premultiplied, QImage::Format_A2BGR30_Premultiplied,
                  QImage::Format_RGB888, QImage::Format_RGBA64}) {
                for (const QColorSpace& profile : {QColorSpace(), QColorSpace(QColorSpace::SRgb),
                                                   QColorSpace(QColorSpace::DisplayP3)}) {
                    for (const QPoint offset : {QPoint(), QPoint(7, 9)}) {
                        QImage source = raster.convertToFormat(format);
                        source.setColorSpace(profile);
                        source.setOffset(offset);
                        const QImage unchanged = source.copy();
                        for (int turns = 0; turns < 4; ++turns) {
                            for (bool horizontal : {false, true}) {
                                for (bool vertical : {false, true}) {
                                    Licasa::ImageEditParameters edit;
                                    edit.quarterTurns = turns;
                                    edit.flipHorizontal = horizontal;
                                    edit.flipVertical = vertical;
                                    edit.cropX = 0.125;
                                    edit.cropY = 0.25;
                                    edit.cropWidth = 0.5;
                                    edit.cropHeight = 0.375;
                                    QTransform rotation;
                                    rotation.rotate(turns * 90.0);
                                    QImage expected =
                                        source.transformed(rotation).mirrored(horizontal, vertical);
                                    expected = expected.copy(referenceCrop(expected.size(), edit));
                                    if (!expected.colorSpace().isValid()) {
                                        expected.setColorSpace(QColorSpace::SRgb);
                                    }
                                    for (bool shared : {false, true}) {
                                        QImage actual = shared ? source : source.copy();
                                        actual.setOffset(offset);
                                        Licasa::ImageEditExecution execution;
                                        execution.requested = Licasa::ImageEditBackend::Cpu;
                                        QVERIFY(Licasa::applyImageEdits(actual, edit, nullptr,
                                                                        &execution));
                                        QCOMPARE(actual, expected);
                                        QCOMPARE(actual.format(), expected.format());
                                        QCOMPARE(actual.offset(), expected.offset());
                                        QCOMPARE(actual.colorSpace(), expected.colorSpace());
                                        QCOMPARE(actual.devicePixelRatio(),
                                                 expected.devicePixelRatio());
                                        QCOMPARE(actual.dotsPerMeterX(), expected.dotsPerMeterX());
                                        QCOMPARE(actual.dotsPerMeterY(), expected.dotsPerMeterY());
                                        QCOMPARE(actual.text("caption"), expected.text("caption"));
                                        QCOMPARE(source, unchanged);
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    void vignetteSymmetryMatchesScalarAtEvenOddAndNarrowDimensions()
    {
        Licasa::ImageEditParameters edit;
        edit.vignette = 0.63;
        Licasa::ImageEditExecution execution;
        execution.requested = Licasa::ImageEditBackend::Cpu;
        for (int width : {1, 2, 3, 4, 7, 16}) {
            for (int height : {1, 2, 3, 4, 7, 16}) {
                const QImage source = randomRaster(QSize(width, height));
                QImage expected = source.copy();
                expected.setColorSpace(QColorSpace::SRgb);
                const double centerX = std::max(1.0, (width - 1) * 0.5);
                const double centerY = std::max(1.0, (height - 1) * 0.5);
                for (int y = 0; y < height; ++y) {
                    for (int x = 0; x < width; ++x) {
                        const QRgb pixel = source.pixel(x, y);
                        if (qAlpha(pixel) == 0) {
                            continue;
                        }
                        const double nx = (x - centerX) / centerX;
                        const double ny = (y - centerY) / centerY;
                        const double radius = std::clamp(std::sqrt(nx * nx + ny * ny), 0.0, 1.5);
                        const double edge = std::clamp((radius - 0.28) / 0.92, 0.0, 1.0);
                        const double factor =
                            1.0 - edit.vignette * 0.72 * (edge * edge * (3.0 - 2.0 * edge));
                        const double gray =
                            0.299 * qRed(pixel) + 0.587 * qGreen(pixel) + 0.114 * qBlue(pixel);
                        const auto channel = [gray, factor](int value) {
                            const double adjusted = gray + (value - gray);
                            return int(std::clamp(std::round(adjusted * factor), 0.0, 255.0));
                        };
                        expected.setPixel(x, y,
                                          qRgba(channel(qRed(pixel)), channel(qGreen(pixel)),
                                                channel(qBlue(pixel)), qAlpha(pixel)));
                    }
                }
                QImage actual = source;
                QVERIFY(Licasa::applyImageEdits(actual, edit, nullptr, &execution));
                QCOMPARE(actual, expected);
            }
        }
    }

    void finalChannelSpecializationMatchesEveryRgbAndHalfBoundaries()
    {
        // The 4096-square fixture processes all 24-bit RGB values with nonzero
        // alpha. A separate row checks hidden RGB in fully transparent pixels.
        QImage source(4096, 4096, QImage::Format_ARGB32);
        source.setColorSpace(QColorSpace::DisplayP3);
        for (int y = 0; y < source.height(); ++y) {
            auto* row = reinterpret_cast<QRgb*>(source.scanLine(y));
            for (int x = 0; x < source.width(); ++x) {
                const quint32 rgb = quint32(y * source.width() + x);
                row[x] = (rgb & 0xffffffu) | (((rgb * 13u + 17u) % 255u + 1u) << 24);
            }
        }
        QList<Licasa::ImageEditParameters> cases;
        Licasa::ImageEditParameters edit;
        edit.exposure = 0.4;
        cases.append(edit);
        edit.exposure = -0.4;
        edit.contrast = 0.3;
        edit.warmth = 0.13;
        edit.tint = -0.17;
        cases.append(edit);
        edit = {};
        edit.contrast = 0.5;
        cases.append(edit);
        const double halfTemperature = 0.5 / 42.0;
        for (double temperature : {std::nextafter(halfTemperature, 0.0), halfTemperature,
                                   std::nextafter(halfTemperature, 1.0)}) {
            edit = {};
            edit.warmth = temperature;
            cases.append(edit);
        }
        Licasa::ImageEditExecution execution;
        execution.requested = Licasa::ImageEditBackend::Cpu;
        const uchar* const originalStorage = source.constBits();
        for (const auto& parameters : cases) {
            QImage actual = source;
            QVERIFY(Licasa::applyImageEdits(actual, parameters, nullptr, &execution));
            for (int y = 0; y < source.height(); ++y) {
                const auto* original = reinterpret_cast<const QRgb*>(source.constScanLine(y));
                const auto* adjusted = reinterpret_cast<const QRgb*>(actual.constScanLine(y));
                for (int x = 0; x < source.width(); ++x) {
                    if (adjusted[x] != referenceChannelControls(original[x], parameters)) {
                        QFAIL(qPrintable(QStringLiteral("RGB=%1 exposure=%2 contrast=%3 warmth=%4")
                                             .arg(original[x] & 0xffffffu, 6, 16, QLatin1Char('0'))
                                             .arg(parameters.exposure, 0, 'g', 17)
                                             .arg(parameters.contrast, 0, 'g', 17)
                                             .arg(parameters.warmth, 0, 'g', 17)));
                    }
                }
            }
            QCOMPARE(source.constBits(), originalStorage);
            QCOMPARE(actual.colorSpace(), source.colorSpace());
            QImage transparent = randomRaster(QSize(256, 1));
            auto* hidden = reinterpret_cast<QRgb*>(transparent.scanLine(0));
            for (int x = 0; x < transparent.width(); ++x) {
                hidden[x] &= 0xffffffu;
            }
            const QImage expected = transparent.copy();
            QVERIFY(Licasa::applyImageEdits(transparent, parameters, nullptr, &execution));
            for (int x = 0; x < transparent.width(); ++x) {
                QCOMPARE(transparent.pixel(x, 0), expected.pixel(x, 0));
            }
        }
    }

    void parallelSharpenPreservesBandBoundariesAndSharedSource()
    {
        const QByteArray priorWorkers = qgetenv("LICASA_CPU_EDIT_WORKERS");
        const auto restore = qScopeGuard([&] {
            if (priorWorkers.isNull()) {
                qunsetenv("LICASA_CPU_EDIT_WORKERS");
            } else {
                qputenv("LICASA_CPU_EDIT_WORKERS", priorWorkers);
            }
        });
        QImage source = randomRaster(QSize(4096, 8193));
        source.setColorSpace(QColorSpace::DisplayP3);
        const QImage unchanged = source.copy();
        Licasa::ImageEditParameters edit;
        edit.sharpen = 0.7;
        Licasa::ImageEditExecution execution;
        execution.requested = Licasa::ImageEditBackend::Cpu;
        qputenv("LICASA_CPU_EDIT_WORKERS", "1");
        QImage serial = source;
        QVERIFY(Licasa::applyImageEdits(serial, edit, nullptr, &execution));
        for (const QByteArray& workers : {QByteArray("2"), QByteArray("8"), QByteArray("16")}) {
            qputenv("LICASA_CPU_EDIT_WORKERS", workers);
            QImage parallel = source;
            QVERIFY(Licasa::applyImageEdits(parallel, edit, nullptr, &execution));
            QCOMPARE(parallel, serial);
            QCOMPARE(parallel.colorSpace(), source.colorSpace());
            QCOMPARE(parallel.devicePixelRatio(), source.devicePixelRatio());
            QCOMPARE(parallel.text("caption"), source.text("caption"));
            QCOMPARE(source, unchanged);
        }
    }

    void cancellationLeavesSharedSourceUntouched()
    {
        QImage source = randomRaster(QSize(4096, 2048));
        const QImage unchanged = source.copy();
        Licasa::ImageEditParameters edit;
        edit.sharpen = 0.7;
        edit.vignette = 0.5;
        edit.shadows = 0.4;
        QImage actual = source;
        std::atomic_bool cancelled{true};
        Licasa::ImageEditExecution execution;
        execution.requested = Licasa::ImageEditBackend::Cpu;
        QVERIFY(!Licasa::applyImageEdits(actual, edit, &cancelled, &execution));
        QCOMPARE(actual.constBits(), source.constBits());
        QCOMPARE(source, unchanged);
    }
};

QTEST_GUILESS_MAIN(RuntimeEditRound2Test)
#include "tst_runtime_edit_round2.moc"

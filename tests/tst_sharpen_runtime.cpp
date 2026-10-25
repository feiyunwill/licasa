#include "imaging/image_edit_pipeline.h"

#include <QColorSpace>
#include <QRandomGenerator>
#include <QTest>

#include <algorithm>
#include <cmath>

namespace {

int originalChannel(int center, int left, int right, int top, int bottom, double amount)
{
    const double detail = center * 4.0 - left - right - top - bottom;
    return int(std::clamp(std::round(center + detail * (amount * 0.48)), 0.0, 255.0));
}

QRgb originalPixel(const QImage& image, int x, int y, double amount)
{
    const QRgb center = image.pixel(x, y);
    const QRgb left = image.pixel(x - 1, y);
    const QRgb right = image.pixel(x + 1, y);
    const QRgb top = image.pixel(x, y - 1);
    const QRgb bottom = image.pixel(x, y + 1);
    return qRgba(
        originalChannel(qRed(center), qRed(left), qRed(right), qRed(top), qRed(bottom), amount),
        originalChannel(qGreen(center), qGreen(left), qGreen(right), qGreen(top), qGreen(bottom),
                        amount),
        originalChannel(qBlue(center), qBlue(left), qBlue(right), qBlue(top), qBlue(bottom),
                        amount),
        qAlpha(center));
}

int neighborValue(int sum, int neighbor) { return std::clamp(sum - neighbor * 255, 0, 255); }

} // namespace

class SharpenRuntimeTest final : public QObject {
    Q_OBJECT

  private slots:
    void everyCenterAndNeighborSumMatchesOriginalRounding()
    {
        // Disjoint 3x3 stencils cover all 256 center values and 1,021 sums.
        // The three channels permute those cases; center alpha also includes 0.
        QImage source(1021 * 3, 256 * 3, QImage::Format_ARGB32);
        source.fill(qRgba(17, 63, 129, 111));
        for (int center = 0; center < 256; ++center) {
            const int y = center * 3 + 1;
            for (int sum = 0; sum <= 1020; ++sum) {
                const int x = sum * 3 + 1;
                source.setPixel(
                    x, y,
                    qRgba(center, 255 - center, (center * 53) % 256, (center * 13 + sum) % 256));
                const int offsets[4][2] = {{-1, 0}, {1, 0}, {0, -1}, {0, 1}};
                for (int neighbor = 0; neighbor < 4; ++neighbor) {
                    source.setPixel(x + offsets[neighbor][0], y + offsets[neighbor][1],
                                    qRgba(neighborValue(sum, neighbor),
                                          neighborValue(1020 - sum, neighbor),
                                          neighborValue((sum * 59) % 1021, neighbor), 255));
                }
            }
        }
        source.setColorSpace(QColorSpace::DisplayP3);
        source.setDevicePixelRatio(1.5);
        source.setText(QStringLiteral("caption"), QStringLiteral("preserved"));
        const QImage unchanged = source.copy();
        const double tie = 1.0 / 96.0;
        const double amounts[] = {
            0.01, std::nextafter(tie, 0.0),   tie,   std::nextafter(tie, 1.0),   0.07, 0.25, 0.5,
            0.7,  std::nextafter(0.875, 0.0), 0.875, std::nextafter(0.875, 1.0), 1.0};
        for (double amount : amounts) {
            QImage actual = source;
            Licasa::ImageEditParameters edit;
            edit.sharpen = amount;
            Licasa::ImageEditExecution execution;
            execution.requested = Licasa::ImageEditBackend::Cpu;
            QVERIFY(Licasa::applyImageEdits(actual, edit, nullptr, &execution));
            QCOMPARE(execution.used, Licasa::ImageEditBackend::Cpu);
            for (int center = 0; center < 256; ++center) {
                const int y = center * 3 + 1;
                const auto* row = reinterpret_cast<const QRgb*>(actual.constScanLine(y));
                for (int sum = 0; sum <= 1020; ++sum) {
                    const int x = sum * 3 + 1;
                    if (row[x] != originalPixel(source, x, y, amount)) {
                        QFAIL(qPrintable(QStringLiteral("amount=%1 center=%2 sum=%3")
                                             .arg(amount, 0, 'g', 17)
                                             .arg(center)
                                             .arg(sum)));
                    }
                }
            }
            QCOMPARE(actual.colorSpace(), source.colorSpace());
            QCOMPARE(actual.devicePixelRatio(), source.devicePixelRatio());
            QCOMPARE(actual.text(QStringLiteral("caption")),
                     source.text(QStringLiteral("caption")));
            QVERIFY(actual.constBits() != source.constBits());
            QCOMPARE(source, unchanged);
        }
    }

    void thresholdAndPaddedFormatsPreserveWholeRaster()
    {
        QRandomGenerator random(0x5a49u);
        for (const QSize size : {QSize(513, 514), QSize(514, 514), QSize(515, 514)}) {
            QImage raster(size, QImage::Format_ARGB32);
            raster.setColorSpace(QColorSpace::SRgb);
            for (int y = 0; y < size.height(); ++y) {
                auto* row = reinterpret_cast<QRgb*>(raster.scanLine(y));
                for (int x = 0; x < size.width(); ++x) {
                    row[x] = random.generate();
                }
            }
            for (auto format : {QImage::Format_ARGB32, QImage::Format_RGB888,
                                QImage::Format_ARGB32_Premultiplied, QImage::Format_RGBA64}) {
                const QImage source = raster.convertToFormat(format);
                const QImage pixels = source.convertToFormat(QImage::Format_ARGB32);
                QImage expected = pixels.copy();
                for (int y = 1; y < size.height() - 1; ++y) {
                    auto* row = reinterpret_cast<QRgb*>(expected.scanLine(y));
                    for (int x = 1; x < size.width() - 1; ++x) {
                        row[x] = originalPixel(pixels, x, y, 0.7);
                    }
                }
                QImage actual = source;
                Licasa::ImageEditParameters edit;
                edit.sharpen = 0.7;
                Licasa::ImageEditExecution execution;
                execution.requested = Licasa::ImageEditBackend::Cpu;
                QVERIFY(Licasa::applyImageEdits(actual, edit, nullptr, &execution));
                QCOMPARE(actual, expected);
                QCOMPARE(source, raster.convertToFormat(format));
            }
        }
    }
};

QTEST_GUILESS_MAIN(SharpenRuntimeTest)
#include "tst_sharpen_runtime.moc"

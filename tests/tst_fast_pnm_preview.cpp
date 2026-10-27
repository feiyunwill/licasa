#include "imaging/fast_pnm_preview.h"

#include <QBuffer>
#include <QFile>
#include <QImageReader>
#include <QTemporaryDir>
#include <QTest>

#include <algorithm>
#include <atomic>
#include <cmath>

namespace {
constexpr quint64 kSourcePixels = 200'000'000;
constexpr qsizetype kOutputBytes = 8 * 1024 * 1024;

QColor pattern(int x, int y, int kind)
{
    if (kind == 1) {
        const int value = ((x + y) & 1) ? 255 : 0;
        return QColor(value, value, value);
    }
    if (kind == 2) {
        return QColor((x & 1) ? 255 : 0, (y & 1) ? 255 : 0, ((x + y) & 1) ? 255 : 0);
    }
    if (kind == 3) {
        return QColor(x == 1 && y == 2 ? 255 : 0, x == 2 && y == 1 ? 255 : 0,
                      x == 0 && y == 0 ? 255 : 0);
    }
    return QColor((x * 17 + y * 31) & 255, (x * 37 + y * 13) & 255, (x * 7 + y * 43) & 255);
}

QByteArray raster(const QSize& source, int kind)
{
    QByteArray bytes(source.width() * source.height() * 3, Qt::Uninitialized);
    auto* destination = reinterpret_cast<uchar*>(bytes.data());
    for (int y = 0; y < source.height(); ++y) {
        for (int x = 0; x < source.width(); ++x) {
            const QColor color = pattern(x, y, kind);
            *destination++ = uchar(color.red());
            *destination++ = uchar(color.green());
            *destination++ = uchar(color.blue());
        }
    }
    return bytes;
}

QByteArray ppm(const QSize& source, int kind)
{
    return "P6\n" + QByteArray::number(source.width()) + ' ' + QByteArray::number(source.height()) +
           "\n255\n" + raster(source, kind);
}

QImage decode(QByteArray& bytes, const QSize& target, quint64 pixels = kSourcePixels,
              qsizetype outputBytes = kOutputBytes, const std::atomic_bool* cancelled = nullptr)
{
    QBuffer device(&bytes);
    if (!device.open(QIODevice::ReadOnly)) {
        return {};
    }
    return Licasa::readFastPnmPreview(device, target, pixels, outputBytes, cancelled);
}

// Deliberately use floating-point geometric integration, without the helper's
// integer spans or separable accumulators, to check every output channel.
QColor areaReference(const QSize& source, const QSize& target, int x, int y, int kind)
{
    const long double left = static_cast<long double>(x) * source.width() / target.width();
    const long double right = static_cast<long double>(x + 1) * source.width() / target.width();
    const long double top = static_cast<long double>(y) * source.height() / target.height();
    const long double bottom = static_cast<long double>(y + 1) * source.height() / target.height();
    long double values[3] = {};
    for (int sy = int(std::floor(top)); sy < int(std::ceil(bottom)); ++sy) {
        const long double height = std::min(bottom, static_cast<long double>(sy + 1)) -
                                   std::max(top, static_cast<long double>(sy));
        for (int sx = int(std::floor(left)); sx < int(std::ceil(right)); ++sx) {
            const long double width = std::min(right, static_cast<long double>(sx + 1)) -
                                      std::max(left, static_cast<long double>(sx));
            const QColor color = pattern(sx, sy, kind);
            values[0] += width * height * color.red();
            values[1] += width * height * color.green();
            values[2] += width * height * color.blue();
        }
    }
    const long double area = (right - left) * (bottom - top);
    return QColor(int(std::floor(values[0] / area + 0.5000000001L)),
                  int(std::floor(values[1] / area + 0.5000000001L)),
                  int(std::floor(values[2] / area + 0.5000000001L)));
}

class ControlledBuffer final : public QBuffer {
  public:
    explicit ControlledBuffer(QByteArray* bytes) : QBuffer(bytes) {}
    qint64 maximumRead = 4093;
    qint64 consumed = 0;
    qint64 cancelAfter = -1;
    qint64 failAfter = -1;
    std::atomic_bool cancelled{false};

  protected:
    qint64 readData(char* destination, qint64 length) override
    {
        if (failAfter >= 0 && consumed >= failAfter) {
            return -1;
        }
        const qint64 count = QBuffer::readData(destination, std::min(length, maximumRead));
        if (count > 0) {
            consumed += count;
            if (cancelAfter >= 0 && consumed >= cancelAfter) {
                cancelled.store(true, std::memory_order_relaxed);
            }
        }
        return count;
    }
};
} // namespace

class FastPnmPreviewTest final : public QObject {
    Q_OBJECT

  private slots:
    void areaFilterMatchesIndependentOracle_data()
    {
        QTest::addColumn<QSize>("source");
        QTest::addColumn<QSize>("target");
        QTest::addColumn<int>("kind");
        QTest::newRow("odd-gradient") << QSize(17, 11) << QSize(7, 3) << 0;
        QTest::newRow("checkerboard") << QSize(8, 8) << QSize(2, 2) << 1;
        QTest::newRow("noninteger-stripes") << QSize(9, 7) << QSize(4, 3) << 2;
        QTest::newRow("independent-channel-impulses") << QSize(5, 3) << QSize(2, 2) << 3;
        QTest::newRow("unchanged-width") << QSize(7, 11) << QSize(7, 3) << 0;
        QTest::newRow("unchanged-height") << QSize(13, 5) << QSize(4, 5) << 2;
        QTest::newRow("whole-image-average") << QSize(19, 13) << QSize(1, 1) << 0;
        QTest::newRow("identity") << QSize(7, 5) << QSize(7, 5) << 0;
    }

    void areaFilterMatchesIndependentOracle()
    {
        QFETCH(QSize, source);
        QFETCH(QSize, target);
        QFETCH(int, kind);
        QByteArray bytes = ppm(source, kind);
        const QImage image = decode(bytes, target);
        QCOMPARE(image.size(), target);
        QCOMPARE(image.format(), QImage::Format_RGB32);
        QVERIFY(image.sizeInBytes() <= kOutputBytes);
        for (int y = 0; y < target.height(); ++y) {
            for (int x = 0; x < target.width(); ++x) {
                QCOMPARE(image.pixelColor(x, y), areaReference(source, target, x, y, kind));
            }
        }
    }

    void checkerboardIsAntialiased()
    {
        QByteArray bytes = ppm(QSize(64, 64), 1);
        const QImage image = decode(bytes, QSize(4, 4));
        QVERIFY(!image.isNull());
        for (int y = 0; y < image.height(); ++y) {
            for (int x = 0; x < image.width(); ++x) {
                QCOMPARE(image.pixelColor(x, y), QColor(128, 128, 128));
            }
        }
    }

    void headerCommentsAndBinaryDelimiter()
    {
        const QByteArray pixels = QByteArray::fromHex("0a20230d09ff");
        for (const char delimiter : {'\n', ' ', '\t', '\v', '\f'}) {
            QByteArray bytes = "P6\n# dimensions\n2\t# width\n1\n# range\n255";
            bytes += delimiter;
            bytes += pixels;
            const QImage image = decode(bytes, QSize(2, 1));
            QCOMPARE(image.size(), QSize(2, 1));
            QCOMPARE(image.pixelColor(0, 0), QColor(10, 32, 35));
            QCOMPARE(image.pixelColor(1, 0), QColor(13, 9, 255));
        }
    }

    void malformedOrUnsupportedHeaders_data()
    {
        QTest::addColumn<QByteArray>("bytes");
        QTest::newRow("plain-p3") << QByteArray("P3\n1 1\n255\n10 20 30\n");
        QTest::newRow("other-pnm") << QByteArray("P5\n1 1\n255\n\x20");
        QTest::newRow("zero-width") << QByteArray("P6\n0 1\n255\nabc");
        QTest::newRow("negative-width") << QByteArray("P6\n-1 1\n255\nabc");
        QTest::newRow("positive-sign") << QByteArray("P6\n+1 1\n255\nabc");
        QTest::newRow("overflowing-number") << QByteArray("P6\n18446744073709551616 1\n255\nabc");
        QTest::newRow("excessive-dimension") << QByteArray("P6\n2147483647 2147483647\n255\nabc");
        QTest::newRow("missing-field-separator") << QByteArray("P6\n1x1\n255\nabc");
        QTest::newRow("unsupported-range") << QByteArray("P6\n1 1\n15\nabc");
        QTest::newRow("sixteen-bit") << QByteArray("P6\n1 1\n65535\nabcdef");
        QTest::newRow("crlf-raster-boundary") << QByteArray("P6\r\n1 1\r\n255\r\nabc");
        QTest::newRow("bare-cr-raster-boundary") << QByteArray("P6\n1 1\n255\rabc");
        QTest::newRow("comment-after-maxval") << QByteArray("P6\n1 1\n255#comment\nabc");
        QTest::newRow("truncated-header") << QByteArray("P6\n1 1\n255");
        QTest::newRow("truncated-raster") << QByteArray("P6\n1 1\n255\nab");
        QTest::newRow("extra-raster-byte") << QByteArray("P6\n1 1\n255\nabcd");
        QTest::newRow("concatenated-images") << QByteArray("P6\n1 1\n255\nabcP6\n1 1\n255\ndef");
        QTest::newRow("unbounded-comment")
            << QByteArray("P6\n#") + QByteArray(65536, 'x') + "\n1 1\n255\nabc";
    }

    void malformedOrUnsupportedHeaders()
    {
        QFETCH(QByteArray, bytes);
        QVERIFY(decode(bytes, QSize(1, 1)).isNull());
    }

    void sourceAndOutputBudgets()
    {
        QByteArray bytes = ppm(QSize(7, 5), 0);
        QVERIFY(decode(bytes, QSize(3, 2), 34).isNull());
        QVERIFY(decode(bytes, QSize(3, 2), 35, 23).isNull());
        QVERIFY(!decode(bytes, QSize(3, 2), 35, 24).isNull());
        QVERIFY(decode(bytes, QSize(8, 2)).isNull());
        QVERIFY(decode(bytes, QSize(0, 2)).isNull());
        QVERIFY(decode(bytes, QSize(3, 2), 0).isNull());
        QVERIFY(decode(bytes, QSize(3, 2), 35, 0).isNull());
        bytes = ppm(QSize(1500, 1500), 0);
        QVERIFY(decode(bytes, QSize(1500, 1500)).isNull());
        bytes = ppm(QSize(8193, 1), 0);
        QVERIFY(decode(bytes, QSize(8193, 1)).isNull());
    }

    void shortReadsCompleteAndCancellationStops()
    {
        QByteArray bytes = ppm(QSize(400, 400), 0);
        ControlledBuffer shortReads(&bytes);
        QVERIFY(shortReads.open(QIODevice::ReadOnly | QIODevice::Unbuffered));
        const QImage image = Licasa::readFastPnmPreview(shortReads, QSize(41, 37), kSourcePixels,
                                                        kOutputBytes, &shortReads.cancelled);
        QCOMPARE(image.size(), QSize(41, 37));
        QCOMPARE(image.pixelColor(17, 23),
                 areaReference(QSize(400, 400), QSize(41, 37), 17, 23, 0));
        QVERIFY(shortReads.consumed > bytes.size()); // Header lookahead is then rewound.

        ControlledBuffer cancelled(&bytes);
        cancelled.cancelAfter = 70'000;
        QVERIFY(cancelled.open(QIODevice::ReadOnly | QIODevice::Unbuffered));
        QVERIFY(Licasa::readFastPnmPreview(cancelled, QSize(41, 37), kSourcePixels, kOutputBytes,
                                           &cancelled.cancelled)
                    .isNull());
        QVERIFY(cancelled.cancelled.load());
        QVERIFY(cancelled.consumed < bytes.size());

        ControlledBuffer failed(&bytes);
        failed.failAfter = 70'000;
        QVERIFY(failed.open(QIODevice::ReadOnly | QIODevice::Unbuffered));
        QVERIFY(
            Licasa::readFastPnmPreview(failed, QSize(41, 37), kSourcePixels, kOutputBytes, nullptr)
                .isNull());

        const std::atomic_bool alreadyCancelled{true};
        QVERIFY(
            decode(bytes, QSize(41, 37), kSourcePixels, kOutputBytes, &alreadyCancelled).isNull());
    }

    void unsupportedValidPpmRetainsQtFallback()
    {
        QByteArray bytes("P3\n2 1\n255\n10 20 30 200 210 220\n");
        QVERIFY(decode(bytes, QSize(2, 1)).isNull());
        QBuffer device(&bytes);
        QVERIFY(device.open(QIODevice::ReadOnly));
        QImageReader reader(&device);
        const QImage image = reader.read();
        QCOMPARE(image.size(), QSize(2, 1));
        QCOMPARE(image.pixelColor(0, 0), QColor(10, 20, 30));
        QCOMPARE(image.pixelColor(1, 0), QColor(200, 210, 220));
    }

    void fileOverload()
    {
        QTemporaryDir directory;
        QVERIFY(directory.isValid());
        const QString path = directory.filePath(QStringLiteral("preview.ppm"));
        QFile file(path);
        QVERIFY(file.open(QIODevice::WriteOnly));
        const QByteArray bytes = ppm(QSize(17, 11), 0);
        QCOMPARE(file.write(bytes), bytes.size());
        file.close();
        const QImage image =
            Licasa::readFastPnmPreview(path, QSize(7, 3), kSourcePixels, kOutputBytes, nullptr);
        QCOMPARE(image.size(), QSize(7, 3));
        QCOMPARE(image.pixelColor(5, 2), areaReference(QSize(17, 11), QSize(7, 3), 5, 2, 0));
        QVERIFY(Licasa::readFastPnmPreview(directory.path(), QSize(1, 1), kSourcePixels,
                                           kOutputBytes, nullptr)
                    .isNull());
    }
};

QTEST_GUILESS_MAIN(FastPnmPreviewTest)
#include "tst_fast_pnm_preview.moc"

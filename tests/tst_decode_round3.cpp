#include "imaging/fast_png_preview.h"
#include "imaging/image_decode_contract.h"

#include <QBuffer>
#include <QColorSpace>
#include <QFile>
#include <QImageIOHandler>
#include <QImageReader>
#include <QImageWriter>
#include <QPainter>
#include <QTemporaryDir>
#include <QTest>
#include <QtEndian>

#include <png.h>

#include <atomic>
#include <cstdio>
#include <memory>
#include <vector>

extern "C" QImageIOHandler* licasaCreateImageHandler();

namespace {
QRgb fixturePixel(int x, int y, bool alpha = true)
{
    return qRgba((x * 3 + y * 7) & 255, (x * 11 + y * 5) & 255, (x * 17 + y * 19) & 255,
                 alpha ? (x * 23 + y * 29) & 255 : 255);
}

bool writePlainPng(const QString& path, const QSize& size)
{
    FILE* file = std::fopen(QFile::encodeName(path).constData(), "wb");
    if (!file) {
        return false;
    }
    png_structp png = png_create_write_struct(PNG_LIBPNG_VER_STRING, nullptr, nullptr, nullptr);
    png_infop info = png ? png_create_info_struct(png) : nullptr;
    if (!info) {
        png_destroy_write_struct(&png, nullptr);
        std::fclose(file);
        return false;
    }
    std::vector<uchar> row(size_t(size.width()) * 4);
    if (setjmp(png_jmpbuf(png))) {
        png_destroy_write_struct(&png, &info);
        std::fclose(file);
        return false;
    }
    png_init_io(png, file);
    png_set_IHDR(png, info, size.width(), size.height(), 8, PNG_COLOR_TYPE_RGBA, PNG_INTERLACE_NONE,
                 PNG_COMPRESSION_TYPE_BASE, PNG_FILTER_TYPE_BASE);
    png_set_filter(png, 0, PNG_FILTER_NONE);
    png_set_compression_level(png, 1);
    png_write_info(png, info);
    for (int y = 0; y < size.height(); ++y) {
        for (int x = 0; x < size.width(); ++x) {
            const QRgb pixel = fixturePixel(x, y);
            row[size_t(x) * 4] = uchar(qRed(pixel));
            row[size_t(x) * 4 + 1] = uchar(qGreen(pixel));
            row[size_t(x) * 4 + 2] = uchar(qBlue(pixel));
            row[size_t(x) * 4 + 3] = uchar(qAlpha(pixel));
        }
        png_write_row(png, row.data());
    }
    png_write_end(png, info);
    png_destroy_write_struct(&png, &info);
    std::fclose(file);
    return true;
}

QByteArray be32(quint32 value)
{
    QByteArray bytes(4, Qt::Uninitialized);
    qToBigEndian(value, reinterpret_cast<uchar*>(bytes.data()));
    return bytes;
}

QByteArray pngChunk(const QByteArray& type, const QByteArray& payload)
{
    QByteArray result = be32(quint32(payload.size())) + type + payload;
    quint32 crc = 0xffffffffu;
    for (qsizetype i = 4; i < result.size(); ++i) {
        crc ^= quint8(result[i]);
        for (int bit = 0; bit < 8; ++bit) {
            crc = (crc & 1u) ? 0xedb88320u ^ (crc >> 1u) : crc >> 1u;
        }
    }
    return result + be32(crc ^ 0xffffffffu);
}

QByteArray apngFirstFrame(const QImage& image, quint8 dispose, QImage* decodedFrame)
{
    QByteArray png;
    QBuffer destination(&png);
    if (!destination.open(QIODevice::WriteOnly) ||
        !QImageWriter(&destination, "png").write(image)) {
        return {};
    }
    *decodedFrame = QImage::fromData(png, "png");
    QByteArray ihdr;
    QByteArray globals;
    QByteArray imageData;
    for (qsizetype offset = 8; offset + 12 <= png.size();) {
        const quint32 size = qFromBigEndian<quint32>(png.constData() + offset);
        const QByteArray type = png.mid(offset + 4, 4);
        const QByteArray complete = png.mid(offset, qsizetype(size) + 12);
        if (type == "IHDR") {
            ihdr = complete;
        } else if (type == "IDAT") {
            imageData += complete;
        } else if (type == "iCCP" || type == "sRGB" || type == "gAMA" || type == "cHRM") {
            globals += complete;
        }
        offset += qsizetype(size) + 12;
    }
    QByteArray control = be32(0) + be32(image.width()) + be32(image.height()) + be32(0) + be32(0);
    control += QByteArray::fromHex("0001000a");
    control.append(char(dispose));
    control.append(char(0));
    return png.first(8) + ihdr + globals + pngChunk("acTL", be32(1) + be32(0)) +
           pngChunk("fcTL", control) + imageData + pngChunk("IEND", {});
}
struct AnimationFrameFixture {
    QImage image;
    QPoint offset;
    quint8 dispose = 0;
    quint8 blend = 0;
};

QByteArray multiFrameApng(const QList<AnimationFrameFixture>& frames, QList<QImage>* decodedFrames)
{
    QByteArray result;
    quint32 sequence = 0;
    for (qsizetype index = 0; index < frames.size(); ++index) {
        const auto& frame = frames[index];
        QByteArray png;
        QBuffer destination(&png);
        if (!destination.open(QIODevice::WriteOnly) ||
            !QImageWriter(&destination, "png").write(frame.image)) {
            return {};
        }
        decodedFrames->append(QImage::fromData(png, "png"));
        QList<QByteArray> payloads;
        QByteArray globals;
        QByteArray ihdr;
        for (qsizetype offset = 8; offset + 12 <= png.size();) {
            const quint32 size = qFromBigEndian<quint32>(png.constData() + offset);
            const QByteArray type = png.mid(offset + 4, 4);
            if (type == "IHDR") {
                ihdr = png.mid(offset, qsizetype(size) + 12);
            } else if (type == "IDAT") {
                payloads.append(png.mid(offset + 8, size));
            } else if (type == "iCCP" || type == "sRGB" || type == "gAMA" || type == "cHRM") {
                globals += png.mid(offset, qsizetype(size) + 12);
            }
            offset += qsizetype(size) + 12;
        }
        if (index == 0) {
            result = png.first(8) + ihdr + globals +
                     pngChunk("acTL", be32(quint32(frames.size())) + be32(0));
        }
        QByteArray control = be32(sequence++) + be32(frame.image.width()) +
                             be32(frame.image.height()) + be32(frame.offset.x()) +
                             be32(frame.offset.y()) + QByteArray::fromHex("0001000a");
        control.append(char(frame.dispose));
        control.append(char(frame.blend));
        result += pngChunk("fcTL", control);
        for (const auto& payload : payloads) {
            result += index == 0 ? pngChunk("IDAT", payload)
                                 : pngChunk("fdAT", be32(sequence++) + payload);
        }
    }
    return result + pngChunk("IEND", {});
}

QImage animationImage(const QSize& size, QImage::Format format, int seed)
{
    QImage image(size, format);
    for (int y = 0; y < size.height(); ++y) {
        for (int x = 0; x < size.width(); ++x) {
            image.setPixelColor(x, y,
                                QColor::fromRgba64(quint16(x * 1997 + y * 61 + seed * 599),
                                                   quint16(x * 1697 + y * 131 + seed * 433),
                                                   quint16(x * 1193 + y * 271 + seed * 317),
                                                   quint16(x * 1877 + y * 101 + seed * 271)));
        }
    }
    image.setColorSpace(QColorSpace::DisplayP3);
    return image;
}
} // namespace

class DecodeRound3Test final : public QObject {
    Q_OBJECT

  private slots:
    void pngSamplingMatchesFloorMapping()
    {
        QTemporaryDir directory;
        QVERIFY(directory.isValid());
        const QSize sourceSize(5001, 5000);
        const QString path = directory.filePath("plain.png");
        QVERIFY(writePlainPng(path, sourceSize));
        QVERIFY(Licasa::fastPngBaseEligible(path, 26'000'000));
        const QSize targets[] = {{1, 1}, {1279, 953}, {5001, 3}, {7003, 3}};
        for (const QSize& target : targets) {
            const auto retained =
                Licasa::readFastPngPreview(path, target, 26'000'000, 8 * 1024 * 1024, nullptr);
            const QImage nearby = Licasa::readFastPngNearbyPreview(path, target, 26'000'000,
                                                                   8 * 1024 * 1024, nullptr);
            QVERIFY(!retained.preview.isNull());
            QCOMPARE(retained.fullBase.size(), sourceSize);
            QCOMPARE(retained.preview, nearby);
            QCOMPARE(nearby.format(), QImage::Format_ARGB32);
            QCOMPARE(nearby.colorSpace(), QColorSpace(QColorSpace::SRgb));
            for (int y = 0; y < target.height(); ++y) {
                const int sourceY = int(quint64(y) * sourceSize.height() / target.height());
                for (int x = 0; x < target.width(); ++x) {
                    const int sourceX = int(quint64(x) * sourceSize.width() / target.width());
                    QCOMPARE(nearby.pixel(x, y), fixturePixel(sourceX, sourceY));
                }
            }
        }
        std::atomic_bool cancelled{true};
        QVERIFY(Licasa::readFastPngNearbyPreview(path, {1279, 953}, 26'000'000, 8 * 1024 * 1024,
                                                 &cancelled)
                    .isNull());
        QVERIFY(Licasa::readFastPngPreview(path, {1279, 953}, 26'000'000, 1, nullptr)
                    .fullBase.isNull());
        QFile file(path);
        QVERIFY(file.open(QIODevice::ReadWrite));
        QVERIFY(file.resize(file.size() - 1));
        file.close();
        const auto truncated =
            Licasa::readFastPngPreview(path, {1279, 953}, 26'000'000, 8 * 1024 * 1024, nullptr);
        QVERIFY(truncated.preview.isNull());
        QVERIFY(truncated.fullBase.isNull());
    }

    void qoiLiteralRefillAndTruncation()
    {
        for (int channels : {3, 4}) {
            for (int prefix = 0; prefix < 5; ++prefix) {
                constexpr int width = 17003;
                QByteArray bytes("qoif", 4);
                bytes += be32(width) + be32(1);
                bytes.append(char(channels));
                bytes.append(char(0));
                QImage expected(width, 1,
                                channels == 4 ? QImage::Format_ARGB32 : QImage::Format_RGB32);
                for (int x = 0; x < width; ++x) {
                    if (x < prefix) {
                        bytes.append(char(0x7f));
                        expected.setPixel(x, 0, qRgb(x + 1, x + 1, x + 1));
                        continue;
                    }
                    const QRgb pixel = fixturePixel(x, 0, channels == 4);
                    bytes.append(char(channels == 4 ? 0xff : 0xfe));
                    bytes.append(char(qRed(pixel)));
                    bytes.append(char(qGreen(pixel)));
                    bytes.append(char(qBlue(pixel)));
                    if (channels == 4) {
                        bytes.append(char(qAlpha(pixel)));
                    }
                    expected.setPixel(x, 0, pixel);
                }
                bytes += QByteArray::fromHex("0000000000000001");
                expected.setColorSpace(QColorSpace::SRgb);
                QBuffer source(&bytes);
                QVERIFY(source.open(QIODevice::ReadOnly));
                QImageReader reader(&source, "qoi");
                Licasa::ImageDecodeContract::configure(reader, 20'000);
                QCOMPARE(reader.read(), expected);
                for (int cut = 65530; cut < 65542; ++cut) {
                    QByteArray truncated = bytes.first(cut);
                    QBuffer shortSource(&truncated);
                    QVERIFY(shortSource.open(QIODevice::ReadOnly));
                    QImageReader shortReader(&shortSource, "qoi");
                    QVERIFY(shortReader.read().isNull());
                }
            }
        }
    }

    void apngFullSourceReplacementPreservesDisposalAndOutputs_data()
    {
        QTest::addColumn<QImage::Format>("format");
        QTest::addColumn<int>("dispose");
        for (const auto format : {QImage::Format_RGBA8888, QImage::Format_RGBA64}) {
            for (int dispose : {0, 1, 2}) {
                const QByteArray name =
                    QByteArray::number(int(format)) + '-' + QByteArray::number(dispose);
                QTest::newRow(name) << format << dispose;
            }
        }
    }

    void apngFullSourceReplacementPreservesDisposalAndOutputs()
    {
        QFETCH(QImage::Format, format);
        QFETCH(int, dispose);
        const QSize canvasSize(31, 23);
        const auto full = [&](int seed, quint8 disposal) {
            return AnimationFrameFixture{animationImage(canvasSize, format, seed), {}, disposal, 0};
        };
        const auto overlay = [&](int seed, quint8 disposal) {
            return AnimationFrameFixture{
                animationImage({13, 11}, format, seed), {3, 2}, disposal, 1};
        };
        // Replace full source frames after each disposal kind, then exercise
        // Over on their resulting canvas. A later source replacement also
        // discards the saved region from an intervening Previous overlay.
        const QList<AnimationFrameFixture> frames = {full(1, 0),
                                                     overlay(2, quint8(dispose)),
                                                     full(3, quint8(dispose)),
                                                     overlay(4, 0),
                                                     full(5, 0),
                                                     overlay(6, 2),
                                                     full(7, 0),
                                                     overlay(8, 0)};
        QList<QImage> decodedFrames;
        QByteArray bytes = multiFrameApng(frames, &decodedFrames);
        QVERIFY(!bytes.isEmpty());
        QCOMPARE(decodedFrames.size(), frames.size());
        QBuffer buffer(&bytes);
        QVERIFY(buffer.open(QIODevice::ReadOnly));
        std::unique_ptr<QImageIOHandler> handler(licasaCreateImageHandler());
        handler->setDevice(&buffer);
        handler->setFormat("apng");
        const auto composition = format == QImage::Format_RGBA64
                                     ? QImage::Format_RGBA64_Premultiplied
                                     : QImage::Format_ARGB32_Premultiplied;
        QImage canvas(canvasSize, composition);
        canvas.fill(Qt::transparent);
        canvas.setColorSpace(decodedFrames.first().colorSpace());
        QImage previousRegion;
        QList<QImage> expectedFrames;
        QList<QImage> actualFrames;
        for (qsizetype index = 0; index < frames.size(); ++index) {
            if (index > 0) {
                const auto& previous = frames[index - 1];
                const QRect previousRect(previous.offset, previous.image.size());
                QPainter disposer(&canvas);
                disposer.setCompositionMode(QPainter::CompositionMode_Source);
                if (previous.dispose == 1) {
                    disposer.fillRect(previousRect, Qt::transparent);
                } else if (previous.dispose == 2) {
                    disposer.drawImage(previousRect.topLeft(), previousRegion);
                }
            }
            const auto& frame = frames[index];
            previousRegion = frame.dispose == 2
                                 ? canvas.copy(QRect(frame.offset, frame.image.size()))
                                 : QImage{};
            QPainter painter(&canvas);
            painter.setCompositionMode(frame.blend == 0 ? QPainter::CompositionMode_Source
                                                        : QPainter::CompositionMode_SourceOver);
            painter.drawImage(frame.offset, decodedFrames[index]);
            painter.end();
            expectedFrames.append(canvas);
            QImage actual;
            QVERIFY(handler->read(&actual));
            QCOMPARE(actual, canvas);
            QCOMPARE(actual.colorSpace(), canvas.colorSpace());
            QCOMPARE(actual.dotsPerMeterX(), canvas.dotsPerMeterX());
            QCOMPARE(actual.dotsPerMeterY(), canvas.dotsPerMeterY());
            QCOMPARE(actual.devicePixelRatio(), canvas.devicePixelRatio());
            QCOMPARE(handler->currentImageNumber(), int(index));
            QCOMPARE(handler->nextImageDelay(), 100);
            actualFrames.append(actual);
        }
        // Retained outputs must keep their pixels after later frame replacement.
        QCOMPARE(actualFrames, expectedFrames);
        QVERIFY(handler->jumpToImage(2));
        QImage revisited;
        QVERIFY(handler->read(&revisited));
        QCOMPARE(revisited, expectedFrames[2]);
        QCOMPARE(actualFrames, expectedFrames);
        QCOMPARE(handler->imageCount(), frames.size());
        QCOMPARE(handler->loopCount(), -1);
    }

    void apngFirstCanvasMatchesPainter_data()
    {
        QTest::addColumn<QImage::Format>("format");
        QTest::addColumn<int>("dispose");
        for (const auto format :
             {QImage::Format_RGB888, QImage::Format_RGBA8888, QImage::Format_RGBA64,
              QImage::Format_Grayscale8, QImage::Format_Grayscale16}) {
            for (int dispose : {0, 1, 2}) {
                const QByteArray name =
                    QByteArray::number(int(format)) + '-' + QByteArray::number(dispose);
                QTest::newRow(name) << format << dispose;
            }
        }
    }

    void apngFirstCanvasMatchesPainter()
    {
        QFETCH(QImage::Format, format);
        QFETCH(int, dispose);
        QImage source(31, 23, format);
        for (int y = 0; y < source.height(); ++y) {
            for (int x = 0; x < source.width(); ++x) {
                source.setPixelColor(x, y,
                                     format == QImage::Format_RGBA64
                                         ? QColor::fromRgba64(quint16(x * 1997 + y * 61),
                                                              quint16(x * 1697 + y * 131),
                                                              quint16(x * 1193 + y * 271),
                                                              quint16(x * 1877 + y * 101))
                                         : QColor::fromRgba(fixturePixel(x, y)));
            }
        }
        if (format != QImage::Format_Grayscale8 && format != QImage::Format_Grayscale16) {
            source.setColorSpace(QColorSpace::DisplayP3);
        }
        QImage decodedFrame;
        QByteArray bytes = apngFirstFrame(source, quint8(dispose), &decodedFrame);
        QVERIFY(!decodedFrame.isNull());
        QVERIFY(!bytes.isEmpty());
        QBuffer buffer(&bytes);
        QVERIFY(buffer.open(QIODevice::ReadOnly));
        std::unique_ptr<QImageIOHandler> handler(licasaCreateImageHandler());
        handler->setDevice(&buffer);
        handler->setFormat("apng");
        QImage actual;
        QVERIFY(handler->read(&actual));
        const auto composition = decodedFrame.format() == QImage::Format_RGBA64 ||
                                         decodedFrame.format() == QImage::Format_RGBX64
                                     ? QImage::Format_RGBA64_Premultiplied
                                     : QImage::Format_ARGB32_Premultiplied;
        QImage expected(source.size(), composition);
        expected.fill(Qt::transparent);
        expected.setColorSpace(decodedFrame.colorSpace());
        QPainter painter(&expected);
        painter.setCompositionMode(QPainter::CompositionMode_Source);
        painter.drawImage(0, 0, decodedFrame);
        painter.end();
        QCOMPARE(actual, expected);
        QCOMPARE(actual.format(), expected.format());
        QCOMPARE(actual.colorSpace(), expected.colorSpace());
        QCOMPARE(actual.dotsPerMeterX(), expected.dotsPerMeterX());
        QCOMPARE(actual.dotsPerMeterY(), expected.dotsPerMeterY());
        QCOMPARE(actual.devicePixelRatio(), expected.devicePixelRatio());
        QCOMPARE(handler->nextImageDelay(), 100);
        QCOMPARE(handler->imageCount(), 1);
        QCOMPARE(handler->loopCount(), -1);
    }
};

QTEST_GUILESS_MAIN(DecodeRound3Test)
#include "tst_decode_round3.moc"

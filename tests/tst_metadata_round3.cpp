#include "imaging/format_support.h"
#include "media/android_motion_photo.h"
#include "media/live_photo_pairing.h"
#include "media/photo_asset_probe.h"
#include "private_metadata_round3.h"

#include <QFile>
#include <QImage>
#include <QLocale>
#include <QTemporaryDir>
#include <QtTest>

#include <algorithm>
#include <atomic>
#include <limits>

using namespace Licasa;

class MetadataRound3Test final : public QObject {
    Q_OBJECT

  private slots:
    void retainsAttributeFirstError()
    {
        QByteArray packet = MetadataRound3Fixtures::xmp(96, 2);
        auto detection = detectAndroidMotionPhotoV1(QString(), packet);
        QCOMPARE(detection.status, AndroidMotionPhotoStatus::NotMotionPhoto);
        QVERIFY(detection.error.isEmpty());
        packet.replace("c:MotionPhotoVersion=\"  +1  \"", "c:MotionPhotoVersion=\"bad\"");
        packet.replace("c:MotionPhoto=\"0\"", "c:MotionPhoto=\"bad\"");
        detection = detectAndroidMotionPhotoV1(QString(), packet);
        QCOMPARE(detection.status, AndroidMotionPhotoStatus::Invalid);
        QCOMPARE(detection.error,
                 QStringLiteral("Invalid Android Motion Photo integer: MotionPhoto"));

        packet = MetadataRound3Fixtures::xmp(0, 2);
        packet.replace("</x:Description>", "</x:Description><x:Other c:MotionPhoto=\"1\"/>");
        detection = detectAndroidMotionPhotoV1(QString(), packet);
        QCOMPARE(detection.status, AndroidMotionPhotoStatus::Invalid);
        QCOMPARE(detection.error,
                 QStringLiteral("Duplicate Android Motion Photo field: MotionPhoto"));
        std::atomic_bool cancelled{true};
        detection = detectAndroidMotionPhotoV1(QString(), packet, &cancelled);
        QCOMPARE(detection.error, QStringLiteral("Motion Photo detection cancelled"));
    }

    void retainsIntegerAcceptanceAndBounds()
    {
        for (const QByteArray& text :
             {QByteArray("-9223372036854775808"), QByteArray("+9223372036854775807"),
              QByteArray(" \t+0\n "), QByteArray("&#160;1&#160;")}) {
            QByteArray packet = MetadataRound3Fixtures::xmp(0, 2);
            packet.replace("c:MotionPhotoVersion=\"  +1  \"",
                           "c:MotionPhotoVersion=\"" + text + "\"");
            QCOMPARE(detectAndroidMotionPhotoV1(QString(), packet).status,
                     AndroidMotionPhotoStatus::NotMotionPhoto);
        }
        for (const QByteArray& text :
             {QByteArray("9223372036854775808"), QByteArray("-9223372036854775809"),
              QByteArray("1x"), QByteArray(" ")}) {
            QByteArray packet = MetadataRound3Fixtures::xmp(0, 2);
            packet.replace("c:MotionPhotoVersion=\"  +1  \"",
                           "c:MotionPhotoVersion=\"" + text + "\"");
            QCOMPARE(detectAndroidMotionPhotoV1(QString(), packet).error,
                     QStringLiteral("Invalid Android Motion Photo integer: MotionPhotoVersion"));
        }
        for (const QByteArray& text :
             {QByteArray("-0"), QByteArray("-1"), QByteArray("18446744073709551616")}) {
            QByteArray packet = MetadataRound3Fixtures::xmp(0, 2);
            packet.replace("c:MicroVideoOffset=\" 18446744073709551615 \"",
                           "c:MicroVideoOffset=\"" + text + "\"");
            QCOMPARE(detectAndroidMotionPhotoV1(QString(), packet).error,
                     QStringLiteral("Invalid Android Motion Photo integer: MicroVideoOffset"));
        }
        QCOMPARE(detectAndroidMotionPhotoV1(QString(), MetadataRound3Fixtures::xmp(0, 64)).status,
                 AndroidMotionPhotoStatus::NotMotionPhoto);
        QCOMPARE(detectAndroidMotionPhotoV1(QString(), MetadataRound3Fixtures::xmp(0, 65)).error,
                 QStringLiteral("Motion Photo Container directory exceeds 64 items"));
    }

    void borrowsExifWithoutEscapingInputLifetime()
    {
        for (const bool little : {false, true}) {
            for (const int bytes : {64, 8192, 512 * 1024}) {
                QByteArray tiff = MetadataRound3Fixtures::appleTiff(bytes, little);
                const QByteArray unchanged = tiff;
                const auto direct = appleLivePhotoIdentifierFromExifTiff(tiff);
                QCOMPARE(tiff, unchanged);
                tiff.fill('x');
                QCOMPARE(direct.status, AppleLivePhotoIdentifierStatus::Valid);
                QCOMPARE(direct.contentIdentifier,
                         QStringLiteral("round-three-content-identifier"));
                const auto heif = appleLivePhotoIdentifierFromHeifExifBlock(
                    MetadataRound3Fixtures::heifExif(bytes, little));
                QCOMPARE(heif.status, direct.status);
                QCOMPARE(heif.contentIdentifier, direct.contentIdentifier);
                QVERIFY(heif.error.isEmpty());
            }
        }
        QByteArray truncated = MetadataRound3Fixtures::appleTiff(64);
        truncated.chop(1);
        const auto invalid = appleLivePhotoIdentifierFromExifTiff(truncated);
        QCOMPARE(invalid.status, AppleLivePhotoIdentifierStatus::Invalid);
        QCOMPARE(invalid.error, QStringLiteral("EXIF SubIFD is malformed"));
    }

    void jpegSkipsIgnoredApp1AndFindsExactMotionRange()
    {
        QTemporaryDir directory;
        QVERIFY(directory.isValid());
        const QString path = directory.filePath(QStringLiteral("camera.jpg"));
        const QUrl url = QUrl::fromLocalFile(path);
        for (const int segments : {0, 1, 15}) {
            const QByteArray bytes = MetadataRound3Fixtures::metadataJpeg(segments, 65000, true);
            QFile file(path);
            QVERIFY(file.open(QIODevice::WriteOnly));
            QCOMPARE(file.write(bytes), bytes.size());
            file.close();
            const auto info = probePhotoAsset(url);
            QCOMPARE(info.kind, PhotoAssetKind::MotionPhoto);
            QVERIFY(info.metadataError.isEmpty());
            QVERIFY(info.motion.has_value());
            QCOMPARE(info.motion->mimeType, QStringLiteral("video/mp4"));
            QCOMPARE(info.motion->embedded.offset, quint64(bytes.size() - 24));
            QCOMPARE(info.motion->embedded.length, quint64(24));
            QCOMPARE(info.motion->presentationTimestampUs, std::optional<qint64>(42));
        }
        const QByteArray plain = MetadataRound3Fixtures::metadataJpeg(1, 65000, false);
        QFile file(path);
        QVERIFY(file.open(QIODevice::WriteOnly));
        QCOMPARE(file.write(plain), plain.size());
        file.close();
        const auto info = probePhotoAsset(url);
        QCOMPARE(info.kind, PhotoAssetKind::Still);
        QVERIFY(info.metadataError.isEmpty());
        QVERIFY(!info.motion.has_value());
    }

    void jpegRetainsTruncationAndScanLimitErrors()
    {
        QTemporaryDir directory;
        QVERIFY(directory.isValid());
        const QString path = directory.filePath(QStringLiteral("truncated.jpg"));
        const auto check = [&](const QByteArray& bytes, const QString& error) {
            QFile file(path);
            QVERIFY(file.open(QIODevice::WriteOnly));
            QCOMPARE(file.write(bytes), bytes.size());
            file.close();
            const auto info = probePhotoAsset(QUrl::fromLocalFile(path));
            QCOMPARE(info.kind, PhotoAssetKind::Still);
            QCOMPARE(info.metadataError, error);
            QVERIFY(!info.motion.has_value());
        };
        const QByteArray complete = MetadataRound3Fixtures::metadataJpeg(1, 65000, false);
        for (const int size : {10, 34, 1000, 65005}) {
            check(complete.first(size), QStringLiteral("JPEG APP1 XMP segment is truncated"));
        }
        QByteArray packet("http://ns.adobe.com/xap/1.0/\0", 29);
        packet += QByteArray(1000, 'x');
        QByteArray xmpTruncated =
            QByteArray::fromHex("ffd8") + MetadataRound3Fixtures::jpegSegment(packet);
        xmpTruncated.chop(1);
        check(xmpTruncated, QStringLiteral("JPEG APP1 XMP segment is truncated"));
        check(MetadataRound3Fixtures::metadataJpeg(17, 65000, false),
              QStringLiteral("JPEG metadata exceeds the 1 MiB probe limit"));
        check(MetadataRound3Fixtures::metadataJpeg(512, 30, false),
              QStringLiteral("JPEG metadata exceeds the segment-count limit"));
        std::atomic_bool cancelled{true};
        const auto info = probePhotoAsset(QUrl::fromLocalFile(path), &cancelled);
        QVERIFY(info.metadataError.isEmpty());
        QVERIFY(!info.motion.has_value());
    }

    void virtualCurrentPreservesLocaleOrderAndWrap()
    {
        QTemporaryDir directory;
        QVERIFY(directory.isValid());
        const QStringList names{QStringLiteral("a.png"), QStringLiteral("c.png"),
                                QStringLiteral("z.png"), QStringLiteral("Ä.png")};
        for (const QString& name : names) {
            QFile file(directory.filePath(name));
            QVERIFY(file.open(QIODevice::WriteOnly));
        }
        QImage pixel(1, 1, QImage::Format_RGB32);
        pixel.fill(Qt::red);
        for (const QString& currentName : {QStringLiteral("0"), QStringLiteral("b"),
                                           QStringLiteral("zz"), QStringLiteral("Ö")}) {
            const QString path = directory.filePath(currentName);
            QVERIFY(pixel.save(path, "PNG"));
            const QUrl current = QUrl::fromLocalFile(path);
            const FormatSupport formats;
            QStringList ordered = names;
            ordered.append(currentName);
            std::sort(ordered.begin(), ordered.end(),
                      [](const QString& left, const QString& right) {
                          const int comparison = left.localeAwareCompare(right);
                          return comparison < 0 || (comparison == 0 && left < right);
                      });
            const int position = int(ordered.indexOf(currentName));
            for (const int preferred : {-1, 0, 1}) {
                for (const int radius : {1, 3, 10, std::numeric_limits<int>::max()}) {
                    QList<QUrl> expected;
                    const auto append = [&](int distance, int direction) {
                        const int index =
                            (position + direction * distance + ordered.size()) % ordered.size();
                        const QUrl url = QUrl::fromLocalFile(directory.filePath(ordered.at(index)));
                        if (!expected.contains(url)) {
                            expected.append(url);
                        }
                    };
                    if (preferred == 0) {
                        for (int distance = 1; distance <= radius && expected.size() < names.size();
                             ++distance) {
                            append(distance, 1);
                            append(distance, -1);
                        }
                    } else {
                        for (const int direction : {preferred, -preferred}) {
                            for (int distance = 1;
                                 distance <= radius && expected.size() < names.size(); ++distance) {
                                append(distance, direction);
                            }
                        }
                    }
                    QCOMPARE(formats.nearbyImages(current, radius, preferred), expected);
                }
            }
            QVERIFY(QFile::remove(path));
        }
    }
};

QTEST_GUILESS_MAIN(MetadataRound3Test)
#include "tst_metadata_round3.moc"

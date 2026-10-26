#include "../src/media/motion/video_frame_image.h"

// Exercise Qt's custom AVIO ownership, including avformat_open_input failure.
// Application preflight deliberately rejects malformed Motion Photos before Qt;
// this test reaches the private plugin directly so that branch stays covered.
#include <QBuffer>
#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QMediaPlayer>
#include <QTest>
#include <QVideoFrame>
#include <QVideoSink>

#include <cstring>

class QtCustomInputTest : public QObject {
    Q_OBJECT
  private slots:
    void initTestCase()
    {
        QCoreApplication::addLibraryPath(QCoreApplication::applicationDirPath() +
                                         QStringLiteral("/plugins"));
    }

    void frameTransforms_data()
    {
        QTest::addColumn<int>("surfaceRotation");
        QTest::addColumn<bool>("surfaceMirrored");
        QTest::addColumn<int>("presentationRotation");
        QTest::addColumn<bool>("presentationMirrored");
        QTest::addColumn<int>("fallbackRotation");
        for (int rotation : {0, 90, 180, 270}) {
            const QByteArray name = QByteArray::number(rotation);
            QTest::newRow(("container-" + name).constData())
                << 0 << false << 0 << false << rotation;
            QTest::newRow(("presentation-" + name).constData())
                << 0 << false << rotation << false << rotation;
            QTest::newRow(("presentation-mirror-" + name).constData())
                << 0 << false << rotation << true << rotation;
#if QT_VERSION >= QT_VERSION_CHECK(6, 7, 0)
            QTest::newRow(("surface-" + name).constData())
                << rotation << false << 0 << false << rotation;
            QTest::newRow(("surface-mirror-" + name).constData())
                << rotation << true << 0 << false << rotation;
            QTest::newRow(("surface-and-presentation-" + name).constData())
                << rotation << true << 90 << true << rotation;
#endif
        }
    }

    void frameTransforms()
    {
        QFETCH(int, surfaceRotation);
        QFETCH(bool, surfaceMirrored);
        QFETCH(int, presentationRotation);
        QFETCH(bool, presentationMirrored);
        QFETCH(int, fallbackRotation);
        QImage original(2, 3, QImage::Format_RGBA8888);
        const QColor colors[] = {Qt::red, Qt::green, Qt::blue, Qt::yellow, Qt::cyan, Qt::magenta};
        for (int y = 0; y < original.height(); ++y) {
            for (int x = 0; x < original.width(); ++x) {
                original.setPixelColor(x, y, colors[y * original.width() + x]);
            }
        }
        QVideoFrameFormat format(original.size(), QVideoFrameFormat::Format_RGBA8888);
#if QT_VERSION >= QT_VERSION_CHECK(6, 7, 0)
        format.setRotation(static_cast<QtVideo::Rotation>(surfaceRotation));
        format.setMirrored(surfaceMirrored);
#endif
        QVideoFrame frame(format);
        QVERIFY(frame.map(QVideoFrame::WriteOnly));
        for (int y = 0; y < original.height(); ++y) {
            std::memcpy(frame.bits(0) + y * frame.bytesPerLine(0), original.constScanLine(y),
                        original.width() * 4);
        }
        frame.unmap();
#if QT_VERSION >= QT_VERSION_CHECK(6, 7, 0)
        frame.setRotation(static_cast<QtVideo::Rotation>(presentationRotation));
#else
        frame.setRotationAngle(static_cast<QVideoFrame::RotationAngle>(presentationRotation));
#endif
        frame.setMirrored(presentationMirrored);
        // Qt 6.8's CPU converter composes the surface transform in this order.
        // The caller must retain that conversion and apply presentation once.
        QTransform surfaceTransform;
        surfaceTransform.rotate(surfaceRotation);
        if (surfaceMirrored) {
            surfaceTransform.scale(-1, 1);
        }
        QImage expected = original.transformed(surfaceTransform);
        const int rotation = presentationRotation != 0 || surfaceRotation != 0 || surfaceMirrored
                                 ? presentationRotation
                                 : fallbackRotation;
        expected = expected.transformed(QTransform().rotate(rotation));
        if (presentationMirrored) {
            expected = expected.mirrored(true, false);
        }
        const QImage actual = Licasa::Detail::videoFramePresentationImage(frame, fallbackRotation);
        QCOMPARE(actual.convertToFormat(QImage::Format_RGBA8888), expected);
    }

    void failedOpenThenSuccessfulReopen()
    {
        QFile fixture(QStringLiteral(LICASA_TEST_ASSET_DIR "/modern/motion/qt-runtime-0deg.mp4"));
        QVERIFY(fixture.open(QIODevice::ReadOnly));
        const QByteArray valid = fixture.readAll();
        QVERIFY(!valid.isEmpty());
        // LeakSanitizer observes both the AVIO context and its FFmpeg-owned
        // replacement buffer. Repetition also checks teardown after errors.
        for (int cycle = 0; cycle < 12; ++cycle) {
            QBuffer invalid;
            QBuffer good;
            QVideoSink sink;
            QMediaPlayer player;
            player.setVideoSink(&sink);
            invalid.setData(QByteArray(4096, '\xff'));
            QVERIFY(invalid.open(QIODevice::ReadOnly));
            player.setSourceDevice(&invalid);
            QTRY_VERIFY_WITH_TIMEOUT(player.error() != QMediaPlayer::NoError, 10000);
            QVERIFY(player.error() != QMediaPlayer::NoError);
            player.setSourceDevice(nullptr);

            good.setData(valid);
            QVERIFY(good.open(QIODevice::ReadOnly));
            player.setSourceDevice(&good);
            player.play();
            const auto recoveryState = [&] {
                return QStringLiteral("reopen cycle %1: state=%2 status=%3 position=%4/%5 "
                                      "buffer=%6/%7 error=%8 (%9)")
                    .arg(cycle)
                    .arg(int(player.playbackState()))
                    .arg(int(player.mediaStatus()))
                    .arg(player.position())
                    .arg(player.duration())
                    .arg(good.pos())
                    .arg(good.size())
                    .arg(int(player.error()))
                    .arg(player.errorString());
            };
            QTRY_VERIFY2_WITH_TIMEOUT(sink.videoFrame().isValid() ||
                                          player.error() != QMediaPlayer::NoError,
                                      qPrintable(recoveryState()), 10000);
            QCOMPARE(player.error(), QMediaPlayer::NoError);
            QVERIFY(sink.videoFrame().isValid());
            player.stop();
            player.setSourceDevice(nullptr);
        }
#ifdef Q_OS_LINUX
        QFile maps(QStringLiteral("/proc/self/maps"));
        QVERIFY(maps.open(QIODevice::ReadOnly));
        const QByteArray privatePlugin = QFile::encodeName(
            QDir::cleanPath(QCoreApplication::applicationDirPath() +
                            QStringLiteral("/plugins/multimedia/libffmpegmediaplugin.so")));
        QVERIFY2(maps.readAll().contains(privatePlugin),
                 "Custom input tests must exercise the exact private FFmpeg plugin");
#endif
    }
};

QTEST_MAIN(QtCustomInputTest)
#include "tst_qt_custom_input.moc"

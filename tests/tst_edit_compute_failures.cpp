#include "imaging/compute/edit_compute.h"

#include <QLibrary>
#include <QtTest>

#include <memory>

class EditComputeFailureTests final : public QObject {
    Q_OBJECT

    struct DriverProbe {
        using Reset = void (*)();
        using Fail = void (*)(int);
        using Count = int (*)();

        QLibrary library;
        Reset reset = nullptr;
        Fail failParameterWrite = nullptr;
        Fail failFinishAfter = nullptr;
        Count uploads = nullptr;
        Count finishes = nullptr;
        Count pendingWrites = nullptr;
        Count unsafeReleases = nullptr;

        explicit DriverProbe(bool cuda)
            : library(cuda ? QStringLiteral("cuda") : QStringLiteral("OpenCL"), 1)
        {}

        bool initialize()
        {
            reset = reinterpret_cast<Reset>(library.resolve("licasaFakeComputeReset"));
            failParameterWrite =
                reinterpret_cast<Fail>(library.resolve("licasaFakeComputeFailParameterWrite"));
            failFinishAfter =
                reinterpret_cast<Fail>(library.resolve("licasaFakeComputeFailFinishAfter"));
            uploads = reinterpret_cast<Count>(library.resolve("licasaFakeComputeUploads"));
            finishes = reinterpret_cast<Count>(library.resolve("licasaFakeComputeFinishes"));
            pendingWrites =
                reinterpret_cast<Count>(library.resolve("licasaFakeComputePendingWrites"));
            unsafeReleases =
                reinterpret_cast<Count>(library.resolve("licasaFakeComputeUnsafeReleases"));
            return reset && failParameterWrite && failFinishAfter && uploads && finishes &&
                   pendingWrites && unsafeReleases;
        }
    };

  private slots:
    void uploadFailureDrainsAndInvalidatesResidentBase_data()
    {
        QTest::addColumn<bool>("cuda");
        QTest::newRow("opencl") << false;
        QTest::newRow("cuda") << true;
    }

    void uploadFailureDrainsAndInvalidatesResidentBase()
    {
        QFETCH(bool, cuda);
        DriverProbe probe(cuda);
        QVERIFY2(probe.initialize(), qPrintable(probe.library.errorString()));
        probe.reset();
        // Keep borrowed host pixels alive through device teardown even when
        // the intentionally broken old driver fails an assertion below.
        QImage original(4, 4, QImage::Format_ARGB32);
        original.fill(Qt::red);
        QImage replacement(4, 4, QImage::Format_ARGB32);
        replacement.fill(Qt::blue);
        QImage result(original.size(), original.format());
        const Licasa::EditUniforms identity{};
        QString error;
        auto device =
            cuda ? Licasa::createCudaEditDevice(error) : Licasa::createOpenClEditDevice(error);
        QVERIFY2(device, qPrintable(error));
        QVERIFY(device->description.contains(QStringLiteral("Deterministic fake compute")));

        const quint64 originalIdentity = quint64(original.cacheKey());

        QVERIFY(device->run(original, result, originalIdentity, identity, true, false, nullptr));
        QCOMPARE(result, original);
        const int uploads = probe.uploads();
        QVERIFY(device->run(original, result, originalIdentity, identity, true, false, nullptr));
        QCOMPARE(probe.uploads(),
                 uploads); // Successful same-source requests still reuse residency.

        const int finishes = probe.finishes();
        probe.failParameterWrite(1);
        QVERIFY(!device->run(replacement, result, quint64(replacement.cacheKey()), identity, true,
                             false, nullptr));
        QCOMPARE(probe.pendingWrites(), 0);
        QVERIFY(probe.finishes() > finishes);
        QCOMPARE(probe.unsafeReleases(), 0);

        // The failed replacement changed the device input. The original
        // identity must be uploaded again, rather than reusing those bytes.
        probe.failParameterWrite(0);
        const int uploadsAfterFailure = probe.uploads();
        QVERIFY(device->run(original, result, originalIdentity, identity, true, false, nullptr));
        QCOMPARE(probe.uploads(), uploadsAfterFailure + 1);
        QCOMPARE(result, original);
        device->releaseImageBuffers();
        QCOMPARE(device->retainedImageBytes(), std::size_t(0));
        device.reset();
        QCOMPARE(probe.pendingWrites(), 0);
        QCOMPARE(probe.unsafeReleases(), 0);
        probe.reset();
    }

    void failedFenceCannotPublishResidentBase_data()
    {
        QTest::addColumn<bool>("cuda");
        QTest::addColumn<int>("failureCountdown");
        QTest::newRow("opencl-command-fence") << false << 1;
        QTest::newRow("cuda-command-fence") << true << 1;
        // The OpenCL fixture's blocking readback also finishes its queue.
        // CUDA readback is synchronous, leaving only the explicit and cleanup fences.
        QTest::newRow("opencl-cleanup-fence") << false << 3;
        QTest::newRow("cuda-cleanup-fence") << true << 2;
    }

    void failedFenceCannotPublishResidentBase()
    {
        QFETCH(bool, cuda);
        QFETCH(int, failureCountdown);
        DriverProbe probe(cuda);
        QVERIFY2(probe.initialize(), qPrintable(probe.library.errorString()));
        probe.reset();
        QImage source(4, 4, QImage::Format_ARGB32);
        source.fill(Qt::green);
        QImage result(source.size(), source.format());
        const Licasa::EditUniforms identity{};
        QString error;
        auto device =
            cuda ? Licasa::createCudaEditDevice(error) : Licasa::createOpenClEditDevice(error);
        QVERIFY2(device, qPrintable(error));

        probe.failFinishAfter(failureCountdown);
        const bool success =
            device->run(source, result, quint64(source.cacheKey()), identity, true, false, nullptr);
        QCOMPARE(success, failureCountdown > 1);
        QCOMPARE(probe.pendingWrites(), 0);
        const int uploadsAfterFailure = probe.uploads();
        QVERIFY(device->run(source, result, quint64(source.cacheKey()), identity, true, false,
                            nullptr));
        QCOMPARE(probe.uploads(), uploadsAfterFailure + 1);
        QCOMPARE(result, source);
        const int uploadsAfterRecovery = probe.uploads();
        QVERIFY(device->run(source, result, quint64(source.cacheKey()), identity, true, false,
                            nullptr));
        QCOMPARE(probe.uploads(), uploadsAfterRecovery);
        device.reset();
        probe.reset();
    }
};

QTEST_GUILESS_MAIN(EditComputeFailureTests)
#include "tst_edit_compute_failures.moc"

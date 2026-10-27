#include "app/application_lifecycle.h"

#include <QtTest>

class ApplicationLifecycleTests final : public QObject {
    Q_OBJECT

  private slots:
    void snapLoginBackgroundHasFiniteLifetime()
    {
        Licasa::BackgroundIdlePolicy policy;
        QVERIFY(!policy.observe(500, 0, true, true));
        QCOMPARE(policy.remainingMs(500), qint64(60000));
        QVERIFY(!policy.observe(60499, 0, true, true));
        QVERIFY(policy.observe(60500, 0, true, true));
        QCOMPARE(policy.remainingMs(60500), qint64(0));
    }

    void probesAndMaintenanceCannotExtendDeadline()
    {
        Licasa::BackgroundIdlePolicy policy;
        QVERIFY(!policy.observe(0, 0, true, true));
        for (qint64 time = 1; time < 60000; time += 37) {
            QVERIFY(!policy.observe(time, 0, true, true));
            QCOMPARE(policy.remainingMs(time), qint64(60000) - time);
        }
        QVERIFY(policy.observe(60000, 0, true, true));
        QVERIFY(policy.observe(70000, 0, true, true));
    }

    void visibleUserActivityStartsNextPeriodOnlyAfterClose()
    {
        Licasa::BackgroundIdlePolicy policy;
        QVERIFY(!policy.observe(0, 0, true, true));
        QVERIFY(!policy.observe(59000, 1, true, true));
        QCOMPARE(policy.remainingMs(59000), qint64(-1));
        QVERIFY(!policy.observe(120000, 2, true, true));
        QVERIFY(!policy.observe(120001, 1, true, true));
        QVERIFY(!policy.observe(120002, 0, true, true));
        QCOMPARE(policy.remainingMs(120002), qint64(60000));
        QVERIFY(!policy.observe(180001, 0, true, true));
        QVERIFY(policy.observe(180002, 0, true, true));
    }

    void disabledBackgroundRetiresOnlyHiddenInstance()
    {
        Licasa::BackgroundIdlePolicy policy;
        QVERIFY(!policy.observe(0, 1, false, true));
        QCOMPARE(policy.remainingMs(0), qint64(-1));
        QVERIFY(policy.observe(1, 0, false, true));
        QCOMPARE(policy.remainingMs(1), qint64(0));
        QVERIFY(!policy.observe(2, 1, false, false));
        QVERIFY(policy.observe(3, 0, false, false));
    }

    void nonSnapKeepsOptedInBackgroundBehavior()
    {
        Licasa::BackgroundIdlePolicy policy;
        QVERIFY(!policy.observe(0, 0, true, false));
        QVERIFY(!policy.observe(86400000, 0, true, false));
        QCOMPARE(policy.remainingMs(86400000), qint64(-1));
        QVERIFY(policy.observe(86400001, 0, false, false));
    }
};

QTEST_GUILESS_MAIN(ApplicationLifecycleTests)
#include "tst_application_lifecycle.moc"

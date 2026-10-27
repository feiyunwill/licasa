#include "app/runtime_error_log.h"

#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QProcess>
#include <QTemporaryDir>
#include <QtTest>

#if defined(Q_OS_UNIX)
#include <sys/stat.h>
#include <unistd.h>
#endif

class RuntimeErrorLogTests final : public QObject {
    Q_OBJECT

  private slots:
    void usesPerUserStateLocation();
    void storesOnlyWarningsAndErrorsPrivately();
    void rotatesAtTheSizeLimit();
    void refusesSymlinkedLogFile();
    void refusesHardlinkedLogFile();
    void refusesFifoLogFileWithoutBlocking();
};

void RuntimeErrorLogTests::usesPerUserStateLocation()
{
    QTemporaryDir home;
    QVERIFY(home.isValid());
    qputenv("XDG_STATE_HOME", home.filePath(QStringLiteral("state")).toUtf8());
    QCOMPARE(Licasa::defaultRuntimeErrorLogPath(),
             home.filePath(QStringLiteral("state/licasa/errors.log")));
    qunsetenv("XDG_STATE_HOME");
    qputenv("SNAP_USER_DATA", home.filePath(QStringLiteral("snap-data")).toUtf8());
    QCOMPARE(Licasa::defaultRuntimeErrorLogPath(),
             home.filePath(QStringLiteral("snap-data/.local/state/licasa/errors.log")));
    qunsetenv("SNAP_USER_DATA");
}

void RuntimeErrorLogTests::storesOnlyWarningsAndErrorsPrivately()
{
    QTemporaryDir home;
    QVERIFY(home.isValid());
    const QString path = home.filePath(QStringLiteral("state/licasa/errors.log"));
    {
        Licasa::RuntimeErrorLog log(path);
        QCOMPARE(log.path(), path);
        qInfo("Routine event should not be persisted");
        qWarning("Unique warning for runtime log test");
        qCritical("Unique error for runtime log test");
    }

    QFile file(path);
    QVERIFY(file.open(QIODevice::ReadOnly));
    const QByteArray content = file.readAll();
    QVERIFY(content.contains("WARNING Unique warning for runtime log test"));
    QVERIFY(content.contains("ERROR Unique error for runtime log test"));
    QVERIFY(!content.contains("Routine event should not be persisted"));

#if defined(Q_OS_UNIX)
    struct stat status {};
    const QByteArray encoded = QFile::encodeName(path);
    QCOMPARE(::stat(encoded.constData(), &status), 0);
    QCOMPARE(status.st_mode & 0777, mode_t(0600));
    const QByteArray directory = QFile::encodeName(QFileInfo(path).absolutePath());
    QCOMPARE(::stat(directory.constData(), &status), 0);
    QCOMPARE(status.st_mode & 0777, mode_t(0700));
#endif
}

void RuntimeErrorLogTests::rotatesAtTheSizeLimit()
{
    QTemporaryDir home;
    QVERIFY(home.isValid());
    const QString path = home.filePath(QStringLiteral("state/licasa/errors.log"));
    {
        Licasa::RuntimeErrorLog log(path);
        qWarning("First warning before rotation");
    }
    QFile file(path);
    QVERIFY(file.open(QIODevice::WriteOnly | QIODevice::Append));
    const QByteArray padding(512 * 1024, 'x');
    QCOMPARE(file.write(padding), qint64(padding.size()));
    file.close();

    {
        Licasa::RuntimeErrorLog log(path);
        qWarning("Warning after rotation");
    }
    QFile previous(path + QStringLiteral(".1"));
    QVERIFY(previous.open(QIODevice::ReadOnly));
    QVERIFY(previous.readAll().contains("First warning before rotation"));
    QVERIFY(file.open(QIODevice::ReadOnly));
    QVERIFY(file.readAll().contains("Warning after rotation"));
    QVERIFY(file.size() < 512 * 1024);

    file.close();
    QVERIFY(file.open(QIODevice::WriteOnly | QIODevice::Append));
    QCOMPARE(file.write(padding), qint64(padding.size()));
    file.close();
    {
        Licasa::RuntimeErrorLog log(path);
        qWarning("Newest warning after another rotation");
    }
    previous.close();
    QVERIFY(previous.open(QIODevice::ReadOnly));
    const QByteArray rotatedContent = previous.readAll();
    QVERIFY(rotatedContent.contains("Warning after rotation"));
    QVERIFY(!rotatedContent.contains("First warning before rotation"));
    file.close();
    QVERIFY(file.open(QIODevice::ReadOnly));
    QVERIFY(file.readAll().contains("Newest warning after another rotation"));
}

void RuntimeErrorLogTests::refusesSymlinkedLogFile()
{
#if defined(Q_OS_UNIX)
    QTemporaryDir home;
    QVERIFY(home.isValid());
    const QString directory = home.filePath(QStringLiteral("state/licasa"));
    QVERIFY(QDir().mkpath(directory));
    QFile target(home.filePath(QStringLiteral("unrelated.txt")));
    QVERIFY(target.open(QIODevice::WriteOnly));
    QCOMPARE(target.write("untouched"), qint64(9));
    target.close();
    QVERIFY(QFile::link(target.fileName(), directory + QStringLiteral("/errors.log")));

    {
        Licasa::RuntimeErrorLog log(directory + QStringLiteral("/errors.log"));
        qWarning("Must not follow a symlinked log");
    }
    QVERIFY(target.open(QIODevice::ReadOnly));
    QCOMPARE(target.readAll(), QByteArray("untouched"));
#else
    QSKIP("Symlinked private log files are tested on Unix");
#endif
}

void RuntimeErrorLogTests::refusesHardlinkedLogFile()
{
#if defined(Q_OS_UNIX)
    QTemporaryDir home;
    QVERIFY(home.isValid());
    const QString directory = home.filePath(QStringLiteral("state/licasa"));
    QVERIFY(QDir().mkpath(directory));
    QFile target(home.filePath(QStringLiteral("unrelated.txt")));
    QVERIFY(target.open(QIODevice::WriteOnly));
    QCOMPARE(target.write("untouched"), qint64(9));
    target.close();
    const QByteArray targetPath = QFile::encodeName(target.fileName());
    const QString logPath = directory + QStringLiteral("/errors.log");
    const QByteArray encodedLog = QFile::encodeName(logPath);
    QCOMPARE(::chmod(targetPath.constData(), 0644), 0);
    QCOMPARE(::link(targetPath.constData(), encodedLog.constData()), 0);

    {
        Licasa::RuntimeErrorLog log(logPath);
        qWarning("Must not modify a hardlinked log target");
    }
    QVERIFY(target.open(QIODevice::ReadOnly));
    QCOMPARE(target.readAll(), QByteArray("untouched"));
    struct stat status {};
    QCOMPARE(::stat(targetPath.constData(), &status), 0);
    QCOMPARE(status.st_mode & 0777, mode_t(0644));
#else
    QSKIP("Hardlinked private log files are tested on Unix");
#endif
}

void RuntimeErrorLogTests::refusesFifoLogFileWithoutBlocking()
{
#if defined(Q_OS_UNIX)
    QTemporaryDir home;
    QVERIFY(home.isValid());
    const QString directory = home.filePath(QStringLiteral("state/licasa"));
    QVERIFY(QDir().mkpath(directory));
    const QString path = directory + QStringLiteral("/errors.log");
    const QByteArray encoded = QFile::encodeName(path);
    QCOMPARE(::mkfifo(encoded.constData(), 0600), 0);

    // Use a child so a regression cannot hang the entire test runner.
    QProcess writer;
    writer.start(QCoreApplication::applicationFilePath(),
                 {QStringLiteral("--probe-log-file"), path});
    const bool finished = writer.waitForFinished(3000);
    if (!finished) {
        writer.kill();
        writer.waitForFinished(1000);
    }
    QVERIFY2(finished, "Writing a warning blocked while opening a FIFO log target");
    QCOMPARE(writer.exitStatus(), QProcess::NormalExit);
    QCOMPARE(writer.exitCode(), 0);
    QVERIFY(writer.readAllStandardError().contains("Licasa could not write its error log."));
    struct stat status {};
    QCOMPARE(::lstat(encoded.constData(), &status), 0);
    QVERIFY(S_ISFIFO(status.st_mode));
#else
    QSKIP("FIFO private log files are tested on Unix");
#endif
}

int main(int argc, char* argv[])
{
    QCoreApplication application(argc, argv);
    const QStringList arguments = application.arguments();
    if (arguments.size() == 3 && arguments.at(1) == QStringLiteral("--probe-log-file")) {
        Licasa::RuntimeErrorLog log(arguments.at(2));
        qWarning("Warning for an invalid runtime log target");
        return 0;
    }
    RuntimeErrorLogTests tests;
    return QTest::qExec(&tests, argc, argv);
}

#include "tst_runtime_error_log.moc"

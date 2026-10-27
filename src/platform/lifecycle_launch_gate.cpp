#include "platform/lifecycle_launch_gate.h"

#include "platform/application_identity.h"

#include <QDir>
#include <QElapsedTimer>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QThread>

#if defined(Q_OS_UNIX)
#include <cerrno>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace Licasa {
namespace {

bool trustedGateAncestry(const QString& directory, bool native)
{
#if defined(Q_OS_UNIX)
    if (QFileInfo(directory).canonicalFilePath() != QDir::cleanPath(directory)) {
        return false;
    }
    bool protectedByPrivateAncestor = false;
    QString current;
    for (const QString& component : directory.split('/', Qt::SkipEmptyParts)) {
        current += '/' + component;
        struct stat status {};
        const QByteArray path = QFile::encodeName(current);
        if (::lstat(path.constData(), &status) != 0 || !S_ISDIR(status.st_mode) ||
            (status.st_uid != 0 && (native || status.st_uid != ::geteuid()))) {
            return false;
        }
        if (!protectedByPrivateAncestor && (status.st_mode & 0022) != 0 &&
            !((status.st_mode & S_ISVTX) != 0 && status.st_uid == 0)) {
            return false;
        }
        if (!native && status.st_uid == ::geteuid() && (status.st_mode & 0077) == 0) {
            protectedByPrivateAncestor = true;
        }
    }
    return true;
#else
    Q_UNUSED(directory);
    Q_UNUSED(native);
    return false;
#endif
}

} // namespace

RuntimeStartupLock::~RuntimeStartupLock() { release(); }

void RuntimeStartupLock::release()
{
#if defined(Q_OS_LINUX)
    if (descriptor_ >= 0) {
        ::close(descriptor_);
        descriptor_ = -1;
    }
#endif
}

bool RuntimeStartupLock::acquire(const QString& path, QString* reason, int timeoutMs)
{
    const auto fail = [reason](const QString& message) {
        if (reason) {
            *reason = message;
        }
        return false;
    };
#if defined(Q_OS_LINUX)
    if (descriptor_ >= 0 || !QDir::isAbsolutePath(path) || QDir::cleanPath(path) != path ||
        path.contains(QChar('\0'))) {
        return fail(QStringLiteral("The runtime startup lock path is invalid."));
    }
    const QFileInfo info(path);
    const QString parent = info.absolutePath();
    if (info.fileName().isEmpty() || !trustedGateAncestry(parent, false)) {
        return fail(QStringLiteral("The runtime startup lock has unsafe parent directories."));
    }
    const QByteArray encodedParent = QFile::encodeName(parent);
    const QByteArray name = QFile::encodeName(info.fileName());
    const int directory =
        ::open(encodedParent.constData(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (directory < 0) {
        return fail(QStringLiteral("Cannot open the private runtime startup directory."));
    }
    int file = -1;
    const auto closeAndFail = [&](const QString& message) {
        if (file >= 0) {
            ::close(file);
        }
        ::close(directory);
        return fail(message);
    };
    const auto privateDirectory = [](const struct stat& status) {
        return S_ISDIR(status.st_mode) && status.st_uid == ::geteuid() &&
               (status.st_mode & 07777) == 0700;
    };
    const auto ownedFile = [](const struct stat& status, bool allowLegacy) {
        const mode_t permissions = status.st_mode & 07777;
        return S_ISREG(status.st_mode) && status.st_uid == ::geteuid() && status.st_nlink == 1 &&
               (permissions == 0600 || (allowLegacy && permissions == 0644));
    };
    struct stat parentStatus {};
    if (::fstat(directory, &parentStatus) != 0 || !privateDirectory(parentStatus)) {
        return closeAndFail(QStringLiteral("The runtime startup directory is not private."));
    }
    file = ::openat(directory, name.constData(),
                    O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK, 0600);
    const bool created = file >= 0;
    if (file < 0 && errno == EEXIST) {
        file = ::openat(directory, name.constData(), O_RDWR | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    }
    struct stat held {};
    if (file < 0 || ::fstat(file, &held) != 0 || !S_ISREG(held.st_mode) ||
        held.st_uid != ::geteuid() || held.st_nlink != 1) {
        return closeAndFail(QStringLiteral("The runtime startup lock is unsafe."));
    }
    // A restrictive inherited umask must not leave our newly created inode
    // inaccessible. Existing entries are never repaired before owning flock.
    if (created && ::fchmod(file, 0600) != 0) {
        return closeAndFail(QStringLiteral("Cannot protect the runtime startup lock."));
    }
    if (::fstat(file, &held) != 0 || !ownedFile(held, true)) {
        return closeAndFail(QStringLiteral("The runtime startup lock has unsafe permissions."));
    }
    QElapsedTimer elapsed;
    elapsed.start();
    const int boundedTimeout = qBound(0, timeoutMs, 2000);
    while (::flock(file, LOCK_EX | LOCK_NB) != 0) {
        const int lockError = errno;
        if (lockError != EWOULDBLOCK && lockError != EAGAIN && lockError != EINTR) {
            return closeAndFail(QStringLiteral("Cannot acquire the runtime startup lock."));
        }
        const qint64 remaining = boundedTimeout - elapsed.elapsed();
        if (remaining <= 0) {
            return closeAndFail(
                QStringLiteral("Another Licasa launch is in progress. Retry shortly."));
        }
        if (lockError != EINTR) {
            QThread::msleep(static_cast<unsigned long>(qMin(qint64(5), remaining)));
        }
    }
    const auto linkedInodeMatches = [&](bool allowLegacy) {
        struct stat currentParent {};
        struct stat retainedParent {};
        struct stat linked {};
        return ::lstat(encodedParent.constData(), &currentParent) == 0 &&
               ::fstat(directory, &retainedParent) == 0 && privateDirectory(currentParent) &&
               privateDirectory(retainedParent) && currentParent.st_dev == parentStatus.st_dev &&
               currentParent.st_ino == parentStatus.st_ino &&
               retainedParent.st_dev == parentStatus.st_dev &&
               retainedParent.st_ino == parentStatus.st_ino && ::fstat(file, &held) == 0 &&
               ownedFile(held, allowLegacy) &&
               ::fstatat(directory, name.constData(), &linked, AT_SYMLINK_NOFOLLOW) == 0 &&
               ownedFile(linked, allowLegacy) && linked.st_dev == held.st_dev &&
               linked.st_ino == held.st_ino;
    };
    if (!linkedInodeMatches(true)) {
        return closeAndFail(
            QStringLiteral("The runtime startup lock path changed. Retry the launch."));
    }
    // Old QLockFile versions used 0644. Migrate only an owned, still-linked
    // inode after taking its advisory lock; never disturb a live old owner.
    if ((held.st_mode & 07777) != 0600 && ::fchmod(file, 0600) != 0) {
        return closeAndFail(QStringLiteral("Cannot protect the legacy runtime startup lock."));
    }
    if (!linkedInodeMatches(false)) {
        return closeAndFail(
            QStringLiteral("The runtime startup lock path changed. Retry the launch."));
    }
    descriptor_ = file;
    ::close(directory);
    return true;
#else
    Q_UNUSED(path);
    Q_UNUSED(timeoutMs);
    return fail(QStringLiteral("Runtime startup coordination is unavailable on this platform."));
#endif
}

LifecycleLaunchGate::~LifecycleLaunchGate() { release(); }

void LifecycleLaunchGate::release()
{
#if defined(Q_OS_UNIX)
    if (inheritedDescriptor_ >= 0) {
        ::close(inheritedDescriptor_);
        inheritedDescriptor_ = -1;
    }
    if (descriptor_ >= 0) {
        ::close(descriptor_);
        descriptor_ = -1;
    }
#endif
}

bool LifecycleLaunchGate::acquire(QString* reason)
{
    const auto fail = [reason](const QString& message) {
        if (reason) {
            *reason = message;
        }
        return false;
    };
    const ApplicationIdentity identity = applicationIdentity(false);
    if (identity.packageFamily == QStringLiteral("snap") ||
        identity.packageFamily == QStringLiteral("portable")) {
        return true;
    }
    const QFileInfo root(identity.installationRoot);
    const bool nativeDeb = identity.executablePath == QStringLiteral("/usr/bin/licasa.bin") &&
                           QFileInfo(identity.executablePath).ownerId() == 0;
    const bool nativeAppImage =
        identity.executablePath == QStringLiteral("/opt/licasa/usr/bin/licasa") &&
        identity.installationRoot == QStringLiteral("/opt/licasa") &&
        QFileInfo(identity.executablePath).ownerId() == 0;
    const bool native = nativeDeb || nativeAppImage;
    const QString directory =
        native ? QStringLiteral("/var/lib/licasa/lifecycle/") +
                     (nativeDeb ? QStringLiteral("native") : QStringLiteral("appimage-native"))
               : QDir(root.absolutePath())
                     .filePath(QStringLiteral(".") + root.fileName() +
                               QStringLiteral(".licasa-lifecycle"));
    const QFileInfo info(directory);
    if (!info.exists() && !info.isSymLink()) {
        return native ? fail(QStringLiteral(
                            "The native installation requires package configuration or repair."))
                      : true;
    }
#if defined(Q_OS_UNIX)
    if (!trustedGateAncestry(directory, native)) {
        return fail(QStringLiteral("The installation update gate has unsafe parent directories."));
    }
    const QByteArray encoded = QFile::encodeName(directory);
    const int dir = ::open(encoded.constData(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (dir < 0) {
        return fail(QStringLiteral("Cannot inspect the installation update gate."));
    }
    struct stat status {};
    const uid_t expectedOwner = native ? uid_t(0) : ::geteuid();
    const mode_t expectedMode = native ? mode_t(0755) : mode_t(0700);
    if (::fstat(dir, &status) != 0 || status.st_uid != expectedOwner ||
        (status.st_mode & 07777) != expectedMode) {
        ::close(dir);
        return fail(QStringLiteral("The installation update gate is not private."));
    }
    descriptor_ = ::openat(dir, "lock", O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (descriptor_ < 0 || ::fstat(descriptor_, &status) != 0 || !S_ISREG(status.st_mode) ||
        status.st_uid != expectedOwner || (status.st_mode & (native ? 0022 : 0077)) != 0 ||
        ::flock(descriptor_, LOCK_SH | LOCK_NB) != 0) {
        ::close(dir);
        release();
        return fail(QStringLiteral("Licasa is being updated. Retry after installation completes."));
    }
    bool descriptorValid = false;
    const int inherited = qEnvironmentVariableIntValue("LICASA_LAUNCH_GATE_FD", &descriptorValid);
    if (descriptorValid && inherited > 2 && inherited != descriptor_) {
        struct stat inheritedStatus {};
        if (::fstat(inherited, &inheritedStatus) != 0 || inheritedStatus.st_dev != status.st_dev ||
            inheritedStatus.st_ino != status.st_ino) {
            ::close(dir);
            release();
            return fail(
                QStringLiteral("The launcher update gate does not match this installation."));
        }
        inheritedDescriptor_ = inherited;
        qunsetenv("LICASA_LAUNCH_GATE_FD");
    }
    const int journal = ::openat(dir, "transaction.json", O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    const int journalError = errno;
    ::close(dir);
    if (journal < 0) {
        if (journalError == ENOENT) {
            return true;
        }
        release();
        return fail(QStringLiteral("Cannot inspect the update recovery record."));
    }
    QFile file;
    if (::fstat(journal, &status) != 0 || !S_ISREG(status.st_mode) ||
        status.st_uid != expectedOwner || (status.st_mode & (native ? 0022 : 0077)) != 0 ||
        status.st_size > 1024 * 1024 ||
        !file.open(journal, QIODevice::ReadOnly, QFileDevice::AutoCloseHandle)) {
        ::close(journal);
        release();
        return fail(QStringLiteral("The update recovery record is unsafe."));
    }
    QJsonParseError error;
    const auto document = QJsonDocument::fromJson(file.read(1024 * 1024 + 1), &error);
    if (error.error != QJsonParseError::NoError || !document.isObject() ||
        document.object().value(QStringLiteral("state")).toString() != QStringLiteral("verified")) {
        release();
        return fail(QStringLiteral(
            "An interrupted Licasa update needs recovery. Run the same installer again."));
    }
    return true;
#else
    return fail(
        QStringLiteral("Installation update coordination is unavailable on this platform."));
#endif
}

} // namespace Licasa

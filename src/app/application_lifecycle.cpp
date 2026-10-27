#include "app/application_lifecycle.h"

#include "app/application_settings.h"
#include "app/window_manager.h"

#include <QCoreApplication>
#include <QDebug>
#include <QSocketNotifier>

#ifdef Q_OS_UNIX
#include <cerrno>
#include <csignal>
#include <fcntl.h>
#include <unistd.h>
#endif

namespace Licasa {
namespace {
#ifdef Q_OS_UNIX
volatile sig_atomic_t terminationWriteDescriptor = -1;

void relayTermination(int signal)
{
    const int savedError = errno;
    const int descriptor = terminationWriteDescriptor;
    if (descriptor >= 0) {
        const unsigned char value = static_cast<unsigned char>(signal);
        const ssize_t written = ::write(descriptor, &value, sizeof(value));
        (void)written;
    }
    errno = savedError;
}
#endif
} // namespace

struct ApplicationLifecycle::TerminationBridge {
#ifdef Q_OS_UNIX
    int descriptors[2]{-1, -1};
    struct sigaction previousTerm {};
    struct sigaction previousInterrupt {};
    bool installed = false;
    std::unique_ptr<QSocketNotifier> notifier;

    ~TerminationBridge()
    {
        notifier.reset();
        if (installed) {
            terminationWriteDescriptor = -1;
            ::sigaction(SIGTERM, &previousTerm, nullptr);
            ::sigaction(SIGINT, &previousInterrupt, nullptr);
        }
        for (const int descriptor : descriptors) {
            if (descriptor >= 0) {
                ::close(descriptor);
            }
        }
    }
#endif
};

ApplicationLifecycle::ApplicationLifecycle(BackgroundModeManager* backgroundModeManager,
                                           WindowManager* windowManager, QObject* parent)
    : QObject(parent), backgroundModeManager_(backgroundModeManager), windowManager_(windowManager),
      snap_(qEnvironmentVariableIsSet("SNAP_NAME"))
{
    clock_.start();
    idleTimer_.setSingleShot(true);
    connect(&idleTimer_, &QTimer::timeout, this, &ApplicationLifecycle::observeState);
    prepareTimer_.setSingleShot(true);
    connect(&prepareTimer_, &QTimer::timeout, this,
            [this] { (void)command(QStringLiteral("cancel-update")); });
    if (backgroundModeManager_) {
        connect(backgroundModeManager_, &BackgroundModeManager::enabledChanged, this,
                &ApplicationLifecycle::observeState);
    }
    if (windowManager_) {
        connect(windowManager_, &WindowManager::lifecycleStateChanged, this,
                &ApplicationLifecycle::observeState);
        connect(windowManager_, &WindowManager::quitRequested, this,
                &ApplicationLifecycle::requestQuit);
    }
    installTerminationBridge();
    // Composition creates the initial window before this first observation.
    QTimer::singleShot(0, this, &ApplicationLifecycle::observeState);
}

ApplicationLifecycle::~ApplicationLifecycle() = default;

bool ApplicationLifecycle::launchAllowed() const { return !prepared_ && !stopping_; }

bool ApplicationLifecycle::prepared() const { return prepared_; }

QJsonObject ApplicationLifecycle::status() const
{
    return {{QStringLiteral("visibleWindows"),
             windowManager_ ? windowManager_->visibleWindowCount() : 0},
            {QStringLiteral("busyReason"),
             windowManager_ ? windowManager_->lifecycleBusyReason() : QString{}},
            {QStringLiteral("backgroundEnabled"),
             backgroundModeManager_ && backgroundModeManager_->enabled()},
            {QStringLiteral("state"), stopping_   ? QStringLiteral("stopping")
                                      : prepared_ ? QStringLiteral("prepared")
                                                  : QStringLiteral("running")},
            {QStringLiteral("idleRemainingMs"), idlePolicy_.remainingMs(clock_.elapsed())},
            {QStringLiteral("prepareLeaseRemainingMs"),
             prepareDeadlineMs_ < 0 ? -1 : qMax<qint64>(0, prepareDeadlineMs_ - clock_.elapsed())}};
}

QJsonObject ApplicationLifecycle::command(QString operation)
{
    QJsonObject response = status();
    if (operation == QStringLiteral("status") || operation == QStringLiteral("health")) {
        response.insert(QStringLiteral("result"), QStringLiteral("ready"));
        return response;
    }
    if (stopping_) {
        response.insert(QStringLiteral("result"), QStringLiteral("busy"));
        return response;
    }
    if (operation == QStringLiteral("quit")) {
        const QString reason = windowManager_ ? windowManager_->userWorkBusyReason() : QString{};
        if (!reason.isEmpty()) {
            response.insert(QStringLiteral("result"), QStringLiteral("busy"));
            response.insert(QStringLiteral("busyReason"), reason);
            return response;
        }
        beginExit();
        response = status();
        response.insert(QStringLiteral("result"), QStringLiteral("accepted"));
        return response;
    }
    if (operation == QStringLiteral("cancel-update")) {
        prepared_ = false;
        prepareDeadlineMs_ = -1;
        prepareTimer_.stop();
        if (windowManager_) {
            windowManager_->setLaunchesAllowed(true);
        }
        emit stateChanged();
        observeState();
    } else if (operation == QStringLiteral("prepare-update") ||
               operation == QStringLiteral("stop-for-update")) {
        if (windowManager_ && !windowManager_->lifecycleBusyReason().isEmpty()) {
            response.insert(QStringLiteral("result"), QStringLiteral("busy"));
            return response;
        }
        if (operation == QStringLiteral("stop-for-update") && !prepared_) {
            response.insert(QStringLiteral("result"), QStringLiteral("busy"));
            response.insert(QStringLiteral("busyReason"),
                            QStringLiteral("prepare-update is required"));
            return response;
        }
        if (!prepared_) {
            // An abandoned direct control request must not strand launch
            // admission. Installer transaction gates have their own recovery.
            prepareDeadlineMs_ = clock_.elapsed() + 30000;
            prepareTimer_.start(30000);
        }
        prepared_ = true;
        idleTimer_.stop();
        if (windowManager_) {
            windowManager_->setLaunchesAllowed(false);
            windowManager_->quiesceForLifecycle();
        }
        emit stateChanged();
        if (operation == QStringLiteral("stop-for-update")) {
            beginExit();
        }
    } else {
        response.insert(QStringLiteral("result"), QStringLiteral("unsupported"));
        return response;
    }
    response = status();
    response.insert(QStringLiteral("result"), operation == QStringLiteral("stop-for-update")
                                                  ? QStringLiteral("accepted")
                                                  : QStringLiteral("ready"));
    return response;
}

void ApplicationLifecycle::observeState()
{
    if (stopping_ || prepared_) {
        return;
    }
    const qint64 now = clock_.elapsed();
    const bool due =
        idlePolicy_.observe(now, windowManager_ ? windowManager_->visibleWindowCount() : 0,
                            backgroundModeManager_ && backgroundModeManager_->enabled(), snap_);
    idleTimer_.stop();
    if (due) {
        if (!windowManager_ || windowManager_->lifecycleBusyReason().isEmpty()) {
            beginExit();
            return;
        }
        // Busy work does not move the deadline; retry after safe completion.
        idleTimer_.start(100);
    } else {
        const qint64 remaining = idlePolicy_.remainingMs(now);
        if (remaining >= 0) {
            idleTimer_.start(int(qMax<qint64>(1, remaining)));
        }
    }
    emit stateChanged();
}

void ApplicationLifecycle::requestQuit()
{
    if (windowManager_ && !windowManager_->userWorkBusyReason().isEmpty()) {
        emit windowManager_->quitBlocked(windowManager_->userWorkBusyReason());
        return;
    }
    beginExit();
}

void ApplicationLifecycle::beginExit()
{
    if (stopping_) {
        return;
    }
    stopping_ = true;
    idleTimer_.stop();
    prepareTimer_.stop();
    if (windowManager_) {
        windowManager_->setLaunchesAllowed(false);
        windowManager_->quiesceForLifecycle();
    }
    emit stateChanged();
    emit exitRequested();
    // Return the command acknowledgment before entering the existing normal
    // shutdown path. The controller must separately verify process exit.
    // QGuiApplication::quit asks visible windows to close; a persistent shell
    // intentionally rejects that request to hide into background mode. Safety
    // has already been checked, so exit through aboutToQuit without that veto.
    QTimer::singleShot(0, QCoreApplication::instance(), [] { QCoreApplication::exit(0); });
}

void ApplicationLifecycle::installTerminationBridge()
{
#ifdef Q_OS_UNIX
    if (terminationWriteDescriptor >= 0) {
        return;
    }
    auto bridge = std::make_unique<TerminationBridge>();
    if (::pipe(bridge->descriptors) != 0) {
        qWarning() << "Could not create the safe termination bridge.";
        return;
    }
    for (const int descriptor : bridge->descriptors) {
        const int status = ::fcntl(descriptor, F_GETFL);
        const int flags = ::fcntl(descriptor, F_GETFD);
        if (status < 0 || flags < 0 || ::fcntl(descriptor, F_SETFL, status | O_NONBLOCK) < 0 ||
            ::fcntl(descriptor, F_SETFD, flags | FD_CLOEXEC) < 0) {
            qWarning() << "Could not configure the safe termination bridge.";
            return;
        }
    }
    struct sigaction action {};
    action.sa_handler = relayTermination;
    ::sigemptyset(&action.sa_mask);
    action.sa_flags = SA_RESTART;
    if (::sigaction(SIGTERM, &action, &bridge->previousTerm) != 0) {
        return;
    }
    if (::sigaction(SIGINT, &action, &bridge->previousInterrupt) != 0) {
        ::sigaction(SIGTERM, &bridge->previousTerm, nullptr);
        return;
    }
    bridge->installed = true;
    terminationWriteDescriptor = bridge->descriptors[1];
    bridge->notifier =
        std::make_unique<QSocketNotifier>(bridge->descriptors[0], QSocketNotifier::Read);
    connect(bridge->notifier.get(), &QSocketNotifier::activated, this, [this] {
        unsigned char buffer[64];
        while (::read(terminationBridge_->descriptors[0], buffer, sizeof(buffer)) > 0) {
        }
        requestQuit();
    });
    terminationBridge_ = std::move(bridge);
#endif
}

} // namespace Licasa

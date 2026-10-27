#pragma once

#include <QElapsedTimer>
#include <QJsonObject>
#include <QObject>
#include <QTimer>

#include <memory>

namespace Licasa {

class BackgroundModeManager;
class WindowManager;

// The caller supplies monotonic time. Probes and optional work observe the same
// deadline; only a visible user window starts a new warm period.
class BackgroundIdlePolicy final {
  public:
    static constexpr qint64 warmPeriodMs = 60000;

    bool observe(qint64 nowMs, int visibleWindows, bool backgroundEnabled, bool finiteWarmth)
    {
        if (visibleWindows > 0) {
            deadlineMs_ = -1;
            return false;
        }
        if (!backgroundEnabled) {
            deadlineMs_ = nowMs;
            return true;
        }
        if (!finiteWarmth) {
            deadlineMs_ = -1;
            return false;
        }
        if (deadlineMs_ < 0) {
            deadlineMs_ = nowMs + warmPeriodMs;
        }
        return nowMs >= deadlineMs_;
    }

    qint64 remainingMs(qint64 nowMs) const
    {
        return deadlineMs_ < 0 ? -1 : qMax<qint64>(0, deadlineMs_ - nowMs);
    }

  private:
    qint64 deadlineMs_ = -1;
};

class ApplicationLifecycle final : public QObject {
    Q_OBJECT
    Q_PROPERTY(bool prepared READ prepared NOTIFY stateChanged)

  public:
    ApplicationLifecycle(BackgroundModeManager* backgroundModeManager, WindowManager* windowManager,
                         QObject* parent = nullptr);
    ~ApplicationLifecycle() override;

    QJsonObject status() const;
    QJsonObject command(QString operation);
    bool launchAllowed() const;
    bool prepared() const;

  signals:
    void stateChanged();
    void exitRequested();

  private:
    void observeState();
    void requestQuit();
    void beginExit();
    void installTerminationBridge();
    struct TerminationBridge;

    BackgroundModeManager* backgroundModeManager_ = nullptr;
    WindowManager* windowManager_ = nullptr;
    QElapsedTimer clock_;
    QTimer idleTimer_;
    QTimer prepareTimer_;
    BackgroundIdlePolicy idlePolicy_;
    std::unique_ptr<TerminationBridge> terminationBridge_;
    bool snap_ = false;
    bool prepared_ = false;
    bool stopping_ = false;
    qint64 prepareDeadlineMs_ = -1;
};

} // namespace Licasa

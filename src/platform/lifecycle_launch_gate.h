#pragma once

#include <QString>

namespace Licasa {

// Ephemeral startup serialization. The private runtime inode is retained;
// kernel flock ownership, rather than durable PID contents, releases on exit.
class RuntimeStartupLock final {
  public:
    RuntimeStartupLock() = default;
    ~RuntimeStartupLock();
    RuntimeStartupLock(const RuntimeStartupLock&) = delete;
    RuntimeStartupLock& operator=(const RuntimeStartupLock&) = delete;
    bool acquire(const QString& path, QString* reason, int timeoutMs = 2000);
    void release();

  private:
    int descriptor_ = -1;
};

// Shared with tools/lifecycle_update.py. Held only during startup, so a live
// viewer can accept a stop while an installer owns the exclusive gate.
class LifecycleLaunchGate final {
  public:
    LifecycleLaunchGate() = default;
    ~LifecycleLaunchGate();
    LifecycleLaunchGate(const LifecycleLaunchGate&) = delete;
    LifecycleLaunchGate& operator=(const LifecycleLaunchGate&) = delete;
    bool acquire(QString* reason);
    void release();

  private:
    int descriptor_ = -1;
    int inheritedDescriptor_ = -1;
};

} // namespace Licasa

#pragma once

#include <QByteArray>
#include <QHash>
#include <QMutex>
#include <QMutexLocker>
#include <QString>
#include <QWaitCondition>

#include <atomic>
#include <memory>

namespace Licasa {

struct ParallelDecodePair {
    QMutex mutex;
    QWaitCondition previewFinishedCondition;
    std::atomic_bool previewPresented = false;
    int maximumMegapixels = 0;
    bool fullOwnsGate = false;
    bool previewBypassedGate = false;
    bool previewFinished = false;

    void finishPreview(bool noFrameWillBePresented)
    {
        QMutexLocker lock(&mutex);
        previewFinished = true;
        if (noFrameWillBePresented) {
            previewPresented.store(true, std::memory_order_release);
        }
        previewFinishedCondition.wakeAll();
    }
};

// A pair ID names one attempt within the current picture-resource scope.
// Keep its marker and role claims until releasePictureResources() cancels that
// scope: a preview can be presented or cancelled before its full request arrives.
// At capacity, new requests use the ordinary serial path rather than evicting a
// pair whose counterpart or presentation event may still arrive.
class ParallelDecodePairRegistry final {
  public:
    enum class Role { Preview, Full };
    static constexpr qsizetype maximumPairs = 128;
    static constexpr qsizetype maximumIdLength = 128;

    std::shared_ptr<ParallelDecodePair> claim(const QString& id, const QByteArray& fingerprint,
                                              Role role)
    {
        if (id.isEmpty() || id.size() > maximumIdLength || fingerprint.size() != 32) {
            return {};
        }
        QMutexLocker lock(&mutex_);
        auto found = pairs_.find(id);
        if (found == pairs_.end()) {
            if (pairs_.size() >= maximumPairs) {
                return {};
            }
            found = pairs_.insert(id, {fingerprint, std::make_shared<ParallelDecodePair>()});
        }
        if (found->fingerprint != fingerprint) {
            return {};
        }
        bool& claimed = role == Role::Preview ? found->previewClaimed : found->fullClaimed;
        if (claimed) {
            return {};
        }
        claimed = true;
        return found->pair;
    }

    void markPresented(const QString& id)
    {
        QMutexLocker lock(&mutex_);
        const auto found = pairs_.constFind(id);
        if (found != pairs_.cend()) {
            found->pair->previewPresented.store(true, std::memory_order_release);
        }
    }

    void clear()
    {
        QMutexLocker lock(&mutex_);
        pairs_.clear();
    }

  private:
    struct Entry {
        QByteArray fingerprint;
        std::shared_ptr<ParallelDecodePair> pair;
        bool previewClaimed = false;
        bool fullClaimed = false;
    };
    QMutex mutex_;
    QHash<QString, Entry> pairs_;
};

} // namespace Licasa

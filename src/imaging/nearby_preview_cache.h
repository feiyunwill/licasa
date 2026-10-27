#pragma once

#include "io/external_file_identity.h"

#include <QDateTime>
#include <QHash>
#include <QImage>
#include <QMutex>
#include <QSet>
#include <QSize>
#include <QStringList>
#include <QUrl>
#include <QWaitCondition>

#include <atomic>
#include <memory>
#include <optional>

class QThreadPool;

namespace Licasa {

class ImageResourcePolicy;

// Bounded neighboring rasters are independent of selected-image decoding.
// The provider drains the owning worker pool before releasing this cache.
struct NearbyPreviewCache : std::enable_shared_from_this<NearbyPreviewCache> {
    void cancelActiveHeavyCodec();
    void clear();
    QImage lookup(const QString& path);
    void waitForActivePng(const QString& path, const std::atomic_bool* selectedCancelled);
    bool setWindow(const QList<QUrl>& urls, const QSize& requested, int memoryMiB);
    void run(ImageResourcePolicy& policy, QThreadPool& pool);
    qsizetype count() const;
    qsizetype retainedBytes() const;
    quint64 hits() const;

  private:
    struct FileStamp {
        QString canonicalPath;
        qint64 size = -1;
        QDateTime modified;
        QDateTime changed;
        std::optional<ExternalFileIdentity> nativeIdentity;

        bool isValid() const { return size >= 0 && !canonicalPath.isEmpty(); }
    };

    static FileStamp fileStamp(const QString& path);
    static bool sameFileStamp(const FileStamp& left, const FileStamp& right);
    static bool coversRequestedSize(const QSize& prepared, const QSize& requested);
    void cancelActive();

    struct Entry {
        QImage image;
        FileStamp stamp;
        QSize preparedFor;
    };

    mutable QMutex mutex;
    QWaitCondition activeFinished;
    QStringList desired;
    QHash<QString, Entry> entries;
    QSet<QString> attempted;
    QSize requestedSize;
    qsizetype byteLimit = 0;
    qsizetype slotLimit = 0;
    qsizetype bytes = 0;
    quint64 hitCount = 0;
    std::shared_ptr<std::atomic_bool> activeCancellation;
    QString activePath;
    QSize activeRequestedSize;
    bool workerRunning = false;
};

} // namespace Licasa

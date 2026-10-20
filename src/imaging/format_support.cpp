#include "imaging/format_support.h"
#include <QDebug>
#include <QDir>
#include <QDirIterator>
#include <QElapsedTimer>
#include <QFileInfo>
#include <QImageReader>
#include <QImageWriter>
#include <QMetaObject>
#include <QSet>
#include <algorithm>
#include <utility>

namespace Licasa {
namespace {
bool lessFileName(const QString& left, const QString& right)
{
    const int comparison = left.localeAwareCompare(right);
    return comparison < 0 || (comparison == 0 && left < right);
}

QStringList scanImageNames(const QString& directory, const QSet<QString>& extensions,
                           const std::atomic<quint64>* generation = nullptr,
                           quint64 expectedGeneration = 0)
{
    QStringList names;
    QDirIterator entries(directory, QDir::Files | QDir::Readable | QDir::NoDotAndDotDot);
    while (entries.hasNext()) {
        if (generation && generation->load(std::memory_order_relaxed) != expectedGeneration) {
            return {};
        }
        entries.next();
        const QFileInfo entry = entries.fileInfo();
        if (extensions.contains(entry.suffix().toLower())) {
            names.append(entry.fileName());
        }
    }
    std::sort(names.begin(), names.end(), lessFileName);
    return names;
}
} // namespace

FormatSupport::FormatSupport(QObject* parent) : QObject(parent)
{
    indexPool_.setMaxThreadCount(1);
    indexPool_.setExpiryTimeout(1000);
    QSet<QString> patterns;
    const QList<QByteArray> formats = QImageReader::supportedImageFormats();

    for (const QByteArray& formatBytes : formats) {
        const QString format = QString::fromLatin1(formatBytes).trimmed().toLower();
        if (format.isEmpty()) {
            continue;
        }

        const auto addExtension = [this, &patterns](const QString& extension) {
            readableExtensions_.insert(extension);
            patterns.insert(QStringLiteral("*.") + extension);
        };

        if (format == QStringLiteral("jpeg") || format == QStringLiteral("jpg")) {
            addExtension(QStringLiteral("jpg"));
            addExtension(QStringLiteral("jpeg"));
        } else if (format == QStringLiteral("tif") || format == QStringLiteral("tiff")) {
            addExtension(QStringLiteral("tif"));
            addExtension(QStringLiteral("tiff"));
        } else if (format == QStringLiteral("svg") || format == QStringLiteral("svgz")) {
            addExtension(QStringLiteral("svg"));
            addExtension(QStringLiteral("svgz"));
        } else {
            addExtension(format);
        }
    }

    QStringList allPatterns = patterns.values();
    std::sort(allPatterns.begin(), allPatterns.end(),
              [](const QString& left, const QString& right) {
                  return left.localeAwareCompare(right) < 0;
              });

    if (!allPatterns.isEmpty()) {
        nameFilters_ << QStringLiteral("Images (%1)").arg(allPatterns.join(' '));
    }
    nameFilters_ << QStringLiteral("All Files (*)");

    QSet<QString> writablePatterns;
    const QList<QByteArray> writableFormats = QImageWriter::supportedImageFormats();
    for (QByteArray formatBytes : writableFormats) {
        const QString format = QString::fromLatin1(formatBytes).trimmed().toLower();
        if (format == QStringLiteral("jpeg") || format == QStringLiteral("jpg")) {
            writablePatterns.insert(QStringLiteral("*.jpg"));
            writablePatterns.insert(QStringLiteral("*.jpeg"));
        } else if (!format.isEmpty()) {
            writablePatterns.insert(QStringLiteral("*.") + format);
        }
    }

    const auto hasWritablePattern = [&writablePatterns](const QString& pattern) {
        return writablePatterns.contains(pattern);
    };
    if (hasWritablePattern(QStringLiteral("*.png"))) {
        saveNameFilters_ << QStringLiteral("PNG image (*.png)");
    }
    if (hasWritablePattern(QStringLiteral("*.jpg"))) {
        saveNameFilters_ << QStringLiteral("JPEG image (*.jpg *.jpeg)");
    }
    if (hasWritablePattern(QStringLiteral("*.webp"))) {
        saveNameFilters_ << QStringLiteral("WebP image (*.webp)");
    }

    QStringList allWritablePatterns = writablePatterns.values();
    std::sort(allWritablePatterns.begin(), allWritablePatterns.end(),
              [](const QString& left, const QString& right) {
                  return left.localeAwareCompare(right) < 0;
              });
    if (!allWritablePatterns.isEmpty()) {
        saveNameFilters_
            << QStringLiteral("All writable images (%1)").arg(allWritablePatterns.join(' '));
    }
}

FormatSupport::~FormatSupport()
{
    indexGeneration_.fetch_add(1, std::memory_order_relaxed);
    indexPool_.clear();
    indexPool_.waitForDone();
}

QStringList FormatSupport::nameFilters() const { return nameFilters_; }

QStringList FormatSupport::saveNameFilters() const { return saveNameFilters_; }

bool FormatSupport::canOpen(const QUrl& url) const
{
    if (!url.isValid() || !url.isLocalFile()) {
        return false;
    }

    const QFileInfo fileInfo(url.toLocalFile());
    if (!fileInfo.exists() || !fileInfo.isFile()) {
        return false;
    }

    const QString suffix = fileInfo.suffix().trimmed().toLower();
    if (!suffix.isEmpty() && readableExtensions_.contains(suffix)) {
        // Known registered formats are admitted by extension. Calling
        // QImageReader::canRead() here can execute a native decoder's metadata
        // path synchronously on the UI thread; camera RAW plugins in particular
        // may open LibRaw before the asynchronous provider has even been queued.
        return true;
    }

    // Preserve best-effort support for extensionless or mislabeled images. The
    // potentially expensive decoder probe is used only when the cheap registry
    // admission above cannot decide.
    QImageReader reader(fileInfo.absoluteFilePath());
    return reader.canRead();
}

void FormatSupport::prepareDirectoryIndex(const QUrl& current)
{
    if (!current.isLocalFile()) {
        return;
    }
    const QFileInfo file(current.toLocalFile());
    if (!file.isFile()) {
        return;
    }
    const QString directory = file.absolutePath();
    const QDateTime modified = QFileInfo(directory).lastModified();
    if ((indexedDirectory_ == directory && indexedDirectoryModified_ == modified) ||
        (preparingDirectory_ == directory && preparingDirectoryModified_ == modified)) {
        return;
    }

    preparingDirectory_ = directory;
    preparingDirectoryModified_ = modified;
    const quint64 generation = indexGeneration_.fetch_add(1, std::memory_order_relaxed) + 1;
    indexPool_.clear();
    const QSet<QString> extensions = readableExtensions_;
    indexPool_.start([this, current, directory, modified, extensions, generation] {
        const bool trace = qEnvironmentVariableIsSet("LICASA_TRACE_DIRECTORY_INDEX");
        QElapsedTimer elapsed;
        if (trace) {
            elapsed.start();
        }
        QStringList names = scanImageNames(directory, extensions, &indexGeneration_, generation);
        if (indexGeneration_.load(std::memory_order_relaxed) != generation) {
            return;
        }
        if (trace) {
            qInfo().noquote() << "[Licasa] Background directory index:" << names.size()
                              << "images in" << elapsed.elapsed() << "ms";
        }
        QMetaObject::invokeMethod(
            this,
            [this, current, directory, modified, generation, names = std::move(names)]() mutable {
                if (indexGeneration_.load(std::memory_order_relaxed) != generation) {
                    return;
                }
                preparingDirectory_.clear();
                preparingDirectoryModified_ = {};
                if (QFileInfo(directory).lastModified() != modified) {
                    prepareDirectoryIndex(current);
                    return;
                }
                indexedDirectory_ = directory;
                indexedDirectoryModified_ = modified;
                indexedNames_ = std::move(names);
                emit directoryIndexReady();
            },
            Qt::QueuedConnection);
    });
}

bool FormatSupport::hasDirectoryIndex(const QUrl& current) const
{
    if (!current.isLocalFile()) {
        return false;
    }
    const QString directory = QFileInfo(current.toLocalFile()).absolutePath();
    return indexedDirectory_ == directory &&
           indexedDirectoryModified_ == QFileInfo(directory).lastModified();
}

const QStringList& FormatSupport::imagesInDirectory(const QString& directory) const
{
    const QDateTime modified = QFileInfo(directory).lastModified();
    if (indexedDirectory_ == directory && indexedDirectoryModified_ == modified) {
        return indexedNames_;
    }

    indexedDirectory_ = directory;
    indexedDirectoryModified_ = modified;
    const bool trace = qEnvironmentVariableIsSet("LICASA_TRACE_DIRECTORY_INDEX");
    QElapsedTimer elapsed;
    if (trace) {
        elapsed.start();
    }
    indexedNames_ = scanImageNames(directory, readableExtensions_);
    if (trace) {
        qInfo().noquote() << "[Licasa] Synchronous directory index:" << indexedNames_.size()
                          << "images in" << elapsed.elapsed() << "ms";
    }
    return indexedNames_;
}

QList<QUrl> FormatSupport::nearbyImages(const QUrl& current, int radius) const
{
    return nearbyImages(current, radius, 0);
}

QList<QUrl> FormatSupport::nearbyImages(const QUrl& current, int radius,
                                        int preferredDirection) const
{
    return nearbyImagesImpl(current, radius, preferredDirection, true);
}

QList<QUrl> FormatSupport::nearbyImagesImpl(const QUrl& current, int radius, int preferredDirection,
                                            bool deferWhileIndexing) const
{
    if (!current.isLocalFile() || radius <= 0) {
        return {};
    }

    const QFileInfo currentFile(current.toLocalFile());
    if (!currentFile.isFile()) {
        return {};
    }

    const QString directory = currentFile.absolutePath();
    if (deferWhileIndexing && directory == preparingDirectory_ &&
        QFileInfo(directory).lastModified() == preparingDirectoryModified_ &&
        (indexedDirectory_ != directory ||
         indexedDirectoryModified_ != preparingDirectoryModified_)) {
        return {};
    }
    const QStringList& indexed = imagesInDirectory(directory);
    const QString currentName = currentFile.fileName();
    QStringList additional;
    const QStringList* names = &indexed;
    auto currentIt = std::lower_bound(indexed.cbegin(), indexed.cend(), currentName, lessFileName);
    if (currentIt == indexed.cend() || *currentIt != currentName) {
        if (!canOpen(current)) {
            return {};
        }
        additional = indexed;
        additional.insert(int(currentIt - indexed.cbegin()), currentName);
        names = &additional;
        currentIt = names->cbegin() + (currentIt - indexed.cbegin());
    }

    const int count = names->size();
    if (count < 2) {
        return {};
    }
    const int index = int(currentIt - names->cbegin());
    QList<QUrl> nearby;
    nearby.reserve(int(std::min<qint64>(count - 1, qint64(radius) * 2)));
    const auto appendNeighbor = [&](int distance, int direction) {
        const int neighbor =
            direction > 0 ? (index + distance) % count : (index - distance + count) % count;
        const QUrl url = QUrl::fromLocalFile(QDir(directory).absoluteFilePath(names->at(neighbor)));
        if (!nearby.contains(url)) {
            nearby.append(url);
        }
    };
    if (preferredDirection != 0) {
        for (const int direction :
             {preferredDirection > 0 ? 1 : -1, preferredDirection > 0 ? -1 : 1}) {
            for (int distance = 1; distance <= radius && nearby.size() < count - 1; ++distance) {
                appendNeighbor(distance, direction);
            }
        }
    } else {
        for (int distance = 1; distance <= radius && nearby.size() < count - 1; ++distance) {
            for (const int direction : {1, -1}) {
                appendNeighbor(distance, direction);
            }
        }
    }
    return nearby;
}

QUrl FormatSupport::adjacentImage(const QUrl& current, int direction) const
{
    if (direction == 0) {
        return {};
    }
    const QList<QUrl> nearby = nearbyImagesImpl(current, 1, 0, false);
    if (nearby.isEmpty()) {
        return {};
    }
    return direction > 0 || nearby.size() == 1 ? nearby.first() : nearby.at(1);
}

} // namespace Licasa

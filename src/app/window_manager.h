#pragma once

#include <QHash>
#include <QObject>
#include <QPointer>
#include <QQmlComponent>
#include <QUrl>
#include <QVariantMap>

#include <memory>

class QQmlApplicationEngine;
class QQuickWindow;

namespace Licasa {

class ImageResourcePolicy;
class AsyncImageProvider;
class AnimationExportService;
class MotionPhotoExportService;
class PreferredCoverFrameCoordinator;
struct WindowMediaState;
class ImageSaveService;
class MotionPhotoResourcePolicy;

class WindowSession final : public QObject {
    Q_OBJECT
    Q_PROPERTY(QUrl initialImageUrl READ initialImageUrl CONSTANT)
    Q_PROPERTY(bool startHidden READ startHidden CONSTANT)
    Q_PROPERTY(bool persistentShell READ persistentShell CONSTANT)

  public:
    WindowSession(QUrl initialImageUrl, bool startHidden, bool persistentShell,
                  QObject* parent = nullptr);

    QUrl initialImageUrl() const;
    bool startHidden() const;
    bool persistentShell() const;
    void requestOpen(const QUrl& url);

  signals:
    void openRequested(const QUrl& url);

  private:
    QUrl initialImageUrl_;
    bool startHidden_ = false;
    bool persistentShell_ = false;
};

class WindowManager final : public QObject {
    Q_OBJECT
    Q_PROPERTY(QUrl defaultImageUrl READ defaultImageUrl CONSTANT)

  public:
    WindowManager(QQmlApplicationEngine* engine, QVariantMap sharedWindowProperties,
                  QObject* parent = nullptr);
    ~WindowManager() override;

    QUrl defaultImageUrl() const;

    void setMotionPhotoResourcePolicy(MotionPhotoResourcePolicy* resourcePolicy) noexcept;
    void setImageResourcePolicy(ImageResourcePolicy* resourcePolicy) noexcept;
    void setImageProvider(AsyncImageProvider* provider) noexcept;
    void setImageSaveService(ImageSaveService* saveService) noexcept;

    int visibleWindowCount() const;
    QString userWorkBusyReason() const;
    QString lifecycleBusyReason() const;
    bool launchesAllowed() const;
    void setLaunchesAllowed(bool allowed);
    void quiesceForLifecycle();

    Q_INVOKABLE bool createWindow(const QUrl& initialUrl = QUrl(), bool startHidden = false,
                                  bool persistentShell = false);
    Q_INVOKABLE bool openInNewWindow(const QUrl& url);
    Q_INVOKABLE QObject* ensureMotionPhotoExportService(QObject* windowObject);
    Q_INVOKABLE QObject* ensureAnimationExportService(QObject* windowObject);
    Q_INVOKABLE QObject* ensurePreferredCoverFrameCoordinator(QObject* windowObject);
    Q_INVOKABLE void releasePictureResources();
    Q_INVOKABLE void releasePictureResourcesForNavigation(const QUrl& nextImage);
    Q_INVOKABLE bool hasDecodedFullBase(const QUrl& url) const;
    Q_INVOKABLE void prepareNearbyPreviews(const QList<QUrl>& urls, const QSize& requestedSize,
                                           int maximumImageMemoryMiB);
    Q_INVOKABLE void markParallelPreviewPresented(const QString& pairId);
    Q_INVOKABLE QVariantMap sampleDisplayedPixel(QObject* windowObject, qreal x, qreal y) const;
    Q_INVOKABLE void destroyWindow(QObject* object);
    Q_INVOKABLE void destroyAllWindows();
    Q_INVOKABLE void quitApplication();

    void showAnyWindow();

  signals:
    void navigationKeyReleased(QObject* window, int direction);
    void initialGraphicsFailed(const QString& message);
    void lifecycleStateChanged();
    void quitRequested();
    void quitBlocked(const QString& reason);

  protected:
    bool eventFilter(QObject* watched, QEvent* event) override;

  private:
    void registerWindow(QQuickWindow* window);
    void pruneDeadWindows();

    QQmlApplicationEngine* engine_ = nullptr;
    QQmlComponent component_;
    QVariantMap sharedWindowProperties_;
    MotionPhotoResourcePolicy* motionPhotoResourcePolicy_ = nullptr;
    ImageResourcePolicy* imageResourcePolicy_ = nullptr;
    AsyncImageProvider* imageProvider_ = nullptr;
    ImageSaveService* imageSaveService_ = nullptr;
    QList<QPointer<QQuickWindow>> windows_;
    QHash<QQuickWindow*, std::shared_ptr<WindowMediaState>> mediaStates_;
    bool graphicsInitialized_ = false;
    bool launchesAllowed_ = true;
};

} // namespace Licasa

#pragma once

#include "diagnostics_common.h"
#include <QCoreApplication>
#include <QDir>
#include <QElapsedTimer>
#include <QFileInfo>
#include <QGuiApplication>
#include <QImageReader>
#include <QJsonArray>
#include <QKeyEvent>
#include <QMouseEvent>
#include <QPointer>
#include <QQmlComponent>
#include <QQmlEngine>
#include <QQuickItem>
#include <QQuickWindow>
#include <QTimer>

// QtGui's exported input bridge is the same one used by QTest::keyClick.
// It sends platform-style key events (including shortcut override handling)
// without adding the QtTest library to the shipped diagnostic runtime.
QT_BEGIN_NAMESPACE
Q_GUI_EXPORT void qt_handleKeyEvent(QWindow*, QEvent::Type, int, Qt::KeyboardModifiers,
                                    const QString&, bool, ushort);
QT_END_NAMESPACE

namespace LicasaDiagnostics {
// Drives the exact production Main.qml and dialogs embedded in the installed
// diagnostics executable. No replacement picker or developer source imports.
class DialogInteractionCheck final : public QObject, public DiagnosticCheck {
  public:
    DialogInteractionCheck(QUrl sample, QString outputDirectory, bool unavailable)
        : sample_(std::move(sample)), outputDirectory_(std::move(outputDirectory)),
          unavailable_(unavailable)
    {}

    bool attach(QQuickWindow* window) override
    {
        window_ = window;
        timer_.setInterval(30);
        connect(&timer_, &QTimer::timeout, this, [this] { poll(); });
        timer_.start();
        elapsed_.start();
        window_->requestActivate();
        return true;
    }

  private:
    QObject* named(const char* name) const
    {
        return window_->findChild<QObject*>(QString::fromLatin1(name));
    }
    QObject* dialog(const char* loader) const
    {
        auto* object = named(loader);
        return object ? object->property("item").value<QObject*>() : nullptr;
    }
    bool visible(QObject* object) const { return object && object->property("visible").toBool(); }
    bool fallbackVisible() const
    {
        for (auto* object : window_->findChildren<QObject*>()) {
            if (object->inherits("QQuickFileDialogImpl") && visible(object) &&
                object->property("opened").toBool() && object->property("activeFocus").toBool()) {
                auto* content =
                    qobject_cast<QQuickItem*>(object->property("contentItem").value<QObject*>());
                if (content && content->window() && content->window()->isExposed()) {
                    return true;
                }
            }
        }
        return false;
    }
    bool mainWindowReady() const
    {
        return window_->isActive() && QGuiApplication::focusWindow() == window_;
    }
    void record(const QString& name, const QString& detail = QString())
    {
        scenarios_.append(QJsonObject{{"name", name}, {"status", "passed"}, {"detail", detail}});
    }
    void finish(const QString& error = QString())
    {
        if (finished_) {
            return;
        }
        finished_ = true;
        timer_.stop();
        if (!error.isEmpty()) {
            scenarios_.append(
                QJsonObject{{"name", "interaction"}, {"status", "failed"}, {"detail", error}});
        }
        auto* engine = qmlEngine(window_);
        printJson(
            {{"status", error.isEmpty() ? "passed" : "failed"},
             {"error", error},
             {"stage", stage_},
             {"qt_version", qVersion()},
             {"window_visible", window_->isVisible()},
             {"window_exposed", window_->isExposed()},
             {"window_width", window_->width()},
             {"window_height", window_->height()},
             {"open_button_found", named("emptyStateOpenButton") != nullptr},
             {"open_picker_visible", visible(dialog("openDialogLoader"))},
             {"active_focus_name",
              window_->activeFocusItem() ? window_->activeFocusItem()->objectName() : QString()},
             {"focus_window_title",
              QGuiApplication::focusWindow() ? QGuiApplication::focusWindow()->title() : QString()},
             {"scenarios", scenarios_},
             {"process", processMetrics()},
             {"qml_import_paths",
              engine ? QJsonArray::fromStringList(engine->importPathList()) : QJsonArray{}},
             {"output_directory", outputDirectory_}});
        QCoreApplication::exit(error.isEmpty() ? 0 : 1);
    }
    void key(int code, Qt::KeyboardModifiers modifiers = Qt::NoModifier)
    {
        QWindow* target = window_;
        for (auto* object : window_->findChildren<QObject*>()) {
            if (object->inherits("QQuickFileDialogImpl") && visible(object)) {
                auto* content =
                    qobject_cast<QQuickItem*>(object->property("contentItem").value<QObject*>());
                if (content && content->window()) {
                    target = content->window();
                }
                break;
            }
        }
        // Qt 6.8 can host its Quick fallback in a separate QQuickPopupWindow.
        // Send input to that live surface, as the desktop does, while Qt 6.4
        // continues using the parent window's in-scene popup.
        QPointer<QWindow> guarded(target);
        timer_.stop(); // Avoid re-entering this state machine while flushing input.
        qt_handleKeyEvent(target, QEvent::KeyPress, code, modifiers, QString(), false, 1);
        QCoreApplication::processEvents();
        if (guarded) {
            qt_handleKeyEvent(guarded, QEvent::KeyRelease, code, modifiers, QString(), false, 1);
            QCoreApplication::processEvents();
        }
        if (!finished_) {
            timer_.start();
        }
    }
    void click(QQuickItem* item)
    {
        const QPointF local = item->mapToScene(QPointF(item->width() / 2, item->height() / 2));
        const QPointF global = window_->mapToGlobal(local.toPoint());
        QMouseEvent press(QEvent::MouseButtonPress, local, global, Qt::LeftButton, Qt::LeftButton,
                          Qt::NoModifier);
        QCoreApplication::sendEvent(window_, &press);
        QMouseEvent release(QEvent::MouseButtonRelease, local, global, Qt::LeftButton, Qt::NoButton,
                            Qt::NoModifier);
        QCoreApplication::sendEvent(window_, &release);
    }
    bool choose(QObject* picker, const QUrl& url)
    {
        if (!visible(picker) || !fallbackVisible()) {
            return false;
        }
        // Select through the live fallback implementation, just as its file
        // delegate does. Changing the public wrapper's selectedFile while the
        // picker is open does not update Qt 6.4's helper selection.
        for (auto* object : window_->findChildren<QObject*>()) {
            if (object->inherits("QQuickFileDialogImpl") && visible(object)) {
                if (!object->setProperty("selectedFile", url) ||
                    object->property("selectedFile").toUrl() != url) {
                    return false;
                }
                return QMetaObject::invokeMethod(object, "accept");
            }
        }
        return false;
    }
    bool invoke(const char* method) { return QMetaObject::invokeMethod(window_, method); }
    void loadSample()
    {
        QMetaObject::invokeMethod(window_, "loadImageUrl",
                                  Q_ARG(QVariant, QVariant::fromValue(sample_)),
                                  Q_ARG(QVariant, QVariant(false)));
    }
    bool imageReady() const
    {
        auto* viewport = named("imageViewport");
        return viewport && viewport->property("imageStatus").toInt() == 1 &&
               window_->property("residentImageUrl").toUrl() == sample_;
    }
    void poll()
    {
        if (elapsed_.elapsed() > 45000) {
            finish(QStringLiteral("Timed out at dialog stage %1").arg(stage_));
            return;
        }
        if (stage_ != lastLoggedStage_) {
            qInfo() << "[Licasa] Dialog qualification stage" << stage_;
            lastLoggedStage_ = stage_;
        }
        switch (stage_) {
        case 0: {
            auto* viewport = named("imageViewport");
            auto* button =
                viewport ? viewport->findChild<QQuickItem*>("emptyStateOpenButton") : nullptr;
            if (!window_->isExposed() || !button || !button->isVisible()) {
                return;
            }
            button->forceActiveFocus();
            focusTarget_ = button;
            click(button);
            ++stage_;
            break;
        }
        case 1: {
            auto* picker = dialog("openDialogLoader");
            if (unavailable_) {
                auto* notifications = named("notificationCenter");
                if (!notifications || notifications->property("toastTitle").toString() !=
                                          QStringLiteral("File picker unavailable")) {
                    return;
                }
                if (visible(picker)) {
                    finish("Missing dependency unexpectedly opened a picker");
                    return;
                }
                record("missing_module_recoverable_error");
                loadSample();
                stage_ = 20;
                break;
            }
            if (!visible(picker) || !fallbackVisible()) {
                return;
            }
            record("open_button_visible_fallback");
            const bool wasFullscreen = window_->property("fullScreenMode").toBool();
            key(Qt::Key_F11);
            if (window_->property("fullScreenMode").toBool() != wasFullscreen) {
                finish("Background fullscreen shortcut fired while picker was open");
                return;
            }
            record("picker_blocks_background_shortcuts");
            key(Qt::Key_Escape);
            ++stage_;
            break;
        }
        case 2:
            if (visible(dialog("openDialogLoader"))) {
                return;
            }
            if (!mainWindowReady() || window_->activeFocusItem() != focusTarget_) {
                return;
            }
            record("cancel_returns_focus");
            key(Qt::Key_O, Qt::ControlModifier);
            ++stage_;
            break;
        case 3: {
            auto* picker = dialog("openDialogLoader");
            if (!visible(picker) || !fallbackVisible()) {
                return;
            }
            record("ctrl_o_reopens_visible_fallback");
            if (!choose(picker, sample_)) {
                finish("Could not accept image selection");
                return;
            }
            ++stage_;
            break;
        }
        case 4:
            if (!imageReady() || !mainWindowReady()) {
                return;
            }
            record("accepted_photo_displayed", sample_.toLocalFile());
            // Apply a real editor adjustment before saving, then assert the
            // resulting image is decoded. This traverses the production save
            // signal and asynchronous image service.
            QMetaObject::invokeMethod(window_, "applyEditorPreset", Q_ARG(QVariant, "vivid"));
            key(Qt::Key_S, Qt::ControlModifier | Qt::ShiftModifier);
            ++stage_;
            break;
        case 5: {
            auto* picker = dialog("saveDialogLoader");
            if (!visible(picker) || !fallbackVisible()) {
                return;
            }
            record("save_as_visible_fallback");
            key(Qt::Key_Escape);
            ++stage_;
            break;
        }
        case 6:
            if (visible(dialog("saveDialogLoader")) || !mainWindowReady()) {
                return;
            }
            if (QFileInfo::exists(outputDirectory_ + "/saved.png")) {
                finish("Cancel created output");
                return;
            }
            record("save_cancel_creates_no_output");
            key(Qt::Key_S, Qt::ControlModifier | Qt::ShiftModifier);
            ++stage_;
            break;
        case 7: {
            auto* picker = dialog("saveDialogLoader");
            if (!visible(picker) || !fallbackVisible()) {
                return;
            }
            if (!choose(picker, QUrl::fromLocalFile(outputDirectory_ + "/saved.png"))) {
                finish("Could not accept save destination");
                return;
            }
            ++stage_;
            break;
        }
        case 8: {
            const QString output = outputDirectory_ + "/saved.png";
            if (!QFileInfo::exists(output)) {
                return;
            }
            QImageReader reader(output);
            if (reader.read().isNull()) {
                finish("Saved image cannot be decoded: " + reader.errorString());
                return;
            }
            record("save_as_output_decodes", output);
            ++stage_;
            break;
        }
        case 9:
        case 10:
        case 11: {
            const QStringList names{"FrameSaveDialog", "MotionExportDialog",
                                    "AnimationExportDialog"};
            const QString name = names.at(stage_ - 9);
            QQmlComponent component(qmlEngine(window_), QUrl("qrc:/Licasa/qml/" + name + ".qml"));
            if (!component.isReady()) {
                finish(name + ": " + component.errorString());
                return;
            }
            QVariantMap initial{
                {"suggestedFile", QUrl::fromLocalFile(outputDirectory_ + "/lazy.png")}};
            if (stage_ == 9) {
                initial.insert("formatSupport", window_->property("formatSupport"));
            } else {
                initial.insert("suggestedSuffix", stage_ == 10 ? "mp4" : "gif");
            }
            lazy_.reset(component.createWithInitialProperties(initial));
            if (!lazy_) {
                finish(name + ": " + component.errorString());
                return;
            }
            lazy_->setParent(window_);
            lazy_->setProperty("parentWindow", QVariant::fromValue(window_));
            QVariant opened;
            if (!QMetaObject::invokeMethod(lazy_.get(), "openSafely",
                                           Q_RETURN_ARG(QVariant, opened)) ||
                !opened.toBool()) {
                finish(name + " could not open");
                return;
            }
            pendingLazyName_ = name;
            stage_ += 30;
            break;
        }
        case 39:
        case 40:
        case 41:
            if (!visible(lazy_.get()) || !fallbackVisible()) {
                return;
            }
            record("lazy_" + pendingLazyName_ + "_visible");
            QMetaObject::invokeMethod(lazy_.get(), "reject");
            lazy_.reset();
            stage_ -= 29;
            break;
        case 12:
            finish();
            break;
        case 20:
            if (!imageReady()) {
                return;
            }
            QMetaObject::invokeMethod(window_, "applyEditorPreset", Q_ARG(QVariant, "vivid"));
            preservedEdits_ = window_->property("editorHasAdjustments").toBool();
            if (!preservedEdits_) {
                finish("Failed to establish unsaved edits before recovery check");
                return;
            }
            key(Qt::Key_S, Qt::ControlModifier | Qt::ShiftModifier);
            ++stage_;
            break;
        case 21: {
            auto* notifications = named("notificationCenter");
            if (!notifications || notifications->property("toastTitle").toString() !=
                                      QStringLiteral("File picker unavailable")) {
                return;
            }
            if (!imageReady() || !window_->property("editorHasAdjustments").toBool()) {
                finish("Dialog failure discarded current photo or unsaved edits");
                return;
            }
            record("missing_module_preserves_photo_and_edits");
            key(Qt::Key_O, Qt::ControlModifier);
            ++stage_;
            break;
        }
        case 22:
            if (visible(dialog("openDialogLoader"))) {
                finish("Missing module retry opened a picker");
                return;
            }
            if (!imageReady() || !window_->property("editorHasAdjustments").toBool()) {
                finish("Retry discarded photo or edits");
                return;
            }
            record("missing_module_retry_preserves_state");
            finish();
            break;
        }
    }
    QQuickWindow* window_ = nullptr;
    QPointer<QQuickItem> focusTarget_;
    QUrl sample_;
    QString outputDirectory_;
    QString pendingLazyName_;
    bool unavailable_ = false;
    bool preservedEdits_ = false;
    bool finished_ = false;
    int stage_ = 0;
    int lastLoggedStage_ = -1;
    QTimer timer_;
    QElapsedTimer elapsed_;
    QJsonArray scenarios_;
    std::unique_ptr<QObject> lazy_;
};
} // namespace LicasaDiagnostics

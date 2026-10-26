#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QGuiApplication>
#include <QQmlComponent>
#include <QQmlEngine>
#include <QQuickItem>
#include <QQuickWindow>
#include <QRegularExpression>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QtTest>
#include <memory>

// Use the production wrappers and Qt's actual fallback picker. The negative
// run has its own process and import tree, so global QML plugin caches cannot
// turn a missing-module test into a false positive.
class FileDialogRuntimeTest final : public QObject {
    Q_OBJECT
  private slots:
    void dialogs_data()
    {
        QTest::addColumn<QString>("file");
        const QDir directory(QStringLiteral(LICASA_SOURCE_DIR "/qml"));
        int wrappers = 0;
        for (const auto& name : directory.entryList({QStringLiteral("*.qml")}, QDir::Files)) {
            QFile source(directory.filePath(name));
            QVERIFY(source.open(QIODevice::ReadOnly));
            // Discovery only: each row below instantiates the production type
            // and invokes its actual recovery API. New wrappers participate
            // automatically, and unknown required properties fail creation.
            if (!QRegularExpression(QStringLiteral(R"(\bFileDialog\s*\{)"))
                     .match(QString::fromUtf8(source.readAll()))
                     .hasMatch()) {
                continue;
            }
            const QString base = QFileInfo(name).completeBaseName();
            QTest::newRow(qPrintable(base)) << base;
            ++wrappers;
        }
        QVERIFY2(wrappers > 0, "No production FileDialog wrappers were discovered");
    }

    void dialogs()
    {
        QFETCH(QString, file);
        QQmlEngine engine;
        QTemporaryDir isolated;
        QVERIFY(isolated.isValid());
        const bool missing = qEnvironmentVariableIsSet("LICASA_TEST_MISSING_DIALOG_MODULE");
        if (missing) {
            // Preserve the real QtQuick/QtQml imports and all labs modules
            // except FolderListModel. No standard/host import path remains.
            const QString qtRoot = QString::fromLocal8Bit(LICASA_TEST_QML_ROOT);
            QVERIFY(QDir(qtRoot).exists());
            const QDir source(qtRoot);
            QDir target(isolated.path());
            for (const auto& entry : source.entryList(QDir::AllEntries | QDir::NoDotAndDotDot)) {
                if (entry != QStringLiteral("Qt")) {
                    QVERIFY(QFile::link(source.filePath(entry), target.filePath(entry)));
                }
            }
            QVERIFY(target.mkpath(QStringLiteral("Qt/labs")));
            const QDir qt(source.filePath(QStringLiteral("Qt")));
            for (const auto& entry : qt.entryList(QDir::AllEntries | QDir::NoDotAndDotDot)) {
                if (entry != QStringLiteral("labs")) {
                    QVERIFY(QFile::link(qt.filePath(entry), target.filePath("Qt/" + entry)));
                }
            }
            const QDir labs(source.filePath(QStringLiteral("Qt/labs")));
            for (const auto& entry : labs.entryList(QDir::AllEntries | QDir::NoDotAndDotDot)) {
                if (entry != QStringLiteral("folderlistmodel")) {
                    QVERIFY(QFile::link(labs.filePath(entry), target.filePath("Qt/labs/" + entry)));
                }
            }
            engine.setImportPathList(
                {isolated.path(), QStringLiteral("qrc:/qt-project.org/imports")});
        }
        QQuickWindow window;
        window.resize(800, 600);
        window.show();
        QQmlComponent component(&engine,
                                QUrl::fromLocalFile(QStringLiteral(LICASA_SOURCE_DIR "/qml/") +
                                                    file + QStringLiteral(".qml")));
        QVERIFY2(component.isReady(), qPrintable(component.errorString()));
        const QVariantMap formats{{"nameFilters", QStringList{"Images (*.png)"}},
                                  {"saveNameFilters", QStringList{"PNG (*.png)"}}};
        QVariantMap initial{{"formatSupport", formats},
                            {"suggestedFile", QUrl::fromLocalFile(isolated.filePath("copy.png"))},
                            {"suggestedSuffix", QStringLiteral("png")}};
        std::unique_ptr<QObject> dialog(component.beginCreate(engine.rootContext()));
        QVERIFY2(dialog, qPrintable(component.errorString()));
        QVERIFY2(dialog->inherits("QQuickFileDialog"),
                 "Discovered wrapper must instantiate a production Qt FileDialog");
        for (auto it = initial.begin(); it != initial.end();) {
            if (dialog->metaObject()->indexOfProperty(it.key().toLatin1().constData()) < 0) {
                it = initial.erase(it);
            } else {
                ++it;
            }
        }
        component.setInitialProperties(dialog.get(), initial);
        component.completeCreate();
        QVERIFY2(!component.isError(), qPrintable(component.errorString()));
        QVERIFY2(dialog, qPrintable(component.errorString()));
        dialog->setParent(&window);
        QVERIFY(dialog->setProperty("parentWindow", QVariant::fromValue(&window)));
        QSignalSpy failed(dialog.get(), SIGNAL(openFailed(QString)));
        QSignalSpy rejected(dialog.get(), SIGNAL(rejected()));
        for (int cycle = 0; cycle < 2; ++cycle) {
            QVariant opened;
            QVERIFY(QMetaObject::invokeMethod(dialog.get(), "openSafely",
                                              Q_RETURN_ARG(QVariant, opened)));
            QCOMPARE(opened.toBool(), !missing);
            if (missing) {
                QCOMPARE(failed.count(), cycle + 1);
                QVERIFY(failed.last().first().toString().contains("Your image and edits are safe"));
                QVERIFY(!dialog->property("visible").toBool());
            } else {
                QTRY_VERIFY(dialog->property("visible").toBool());
                bool fallbackVisible = false;
                for (auto* object : window.findChildren<QObject*>()) {
                    if (object->inherits("QQuickFileDialogImpl") &&
                        object->property("visible").toBool()) {
                        fallbackVisible = true;
                    }
                }
                QVERIFY2(fallbackVisible, "Qt fallback picker content did not become visible");
                QQuickItem* content = nullptr;
                for (auto* object : window.findChildren<QObject*>()) {
                    if (object->inherits("QQuickFileDialogImpl") &&
                        object->property("visible").toBool()) {
                        content = qobject_cast<QQuickItem*>(
                            object->property("contentItem").value<QObject*>());
                    }
                }
                QVERIFY(content);
                QTRY_VERIFY(content->window() && content->window()->isExposed());
                QTest::keyClick(content->window(), Qt::Key_Escape);
                QTRY_VERIFY(!dialog->property("visible").toBool());
                QCOMPARE(rejected.count(), cycle + 1);
            }
        }
    }
};

int main(int argc, char** argv)
{
    QCoreApplication::setAttribute(Qt::AA_DontUseNativeDialogs);
    QGuiApplication application(argc, argv);
    FileDialogRuntimeTest test;
    return QTest::qExec(&test, argc, argv);
}
#include "tst_file_dialog_runtime.moc"

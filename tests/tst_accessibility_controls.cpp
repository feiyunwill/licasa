#include <QAccessible>
#include <QGuiApplication>
#include <QQmlComponent>
#include <QQmlEngine>
#include <QQuickItem>
#include <QQuickWindow>
#include <QSignalSpy>
#include <QtTest>
#include <memory>

class AccessibilityControlsTest final : public QObject {
    Q_OBJECT
  private slots:
    void sliders_data()
    {
        QTest::addColumn<QString>("file");
        QTest::addColumn<QVariantMap>("initial");
        QTest::addColumn<QByteArray>("signal");
        QTest::newRow("editor adjustment") << QString("AdjustRow")
                                           << QVariantMap{{"label", "Exposure"},
                                                          {"value", 0.25},
                                                          {"from", -1.0},
                                                          {"to", 1.0},
                                                          {"keyboardStep", 0.1}}
                                           << QByteArray(SIGNAL(valueRequested(double)));
        QTest::newRow("settings value") << QString("ValueRow")
                                        << QVariantMap{{"title", "Opacity"},
                                                       {"value", 0.25},
                                                       {"from", -1.0},
                                                       {"to", 1.0},
                                                       {"step", 0.1}}
                                        << QByteArray(SIGNAL(valueChangedByUser(double)));
    }
    void sliders()
    {
        QFETCH(QString, file);
        QFETCH(QVariantMap, initial);
        QFETCH(QByteArray, signal);
        QQmlEngine engine;
        QQmlComponent component(
            &engine, QUrl::fromLocalFile(QStringLiteral(LICASA_SOURCE_DIR "/qml/components/") +
                                         file + ".qml"));
        std::unique_ptr<QObject> control(component.createWithInitialProperties(initial));
        QVERIFY2(control, qPrintable(component.errorString()));
        auto* accessible = QAccessible::queryAccessibleInterface(control.get());
        QVERIFY(accessible);
        QCOMPARE(accessible->role(), QAccessible::Slider);
        QVERIFY(!accessible->text(QAccessible::Name).isEmpty());
        QVERIFY(accessible->state().focusable);
        auto* values = accessible->valueInterface();
        QVERIFY(values);
        QCOMPARE(values->currentValue().toDouble(), 0.25);
        QCOMPARE(values->minimumValue().toDouble(), -1.0);
        QCOMPARE(values->maximumValue().toDouble(), 1.0);
        QCOMPARE(values->minimumStepSize().toDouble(), 0.1);
        QSignalSpy requested(control.get(), signal.constData());
        QVERIFY(requested.isValid());
        auto* actions = accessible->actionInterface();
        QVERIFY(actions);
        actions->doAction(QAccessibleActionInterface::increaseAction());
        QCOMPARE(requested.count(), 1);
        QCOMPARE(requested.last().first().toDouble(), 0.35);
        actions->doAction(QAccessibleActionInterface::decreaseAction());
        QCOMPARE(requested.count(), 2);
        QCOMPARE(requested.last().first().toDouble(), 0.15);
        // Screen-reader actions must go through the same production request
        // signal as keyboard/pointer input, preserving the parent's binding.
        QCOMPARE(control->property("value").toDouble(), 0.25);
    }
    void toggleStateAndAction()
    {
        QQmlEngine engine;
        QQmlComponent component(&engine, QUrl::fromLocalFile(QStringLiteral(
                                             LICASA_SOURCE_DIR "/qml/components/ToggleRow.qml")));
        std::unique_ptr<QObject> control(
            component.createWithInitialProperties({{"title", "Smooth motion"}, {"checked", true}}));
        QVERIFY2(control, qPrintable(component.errorString()));
        auto* accessible = QAccessible::queryAccessibleInterface(control.get());
        QVERIFY(accessible);
        QCOMPARE(accessible->role(), QAccessible::CheckBox);
        QVERIFY(accessible->state().checkable);
        QVERIFY(accessible->state().checked);
        QSignalSpy toggled(control.get(), SIGNAL(toggled(bool)));
        accessible->actionInterface()->doAction(QAccessibleActionInterface::toggleAction());
        QCOMPARE(toggled.count(), 1);
        QCOMPARE(toggled.first().first().toBool(), false);
        QVERIFY(control->property("checked").toBool());
    }
    void selectedButtonsExposeState()
    {
        QQmlEngine engine;
        QQmlComponent component(&engine, QUrl::fromLocalFile(QStringLiteral(
                                             LICASA_SOURCE_DIR "/qml/components/ActionChip.qml")));
        std::unique_ptr<QObject> control(component.createWithInitialProperties(
            {{"text", "PNG"}, {"checkable", true}, {"checked", true}}));
        QVERIFY2(control, qPrintable(component.errorString()));
        auto* accessible = QAccessible::queryAccessibleInterface(control.get());
        QVERIFY(accessible);
        QCOMPARE(accessible->text(QAccessible::Name), QString("PNG"));
        QVERIFY(accessible->state().checkable);
        QVERIFY(accessible->state().checked);
        QSignalSpy pressed(control.get(), SIGNAL(clicked()));
        accessible->actionInterface()->doAction(QAccessibleActionInterface::pressAction());
        QCOMPARE(pressed.count(), 1);
    }
};
int main(int argc, char** argv)
{
    QGuiApplication application(argc, argv);
    QAccessible::setActive(true);
    AccessibilityControlsTest test;
    return QTest::qExec(&test, argc, argv);
}
#include "tst_accessibility_controls.moc"

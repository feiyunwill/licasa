#include "platform/graphics_backend.h"

#include <QByteArray>
#include <QColorSpace>
#include <QCoreApplication>
#include <QString>
#include <QSurfaceFormat>
#include <QtTest>

#include <array>

namespace {
constexpr std::array<const char*, 3> environmentNames{"QT_QUICK_BACKEND", "QSG_RHI_BACKEND",
                                                      "QT_OPENGL"};
constexpr std::array<Qt::ApplicationAttribute, 3> apiAttributes{
    Qt::AA_UseDesktopOpenGL, Qt::AA_UseSoftwareOpenGL, Qt::AA_UseOpenGLES};

void setApiAttribute(Qt::ApplicationAttribute attribute, bool enabled)
{
    if (QCoreApplication::testAttribute(attribute) == enabled) {
        return;
    }
    // This GUI-less suite reads only the policy flags and never creates an
    // OpenGL context. Account for Qt's warning about changing rendering flags
    // after the test framework has created its QCoreApplication.
    const char* name = attribute == Qt::AA_UseDesktopOpenGL    ? "AA_UseDesktopOpenGL"
                       : attribute == Qt::AA_UseSoftwareOpenGL ? "AA_UseSoftwareOpenGL"
                                                               : "AA_UseOpenGLES";
    const QByteArray warning =
        QByteArray("Attribute Qt::") + name + " must be set before QCoreApplication is created.";
    QTest::ignoreMessage(QtWarningMsg, warning.constData());
    QCoreApplication::setAttribute(attribute, enabled);
}

QSurfaceFormat configuredDefaultFormat()
{
    QSurfaceFormat format;
    format.setDepthBufferSize(24);
    format.setStencilBufferSize(8);
    format.setRedBufferSize(10);
    format.setGreenBufferSize(10);
    format.setBlueBufferSize(10);
    format.setAlphaBufferSize(2);
    format.setSamples(4);
    format.setSwapInterval(2);
    format.setSwapBehavior(QSurfaceFormat::TripleBuffer);
    format.setStereo(true);
    format.setOption(QSurfaceFormat::DebugContext);
    format.setOption(QSurfaceFormat::ResetNotification);
    format.setColorSpace(QColorSpace::SRgb);
    return format;
}

QSurfaceFormat expectedAutomaticFormat(QSurfaceFormat format)
{
#ifdef Q_OS_LINUX
    format.setRenderableType(QSurfaceFormat::OpenGLES);
    format.setVersion(3, 0);
#endif
    return format;
}
} // namespace

class GraphicsBackendTests : public QObject {
    Q_OBJECT

  private slots:
    void init()
    {
        savedFormat_ = QSurfaceFormat::defaultFormat();
        for (std::size_t i = 0; i < environmentNames.size(); ++i) {
            savedEnvironmentPresent_[i] = qEnvironmentVariableIsSet(environmentNames[i]);
            savedEnvironment_[i] = qgetenv(environmentNames[i]);
            qunsetenv(environmentNames[i]);
        }
        for (std::size_t i = 0; i < apiAttributes.size(); ++i) {
            savedAttributes_[i] = QCoreApplication::testAttribute(apiAttributes[i]);
            setApiAttribute(apiAttributes[i], false);
        }
        QSurfaceFormat::setDefaultFormat(configuredDefaultFormat());
    }

    void cleanup()
    {
        QSurfaceFormat::setDefaultFormat(savedFormat_);
        for (std::size_t i = 0; i < environmentNames.size(); ++i) {
            if (savedEnvironmentPresent_[i]) {
                qputenv(environmentNames[i], savedEnvironment_[i]);
            } else {
                qunsetenv(environmentNames[i]);
            }
        }
        for (std::size_t i = 0; i < apiAttributes.size(); ++i) {
            setApiAttribute(apiAttributes[i], savedAttributes_[i]);
        }
    }

    void automaticWaylandUsesGlesWithoutDiscardingFormatProperties_data()
    {
        QTest::addColumn<QString>("platform");
        QTest::newRow("wayland") << QStringLiteral("wayland");
        QTest::newRow("wayland-egl") << QStringLiteral("wayland-egl");
        QTest::newRow("wayland-custom") << QStringLiteral("wayland-custom");
    }

    void automaticWaylandUsesGlesWithoutDiscardingFormatProperties()
    {
        QFETCH(QString, platform);
        const QSurfaceFormat before = QSurfaceFormat::defaultFormat();
        Licasa::configureSurfaceFormatForPlatform(platform);
        const QSurfaceFormat actual = QSurfaceFormat::defaultFormat();
        QCOMPARE(actual, expectedAutomaticFormat(before));
        QCOMPARE(actual.profile(), QSurfaceFormat::NoProfile);
        QCOMPARE(actual.colorSpace(), before.colorSpace());
        for (const char* name : environmentNames) {
            QVERIFY(!qEnvironmentVariableIsSet(name));
        }
        for (const auto attribute : apiAttributes) {
            QVERIFY(!QCoreApplication::testAttribute(attribute));
        }
    }

    void otherPlatformsKeepTheirExistingFormat_data()
    {
        QTest::addColumn<QString>("platform");
        for (const char* platform : {"xcb", "offscreen", "minimal", "eglfs", "linuxfb", "windows",
                                     "cocoa", "waylandish", "Wayland", ""}) {
            QTest::newRow(platform[0] ? platform : "empty") << QString::fromLatin1(platform);
        }
    }

    void otherPlatformsKeepTheirExistingFormat()
    {
        QFETCH(QString, platform);
        const QSurfaceFormat before = QSurfaceFormat::defaultFormat();
        Licasa::configureSurfaceFormatForPlatform(platform);
        QCOMPARE(QSurfaceFormat::defaultFormat(), before);
    }

    void explicitEnvironmentChoiceIsPreserved_data()
    {
        QTest::addColumn<QByteArray>("variable");
        QTest::addColumn<QByteArray>("value");
        for (const char* variable : environmentNames) {
            for (const char* value : {"software", "opengl", "vulkan", "desktop", "unexpected"}) {
                const QByteArray row = QByteArray(variable) + '=' + value;
                QTest::newRow(row.constData()) << QByteArray(variable) << QByteArray(value);
            }
        }
    }

    void explicitEnvironmentChoiceIsPreserved()
    {
        QFETCH(QByteArray, variable);
        QFETCH(QByteArray, value);
        QVERIFY(qputenv(variable.constData(), value));
        const QSurfaceFormat before = QSurfaceFormat::defaultFormat();
        Licasa::configureSurfaceFormatForPlatform(QStringLiteral("wayland"));
        QCOMPARE(QSurfaceFormat::defaultFormat(), before);
        QCOMPARE(qgetenv(variable.constData()), value);
    }

    void emptyEnvironmentValuesStillPermitAutomaticSelection_data()
    {
        QTest::addColumn<QByteArray>("variable");
        QTest::addColumn<QByteArray>("value");
        for (const char* variable : environmentNames) {
            const QByteArray prefix(variable);
            QTest::newRow((prefix + "=empty").constData()) << prefix << QByteArray("");
            QTest::newRow((prefix + "=whitespace").constData()) << prefix << QByteArray(" \t");
        }
    }

    void emptyEnvironmentValuesStillPermitAutomaticSelection()
    {
        QFETCH(QByteArray, variable);
        QFETCH(QByteArray, value);
        QVERIFY(qputenv(variable.constData(), value));
        const QSurfaceFormat before = QSurfaceFormat::defaultFormat();
        Licasa::configureSurfaceFormatForPlatform(QStringLiteral("wayland-egl"));
        QCOMPARE(QSurfaceFormat::defaultFormat(), expectedAutomaticFormat(before));
        QVERIFY(qEnvironmentVariableIsSet(variable.constData()));
        QCOMPARE(qgetenv(variable.constData()), value);
    }

    void explicitQtApiAttributeIsPreserved_data()
    {
        QTest::addColumn<int>("attribute");
        QTest::newRow("desktop-opengl") << int(Qt::AA_UseDesktopOpenGL);
        QTest::newRow("software-opengl") << int(Qt::AA_UseSoftwareOpenGL);
        QTest::newRow("opengles") << int(Qt::AA_UseOpenGLES);
    }

    void explicitQtApiAttributeIsPreserved()
    {
        QFETCH(int, attribute);
        const auto choice = static_cast<Qt::ApplicationAttribute>(attribute);
        setApiAttribute(choice, true);
        const QSurfaceFormat before = QSurfaceFormat::defaultFormat();
        Licasa::configureSurfaceFormatForPlatform(QStringLiteral("wayland"));
        QCOMPARE(QSurfaceFormat::defaultFormat(), before);
        QVERIFY(QCoreApplication::testAttribute(choice));
    }

    void explicitSurfaceApiIsPreserved_data()
    {
        QTest::addColumn<int>("api");
        QTest::newRow("desktop-opengl") << int(QSurfaceFormat::OpenGL);
        QTest::newRow("opengles") << int(QSurfaceFormat::OpenGLES);
        QTest::newRow("openvg") << int(QSurfaceFormat::OpenVG);
    }

    void explicitSurfaceApiIsPreserved()
    {
        QFETCH(int, api);
        QSurfaceFormat before = QSurfaceFormat::defaultFormat();
        before.setRenderableType(static_cast<QSurfaceFormat::RenderableType>(api));
        before.setVersion(4, 1);
        QSurfaceFormat::setDefaultFormat(before);
        Licasa::configureSurfaceFormatForPlatform(QStringLiteral("wayland"));
        QCOMPARE(QSurfaceFormat::defaultFormat(), before);
    }

    void explicitSurfaceProfileIsPreserved_data()
    {
        QTest::addColumn<int>("profile");
        QTest::newRow("core") << int(QSurfaceFormat::CoreProfile);
        QTest::newRow("compatibility") << int(QSurfaceFormat::CompatibilityProfile);
    }

    void explicitSurfaceProfileIsPreserved()
    {
        QFETCH(int, profile);
        QSurfaceFormat before = QSurfaceFormat::defaultFormat();
        before.setProfile(static_cast<QSurfaceFormat::OpenGLContextProfile>(profile));
        before.setVersion(4, 1);
        QSurfaceFormat::setDefaultFormat(before);
        Licasa::configureSurfaceFormatForPlatform(QStringLiteral("wayland"));
        QCOMPARE(QSurfaceFormat::defaultFormat(), before);
    }

  private:
    QSurfaceFormat savedFormat_;
    std::array<QByteArray, environmentNames.size()> savedEnvironment_;
    std::array<bool, environmentNames.size()> savedEnvironmentPresent_{};
    std::array<bool, apiAttributes.size()> savedAttributes_{};
};

QTEST_GUILESS_MAIN(GraphicsBackendTests)
#include "tst_graphics_backend.moc"

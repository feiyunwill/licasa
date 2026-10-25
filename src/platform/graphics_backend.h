#pragma once

class QQuickWindow;
class QString;

namespace Licasa {

void configureSurfaceFormatForRequestedOpenGL();
void configureSurfaceFormatForPlatform(const QString& platformName);
void logWindowGraphicsBackend(QQuickWindow* window, const QString& stage);

} // namespace Licasa

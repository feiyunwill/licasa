#pragma once

#include <QJsonObject>
#include <QString>

namespace Licasa {

struct ApplicationIdentity {
    QString installationId;
    QString generation;
    QString generationObject;
    QString packageFamily;
    QString packageRevision;
    QString installationRoot;
    QString executablePath;
    QString sourceReceipt;

    QJsonObject toJson() const;
};

ApplicationIdentity applicationIdentity(bool includeGeneration = true);
QString applicationLaunchPath();
QString lifecycleEndpointName();
QString processStartIdentity(qint64 pid);
QString processExecutablePath(qint64 pid);
QString processExecutableHash(qint64 pid);
bool sameProcessExecutable(qint64 firstPid, qint64 secondPid);
bool canReuseExecutableGeneration(qint64 firstPid, qint64 secondPid);
QString processExecutableObject(qint64 pid);
bool sameInstallationProcessExists(QString* reason = nullptr,
                                   bool requireCompleteInspection = true);
bool scopedInstallationProcessExists(QString* reason = nullptr, int* unknownProcessCount = nullptr,
                                     bool* controlClientPending = nullptr);

} // namespace Licasa

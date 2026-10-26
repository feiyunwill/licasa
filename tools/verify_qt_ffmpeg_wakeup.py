#!/usr/bin/env python3
"""Deterministically qualify the prepared Qt 6.4 demuxer wait protocol.

Compile Qt's actual Thread implementation and startDecoding method. A barrier
exists only in the generated test copy, between shouldWait() and wait(). The
unprotected negative control must lose its notification and resume on a second
notification; the prepared method must deliver work with its first notification.
No test hooks are compiled into the private production plugin.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

GUARD = "QMutexLocker locker(&mutex);"


def start_method(header: str) -> str:
    match = re.search(r"    void startDecoding\(\)\s*\{", header)
    if not match:
        raise RuntimeError("Qt demuxer start method changed; review its wait protocol")
    begin = match.start()
    end = match.end()
    depth = 1
    while end < len(header) and depth:
        depth += (header[end] == "{") - (header[end] == "}")
        end += 1
    method = header[begin:end]
    predicate = "m_isStopped.storeRelaxed(false);"
    if depth or method.count(GUARD) != 1 or method.count(predicate) != 1 or method.index(GUARD) > method.index(predicate):
        raise RuntimeError("Qt demuxer start must protect its predicate and notification with Thread::mutex")
    return method


HARNESS = r'''
#include "qffmpegthread_p.h"
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QDebug>
#include <atomic>
#include <thread>

static std::atomic<QFFmpeg::Thread *> selected{nullptr};
static std::atomic_bool armed{true}, ready{false}, releaseWait{false}, lockAttempted{false};
template<typename Predicate> static void await(Predicate predicate)
{
    QElapsedTimer timer;
    timer.start();
    while (!predicate()) {
        if (timer.elapsed() > 2000) qFatal("Wait protocol barrier did not complete");
        QThread::msleep(1);
    }
}
void licasaCheckedWait(QFFmpeg::Thread *thread)
{
    if (thread == selected.load() && armed.exchange(false)) {
        ready.store(true);
        await([] { return releaseWait.load(); });
    }
}
static void beforeStartLock() { lockAttempted.store(true); }
class Probe final : public QFFmpeg::Thread
{
    QAtomicInteger<bool> m_isStopped{true};
    void updateEnabledStreams() {} // Packet selection is outside this wait-protocol test.
    bool shouldWait() const override { return m_isStopped.loadRelaxed(); }
    void loop() override { ++deliveries; m_isStopped.storeRelaxed(true); }
public:
    std::atomic_int deliveries{0};
    bool stopped() const { return m_isStopped.loadRelaxed(); }
@METHOD@
};
int main(int argc, char **argv)
{
    QCoreApplication app(argc, argv);
    auto *probe = new Probe;
    selected.store(probe);
    probe->start();
    await([] { return ready.load(); });
#if @POSITIVE@
    std::thread starter([&] { probe->startDecoding(); });
    await([] { return lockAttempted.load(); });
    releaseWait.store(true);
    starter.join();
    await([&] { return probe->deliveries.load() == 1; });
    qInfo("PASS protected start: first notification delivered work");
#else
    probe->startDecoding(); // Must finish while the worker still owns its wait mutex.
    releaseWait.store(true);
    {
        // Acquiring this proves condition.wait() atomically registered and
        // released the mutex. Its earlier notification has already been lost.
        QMutexLocker lock(&probe->mutex);
        if (probe->deliveries.load() != 0 || probe->stopped())
            qFatal("Unprotected negative control did not reproduce the lost notification");
        probe->wake();
    }
    await([&] { return probe->deliveries.load() == 1; });
    qInfo("PASS negative control: runnable worker stayed asleep until second notification");
#endif
    probe->kill();
}
'''


def qualify(source: Path, work: Path, *, jobs=4, environment=None, prefixes=()):
    ffmpeg = source / "src/plugins/multimedia/ffmpeg"
    thread = ffmpeg / "qffmpegthread.cpp"
    thread_header = ffmpeg / "qffmpegthread_p.h"
    decoder_header = ffmpeg / "qffmpegdecoder_p.h"
    method = start_method(decoder_header.read_text())
    actual_thread = thread.read_text()
    marker = "        QElapsedTimer timer;"
    if actual_thread.count(marker) != 1:
        raise RuntimeError("Qt Thread wait changed; review deterministic barrier placement")
    generated = work / "source"
    generated.mkdir(parents=True, exist_ok=True)
    instrumented = actual_thread.replace("QT_BEGIN_NAMESPACE", "void licasaCheckedWait(QFFmpeg::Thread *);\n\nQT_BEGIN_NAMESPACE", 1)
    instrumented = instrumented.replace(marker, "        licasaCheckedWait(this);\n" + marker)
    (generated / thread.name).write_text(instrumented)
    (generated / thread_header.name).write_bytes(thread_header.read_bytes())
    # Thread itself only uses Core. The private Multimedia umbrella normally
    # supplies QElapsedTimer transitively; avoid requiring unshipped distro
    # Multimedia private headers just to compile this unchanged worker class.
    (generated / "private").mkdir(exist_ok=True)
    (generated / "private/qtmultimediaglobal_p.h").write_text("#include <QtCore/qelapsedtimer.h>\n")
    for name, positive in (("protected", True), ("unprotected", False)):
        selected_method = (method.replace(GUARD, "beforeStartLock();\n        " + GUARD)
                           if positive else method.replace(GUARD, ""))
        (generated / f"{name}.cpp").write_text(HARNESS.replace("@METHOD@", selected_method).replace("@POSITIVE@", str(int(positive))))
    (generated / "CMakeLists.txt").write_text('''cmake_minimum_required(VERSION 3.21)
project(QtDemuxerWaitQualification LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(Qt6 6.4.2 EXACT REQUIRED COMPONENTS Core)
foreach(name protected unprotected)
    add_executable(${name} ${name}.cpp qffmpegthread.cpp)
    target_include_directories(${name} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
    target_link_libraries(${name} PRIVATE Qt6::Core)
endforeach()
''')
    build = work / "build"
    configure = ["cmake", "-S", str(generated), "-B", str(build), "-G", "Ninja"]
    if prefixes:
        configure += ["-DCMAKE_PREFIX_PATH=" + ";".join(prefixes)]
    environment = dict(os.environ) if environment is None else environment
    subprocess.run(configure, env=environment, check=True)
    subprocess.run(["cmake", "--build", str(build), "--parallel", str(jobs)], env=environment, check=True)
    cases = []
    for name in ("unprotected", "protected"):
        result = subprocess.run([str(build / name)], env=environment, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
        (work / f"{name}.log").write_text(result.stdout)
        print(result.stdout, end="", flush=True)
        cases.append({"name": name, "exit_code": result.returncode})
        if result.returncode:
            raise RuntimeError(f"Qt demuxer wait qualification failed: {name}")
    report = {"status": "passed", "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in (thread, thread_header, decoder_header)},
              "method_sha256": hashlib.sha256(method.encode()).hexdigest(), "cases": cases}
    (work / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("jobs must be positive")
    qualify(args.source_dir.resolve(), args.work_dir.resolve(), jobs=args.jobs)


if __name__ == "__main__":
    main()

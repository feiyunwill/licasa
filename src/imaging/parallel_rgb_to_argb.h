#pragma once

#include <QColorSpace>
#include <QImage>
#include <QThread>

#include <algorithm>
#include <system_error>
#include <thread>
#include <vector>

namespace Licasa {
inline QImage parallelRgbToArgb(const QImage& source)
{
    const quint64 pixels = quint64(source.width()) * source.height();
    const int workers = std::min({8, QThread::idealThreadCount(), source.height() / 256});
    if (source.format() != QImage::Format_RGB888 || pixels < 16'000'000 || workers < 2) {
        return source.convertToFormat(QImage::Format_ARGB32);
    }

    QImage output(source.size(), QImage::Format_ARGB32);
    if (output.isNull()) {
        return {};
    }
    output.setColorSpace(source.colorSpace());
    const uchar* input = source.constBits();
    uchar* result = output.bits();
    const qsizetype inputStride = source.bytesPerLine();
    const qsizetype outputStride = output.bytesPerLine();
    std::vector<std::thread> threads;
    threads.reserve(size_t(workers));
    try {
        for (int worker = 0; worker < workers; ++worker) {
            const int first = worker * source.height() / workers;
            const int end = (worker + 1) * source.height() / workers;
            threads.emplace_back([=] {
                for (int y = first; y < end; ++y) {
                    const uchar* rgb = input + qsizetype(y) * inputStride;
                    auto* argb = reinterpret_cast<QRgb*>(result + qsizetype(y) * outputStride);
                    for (int x = 0; x < source.width(); ++x) {
                        argb[x] = qRgb(rgb[3 * x], rgb[3 * x + 1], rgb[3 * x + 2]);
                    }
                }
            });
        }
    } catch (const std::system_error&) {
        for (auto& thread : threads) {
            thread.join();
        }
        return source.convertToFormat(QImage::Format_ARGB32);
    }
    for (auto& thread : threads) {
        thread.join();
    }
    return output;
}
} // namespace Licasa

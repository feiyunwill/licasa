#include "imaging/fast_pnm_preview.h"

#include <QByteArray>
#include <QDebug>
#include <QElapsedTimer>
#include <QFile>
#include <QFileInfo>

#include <algorithm>
#include <array>
#include <limits>
#include <new>
#include <vector>

namespace Licasa {
namespace {
constexpr qint64 kMaximumHeaderBytes = 64 * 1024;
constexpr quint64 kMaximumInputBlockBytes = 1024 * 1024;
constexpr quint64 kMaximumFilterBytes = 1024 * 1024;
constexpr quint64 kMaximumOutputBytes = 8 * 1024 * 1024;
constexpr quint64 kMaximumOutputPixels = 2'000'000;
constexpr quint64 kMaximumSourceDimension = 20'000;
constexpr int kMaximumOutputDimension = 8192;

bool isCancelled(const std::atomic_bool* cancelled)
{
    return cancelled && cancelled->load(std::memory_order_relaxed);
}

bool isSpace(char value)
{
    return value == ' ' || value == '\t' || value == '\n' || value == '\r' || value == '\v' ||
           value == '\f';
}

bool readExactly(QIODevice& device, char* destination, qint64 length,
                 const std::atomic_bool* cancelled)
{
    qint64 done = 0;
    while (done < length) {
        if (isCancelled(cancelled)) {
            return false;
        }
        const qint64 count = device.read(destination + done, length - done);
        if (count <= 0 || count > length - done) {
            return false;
        }
        done += count;
    }
    return !isCancelled(cancelled);
}

struct Header {
    quint64 width = 0;
    quint64 height = 0;
    qint64 rasterOffset = 0;
};

Header parseHeader(const QByteArray& bytes)
{
    if (bytes.size() < 3 || bytes[0] != 'P' || bytes[1] != '6' || !isSpace(bytes[2])) {
        return {};
    }
    qsizetype cursor = 2;
    const auto skipSpace = [&] {
        for (;;) {
            while (cursor < bytes.size() && isSpace(bytes[cursor])) {
                ++cursor;
            }
            if (cursor == bytes.size() || bytes[cursor] != '#') {
                return cursor < bytes.size();
            }
            while (cursor < bytes.size() && bytes[cursor] != '\n' && bytes[cursor] != '\r') {
                ++cursor;
            }
        }
    };
    const auto number = [&](quint64& value) {
        if (!skipSpace()) {
            return false;
        }
        value = 0;
        int digits = 0;
        while (cursor < bytes.size() && bytes[cursor] >= '0' && bytes[cursor] <= '9') {
            if (++digits > 10) {
                return false;
            }
            value = value * 10 + quint64(bytes[cursor++] - '0');
        }
        return digits != 0 && cursor < bytes.size();
    };
    Header result;
    quint64 maximum = 0;
    if (!number(result.width) || (!isSpace(bytes[cursor]) && bytes[cursor] != '#') ||
        !number(result.height) || (!isSpace(bytes[cursor]) && bytes[cursor] != '#') ||
        !number(maximum) || maximum != 255 || !isSpace(bytes[cursor]) || bytes[cursor] == '\r') {
        return {};
    }
    // Only this single delimiter belongs to the header. The next byte may be
    // binary whitespace or '#'. CR/CRLF variants keep the established reader.
    result.rasterOffset = qint64(cursor + 1);
    return result;
}

struct HorizontalSpan {
    int first = 0;
    int last = 0;
    quint32 firstWeight = 0;
    quint32 lastWeight = 0;
};
using Channels = std::array<quint64, 3>;

void filterRow(const uchar* source, const std::vector<HorizontalSpan>& spans,
               std::vector<Channels>& horizontal, quint64 sourceWidth, quint64 targetWidth,
               const std::atomic_bool* cancelled)
{
    for (size_t x = 0; x < spans.size(); ++x) {
        if ((x & 255) == 0 && isCancelled(cancelled)) {
            return;
        }
        const HorizontalSpan& span = spans[x];
        const uchar* first = source + size_t(span.first) * 3;
        Channels& result = horizontal[x];
        if (span.first == span.last) {
            result = {quint64(first[0]) * sourceWidth, quint64(first[1]) * sourceWidth,
                      quint64(first[2]) * sourceWidth};
            continue;
        }
        const uchar* last = source + size_t(span.last) * 3;
        Channels interior{};
        for (int column = span.first + 1; column < span.last; ++column) {
            const uchar* pixel = source + size_t(column) * 3;
            interior[0] += pixel[0];
            interior[1] += pixel[1];
            interior[2] += pixel[2];
        }
        for (size_t channel = 0; channel < 3; ++channel) {
            result[channel] = quint64(first[channel]) * span.firstWeight +
                              interior[channel] * targetWidth +
                              quint64(last[channel]) * span.lastWeight;
        }
    }
}
} // namespace

QImage readFastPnmPreview(QIODevice& device, const QSize& target, quint64 maximumSourcePixels,
                          qsizetype maximumOutputBytes, const std::atomic_bool* cancelled)
{
    if (!device.isReadable() || device.isSequential() || !target.isValid() || target.isEmpty() ||
        target.width() > kMaximumOutputDimension || target.height() > kMaximumOutputDimension ||
        maximumSourcePixels == 0 || maximumOutputBytes <= 0 || isCancelled(cancelled)) {
        return {};
    }
    const quint64 outputPixels = quint64(target.width()) * quint64(target.height());
    if (outputPixels > kMaximumOutputPixels ||
        outputPixels > std::min(quint64(maximumOutputBytes), kMaximumOutputBytes) / 4) {
        return {};
    }
    const qint64 fileBytes = device.size();
    if (fileBytes <= 0 || !device.seek(0)) {
        return {};
    }
    const bool tracing = qEnvironmentVariableIsSet("LICASA_TRACE_FAST_PNM");
    QElapsedTimer timer;
    if (tracing) {
        timer.start();
    }
    try {
        QByteArray prefix(std::min(fileBytes, kMaximumHeaderBytes), Qt::Uninitialized);
        if (!readExactly(device, prefix.data(), prefix.size(), cancelled)) {
            return {};
        }
        const Header header = parseHeader(prefix);
        if (header.width == 0 || header.height == 0 || header.width > kMaximumSourceDimension ||
            header.height > kMaximumSourceDimension ||
            header.width > maximumSourcePixels / header.height ||
            quint64(target.width()) > header.width || quint64(target.height()) > header.height) {
            return {};
        }
        const quint64 rowBytes = header.width * 3;
        const quint64 rasterBytes = rowBytes * header.height;
        if (rowBytes > kMaximumInputBlockBytes || header.rasterOffset <= 0 ||
            quint64(header.rasterOffset) > quint64(fileBytes) ||
            rasterBytes != quint64(fileBytes) - quint64(header.rasterOffset) ||
            !device.seek(header.rasterOffset)) {
            return {};
        }
        // Each target pixel needs one span and two RGB accumulators. The
        // output, input block, and filter scratch have separate fixed bounds.
        const quint64 filterBytes =
            quint64(target.width()) * (sizeof(HorizontalSpan) + 2 * sizeof(Channels));
        if (filterBytes > kMaximumFilterBytes) {
            return {};
        }
        QImage output(target, QImage::Format_RGB32);
        if (output.isNull() || output.sizeInBytes() > maximumOutputBytes ||
            quint64(output.sizeInBytes()) > kMaximumOutputBytes) {
            return {};
        }
        const quint64 targetWidth = quint64(target.width());
        const quint64 targetHeight = quint64(target.height());
        std::vector<HorizontalSpan> spans(size_t(target.width()));
        std::vector<Channels> horizontal(size_t(target.width()));
        std::vector<Channels> accumulated(size_t(target.width()));
        for (int x = 0; x < target.width(); ++x) {
            const quint64 left = quint64(x) * header.width;
            const quint64 right = quint64(x + 1) * header.width;
            HorizontalSpan& span = spans[size_t(x)];
            span.first = int(left / targetWidth);
            span.last = int((right - 1) / targetWidth);
            span.firstWeight =
                quint32(std::min(right, quint64(span.first + 1) * targetWidth) - left);
            span.lastWeight = quint32(right - quint64(span.last) * targetWidth);
        }
        const quint64 rowsPerBlock = std::min(header.height, kMaximumInputBlockBytes / rowBytes);
        QByteArray input(qsizetype(rowsPerBlock * rowBytes), Qt::Uninitialized);
        // The integer overlap coordinates have denominators targetWidth and
        // targetHeight; their normalized output denominator is width*height.
        // At 20,000 per dimension even 255*width*height fits comfortably in 64 bits.
        const quint64 denominator = header.width * header.height;
        int outputY = 0;
        for (quint64 firstRow = 0; firstRow < header.height; firstRow += rowsPerBlock) {
            const quint64 rows = std::min(rowsPerBlock, header.height - firstRow);
            if (!readExactly(device, input.data(), qint64(rows * rowBytes), cancelled)) {
                return {};
            }
            for (quint64 row = 0; row < rows; ++row) {
                if (isCancelled(cancelled)) {
                    return {};
                }
                const auto* source =
                    reinterpret_cast<const uchar*>(input.constData()) + row * rowBytes;
                filterRow(source, spans, horizontal, header.width, targetWidth, cancelled);
                if (isCancelled(cancelled)) {
                    return {};
                }
                quint64 top = (firstRow + row) * targetHeight;
                const quint64 bottom = top + targetHeight;
                while (top < bottom) {
                    const quint64 outputBottom = quint64(outputY + 1) * header.height;
                    const quint64 weight = std::min(bottom, outputBottom) - top;
                    for (size_t x = 0; x < horizontal.size(); ++x) {
                        for (size_t channel = 0; channel < 3; ++channel) {
                            accumulated[x][channel] += horizontal[x][channel] * weight;
                        }
                    }
                    top += weight;
                    if (top == outputBottom) {
                        auto* destination = reinterpret_cast<QRgb*>(output.scanLine(outputY++));
                        for (size_t x = 0; x < accumulated.size(); ++x) {
                            const Channels& value = accumulated[x];
                            destination[x] = qRgb(int((value[0] + denominator / 2) / denominator),
                                                  int((value[1] + denominator / 2) / denominator),
                                                  int((value[2] + denominator / 2) / denominator));
                            accumulated[x] = {};
                        }
                    }
                }
            }
        }
        if (outputY != target.height() || device.size() != fileBytes || device.pos() != fileBytes ||
            isCancelled(cancelled)) {
            return {};
        }
        if (tracing) {
            qInfo().noquote() << "[Licasa] Fast PNM preview:" << "source="
                              << QSize(int(header.width), int(header.height)) << "output=" << target
                              << "elapsed_ms=" << double(timer.nsecsElapsed()) / 1000000.0;
        }
        return output;
    } catch (const std::bad_alloc&) {
        return {};
    }
}

QImage readFastPnmPreview(const QString& path, const QSize& target, quint64 maximumSourcePixels,
                          qsizetype maximumOutputBytes, const std::atomic_bool* cancelled)
{
    if (isCancelled(cancelled) || !QFileInfo(path).isFile()) {
        return {};
    }
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return {};
    }
    return readFastPnmPreview(file, target, maximumSourcePixels, maximumOutputBytes, cancelled);
}
} // namespace Licasa

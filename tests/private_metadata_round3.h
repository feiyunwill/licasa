#pragma once

#include <QByteArray>
#include <QString>
#include <QtEndian>

namespace MetadataRound3Fixtures {

inline QByteArray xmp(int foreignAttributes, int items)
{
    QByteArray packet =
        "<x:xmpmeta xmlns:x=\"adobe:ns:meta/\" "
        "xmlns:c=\"http://ns.google.com/photos/1.0/camera/\" "
        "xmlns:v=\"urn:camera-vendor\" "
        "xmlns:k=\"http://ns.google.com/photos/1.0/container/\" "
        "xmlns:i=\"http://ns.google.com/photos/1.0/container/item/\"><x:Description";
    for (int index = 0; index < foreignAttributes; ++index) {
        packet += " v:Field" + QByteArray::number(index) + "=\"camera metadata\"";
    }
    packet += " c:MotionPhoto=\"0\" c:MotionPhotoVersion=\"  +1  \" "
              "c:MotionPhotoPresentationTimestampUs=\" 9223372036854775807 \" "
              "c:MicroVideo=\"0\" c:MicroVideoVersion=\"1\" "
              "c:MicroVideoOffset=\" 18446744073709551615 \" "
              "c:MicroVideoPresentationTimestampUs=\" -1 \"><k:Directory>";
    for (int index = 0; index < items; ++index) {
        packet += "<k:Item i:Mime=\"image/jpeg\" i:Semantic=\"Auxiliary\" "
                  "i:Length=\" 18446744073709551615 \" i:Padding=\" +0 \"/>";
    }
    packet += "</k:Directory></x:Description></x:xmpmeta>";
    return packet;
}

inline void append16(QByteArray& bytes, quint16 value, bool little)
{
    char raw[2];
    if (little) {
        qToLittleEndian(value, reinterpret_cast<uchar*>(raw));
    } else {
        qToBigEndian(value, reinterpret_cast<uchar*>(raw));
    }
    bytes.append(raw, 2);
}

inline void append32(QByteArray& bytes, quint32 value, bool little)
{
    char raw[4];
    if (little) {
        qToLittleEndian(value, reinterpret_cast<uchar*>(raw));
    } else {
        qToBigEndian(value, reinterpret_cast<uchar*>(raw));
    }
    bytes.append(raw, 4);
}

inline QByteArray appleTiff(int makerBytes, bool little = false)
{
    const QByteArray identifier("round-three-content-identifier");
    QByteArray maker("Apple iOS\0", 10);
    maker += QByteArray::fromHex(little ? "00014949" : "00014d4d");
    append16(maker, 1, little);
    append16(maker, 0x0011, little);
    append16(maker, 2, little);
    append32(maker, quint32(identifier.size() + 1), little);
    append32(maker, 32, little);
    append32(maker, 0, little);
    maker += identifier;
    maker += '\0';
    if (makerBytes > maker.size()) {
        maker += QByteArray(makerBytes - maker.size(), '\0');
    }

    QByteArray tiff = little ? QByteArray::fromHex("49492a00") : QByteArray("MM\0*", 4);
    append32(tiff, 8, little);
    append16(tiff, 1, little);
    append16(tiff, 0x8769, little);
    append16(tiff, 4, little);
    append32(tiff, 1, little);
    append32(tiff, 26, little);
    append32(tiff, 0, little);
    append16(tiff, 1, little);
    append16(tiff, 0x927c, little);
    append16(tiff, 7, little);
    append32(tiff, quint32(maker.size()), little);
    append32(tiff, 44, little);
    append32(tiff, 0, little);
    return tiff + maker;
}

inline QByteArray heifExif(int makerBytes, bool little = false)
{
    QByteArray block;
    append32(block, 128, false);
    block += QByteArray(128, '\0');
    return block + appleTiff(makerBytes, little);
}

inline QByteArray jpegSegment(const QByteArray& payload)
{
    QByteArray segment = QByteArray::fromHex("ffe1");
    append16(segment, quint16(payload.size() + 2), false);
    return segment + payload;
}

inline QByteArray metadataJpeg(int ignoredSegments, int payloadBytes, bool withMotion)
{
    QByteArray jpeg = QByteArray::fromHex("ffd8");
    for (int index = 0; index < ignoredSegments; ++index) {
        QByteArray payload("Exif\0\0", 6);
        payload += QByteArray(payloadBytes - payload.size(), char(index + 1));
        jpeg += jpegSegment(payload);
    }
    if (withMotion) {
        QByteArray packet("http://ns.adobe.com/xap/1.0/\0", 29);
        packet += "<x xmlns:c=\"http://ns.google.com/photos/1.0/camera/\" "
                  "xmlns:k=\"http://ns.google.com/photos/1.0/container/\" "
                  "xmlns:i=\"http://ns.google.com/photos/1.0/container/item/\" "
                  "c:MotionPhoto=\"1\" c:MotionPhotoVersion=\"1\" "
                  "c:MotionPhotoPresentationTimestampUs=\"42\">"
                  "<k:Directory><k:Item i:Mime=\"image/jpeg\" i:Semantic=\"Primary\"/>"
                  "<k:Item i:Mime=\"video/mp4\" i:Semantic=\"MotionPhoto\" i:Length=\"24\"/>"
                  "</k:Directory></x>";
        jpeg += jpegSegment(packet);
    }
    jpeg += QByteArray::fromHex("ffd9");
    if (withMotion) {
        jpeg += QByteArray::fromHex("000000186674797069736f6d0000020069736f6d6d703431");
    }
    return jpeg;
}

} // namespace MetadataRound3Fixtures

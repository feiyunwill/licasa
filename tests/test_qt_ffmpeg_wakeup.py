#!/usr/bin/env python3
"""Source drift must require review of the deterministic Qt wait harness."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from verify_qt_ffmpeg_wakeup import start_method


class QtDemuxerWakeupContract(unittest.TestCase):
    def test_extracts_actual_complete_start_method(self):
        method = """    void startDecoding()
    {
        QMutexLocker locker(&mutex);
        m_isStopped.storeRelaxed(false);
        updateEnabledStreams();
        wake();
    }"""
        self.assertEqual(start_method("class Demuxer {\n" + method + "\n};"), method)

    def test_rejects_unprotected_or_reordered_predicate(self):
        for body in ("m_isStopped.storeRelaxed(false); wake();",
                     "m_isStopped.storeRelaxed(false); QMutexLocker locker(&mutex); wake();"):
            with self.subTest(body=body), self.assertRaises(RuntimeError):
                start_method("    void startDecoding() { " + body + " }")

    def test_source_restructure_requires_explicit_review(self):
        for header in ("", "    void startDecoding() {", "    void startDecode() {}",
                       "    void startDecoding() { QMutexLocker locker(&mutex); wake(); }"):
            with self.subTest(header=header), self.assertRaises(RuntimeError):
                start_method(header)


if __name__ == "__main__":
    unittest.main()

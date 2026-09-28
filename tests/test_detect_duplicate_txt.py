import io
import json
import os
import random
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import detect_duplicate_txt as detector


PHRASE = "春江潮水连海平海上明月共潮生"
OTHER = "完全不同的故事从这里开始写起"
CYCLE = "子丑寅卯辰巳午未申酉戌亥"


def raw_shingles(text, ngram, step):
    text = detector.normalize_text(text)
    if len(text) < ngram:
        return set()
    return {
        text[index:index + ngram]
        for index in range(0, len(text) - ngram + 1, step)
    }


def raw_scores(text_a, text_b, ngram, step):
    left = raw_shingles(text_a, ngram, step)
    right = raw_shingles(text_b, ngram, step)
    intersection = len(left & right)
    jaccard = intersection / len(left | right)
    containment = intersection / min(len(left), len(right))
    return jaccard, containment


def write_text(directory, name, text, encoding="utf-8"):
    path = os.path.join(directory, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=encoding) as handle:
        handle.write(text)
    return path


class MetricTests(unittest.TestCase):
    def test_normalize_keeps_letters_numbers_and_chinese(self):
        self.assertEqual(
            detector.normalize_text("Hello, 世界! 123 — Café"),
            "hello世界123caf",
        )

    def test_fingerprint_count_matches_raw_shingles(self):
        text = "".join(chr(0x4E00 + index) for index in range(120))
        raw = raw_shingles(text, 10, 2)
        self.assertEqual(len(detector.make_shingles(text, 10, 2)), len(raw))
        self.assertEqual(len(raw), len({text[i:i + 10] for i in range(0, 111, 2)}))

    def test_large_text_fingerprints_do_not_collide(self):
        rng = random.Random(0)
        text = "".join(chr(0x4E00 + rng.randrange(20000)) for _ in range(200_000))
        raw = raw_shingles(text, 10, 2)
        self.assertEqual(len(detector.make_shingles(text, 10, 2)), len(raw))
        self.assertGreater(len(raw), 90000)

    def test_fingerprint_scores_match_raw_strings(self):
        left = PHRASE * 40
        right = PHRASE * 30 + OTHER * 20
        raw_jaccard, raw_containment = raw_scores(left, right, 30, 5)
        left_set = detector.make_shingles(detector.normalize_text(left), 30, 5)
        right_set = detector.make_shingles(detector.normalize_text(right), 30, 5)
        self.assertAlmostEqual(
            detector.jaccard_similarity(left_set, right_set),
            raw_jaccard,
        )
        smaller, larger = (
            (left_set, right_set)
            if len(left_set) <= len(right_set)
            else (right_set, left_set)
        )
        self.assertAlmostEqual(
            detector.containment_similarity(smaller, larger),
            raw_containment,
        )

    def test_risk_label_boundaries(self):
        self.assertEqual(detector.get_risk_label(0.0, 0.80), "EXTREME")
        self.assertEqual(detector.get_risk_label(0.0, 0.50), "VERY HIGH")
        self.assertEqual(detector.get_risk_label(0.30, 0.0), "VERY HIGH")
        self.assertEqual(detector.get_risk_label(0.0, 0.30), "HIGH")
        self.assertEqual(detector.get_risk_label(0.15, 0.0), "HIGH")
        self.assertEqual(detector.get_risk_label(0.0, 0.18), "MEDIUM")
        self.assertEqual(detector.get_risk_label(0.08, 0.0), "MEDIUM")
        self.assertEqual(detector.get_risk_label(0.07, 0.12), "LOW")

    def test_progress_is_written_to_stderr(self):
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            detector.print_progress(1, 2, time.time(), "Comparing")
        message = buffer.getvalue()
        self.assertIn("Comparing", message)
        self.assertIn("50.00%", message)
        self.assertTrue(message.startswith("\r["))


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = self.temp.name

    def tearDown(self):
        self.temp.cleanup()

    def test_partial_copy_matches_raw_scores(self):
        write_text(self.directory, "a.txt", PHRASE * 40)
        write_text(self.directory, "b.txt", PHRASE * 30 + OTHER * 20)
        write_text(self.directory, "c.txt", CYCLE * 40)

        result = detector.analyze(self.directory, mode_name="near-duplicate")
        raw_jaccard, raw_containment = raw_scores(
            PHRASE * 40,
            PHRASE * 30 + OTHER * 20,
            30,
            5,
        )

        self.assertEqual(result["file_count"], 3)
        self.assertEqual(len(result["pairs"]), 1)
        pair = result["pairs"][0]
        self.assertEqual((pair["file1"], pair["file2"]), ("a.txt", "b.txt"))
        self.assertAlmostEqual(pair["jaccard"], raw_jaccard, places=6)
        self.assertAlmostEqual(pair["containment"], raw_containment, places=6)
        self.assertEqual(pair["risk"], "EXTREME")
        self.assertEqual(pair["containment"], 1.0)
        self.assertEqual(result["comparison_method"], "pairwise")

        report = detector.format_text_report(result)
        self.assertIn("Jaccard: 0.424, Containment: 1.000", report)
        self.assertIn("Lengths: 560 chars vs 700 chars", report)
        self.assertNotIn("Elapsed:", report)

    def test_unrelated_files_are_not_reported(self):
        write_text(self.directory, "a.txt", "".join(chr(0x4E00 + i) for i in range(400)))
        write_text(self.directory, "b.txt", "".join(chr(0x6000 + i) for i in range(400)))
        result = detector.analyze(self.directory)
        self.assertEqual(result["pairs"], [])

    def test_exact_copies_are_grouped(self):
        text = PHRASE * 20
        for index in range(12):
            write_text(self.directory, f"copy_{index:02d}.txt", text)

        result = detector.analyze(self.directory, mode_name="near-duplicate")
        self.assertEqual(result["unique_texts"], 1)
        self.assertEqual(result["fuzzy_group_count"], 1)
        self.assertEqual(result["comparison_method"], "pairwise")
        self.assertEqual(len(result["pairs"]), 66)
        self.assertTrue(all(pair["jaccard"] == 1.0 for pair in result["pairs"]))
        self.assertTrue(all(pair["risk"] == "EXTREME" for pair in result["pairs"]))
        self.assertEqual(result["pairs"][0]["file1"], "copy_00.txt")
        self.assertEqual(result["pairs"][0]["file2"], "copy_01.txt")

    def test_exact_group_expands_partial_matches(self):
        shared = "".join(chr(0x4E00 + index) for index in range(600))
        extra = "".join(chr(0x7000 + index) for index in range(400))
        write_text(self.directory, "a_second.txt", shared)
        write_text(self.directory, "a_first.txt", shared)
        write_text(self.directory, "b.txt", shared[:500] + extra)

        pairwise = detector.analyze(self.directory, method="pairwise")
        indexed = detector.analyze(self.directory, method="index")
        self.assertEqual(pairwise["unique_texts"], 2)
        self.assertEqual(self._pair_key(pairwise), self._pair_key(indexed))

        exact = [
            pair for pair in pairwise["pairs"]
            if {pair["file1"], pair["file2"]} == {"a_first.txt", "a_second.txt"}
        ]
        self.assertEqual(len(exact), 1)
        self.assertEqual(exact[0]["jaccard"], 1.0)

        cross = [
            pair for pair in pairwise["pairs"]
            if "b.txt" in (pair["file1"], pair["file2"])
        ]
        self.assertEqual(len(cross), 2)
        self.assertAlmostEqual(cross[0]["jaccard"], cross[1]["jaccard"], places=6)
        self.assertAlmostEqual(
            cross[0]["containment"],
            cross[1]["containment"],
            places=6,
        )
        self.assertGreater(cross[0]["containment"], 0.5)

    def test_short_exact_duplicates_are_reported(self):
        write_text(self.directory, "one.txt", "甲乙丙")
        write_text(self.directory, "two.txt", "甲乙丙")
        write_text(self.directory, "three.txt", "丁戊己")
        result = detector.analyze(self.directory, mode_name="chinese-novel-strict")
        self.assertEqual(result["short_file_count"], 3)
        self.assertEqual(len(result["pairs"]), 1)
        self.assertEqual(result["pairs"][0]["file1"], "one.txt")
        self.assertEqual(result["pairs"][0]["file2"], "two.txt")
        self.assertEqual(result["pairs"][0]["risk"], "EXTREME")
        self.assertIn("only checked for exact duplicates", detector.format_text_body(result))

    def test_punctuation_only_files_are_skipped(self):
        write_text(self.directory, "empty.txt", " \n\t，。！？")
        write_text(self.directory, "real.txt", PHRASE * 5)
        result = detector.analyze(self.directory)
        self.assertEqual(result["pairs"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["file"], "empty.txt")
        self.assertIn("normalization", result["skipped"][0]["reason"])

    def test_min_chars_skips_small_files(self):
        write_text(self.directory, "small.txt", PHRASE)
        write_text(self.directory, "large_a.txt", PHRASE * 10)
        write_text(self.directory, "large_b.txt", PHRASE * 10)
        result = detector.analyze(self.directory, min_chars=100)
        self.assertEqual(result["skipped"][0]["file"], "small.txt")
        self.assertEqual(len(result["pairs"]), 1)
        self.assertEqual(result["pairs"][0]["file1"], "large_a.txt")

    def test_directory_named_txt_is_ignored(self):
        os.mkdir(os.path.join(self.directory, "notes.txt"))
        write_text(self.directory, "real.txt", PHRASE * 5)
        result = detector.analyze(self.directory)
        self.assertEqual(result["file_count"], 1)
        self.assertEqual(result["skipped"], [])

    def test_recursive_scan(self):
        write_text(self.directory, "top.txt", PHRASE * 8)
        write_text(self.directory, os.path.join("chapters", "one.txt"), PHRASE * 8)
        flat = detector.analyze(self.directory)
        nested = detector.analyze(self.directory, recursive=True)
        self.assertEqual(flat["file_count"], 1)
        self.assertEqual(nested["file_count"], 2)
        self.assertEqual(nested["pairs"][0]["file1"], "chapters/one.txt")
        self.assertEqual(nested["pairs"][0]["file2"], "top.txt")

    def test_hardlink_is_an_exact_duplicate(self):
        source = write_text(self.directory, "original.txt", PHRASE * 6)
        alias = os.path.join(self.directory, "alias.txt")
        try:
            os.link(source, alias)
        except OSError:
            self.skipTest("hardlinks are not supported on this filesystem")

        result = detector.analyze(self.directory, mode_name="near-duplicate")
        self.assertEqual(result["unique_texts"], 1)
        self.assertEqual(len(result["pairs"]), 1)
        self.assertEqual(result["pairs"][0]["jaccard"], 1.0)
        self.assertEqual(
            {result["pairs"][0]["file1"], result["pairs"][0]["file2"]},
            {"alias.txt", "original.txt"},
        )

    def test_broken_symlink_is_skipped(self):
        os.symlink(
            os.path.join(self.directory, "missing.txt"),
            os.path.join(self.directory, "broken.txt"),
        )
        write_text(self.directory, "real.txt", PHRASE * 4)
        result = detector.analyze(self.directory)
        self.assertEqual(result["file_count"], 2)
        self.assertEqual(result["skipped"][0]["file"], "broken.txt")
        self.assertIn("could not read file", result["skipped"][0]["reason"])

    def test_encodings_round_trip_to_the_same_text(self):
        simplified = "这是一段简体中文小说文本，用来测试编码是否正确识别。" * 4
        traditional = "這是一段繁體中文小說文本，用來測試編碼是否正確識別。" * 4
        english = "The café opened early — and the owner smiled."

        write_text(self.directory, "simplified-utf8.txt", simplified, "utf-8")
        write_text(self.directory, "simplified-gb.txt", simplified, "gb18030")
        write_text(self.directory, "traditional-utf8.txt", traditional, "utf-8")
        write_text(self.directory, "traditional-big5.txt", traditional, "big5")
        write_text(self.directory, "english-utf8.txt", english, "utf-8")
        with open(os.path.join(self.directory, "english-cp1252.txt"), "wb") as handle:
            handle.write(english.encode("cp1252"))
        with open(os.path.join(self.directory, "simplified-utf16.txt"), "wb") as handle:
            handle.write(simplified.encode("utf-16-le"))
        with open(os.path.join(self.directory, "simplified-utf16be.txt"), "wb") as handle:
            handle.write(simplified.encode("utf-16-be"))
        with open(os.path.join(self.directory, "bom-utf8.txt"), "wb") as handle:
            handle.write(b"\xef\xbb\xbf" + simplified.encode("utf-8"))

        result = detector.analyze(self.directory, mode_name="near-duplicate")
        pairs = {
            (pair["file1"], pair["file2"])
            for pair in result["pairs"]
            if pair["jaccard"] == 1.0
        }
        self.assertIn(("simplified-gb.txt", "simplified-utf8.txt"), pairs)
        self.assertIn(("bom-utf8.txt", "simplified-utf8.txt"), pairs)
        self.assertIn(("simplified-utf16.txt", "simplified-utf8.txt"), pairs)
        self.assertIn(("simplified-utf16be.txt", "simplified-utf8.txt"), pairs)
        self.assertIn(("traditional-big5.txt", "traditional-utf8.txt"), pairs)
        self.assertIn(("english-cp1252.txt", "english-utf8.txt"), pairs)
        self.assertEqual(result["skipped"], [])

    def test_methods_agree_and_large_libraries_use_the_index(self):
        count = 143
        for index in range(count):
            prefix = "共同的开头被写在每一份草稿里面"
            suffix = "".join(chr(0x4E00 + (index * 3 + offset) % 20000) for offset in range(24))
            write_text(self.directory, f"draft_{index:03d}.txt", prefix + suffix)

        pairwise = detector.analyze(self.directory, method="pairwise", ngram=8, step=2)
        indexed = detector.analyze(
            self.directory,
            method="index",
            ngram=8,
            step=2,
            max_index_updates=10**9,
        )
        automatic = detector.analyze(self.directory, ngram=8, step=2)
        fallback = detector.analyze(
            self.directory,
            ngram=8,
            step=2,
            max_index_updates=0,
        )

        self.assertEqual(self._pair_key(pairwise), self._pair_key(indexed))
        self.assertEqual(self._pair_key(pairwise), self._pair_key(automatic))
        self.assertEqual(indexed["comparison_method"], "index")
        self.assertEqual(automatic["comparison_method"], "index")
        self.assertEqual(fallback["comparison_method"], "pairwise")
        self.assertGreater(len(pairwise["pairs"]), 0)

        sample = pairwise["pairs"][0]
        with open(os.path.join(self.directory, sample["file1"]), encoding="utf-8") as handle:
            left = handle.read()
        with open(os.path.join(self.directory, sample["file2"]), encoding="utf-8") as handle:
            right = handle.read()
        raw_jaccard, raw_containment = raw_scores(left, right, 8, 2)
        self.assertAlmostEqual(sample["jaccard"], raw_jaccard, places=6)
        self.assertAlmostEqual(sample["containment"], raw_containment, places=6)

    def test_zero_threshold_keeps_non_overlapping_pairs(self):
        for index in range(4):
            write_text(
                self.directory,
                f"{index}.txt",
                "".join(chr(0x4E00 + index * 30 + offset) for offset in range(40)),
            )
        result = detector.analyze(
            self.directory,
            similarity=0,
            containment=0.2,
            ngram=8,
            step=2,
            method="index",
        )
        self.assertEqual(result["comparison_method"], "pairwise")
        self.assertEqual(len(result["pairs"]), 6)

    def test_custom_threshold_filters_weak_overlap(self):
        write_text(self.directory, "a.txt", PHRASE * 20 + OTHER * 20)
        write_text(self.directory, "b.txt", PHRASE * 4 + CYCLE * 20)
        loose = detector.analyze(self.directory, mode_name="chinese-novel-sensitive")
        strict = detector.analyze(
            self.directory,
            mode_name="chinese-novel-sensitive",
            similarity=0.99,
            containment=0.99,
        )
        self.assertEqual(len(loose["pairs"]), 1)
        self.assertEqual(strict["pairs"], [])
        self.assertIn("custom settings", strict["mode_description"])

    def test_empty_directory_message(self):
        result = detector.analyze(self.directory)
        self.assertIn("No .txt files found.", detector.format_text_body(result))

    def _pair_key(self, result):
        return [
            (
                pair["file1"],
                pair["file2"],
                round(pair["jaccard"], 6),
                round(pair["containment"], 6),
                pair["risk"],
            )
            for pair in result["pairs"]
        ]


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = self.temp.name

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "detect_duplicate_txt.py"), *args],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_missing_directory_exits_with_error(self):
        missing = os.path.join(self.directory, "missing")
        completed = self.run_cli(missing)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("directory does not exist", completed.stderr)
        self.assertEqual(completed.stdout, "")

    def test_text_report_has_no_progress_when_stdout_is_piped(self):
        write_text(self.directory, "a.txt", PHRASE * 40)
        write_text(self.directory, "b.txt", PHRASE * 30 + OTHER * 20)
        write_text(self.directory, "c.txt", CYCLE * 40)
        completed = self.run_cli(self.directory, "--mode", "near-duplicate")
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, "")
        self.assertNotIn("Elapsed:", completed.stdout)
        self.assertIn("Found txt files: 3", completed.stdout)
        self.assertIn("[EXTREME] a.txt <-> b.txt", completed.stdout)
        self.assertIn("Jaccard: 0.424, Containment: 1.000", completed.stdout)

    def test_json_output_file(self):
        write_text(self.directory, "a.txt", PHRASE * 12)
        write_text(self.directory, "b.txt", PHRASE * 12)
        output = os.path.join(self.directory, "report.json")
        completed = self.run_cli(
            self.directory,
            "--format",
            "json",
            "--output",
            output,
            "--quiet",
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("Wrote report to", completed.stderr)
        with open(output, encoding="utf-8") as handle:
            payload = json.loads(handle.read())
        self.assertEqual(payload["file_count"], 2)
        self.assertEqual(payload["pairs"][0]["risk"], "EXTREME")
        self.assertEqual(payload["pairs"][0]["jaccard"], 1.0)

    def test_invalid_threshold_is_rejected(self):
        completed = self.run_cli(self.directory, "--jaccard", "1.5")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("between 0 and 1", completed.stderr)

    def test_help_lists_modes(self):
        completed = self.run_cli("--help")
        self.assertEqual(completed.returncode, 0)
        self.assertIn("chinese-novel-balanced", completed.stdout)
        self.assertIn("near-duplicate", completed.stdout)


if __name__ == "__main__":
    unittest.main()

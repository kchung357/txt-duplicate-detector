"""Detect suspiciously similar .txt files.

Comparison uses character n-gram shingles, Jaccard similarity, and
containment. The tool is meant for novels and other plain-text collections.
It reports suspicious overlap; it does not prove plagiarism.
"""

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import time


MODES = {
    "chinese-novel-sensitive": {
        "ngram": 8,
        "step": 2,
        "similarity": 0.05,
        "containment": 0.12,
        "description": "Chinese novels, sensitive copycat detection"
    },
    "chinese-novel-balanced": {
        "ngram": 10,
        "step": 2,
        "similarity": 0.07,
        "containment": 0.15,
        "description": "Chinese novels, recommended default"
    },
    "chinese-novel-strict": {
        "ngram": 12,
        "step": 3,
        "similarity": 0.12,
        "containment": 0.25,
        "description": "Chinese novels, fewer false positives"
    },
    "english-book-balanced": {
        "ngram": 30,
        "step": 5,
        "similarity": 0.08,
        "containment": 0.18,
        "description": "English books, balanced detection"
    },
    "near-duplicate": {
        "ngram": 30,
        "step": 5,
        "similarity": 0.50,
        "containment": 0.75,
        "description": "Same book or near-duplicate files"
    },
}

# Direct set intersection is faster for a modest number of texts. Above this
# many unique-text pairs, an inverted index avoids comparing unrelated books.
INVERTED_INDEX_MIN_PAIRS = 10000

# A shingle shared by many files makes the inverted index slower than direct
# comparison. Fall back when the estimated pair updates exceed this budget.
MAX_INDEX_UPDATES = 20000000

_NON_TEXT = re.compile(r"[^0-9a-z\u4e00-\u9fff]")


def format_time(seconds):
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = seconds % 3600 // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def print_progress(current, total, start_time, action="Working"):
    percent = current / total if total else 1
    bar_length = 35
    filled_length = int(bar_length * percent)
    bar = "#" * filled_length + "-" * (bar_length - filled_length)
    elapsed = time.time() - start_time

    if current > 0 and total:
        remaining = elapsed / current * total - elapsed
    else:
        remaining = 0

    message = (
        f"\r[{bar}] "
        f"{percent * 100:6.2f}% "
        f"{current}/{total} "
        f"Elapsed: {format_time(elapsed)} "
        f"ETA: {format_time(remaining)} "
        f"{action}"
    )
    sys.stderr.write(message)
    sys.stderr.flush()


class ProgressBar:
    """Throttled stderr progress bar. Disabled when stderr is not a terminal."""

    def __init__(self, enabled):
        self.enabled = enabled
        self.total = 0
        self.current = 0
        self.action = ""
        self.start_time = time.time()
        self.last_draw = 0.0
        self.active = False

    def start(self, total, action):
        self.total = total
        self.current = 0
        self.action = action
        self.start_time = time.time()
        self.last_draw = 0.0
        self.active = self.enabled and total > 0
        if self.active:
            self._draw()

    def advance(self, amount=1):
        if not self.active:
            return
        self.current += amount
        self._draw_if_due()

    def set(self, current):
        if not self.active:
            return
        self.current = current
        self._draw_if_due()

    def finish(self):
        if not self.active:
            return
        self.current = self.total
        self._draw()
        sys.stderr.write("\n")
        sys.stderr.flush()
        self.active = False

    def _draw_if_due(self):
        now = time.time()
        if self.current >= self.total or now - self.last_draw >= 0.1:
            self._draw()
            self.last_draw = now

    def _draw(self):
        print_progress(self.current, self.total, self.start_time, self.action)


def read_file_bytes(file_path):
    with open(file_path, "rb") as file:
        return file.read()


def _text_score(text):
    """Prefer readable text over a decoding that happens to succeed."""
    cjk = 0
    letters = 0
    nuls = 0
    for char in text:
        if "\u4e00" <= char <= "\u9fff":
            cjk += 1
        elif char.isascii() and char.isalnum():
            letters += 1
        elif char == "\x00":
            nuls += 1
    return (cjk, letters, -nuls)


def _utf16_without_bom(data):
    """Return UTF-16 decodings whose high bytes look like text.

    Chinese UTF-16 does not contain a NUL in every other byte, so a NUL
    count is not enough. A real UTF-16 stream keeps one byte of each unit
    in ASCII (zero) or in the CJK high-byte range.
    """
    if len(data) < 4 or len(data) % 2:
        return []

    candidates = []
    # Index 1 is the high byte for little-endian, index 0 for big-endian.
    endian_checks = (
        ("utf-16-le", data[1::2]),
        ("utf-16-be", data[0::2]),
    )
    for encoding, high_bytes in endian_checks:
        zeros = high_bytes.count(0)
        cjk_high = sum(1 for byte in high_bytes if 0x4E <= byte <= 0x9F)
        if (zeros + cjk_high) / len(high_bytes) < 0.75:
            continue
        try:
            candidates.append(data.decode(encoding))
        except UnicodeDecodeError:
            pass
    return candidates


def decode_text(data):
    """Decode plain text from the encodings common in novel dumps.

    UTF-8 is preferred when it is valid. When several legacy decodings
    succeed, the one with more readable letters and Chinese characters wins.
    """
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16")

    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass

    candidates = _utf16_without_bom(data)
    for encoding in ("gb18030", "big5"):
        try:
            candidates.append(data.decode(encoding))
        except UnicodeDecodeError:
            pass

    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        return max(candidates, key=_text_score)

    try:
        return data.decode("cp1252")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def normalize_text(text):
    text = text.lower()
    # Keep Chinese characters, English letters, and numbers only.
    return _NON_TEXT.sub("", text)


def make_shingles(text, n=10, step=2):
    """Return 64-bit BLAKE2s fingerprints for character n-grams."""
    if n < 1 or step < 1:
        raise ValueError("n-gram size and step must be at least 1")
    if len(text) < n:
        return set()

    raw = text.encode("utf-32-le")
    width = n * 4
    stride = step * 4
    fingerprint = hashlib.blake2s
    shingles = set()
    add = shingles.add
    last = len(raw) - width + 1

    for start in range(0, last, stride):
        digest = fingerprint(raw[start:start + width], digest_size=8).digest()
        add(int.from_bytes(digest, "little"))

    return shingles


def jaccard_similarity(set1, set2):
    if not set1 or not set2:
        return 0.0

    intersection_count = len(set1 & set2)
    union_count = len(set1 | set2)

    if union_count == 0:
        return 0.0

    return intersection_count / union_count


def containment_similarity(smaller_set, larger_set):
    if not smaller_set:
        return 0.0

    return len(smaller_set & larger_set) / len(smaller_set)


def similarity_from_counts(count_a, count_b, intersection):
    if count_a <= 0 or count_b <= 0:
        return 0.0, 0.0

    union = count_a + count_b - intersection
    if union <= 0:
        return 0.0, 0.0

    jaccard = intersection / union
    containment = intersection / min(count_a, count_b)
    return jaccard, containment


def is_match(jaccard, containment, intersection, similarity_threshold, containment_threshold):
    # A threshold of 0 accepts every pair of shingled files, including pairs
    # whose overlap is empty. That matches the previous comparison rule.
    if similarity_threshold <= 0 or containment_threshold <= 0:
        return True
    if intersection <= 0:
        return False
    return (
        jaccard >= similarity_threshold
        or containment >= containment_threshold
    )


def get_risk_label(jaccard, containment):
    if containment >= 0.80:
        return "EXTREME"
    if containment >= 0.50 or jaccard >= 0.30:
        return "VERY HIGH"
    if containment >= 0.30 or jaccard >= 0.15:
        return "HIGH"
    if containment >= 0.18 or jaccard >= 0.08:
        return "MEDIUM"
    return "LOW"


def make_pair(
    name_a,
    name_b,
    jaccard,
    containment,
    chars_a,
    chars_b,
    shingles_a,
    shingles_b,
):
    if name_a > name_b:
        name_a, name_b = name_b, name_a
        chars_a, chars_b = chars_b, chars_a
        shingles_a, shingles_b = shingles_b, shingles_a

    return {
        "file1": name_a,
        "file2": name_b,
        "jaccard": jaccard,
        "containment": containment,
        "risk": get_risk_label(jaccard, containment),
        "file1_chars": chars_a,
        "file2_chars": chars_b,
        "file1_shingles": shingles_a,
        "file2_shingles": shingles_b,
    }


def discover_entries(directory, recursive):
    paths = []

    if recursive:
        for root, dirnames, filenames in os.walk(directory):
            dirnames.sort()
            for filename in filenames:
                if filename.lower().endswith(".txt"):
                    paths.append(os.path.join(root, filename))
    else:
        for filename in os.listdir(directory):
            if filename.lower().endswith(".txt"):
                paths.append(os.path.join(directory, filename))

    entries = []
    for path in paths:
        display = os.path.relpath(path, directory)
        try:
            stat_result = os.stat(path)
        except OSError as error:
            entries.append({
                "display": display,
                "path": path,
                "error": error,
            })
            continue

        if not stat.S_ISREG(stat_result.st_mode):
            continue

        entries.append({
            "display": display,
            "path": path,
            "inode": (stat_result.st_dev, stat_result.st_ino),
        })

    entries.sort(key=lambda item: item["display"])
    return entries


def load_groups(entries, ngram_size, step, min_chars, progress):
    """Read texts, group exact normalized copies, and shingle one copy of each."""
    progress.start(len(entries), "Reading/building")
    groups_by_key = {}
    groups = []
    name_to_group = {}
    skipped = []
    short_file_count = 0
    aliases = []
    seen_inodes = {}

    for entry in entries:
        display = entry["display"]

        if "error" in entry:
            skipped.append({
                "file": display,
                "reason": "could not read file: "
                + str(entry["error"].strerror or entry["error"]),
            })
            progress.advance()
            continue

        original = seen_inodes.get(entry["inode"])
        if original is not None:
            aliases.append((display, original))
            progress.advance()
            continue
        seen_inodes[entry["inode"]] = display

        try:
            data = read_file_bytes(entry["path"])
        except OSError as error:
            skipped.append({
                "file": display,
                "reason": "could not read file: " + str(error.strerror or error),
            })
            progress.advance()
            continue

        text = normalize_text(decode_text(data))
        if not text:
            skipped.append({
                "file": display,
                "reason": "no letters, numbers, or Chinese characters after normalization",
            })
            progress.advance()
            continue

        if min_chars and len(text) < min_chars:
            skipped.append({
                "file": display,
                "reason": f"shorter than {min_chars} characters after normalization",
            })
            progress.advance()
            continue

        if len(text) < ngram_size:
            short_file_count += 1

        # Length is part of the key so different texts cannot share a truncated digest.
        # Only the first copy of a text is shingled.
        key = (len(text), hashlib.md5(text.encode("utf-8")).hexdigest())
        group = groups_by_key.get(key)
        if group is None:
            if len(text) < ngram_size:
                shingles = set()
            else:
                shingles = make_shingles(text, ngram_size, step)
            group = {
                "names": [display],
                "char_count": len(text),
                "shingle_count": len(shingles),
                "shingles": shingles,
            }
            groups_by_key[key] = group
            groups.append(group)
        else:
            group["names"].append(display)

        name_to_group[display] = group
        progress.advance()

    for display, original in aliases:
        group = name_to_group.get(original)
        if group is None:
            skipped.append({
                "file": display,
                "reason": f"same file as {original}",
            })
            continue
        group["names"].append(display)
        if group["char_count"] < ngram_size:
            short_file_count += 1

    progress.finish()
    skipped.sort(key=lambda item: item["file"])
    return groups, skipped, short_file_count


def exact_duplicate_pairs(groups):
    pairs = []
    for group in groups:
        names = sorted(group["names"])
        if len(names) < 2:
            continue
        for left in range(len(names)):
            for right in range(left + 1, len(names)):
                pairs.append(make_pair(
                    names[left],
                    names[right],
                    1.0,
                    1.0,
                    group["char_count"],
                    group["char_count"],
                    group["shingle_count"],
                    group["shingle_count"],
                ))
    return pairs


def expand_matches(groups, matches):
    pairs = []
    for left_id, right_id, jaccard, containment in matches:
        left = groups[left_id]
        right = groups[right_id]
        for name_a in left["names"]:
            for name_b in right["names"]:
                pairs.append(make_pair(
                    name_a,
                    name_b,
                    jaccard,
                    containment,
                    left["char_count"],
                    right["char_count"],
                    left["shingle_count"],
                    right["shingle_count"],
                ))
    return pairs


def compare_pairwise(groups, similarity_threshold, containment_threshold, progress):
    indexed = [
        (group_id, group)
        for group_id, group in enumerate(groups)
        if group["shingle_count"]
    ]
    total = len(indexed) * (len(indexed) - 1) // 2
    progress.start(total, "Comparing")
    matches = []

    for left in range(len(indexed)):
        left_id, left_group = indexed[left]
        left_set = left_group["shingles"]
        for right in range(left + 1, len(indexed)):
            right_id, right_group = indexed[right]
            intersection = len(left_set & right_group["shingles"])
            jaccard, containment = similarity_from_counts(
                left_group["shingle_count"],
                right_group["shingle_count"],
                intersection,
            )
            if is_match(
                jaccard,
                containment,
                intersection,
                similarity_threshold,
                containment_threshold,
            ):
                matches.append((left_id, right_id, jaccard, containment))
            progress.advance()

    progress.finish()
    return matches, "pairwise"


def index_update_cost(index, budget):
    cost = 0
    for posting in index.values():
        length = len(posting)
        if length < 2:
            continue
        cost += length * (length - 1) // 2
        if cost > budget:
            return cost
    return cost


def compare_index(
    groups,
    similarity_threshold,
    containment_threshold,
    progress,
    max_updates,
):
    total_insertions = sum(group["shingle_count"] for group in groups)
    progress.start(total_insertions, "Indexing")
    index = {}
    done = 0

    for group_id, group in enumerate(groups):
        shingles = group["shingles"]
        if not shingles:
            continue
        for shingle in shingles:
            posting = index.get(shingle)
            if posting is None:
                index[shingle] = [group_id]
            else:
                posting.append(group_id)
        done += len(shingles)
        progress.set(done)

    progress.finish()

    if index_update_cost(index, max_updates) > max_updates:
        del index
        return compare_pairwise(
            groups,
            similarity_threshold,
            containment_threshold,
            progress,
        )

    for group in groups:
        group["shingles"] = None

    progress.start(len(index), "Comparing")
    overlaps = {}
    seen = 0
    for posting in index.values():
        length = len(posting)
        if length >= 2:
            for left in range(length):
                left_id = posting[left]
                for right in range(left + 1, length):
                    right_id = posting[right]
                    if left_id < right_id:
                        key = (left_id, right_id)
                    else:
                        key = (right_id, left_id)
                    overlaps[key] = overlaps.get(key, 0) + 1
        seen += 1
        if seen & 4095 == 0:
            progress.set(seen)

    progress.set(len(index))
    progress.finish()
    del index

    matches = []
    for (left_id, right_id), intersection in overlaps.items():
        jaccard, containment = similarity_from_counts(
            groups[left_id]["shingle_count"],
            groups[right_id]["shingle_count"],
            intersection,
        )
        if is_match(
            jaccard,
            containment,
            intersection,
            similarity_threshold,
            containment_threshold,
        ):
            matches.append((left_id, right_id, jaccard, containment))

    return matches, "index"


def analyze(
    directory,
    mode_name="chinese-novel-balanced",
    ngram=None,
    step=None,
    similarity=None,
    containment=None,
    recursive=False,
    min_chars=0,
    show_progress=False,
    method="auto",
    max_index_updates=MAX_INDEX_UPDATES,
    on_start=None,
):
    if mode_name not in MODES:
        raise ValueError(f"unknown mode: {mode_name}")
    if method not in ("auto", "pairwise", "index"):
        raise ValueError(f"unknown comparison method: {method}")
    if min_chars < 0:
        raise ValueError("min-chars must be at least 0")
    if max_index_updates < 0:
        raise ValueError("max index updates must be at least 0")

    mode = MODES[mode_name]
    ngram_size = mode["ngram"] if ngram is None else ngram
    step_size = mode["step"] if step is None else step
    similarity_threshold = mode["similarity"] if similarity is None else similarity
    containment_threshold = (
        mode["containment"] if containment is None else containment
    )

    if ngram_size < 1 or step_size < 1:
        raise ValueError("n-gram size and step must be at least 1")
    if not 0 <= similarity_threshold <= 1 or not 0 <= containment_threshold <= 1:
        raise ValueError("thresholds must be between 0 and 1")

    description = mode["description"]
    if (
        ngram is not None
        or step is not None
        or similarity is not None
        or containment is not None
    ):
        description += " (custom settings)"

    if not os.path.isdir(directory):
        raise FileNotFoundError(directory)

    entries = discover_entries(directory, recursive)
    file_count = len(entries)
    result = {
        "directory": directory,
        "mode": mode_name,
        "mode_description": description,
        "ngram": ngram_size,
        "step": step_size,
        "similarity": similarity_threshold,
        "containment": containment_threshold,
        "recursive": bool(recursive),
        "min_chars": min_chars,
        "file_count": file_count,
        "total_comparisons": file_count * (file_count - 1) // 2,
        "unique_texts": 0,
        "fuzzy_group_count": 0,
        "comparison_method": "pairwise",
        "short_file_count": 0,
        "skipped": [],
        "pairs": [],
    }

    if on_start is not None:
        on_start(result)

    progress = ProgressBar(show_progress and sys.stderr.isatty())
    groups, skipped, short_file_count = load_groups(
        entries,
        ngram_size,
        step_size,
        min_chars,
        progress,
    )

    fuzzy_group_count = sum(1 for group in groups if group["shingle_count"])
    possible_pairs = fuzzy_group_count * (fuzzy_group_count - 1) // 2
    # Zero thresholds accept non-overlapping pairs, which an index cannot see.
    thresholds_allow_index = (
        similarity_threshold > 0 and containment_threshold > 0
    )
    use_index = thresholds_allow_index and (
        method == "index"
        or (
            method == "auto"
            and possible_pairs > INVERTED_INDEX_MIN_PAIRS
        )
    )

    if fuzzy_group_count < 2:
        matches = []
        method_used = "pairwise"
    elif use_index:
        matches, method_used = compare_index(
            groups,
            similarity_threshold,
            containment_threshold,
            progress,
            max_index_updates,
        )
    else:
        matches, method_used = compare_pairwise(
            groups,
            similarity_threshold,
            containment_threshold,
            progress,
        )

    for group in groups:
        group["shingles"] = None

    pairs = exact_duplicate_pairs(groups)
    pairs.extend(expand_matches(groups, matches))
    pairs.sort(
        key=lambda item: (
            -item["containment"],
            -item["jaccard"],
            item["file1"],
            item["file2"],
        )
    )

    result["skipped"] = skipped
    result["short_file_count"] = short_file_count
    result["unique_texts"] = len(groups)
    result["fuzzy_group_count"] = fuzzy_group_count
    result["comparison_method"] = method_used
    result["pairs"] = pairs
    return result


def format_header(result):
    lines = [
        f"Directory: {result['directory']}",
        f"Found txt files: {result['file_count']}",
        f"Mode: {result['mode']}",
        f"Mode description: {result['mode_description']}",
        f"N-gram size: {result['ngram']}",
        f"Step: {result['step']}",
        f"Jaccard threshold: {result['similarity']}",
        f"Containment threshold: {result['containment']}",
        f"Total comparisons: {result['total_comparisons']}",
        "",
    ]
    return "\n".join(lines) + "\n"


def format_text_body(result):
    lines = ["Finished.", ""]
    pairs = result["pairs"]

    if result["file_count"] == 0:
        lines.append("No .txt files found.")
    elif not pairs:
        lines.append("No suspiciously similar files found.")
    else:
        lines.append(f"Suspiciously similar file pairs: {len(pairs)}")
        lines.append("")
        for index, item in enumerate(pairs, start=1):
            lines.append(
                f"{index}. [{item['risk']}] {item['file1']} <-> {item['file2']}"
            )
            lines.append(
                f"   Jaccard: {item['jaccard']:.3f}, "
                f"Containment: {item['containment']:.3f}"
            )
            lines.append(
                f"   Lengths: {item['file1_chars']} chars vs "
                f"{item['file2_chars']} chars"
            )
            lines.append("")

    if result["skipped"]:
        if lines[-1] != "":
            lines.append("")
        label = "file" if len(result["skipped"]) == 1 else "files"
        lines.append(f"Skipped {len(result['skipped'])} {label}:")
        for item in result["skipped"]:
            lines.append(f"- {item['file']}: {item['reason']}")

    if result["short_file_count"]:
        if lines[-1] != "":
            lines.append("")
        noun = "file is" if result["short_file_count"] == 1 else "files are"
        verb = "was" if result["short_file_count"] == 1 else "were"
        lines.append(
            f"Note: {result['short_file_count']} {noun} shorter than the "
            f"{result['ngram']}-character n-gram and {verb} "
            "only checked for exact duplicates."
        )

    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    return text


def format_text_report(result):
    return format_header(result) + format_text_body(result)


def format_json_report(result):
    payload = {
        "directory": result["directory"],
        "mode": result["mode"],
        "mode_description": result["mode_description"],
        "ngram": result["ngram"],
        "step": result["step"],
        "jaccard_threshold": result["similarity"],
        "containment_threshold": result["containment"],
        "recursive": result["recursive"],
        "min_chars": result["min_chars"],
        "file_count": result["file_count"],
        "total_comparisons": result["total_comparisons"],
        "unique_texts": result["unique_texts"],
        "fuzzy_group_count": result["fuzzy_group_count"],
        "comparison_method": result["comparison_method"],
        "short_file_count": result["short_file_count"],
        "skipped": result["skipped"],
        "pairs": [
            {
                "file1": item["file1"],
                "file2": item["file2"],
                "jaccard": round(item["jaccard"], 6),
                "containment": round(item["containment"], 6),
                "risk": item["risk"],
                "file1_chars": item["file1_chars"],
                "file2_chars": item["file2_chars"],
                "file1_shingles": item["file1_shingles"],
                "file2_shingles": item["file2_shingles"],
            }
            for item in result["pairs"]
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _positive_int(value):
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def _non_negative_int(value):
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if number < 0:
        raise argparse.ArgumentTypeError("must be at least 0")
    return number


def _unit_interval(value):
    try:
        number = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number") from error
    if not 0 <= number <= 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return number


def mode_epilog():
    lines = ["detection modes:"]
    for name in sorted(MODES):
        mode = MODES[name]
        lines.append(
            f"  {name}: {mode['description']} "
            f"(n={mode['ngram']}, step={mode['step']}, "
            f"jaccard={mode['similarity']}, containment={mode['containment']})"
        )
    return "\n".join(lines)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Detect suspiciously similar novel .txt files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=mode_epilog(),
    )
    parser.add_argument(
        "directory",
        help="Directory containing .txt files",
    )
    parser.add_argument(
        "--mode",
        choices=sorted(MODES.keys()),
        default="chinese-novel-balanced",
        help="Detection mode. Default: chinese-novel-balanced",
    )
    parser.add_argument(
        "--ngram",
        type=_positive_int,
        help="Override the mode's n-gram size",
    )
    parser.add_argument(
        "--step",
        type=_positive_int,
        help="Override the mode's step between n-grams",
    )
    parser.add_argument(
        "--jaccard",
        type=_unit_interval,
        help="Override the mode's Jaccard threshold (0 to 1)",
    )
    parser.add_argument(
        "--containment",
        type=_unit_interval,
        help="Override the mode's containment threshold (0 to 1)",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Also scan .txt files in subdirectories",
    )
    parser.add_argument(
        "--min-chars",
        type=_non_negative_int,
        default=0,
        help="Ignore files with fewer normalized characters than this",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Report format. Default: text",
    )
    parser.add_argument(
        "--output",
        help="Write the report to this file instead of stdout",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Hide the progress bar",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if not os.path.isdir(args.directory):
        print(
            f"Error: directory does not exist: {args.directory}",
            file=sys.stderr,
        )
        return 1

    stream_header = args.format == "text" and not args.output

    def on_start(info):
        sys.stdout.write(format_header(info))
        sys.stdout.flush()

    try:
        result = analyze(
            directory=args.directory,
            mode_name=args.mode,
            ngram=args.ngram,
            step=args.step,
            similarity=args.jaccard,
            containment=args.containment,
            recursive=args.recursive,
            min_chars=args.min_chars,
            show_progress=not args.quiet,
            on_start=on_start if stream_header else None,
        )
    except OSError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if args.format == "json":
        report = format_json_report(result)
    elif stream_header:
        report = format_text_body(result)
    else:
        report = format_text_report(result)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as output_file:
                output_file.write(report)
        except OSError as error:
            print(f"Error: could not write report: {error}", file=sys.stderr)
            return 1
        print(f"Wrote report to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())

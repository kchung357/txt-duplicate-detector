import os
import argparse
import hashlib
import time
import sys
import re


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


def format_time(seconds):
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = seconds % 3600 // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def shorten_text(text, max_length=60):
    if len(text) <= max_length:
        return text
    return text[:28] + "..." + text[-29:]


def print_progress(current, total, start_time, action="Working"):
    percent = current / total if total else 1
    bar_length = 35
    filled_length = int(bar_length * percent)
    bar = "#" * filled_length + "-" * (bar_length - filled_length)

    elapsed = time.time() - start_time

    if current > 0:
        estimated_total = elapsed / current * total
        remaining = estimated_total - elapsed
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

    sys.stdout.write(message)
    sys.stdout.flush()


def read_file_bytes(file_path):
    with open(file_path, "rb") as file:
        return file.read()


def decode_text(data):
    encodings = ["utf-8", "utf-8-sig", "big5", "gb18030"]

    for encoding in encodings:
        try:
            return data.decode(encoding)
        except Exception:
            pass

    return data.decode("utf-8", errors="ignore")


def normalize_text(text):
    text = text.lower()

    # Keep Chinese characters, English letters, and numbers only.
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", text)

    return text


def make_shingles(text, n=10, step=2):
    if len(text) < n:
        return set()

    shingles = set()

    for i in range(0, len(text) - n + 1, step):
        piece = text[i:i + n]
        digest = hashlib.md5(piece.encode("utf-8")).hexdigest()
        shingles.add(digest)

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


def get_file_info(file_path, ngram_size, step):
    data = read_file_bytes(file_path)
    text = decode_text(data)
    normalized = normalize_text(text)
    shingles = make_shingles(normalized, ngram_size, step)

    return {
        "path": file_path,
        "char_count": len(normalized),
        "shingle_count": len(shingles),
        "shingles": shingles,
    }


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


def find_suspicious_files(directory, mode_name):
    mode = MODES[mode_name]

    ngram_size = mode["ngram"]
    step = mode["step"]
    similarity_threshold = mode["similarity"]
    containment_threshold = mode["containment"]

    txt_files = [
        file for file in os.listdir(directory)
        if file.lower().endswith(".txt")
    ]

    txt_files.sort()

    file_count = len(txt_files)
    total_reading = file_count
    total_comparisons = file_count * (file_count - 1) // 2
    total_work = total_reading + total_comparisons

    print(f"Directory: {directory}")
    print(f"Found txt files: {file_count}")
    print(f"Mode: {mode_name}")
    print(f"Mode description: {mode['description']}")
    print(f"N-gram size: {ngram_size}")
    print(f"Step: {step}")
    print(f"Jaccard threshold: {similarity_threshold}")
    print(f"Containment threshold: {containment_threshold}")
    print(f"Total comparisons: {total_comparisons}")
    print()

    if file_count < 2:
        return []

    file_infos = {}
    suspicious_files = []

    start_time = time.time()
    completed_work = 0

    # Reading and shingle building
    for file_name in txt_files:
        file_path = os.path.join(directory, file_name)

        try:
            file_infos[file_name] = get_file_info(
                file_path,
                ngram_size,
                step
            )
        except Exception as error:
            print()
            print(f"ERROR reading {file_name}: {error}")

        completed_work += 1
        print_progress(completed_work, total_work, start_time, "Reading/building")

    # Comparing
    for i in range(file_count):
        file1 = txt_files[i]

        if file1 not in file_infos:
            continue

        info1 = file_infos[file1]
        set1 = info1["shingles"]

        for j in range(i + 1, file_count):
            file2 = txt_files[j]

            if file2 not in file_infos:
                completed_work += 1
                print_progress(completed_work, total_work, start_time, "Comparing")
                continue

            info2 = file_infos[file2]
            set2 = info2["shingles"]

            if set1 and set2:
                jaccard = jaccard_similarity(set1, set2)

                if len(set1) <= len(set2):
                    containment = containment_similarity(set1, set2)
                else:
                    containment = containment_similarity(set2, set1)

                if (
                    jaccard >= similarity_threshold
                    or containment >= containment_threshold
                ):
                    risk = get_risk_label(jaccard, containment)

                    suspicious_files.append({
                        "file1": file1,
                        "file2": file2,
                        "jaccard": jaccard,
                        "containment": containment,
                        "risk": risk,
                        "file1_chars": info1["char_count"],
                        "file2_chars": info2["char_count"],
                        "file1_shingles": info1["shingle_count"],
                        "file2_shingles": info2["shingle_count"],
                    })

            completed_work += 1
            print_progress(completed_work, total_work, start_time, "Comparing")

    print()

    suspicious_files.sort(
        key=lambda item: (
            item["containment"],
            item["jaccard"]
        ),
        reverse=True
    )

    return suspicious_files


def print_results(suspicious_files):
    print()
    print("Finished.")
    print()

    if not suspicious_files:
        print("No suspiciously similar files found.")
        return

    print(f"Suspiciously similar file pairs: {len(suspicious_files)}")
    print()

    for index, item in enumerate(suspicious_files, start=1):
        print(f"{index}. [{item['risk']}] {item['file1']} <-> {item['file2']}")
        print(
            f"   Jaccard: {item['jaccard']:.3f}, "
            f"Containment: {item['containment']:.3f}"
        )
        print(
            f"   Lengths: {item['file1_chars']} chars vs "
            f"{item['file2_chars']} chars"
        )
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Detect suspiciously similar novel .txt files."
    )

    parser.add_argument(
        "directory",
        help="Directory containing .txt files"
    )

    parser.add_argument(
        "--mode",
        choices=sorted(MODES.keys()),
        default="chinese-novel-balanced",
        help="Detection mode. Default: chinese-novel-balanced"
    )

    args = parser.parse_args()

    if not os.path.isdir(args.directory):
        print(f"Error: directory does not exist: {args.directory}")
        return

    suspicious_files = find_suspicious_files(
        directory=args.directory,
        mode_name=args.mode
    )

    print_results(suspicious_files)


if __name__ == "__main__":
    main()
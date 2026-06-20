# TXT Duplicate Detector

A Python command-line tool for detecting suspiciously similar `.txt` files using n-gram shingles, Jaccard similarity, and containment similarity.

This project is useful for comparing large collections of text files such as novels, books, drafts, chapters, archives, or exported plain-text documents.

## Features

- Detects suspiciously similar `.txt` files in a directory
- Supports Chinese and English text
- Multiple detection modes
- Uses n-gram shingling for fuzzy text matching
- Calculates Jaccard similarity
- Calculates containment similarity
- Displays progress, elapsed time, and estimated remaining time
- No external Python dependencies required

## Requirements

- Python 3.8 or newer

This tool uses only Python standard-library modules.

## Installation

Clone the repository:

```bash
git clone https://github.com/kchung357/txt-duplicate-detector.git
cd txt-duplicate-detector
```

No package installation is required.

## Usage

Run the detector on a directory containing `.txt` files:

```bash
python detect_duplicated_txt.py /path/to/txt/files
```

Example:

```bash
python detect_duplicated_txt.py ./books
```

Use a specific detection mode:

```bash
python detect_duplicated_txt.py ./books --mode chinese-novel-balanced
```

## Detection Modes

| Mode | Description |
|---|---|
| `chinese-novel-sensitive` | Chinese novels, sensitive copycat detection |
| `chinese-novel-balanced` | Chinese novels, recommended default |
| `chinese-novel-strict` | Chinese novels, fewer false positives |
| `english-book-balanced` | English books, balanced detection |
| `near-duplicate` | Same book or near-duplicate files |

Default mode:

```text
chinese-novel-balanced
```

## Example Output

```text
Directory: ./books
Found txt files: 12
Mode: chinese-novel-balanced
Mode description: Chinese novels, recommended default
N-gram size: 10
Step: 2
Jaccard threshold: 0.07
Containment threshold: 0.15
Total comparisons: 66

Finished.

Suspiciously similar file pairs: 2

1. [HIGH] novel_a.txt <-> novel_b.txt
   Jaccard: 0.182, Containment: 0.341
   Lengths: 143202 chars vs 150884 chars

2. [MEDIUM] story_1.txt <-> story_2.txt
   Jaccard: 0.094, Containment: 0.201
   Lengths: 98550 chars vs 102330 chars
```

## How It Works

The script:

1. Reads all `.txt` files in the selected directory.
2. Decodes files using common encodings:
   - UTF-8
   - UTF-8 with BOM
   - Big5
   - GB18030
3. Normalizes the text.
4. Keeps only:
   - Chinese characters
   - English letters
   - Numbers
5. Splits the normalized text into n-gram shingles.
6. Hashes each shingle with MD5.
7. Compares file pairs using:
   - Jaccard similarity
   - Containment similarity
8. Reports pairs that exceed the selected thresholds.

## Similarity Metrics

### Jaccard Similarity

Jaccard similarity measures the overall overlap between two files:

```text
intersection / union
```

### Containment Similarity

Containment similarity measures how much of the smaller file appears inside the larger file:

```text
intersection / smaller_set_size
```

## Performance Notes

The script compares every pair of `.txt` files.

The number of comparisons is:

```text
file_count * (file_count - 1) / 2
```

Examples:

| Number of files | Comparisons |
|---:|---:|
| 10 | 45 |
| 100 | 4,950 |
| 1,000 | 499,500 |
| 10,000 | 49,995,000 |

Large folders may take a long time to process.

## Important Notes

This tool identifies suspicious text similarity. It does not prove plagiarism, copyright infringement, or legal wrongdoing.

Manual review is recommended before making any conclusion.

## Privacy Warning

Do not commit private, copyrighted, or sensitive `.txt` files to this repository.

Recommended ignored folders:

```text
data/
books/
novels/
txt/
output/
```

## License

This project is licensed under the MIT License.

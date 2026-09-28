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
- Shows risk levels such as `LOW`, `MEDIUM`, `HIGH`, `VERY HIGH`, and `EXTREME`
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
python detect_duplicate_txt.py /path/to/txt/files
```

Example:

```bash
python detect_duplicate_txt.py ./books
```

Use a specific detection mode:

```bash
python detect_duplicate_txt.py ./books --mode chinese-novel-balanced
```

Scan subdirectories, raise the thresholds, or write a JSON report:

```bash
python detect_duplicate_txt.py ./books --recursive
python detect_duplicate_txt.py ./books --jaccard 0.2 --containment 0.4
python detect_duplicate_txt.py ./books --format json --output report.json
```

Progress is written to stderr, so a text report can be redirected:

```bash
python detect_duplicate_txt.py ./books > report.txt
```

## Options

| Option | Description |
|---|---|
| `--mode` | Detection mode. Default: `chinese-novel-balanced` |
| `--ngram` | Override the selected mode's n-gram size |
| `--step` | Override the step between n-grams |
| `--jaccard` | Override the Jaccard threshold, from 0 to 1 |
| `--containment` | Override the containment threshold, from 0 to 1 |
| `--recursive` | Also scan `.txt` files in subdirectories |
| `--min-chars` | Ignore files with fewer normalized characters than this |
| `--format text\|json` | Report format. Default: `text` |
| `--output` | Write the report to a file instead of stdout |
| `--quiet` | Hide the progress bar |

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

The script compares `.txt` files using fuzzy text similarity.

The process is:

1. Read all `.txt` files in the selected directory.
2. Decode files using common encodings:
   - UTF-8, including a leading BOM
   - UTF-16, with or without a BOM
   - Big5 and GB18030, choosing the decoding with more Chinese characters when both succeed
   - Windows-1252, for Western European text that is not valid UTF-8
3. Normalize the text.
4. Keep only:
   - Chinese characters
   - English letters
   - Numbers
5. Split the normalized text into n-gram shingles.
6. Fingerprint each shingle with a 64-bit hash.
7. Group files whose normalized text is identical.
8. Compare the remaining texts with:
   - Jaccard similarity
   - Containment similarity
9. Report pairs that exceed the selected thresholds, including every copy of an exact duplicate.

## Similarity Metrics

### Jaccard Similarity

Jaccard similarity measures the overall overlap between two files.

```text
intersection / union
```

Higher values mean the two files share more text patterns overall.

### Containment Similarity

Containment similarity measures how much of the smaller file appears inside the larger file.

```text
intersection / smaller_set_size
```

This is useful for detecting cases where one file may be partially copied into another larger file.

## Risk Levels

The script assigns a risk label based on Jaccard similarity and containment similarity.

| Risk | Rule | Meaning |
|---|---|---|
| `EXTREME` | Containment is at least 0.80 | One file may be mostly contained in another |
| `VERY HIGH` | Containment is at least 0.50, or Jaccard is at least 0.30 | Very strong similarity |
| `HIGH` | Containment is at least 0.30, or Jaccard is at least 0.15 | Strong similarity |
| `MEDIUM` | Containment is at least 0.18, or Jaccard is at least 0.08 | Noticeable similarity |
| `LOW` | Below the bands above | Small amount of similarity |

The labels use these fixed bands. They do not move with the selected mode, so a sensitive mode can report a pair that is still labeled `LOW`.

These labels are only indicators. Results should be reviewed manually.

## Performance Notes

The report can include every pair of `.txt` files. That set has this many pairs:

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

Two shortcuts keep that work smaller without changing the scores:

- Files with the same normalized text are grouped first. Every copy is still reported, but a folder full of exact copies does not rebuild or compare the same shingles once per copy.
- After about 10,000 unique-text pairs, unrelated books are compared through an inverted index of shingles instead of a direct pair loop. If the same shingles are shared by a very large number of files, the tool goes back to direct comparison.

Shingle fingerprints are 64-bit values rather than MD5 hex strings, so large collections use less memory. On ordinary novels the similarity scores match a comparison of the raw n-grams.

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

Add them to your `.gitignore` if you use those folders locally.

## Tests

```bash
python -m unittest discover -s tests -t .
```

## License

This project is licensed under the MIT License.

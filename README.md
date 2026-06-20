# TXT Duplicate Detector

A simple command-line tool for detecting suspiciously similar `.txt` files.

This tool is designed for comparing large text files such as novels, books, chapters, drafts, archives, or exported plain-text documents. It uses normalized text, n-gram shingles, Jaccard similarity, and containment similarity to find possible duplicates or copycat files.

## Features

- Detects similar `.txt` files in a directory
- Supports Chinese and English text
- Multiple detection modes
- Uses n-gram shingling for fuzzy matching
- Reports Jaccard similarity and containment similarity
- Includes progress bar, elapsed time, and ETA
- No external dependencies

## Requirements

- Python 3.8+

No third-party packages are required.

## Usage

```bash
python detect_duplicated_txt.py /path/to/txt/files

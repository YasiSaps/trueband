# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-01

First public release.

### Added
- `trueband` command: recursive scanning of MP3, AAC/M4A, FLAC, WAV, AIFF, Ogg Vorbis
  and Opus files via ffmpeg, with table, CSV and JSON reports.
- Spectral cutoff detection: Welch-averaged Blackman-Harris spectrum, 100 Hz median
  bands, a 3-band run rule, and a "cliff" metric that separates encoder low-pass filters
  from natural roll-off.
- Thresholds calibrated against real LAME, ffmpeg AAC and Opus encodes and re-encode
  chains (`scripts/calibrate.py`, `docs/VALIDATION.md`).
- Format-aware notes: re-encode, fake lossless, genuinely low bitrate, gradual
  roll-off, sample-rate-limited.
- `--include`/`--exclude` globs, `--workers`, a progress bar, a resumable `--cache`,
  `--fail-on`, and `--explain FILE` with an optional `--plot` PNG.
- Graceful handling of missing ffmpeg, empty, truncated, corrupt and non-audio files,
  permission errors, silence, short clips, cover art and awkward filenames.

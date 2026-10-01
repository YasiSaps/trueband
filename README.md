# trueband

[![CI](https://github.com/YasiSaps/trueband/actions/workflows/ci.yml/badge.svg)](https://github.com/YasiSaps/trueband/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

**Find the tracks in your music library that aren't the quality they claim to be.**

A file can *say* it's a 320 kbps MP3 or a lossless FLAC when it was really made from a
low-quality 128 kbps rip. The label changed, but the sound didn't. trueband ignores the
label and checks the audio itself. It gives you a list of the tracks worth replacing.

## Quick start

```bash
brew install ffmpeg                                     # 1. install ffmpeg (Linux: sudo apt install ffmpeg)
pipx install git+https://github.com/YasiSaps/trueband   # 2. install trueband
trueband ~/Music                                        # 3. scan a folder
```

You get one line per track, worst first (this is real output from the
[demo library](#try-it-on-a-demo-library)):

```console
STATUS      CUTOFF  CLIFF  CODEC  KBPS  PATH
suspect      16.8k   92dB  mp3     320  Downloads/Peak Time Anthem (320).mp3  (tagged 320 kbps, but a typical 320 kbps encode reaches ~20.2 kHz: likely re-encoded from a lower-bitrate source)
suspect      17.3k   92dB  flac   1687  Downloads/Rare Edit [FLAC].flac  (lossless file with a lossy-style cutoff: likely converted from a lossy source)
borderline   18.9k   92dB  mp3     192  Downloads/Old Promo.mp3  (bandwidth consistent with its 192 kbps bitrate: a genuinely low-bitrate file)
ok           20.2k   91dB  mp3     320  House/Warehouse Tool.mp3
Scanned 4 files: 2 suspect, 1 borderline, 1 ok, 0 error (0.3s)
```

## What the results mean

| Status | In plain words | What to do |
|---|---|---|
| **suspect** | Lower quality than the file claims. It most likely came from a 160 kbps or lower source. | Listen to it, and look for a better copy. |
| **borderline** | Roughly 192 kbps quality. | Usually fine. Consider replacing it if it's going on a big sound system. |
| **ok** | Full quality. | Nothing. |
| **error** | Couldn't be read: empty, broken, or not really audio. | Check or delete the file. |

The note at the end of each line says *why*: re-encoded at a higher bitrate, a "fake"
FLAC/WAV made from an MP3, honestly low bitrate, or a naturally dark recording.

> [!IMPORTANT]
> **Treat the results as a shortlist, not a verdict.** Some music is naturally dull at
> the top end (old recordings, solo piano, lo-fi), and a few low-quality sources can't
> be detected at all. Listen before you delete anything. See [Limitations](#limitations).

## Common tasks

```bash
trueband ~/Music --exclude STEMS                     # skip folders (or files) by name or pattern
trueband ~/Music --show flagged -f csv -o review.csv # only the problem files, as a spreadsheet
trueband ~/Music --cache ~/.cache/trueband.json      # big library: resume where you left off
trueband --explain "track.mp3"                       # show why one file got its verdict
trueband incoming/ --fail-on suspect -q              # for scripts: exit code 1 if anything is suspect
```

`--explain` draws the file's frequency spectrum in the terminal. The point where the
bars collapse is where the original encoder cut the sound off:

```console
$ trueband --explain "demo/Downloads/Peak Time Anthem (320).mp3"
File:        demo/Downloads/Peak Time Anthem (320).mp3
Format:      mp3, 44100 Hz, 2 ch, 320 kbps, 40.0s
Status:      SUSPECT  (tagged 320 kbps, but a typical 320 kbps encode reaches ~20.2 kHz: likely re-encoded from a lower-bitrate source)
Cutoff:      16.80 kHz   (Nyquist 22.05 kHz)
Cliff:       92 dB   (>= 20 dB looks like an encoder filter)
Thresholds:  suspect < 18.0 kHz, borderline < 19.2 kHz, content = within 45 dB of the 1-5 kHz level

        band  level vs 1-5 kHz reference ('|' = -45 dB content threshold)
  ...
 15.5-16.0k  ████████████████████████████|██████████████████···   -7.0 dB
 16.0-16.5k  ████████████████████████████|█████████████████····   -7.4 dB
 16.5-17.0k  ████████████████████████████|█····················  -40.3 dB  <- cutoff
 17.0-17.5k  ████████····················|·····················  -84.2 dB
 17.5-18.0k  ····························|·····················  -99.8 dB
  ...
```

---

*Everything below is reference detail.*

- [Install options](#install-options)
- [All options](#all-options)
- [Reading the report columns](#reading-the-report-columns)
- [What a low cutoff means depends on the format](#what-a-low-cutoff-means-depends-on-the-format)
- [How it works](#how-it-works)
- [How the thresholds were chosen](#how-the-thresholds-were-chosen)
- [Limitations](#limitations)
- [Prior art](#prior-art)
- [Development](#development)

## Install options

trueband needs **Python 3.10+** and **[ffmpeg](https://ffmpeg.org/)** (`ffmpeg` and
`ffprobe`) on your `PATH`. Its only Python dependency is numpy.

- **ffmpeg:** `brew install ffmpeg` (macOS), `sudo apt install ffmpeg` (Debian/Ubuntu),
  `winget install ffmpeg` (Windows). If it lives somewhere unusual, set
  `TRUEBAND_FFMPEG` and `TRUEBAND_FFPROBE`. If ffmpeg is missing, trueband prints an
  install hint, not a stack trace.
- **trueband:** `pipx install git+https://github.com/YasiSaps/trueband` (recommended),
  `pip install git+https://github.com/YasiSaps/trueband`, or `pip install .` from a clone.
- **PNG plots for `--explain --plot`:**
  `pipx install "trueband[plot] @ git+https://github.com/YasiSaps/trueband"`.

CI runs on Linux and macOS. Windows should work, since everything goes through ffmpeg
and pathlib, but it isn't tested in CI yet. Reports from Windows users are welcome.

## All options

| Option | Default | What it does |
|---|---|---|
| `PATH ...` | | Files and/or folders. Folders are scanned recursively. |
| `-f, --format` | `table` | `table`, `csv` or `json`. JSON includes the settings used. |
| `-o, --output FILE` | stdout | Write the report to a file. |
| `--show` | `all` | `all`, `flagged` (everything except ok) or `suspect`. |
| `--sort` | `status` | `status` (worst first), `cutoff` or `path`. |
| `--include GLOB` | | Only scan matching files. Repeatable. |
| `--exclude GLOB` | | Skip matching files or folders. Repeatable. A bare name like `STEMS` matches any folder or file with that name. A path glob like `'*/Samples/*'` matches against the path relative to the scanned folder. |
| `--extensions` | `mp3,m4a,aac,mp4,flac,wav,aif,aiff,ogg,oga,opus` | Extensions picked up when walking folders. Files named explicitly are always scanned. |
| `--include-hidden` | off | Also scan dot-files, e.g. macOS `._` resource forks (skipped by default because they aren't audio). |
| `--suspect-below FREQ` | `18k` | Cutoff below this is `suspect`. Accepts `18000`, `18k`, `18kHz`. |
| `--borderline-below FREQ` | `19.2k` | Cutoff below this is `borderline`. |
| `--margin-db DB` | `45` | How far below the 1-5 kHz level still counts as content. |
| `--sharp-cliff-db DB` | `20` | Minimum drop across the cutoff that counts as an encoder-style cliff. |
| `--segment-seconds S` | `6` | Length of each analysed excerpt. |
| `-j, --workers N` | min(8, CPUs) | Files analysed in parallel. |
| `--cache FILE` | | JSON cache of results, keyed by path + size + modification time. Changing analysis settings or upgrading trueband invalidates it. |
| `--fail-on` | | `suspect` or `borderline`: exit 1 if any file is at least that bad. |
| `--color` | `auto` | Colour the status column (`auto` respects `NO_COLOR`). |
| `--no-progress`, `-v`, `-q` | | Progress bar off; more logging (`-vv` debug); errors only. |
| `--explain FILE`, `--plot PNG` | | Inspect one file instead of scanning. The plot needs `trueband[plot]`. |

Exit status: `0` success (even if files were flagged, unless `--fail-on`), `1` flagged
with `--fail-on` (or `--explain` on an unreadable file), `2` usage error or ffmpeg
missing, `130` interrupted (with `--cache`, progress so far is saved).

## Try it on a demo library

```bash
python scripts/make_demo_library.py demo/
trueband demo/
```

## Reading the report columns

| Column | Meaning |
|---|---|
| **STATUS** | `suspect`: cutoff below 18 kHz, typical of a ≤160 kbps lossy source. `borderline`: 18-19.2 kHz, typical of a ~192 kbps source. `ok`: content reaches (near) the top of the audible band. `error`: couldn't be analysed, with the reason given. |
| **CUTOFF** | The highest frequency with sustained content, to 100 Hz resolution. |
| **CLIFF** | How sharply the level drops across the cutoff. Encoder low-pass filters measured **69-93 dB**. Natural roll-offs (dark recordings) measured **4-8 dB**. A flagged file with a small cliff is the most likely false positive. |
| **CODEC / KBPS** | What the file claims to be, from ffprobe. |
| **note** | A plain-language reading: re-encode, fake lossless, genuinely low bitrate, gradual roll-off, or sample-rate-limited. |

A sensible way to work through a report: start with `suspect` rows that have a big
cliff and a re-encode or fake-lossless note. Those are the strongest cases. Be most
cautious about "gradual roll-off" rows, because those files may be fine. Use
`--explain` (or [Spek](https://www.spek.cc/)) to look before acting.

## What a low cutoff means depends on the format

The measurement is the same for every format. What it tells you depends on what the file
claims to be:

| File type | A low, sharp cutoff means... | Typical story |
|---|---|---|
| **MP3, AAC/M4A, Ogg Vorbis** at a high bitrate | it was **re-encoded from a lower-bitrate source**. Re-encoding at 320 kbps can't restore what the first encoder threw away. | A 128 kbps rip, "upgraded" to 320 kbps |
| **MP3, AAC** at a low bitrate | it's **honestly low quality**. The bandwidth matches the tag. | An old 128 kbps download |
| **FLAC, WAV, AIFF, ALAC** | it's a **lossy file converted to a lossless format** (a "fake FLAC"). The file is large, but the sound is only as good as the lossy source. | An MP3 burned to CD and ripped, or converted to FLAC/WAV for a CDJ |
| **Opus** | rarely anything. Opus keeps ~20 kHz bandwidth even at 64 kbps, so this test can't judge Opus sources. | |

## How it works

In plain language:

1. **Probe.** `ffprobe` reads the codec, sample rate, channel count, duration and tagged
   bitrate of the first audio stream. Embedded cover art is ignored.
2. **Sample.** `ffmpeg` decodes three 6-second excerpts at 20%, 50% and 75% through the
   track. That skips intros and outros, which are often thin. Short clips are analysed
   whole.
3. **Measure the spectrum.** Each excerpt is cut into overlapping frames, windowed
   (Blackman-Harris) and FFT'd. Power is averaged over all frames and both channels
   (Welch's method). Channels are averaged rather than summed to mono, so
   out-of-phase content can't cancel.
4. **Smooth.** The spectrum is grouped into 100 Hz bands, taking the *median* level in
   each band. Medians ignore isolated tones such as a pilot tone or a single bright
   partial.
5. **Find the cutoff.** The median level from 1 to 5 kHz is the reference. Walking down
   from the top, the cutoff is the top of the highest run of three adjacent bands
   (300 Hz) that sit within 45 dB of that reference. Loudness doesn't matter, because
   everything is relative.
6. **Measure the cliff.** The level 0.5-1.5 kHz below the cutoff is compared with the
   level 0.5-1.5 kHz above it. Encoders cut like a wall. Natural recordings fade
   gradually.
7. **Classify** against the thresholds, and write a note that takes the format and
   tagged bitrate into account.

Why this works: lossy encoders save bits by applying a steep low-pass filter whose
frequency depends on the bitrate. LAME, for example, filters at about 16.7 kHz at
128 kbps and about 20.2 kHz at 320 kbps. Whatever happens to the file afterwards, that
missing top end stays missing.

## How the thresholds were chosen

The prototype used thresholds picked by eye. The defaults now come from measurements.
[`scripts/calibrate.py`](scripts/calibrate.py) encodes full-band test signals with real
encoders and re-encode chains, then reports what trueband measures for each. Summary,
from the full table in [docs/VALIDATION.md](docs/VALIDATION.md):

| Source | Measured cutoff | Default status |
|---|---:|---|
| MP3 96 / 128 / 160 kbps (LAME) | 15.2 / 16.7-16.8 / 17.4-17.5 kHz | suspect |
| AAC 96 / 128 kbps (ffmpeg) | 15.9 / 17.3 kHz | suspect |
| MP3 192 kbps | 18.8-18.9 kHz | borderline |
| AAC 192 kbps | 19.3-19.4 kHz | ok |
| MP3 256 / 320 kbps | 19.5 / 20.2 kHz | ok |
| 128k MP3 → 320k MP3, 128k AAC → 256k AAC, 128k → FLAC | *unchanged from the source* | suspect |

Each threshold sits several hundred Hz from the nearest measured cluster. The
prototype's 17 kHz threshold would have let 128 kbps AAC sources (17.3 kHz) through as
merely "borderline". That was the main reason to change it.

The test suite (200+ tests, run in CI on every push) re-checks all of this:

- **Synthetic recovery:** noise with a deliberately imposed cutoff (4-21 kHz; sample
  rates 22.05-192 kHz; mono, stereo, out-of-phase stereo; white to dark spectral tilt;
  levels from 0 to -60 dB; with loud stray tones above the cutoff). The detector must
  recover the cutoff to within 150 Hz.
- **Real encoders:** MP3, AAC, Opus, Vorbis, FLAC, WAV and AIFF at known bitrates, plus
  the re-encode and fake-lossless chains above, through the full ffprobe/ffmpeg
  pipeline. Each must get the expected status and note.
- **Real-world mess:** empty, truncated, random-byte and text-as-`.mp3` files,
  permission errors, silence, 0.15 s clips, cover art, and filenames with colons,
  leading dashes and Unicode.

On the author's own DJ library (959 MP3/AAC files), trueband reproduced the prototype's
results. With the prototype's thresholds it gives 590 ok / 68 borderline / 301 suspect,
against the prototype's 593 / 62 / 300. The calibrated defaults move the 17-18 kHz group
(AAC-128-like sources) from borderline to suspect. See
[docs/VALIDATION.md](docs/VALIDATION.md).

## Limitations

Be clear about what this tool can and can't see:

- **It measures bandwidth, nothing else.** Pre-echo, "swirly" artefacts, clipping, bad
  mastering, wrong pitch and so on are invisible to it.
- **Modern encoders at low bitrates can keep full bandwidth.** Opus (YouTube's usual audio
  format) reaches ~20 kHz even at 64 kbps, and ffmpeg's AAC encoder doesn't low-pass at
  all at 256 kbps and above. A rip from such a source passes this test, even if it sounds
  worse than the bandwidth suggests.
- **High-bitrate lossy → lossless isn't detected.** A 320 kbps MP3 converted to FLAC has
  ~20 kHz bandwidth and comes out `ok`. Tools that look for other codec fingerprints
  (see [Prior art](#prior-art)) go further here.
- **Some music is genuinely band-limited.** Old recordings, vinyl and tape transfers,
  lo-fi, heavily filtered intros, acoustic material, and stems or acapellas can all show
  a low cutoff. The CLIFF column and the "gradual roll-off" note help here, but don't
  settle it.
- **Thresholds are calibrated on LAME and ffmpeg's AAC.** Other encoders (FhG, Apple,
  Vorbis, older LAME versions) use slightly different low-pass frequencies. A file
  near a threshold could land on either side.
- **It samples the track.** Three 6-second excerpts represent the whole file. A track
  that changes character dramatically could be mis-measured. Increase
  `--segment-seconds` if that matters.
- **Low sample rates cap bandwidth.** A 22.05 kHz file can't go above 11 kHz. trueband
  says so in the note rather than calling it a re-encode.

## Prior art

trueband applies a long-standing idea, and shares no code with any of these:

- **[auCDtect](https://en.wikipedia.org/wiki/AuCDtect)** and **Lossless Audio Checker**
  analyse supposedly lossless audio for signs of a lossy (MPEG) origin. This is the
  "fake lossless" problem these tools made well known.
- **Fakin' The Funk?** is a GUI app aimed at DJs that estimates the "real" bitrate of
  library tracks from their spectrum.
- **[Spek](https://www.spek.cc/)**, Audacity's spectrogram view and SoX's `spectrogram`
  effect are the classic way to check this by eye: look for the flat ceiling where an
  encoder cut the top off. trueband automates that look across a whole library, and
  `--explain` shows you the same picture.

If you only need to check a handful of files, a spectrogram viewer is all you need.
trueband is for when you have a thousand.

## Development

```bash
git clone https://github.com/YasiSaps/trueband && cd trueband
uv sync                                   # or: python -m venv .venv && pip install -e . pytest ruff mypy
uv run pytest                             # ffmpeg tests are skipped automatically without ffmpeg
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run python scripts/calibrate.py        # regenerate the calibration table
```

Code layout: `analysis.py` (pure-numpy spectrum and cutoff maths, no I/O),
`ffmpeg.py` (probe/decode wrappers), `track.py` (one file end to end),
`classify.py` (thresholds and notes), `scan.py` (discovery, parallelism, cache),
`report.py` / `explain.py` (output), `cli.py`.

Issues and pull requests are welcome. If trueband misjudges a file, please include the
`--explain` output and, if you can, where the file came from.

## License

[MIT](LICENSE)

# Validation

This document records how trueband's method and default thresholds were checked, so
anyone can see (and reproduce) the evidence rather than take the defaults on trust.

## 1. Synthetic cutoff recovery (`tests/test_analysis.py`)

Noise is generated with an exactly known spectrum and fed straight into the analysis
code. The detector must recover the imposed cutoff to within **150 Hz** (its bands are
100 Hz wide).

| Dimension | Values tested |
|---|---|
| Brick-wall cutoff at 44.1 kHz | 4, 8, 11.025, 14, 15, 16, 16.5, 17, 18, 19, 19.5, 20, 20.5, 21 kHz |
| Sample rate | 22.05, 32, 44.1, 48, 88.2, 96, 192 kHz (cutoff at 40/70/90% of Nyquist) |
| Spectral tilt | white, pink (-3 dB/oct), brown above 200 Hz (-6 dB/oct) |
| Level | 0, -20, -40, -60 dB |
| Channels | mono, stereo, out-of-phase stereo (L = -R) |
| Filter shape | brick-wall and a 400 Hz raised-cosine transition |
| Interference | loud steady tones at 17.33-20 kHz over a 16 kHz-limited signal (amplitudes 0.01-0.2), and two tones at once |
| Length | 0.5 s up to 12 s; multiple excerpts combined |

Also checked: full-band signals reach Nyquist, a larger `--margin-db` never lowers the
cutoff, silence, NaNs and too-short input raise clear errors, and the cliff metric
separates a 300 Hz-transition filter (> 40 dB) from a 24 dB/oct natural roll-off
(< 15 dB).

**A bug this found:** the prototype's Hann window and two-band rule let a loud 19 kHz tone
over a 16 kHz rip be reported as a 19.1 kHz cutoff. The fix was a Blackman-Harris window
(sidelobes below -92 dB) and a three-band (300 Hz) run requirement. The tone tests guard
against regressions.

## 2. Real encoders and re-encode chains (`scripts/calibrate.py`, `tests/test_encoders.py`)

Two 40-second stereo test sources were generated: "pink" (-3 dB/oct) and "dark"
(-6 dB/oct), both with a pulsing 2 Hz envelope so encoders see transients. Each source
was encoded with ffmpeg (libmp3lame, ffmpeg's native `aac`, libopus) and pushed
through re-encode chains. trueband then measured the result through its normal
ffprobe/ffmpeg pipeline.

Environment: ffmpeg 9.0.2 (Homebrew), numpy 2.5.3, Apple M4. Re-run with
`python scripts/calibrate.py`.

| source | encode chain | measured cutoff | cliff |
|---|---|---:|---:|
| pink | original WAV (full band) | 22.1 kHz | - |
| pink | mp3 96k | 15.3 kHz | 85 dB |
| pink | mp3 128k | 16.8 kHz | 92 dB |
| pink | mp3 160k | 17.5 kHz | 92 dB |
| pink | mp3 192k | 18.9 kHz | 92 dB |
| pink | mp3 256k | 19.5 kHz | 91 dB |
| pink | mp3 320k | 20.2 kHz | 91 dB |
| pink | aac 96k | 15.9 kHz | 92 dB |
| pink | aac 128k | 17.3 kHz | 92 dB |
| pink | aac 192k | 19.4 kHz | 92 dB |
| pink | aac 256k | 22.1 kHz | - |
| pink | aac 320k | 22.1 kHz | - |
| pink | opus 64k | 20.4 kHz | 71 dB |
| pink | opus 96k | 20.4 kHz | 70 dB |
| pink | opus 128k | 20.4 kHz | 70 dB |
| pink | opus 160k | 20.4 kHz | 71 dB |
| pink | mp3 128k -> mp3 320k | 16.8 kHz | 92 dB |
| pink | aac 128k -> mp3 320k | 17.5 kHz | 92 dB |
| pink | aac 128k -> aac 256k | 17.3 kHz | 92 dB |
| pink | mp3 128k -> FLAC | 16.8 kHz | 92 dB |
| pink | aac 128k -> FLAC | 17.3 kHz | 92 dB |
| pink | mp3 192k -> FLAC | 18.9 kHz | 92 dB |
| pink | mp3 320k -> FLAC | 20.2 kHz | 91 dB |
| dark | original WAV (full band) | 22.1 kHz | - |
| dark | mp3 96k | 15.2 kHz | 73 dB |
| dark | mp3 128k | 16.7 kHz | 78 dB |
| dark | mp3 160k | 17.4 kHz | 80 dB |
| dark | mp3 192k | 18.8 kHz | 81 dB |
| dark | mp3 256k | 19.5 kHz | 77 dB |
| dark | mp3 320k | 20.2 kHz | 78 dB |
| dark | aac 96k | 15.9 kHz | 85 dB |
| dark | aac 128k | 17.3 kHz | 85 dB |
| dark | aac 192k | 19.3 kHz | 83 dB |
| dark | aac 256k | 22.1 kHz | - |
| dark | aac 320k | 22.1 kHz | - |
| dark | opus 64k | 20.3 kHz | 69 dB |
| dark | opus 96k | 20.3 kHz | 69 dB |
| dark | opus 128k | 20.3 kHz | 69 dB |
| dark | opus 160k | 20.3 kHz | 69 dB |
| dark | mp3 128k -> mp3 320k | 16.7 kHz | 78 dB |
| dark | aac 128k -> mp3 320k | 17.4 kHz | 81 dB |
| dark | aac 128k -> aac 256k | 17.3 kHz | 86 dB |
| dark | mp3 128k -> FLAC | 16.7 kHz | 78 dB |
| dark | aac 128k -> FLAC | 17.3 kHz | 85 dB |
| dark | mp3 192k -> FLAC | 18.8 kHz | 81 dB |
| dark | mp3 320k -> FLAC | 20.2 kHz | 78 dB |

### What the table shows

- **The cutoff survives re-encoding.** 128k MP3 → 320k MP3, 128k AAC → 256k AAC and
  128k → FLAC all measure the same as the 128k source, to within 100 Hz. This is the
  property the whole tool relies on.
- **Spectral tilt barely matters.** Pink and dark sources agree to within 100 Hz.
- **Clusters:** ≤160 kbps sources fall at 15.2-17.5 kHz, 192 kbps at 18.8-19.4 kHz, and
  ≥256 kbps MP3 at 19.5 kHz and above.
- **Opus and high-bitrate ffmpeg AAC keep (near) full bandwidth.** Opus measures
  ~20.3 kHz at every bitrate tested, including 64 kbps. ffmpeg's AAC at 256 kbps and
  above has no low-pass at all. These sources **cannot be judged by cutoff**, and the
  README says so.

### Thresholds chosen

| Threshold | Default | Margin to nearest cluster |
|---|---:|---|
| suspect below | **18.0 kHz** | 500 Hz above MP3 160k (17.5), 800 Hz below MP3 192k (18.8) |
| borderline below | **19.2 kHz** | 300 Hz above MP3 192k (18.9), 300 Hz below MP3 256k (19.5) |
| sharp cliff | **20 dB** | encoders measured 69-93 dB, natural roll-offs 4-8 dB |

The prototype used 17 kHz / 19 kHz, picked by eye. The data showed 17 kHz was too low:
ffmpeg AAC 128k (17.3 kHz) and LAME 160k (17.4-17.5 kHz) sources would only have been
"borderline". AAC 192k (19.3-19.4 kHz) now counts as ok, while MP3 192k stays
borderline. That reflects their real bandwidth.

## 3. Natural roll-off vs encoder cliff

Pink noise through Butterworth-shaped low-passes (no encoder involved):

| Corner | Slope | Measured cutoff | Cliff |
|---|---|---:|---:|
| 3 kHz | 6, 12 dB/oct | full band | - |
| 3 kHz | 24 dB/oct | 10.2 kHz | 7.6 dB |
| 5 kHz | 24 dB/oct | 14.9 kHz | 5.1 dB |
| 8 kHz | any up to 24 dB/oct | full band | - |

So a steep natural roll-off *can* produce a "suspect" cutoff. Its cliff, though, is an
order of magnitude smaller than any encoder's. That's why the report shows the cliff,
and labels such files "gradual roll-off, may be the recording itself" instead of
calling them re-encodes.

## 4. Real-library sanity check

trueband was run read-only over the author's DJ library: 1,089 files, mostly MP3/M4A,
plus WAV stems. That's the library the original prototype was tuned on. Aggregate
results only:

| | ok | borderline | suspect |
|---|---:|---:|---:|
| Prototype (17k/19k thresholds, MP3/M4A only, 956 files) | 593 | 62 | 300 |
| trueband, prototype thresholds (959 MP3/M4A) | 590 | 68 | 301 |
| trueband, calibrated defaults (959 MP3/M4A) | 583 | 24 | 352 |

- trueband reproduces the prototype's split. The rewrite didn't change what is measured
  on real music.
- The calibrated thresholds move the 17-18 kHz group (AAC-128/MP3-160-like sources)
  from borderline to suspect.
- Of the flagged files (excluding the stems folder), 226 were noted as likely
  re-encodes (tagged bitrate far above what their bandwidth supports), 136 as honestly
  low-bitrate, and 16 as gradual roll-offs to check by ear.
- 123 of the 126 WAV stems in a "STEMS" folder came out suspect, all at about
  17.5 kHz, with cliffs of 21-55 dB (10th-90th percentile). A shared, sharp cutoff
  like that points to a lossy step somewhere in how the stems were made, for example
  stem separation run on a lossy master. It's plausible but not proof. It's also why
  `--exclude STEMS` appears in the examples.
- Speed: 1,089 files in about 50 seconds with 8 workers on an Apple M4.

Switching from the prototype's Hann window and two-band rule to the current method
moved the cutoff by 300 Hz or more on only 25 of about 1,060 files. The largest change,
a stem that went from "full band" to 17.7 kHz, was inspected with `--explain`. Content
drops by about 33 dB at 17.5 kHz, onto a noise floor sitting right at the -45 dB line.
The old rule let that floor count as content. The new one follows the visible cliff.

## Known gaps in validation

- Calibration covers LAME and ffmpeg's AAC. Apple's AAC encoder, FhG MP3, Vorbis at
  various qualities and streaming-service sources weren't measured directly.
- There's no ground-truth labelled real-music corpus. The real-library check confirms
  consistency and plausibility, not accuracy against known provenance.

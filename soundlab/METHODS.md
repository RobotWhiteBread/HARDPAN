# soundlab methods

How soundlab measures your samples and draws the Sound Galaxy. Every number is plain signal math run on your own machine. No audio is uploaded and no AI model listens to your files.

## 1. Measuring each file (analyze_samples.py)

Each file is read with `soundfile`. Only the first 30 seconds are measured. Stereo files are mixed to mono for most measures (width uses both channels). The mono signal is cut into 2048-sample windows with a Hann window and a hop of 512 samples, and each window becomes a magnitude spectrum (short-time Fourier transform).

| Field | Method |
|---|---|
| `peak_db`, `rms_db` | Highest absolute sample and root mean square level, in dBFS. |
| `crest_db` (Punch) | `peak_db - rms_db`. Spiky drums score high, compressed or sustained sounds low. |
| `attack_ms` (Snap) | Time from the start to the loudest window of the energy envelope. Resolution is one hop (about 11.6 ms at 44.1 kHz). The galaxy uses `-log(attack_ms)` so faster attacks score higher. |
| `decay_ms` | Time from the loudest window until the envelope falls below 10% of its peak. |
| `centroid_hz` (Brightness) | Spectral centroid of the average spectrum: the frequency where the spectrum balances. Shown on a log scale. |
| `rolloff_hz` | Frequency below which 85% of the spectral energy sits. |
| `flatness` (Noisiness) | Geometric mean of the average spectrum divided by its arithmetic mean. Near 0 for pitched sounds, near 1 for white noise. Shown as its square root. |
| `sub_frac`, `low_frac`, `mid_frac`, `high_frac` | Share of total energy in 20 to 60 Hz, 60 to 250 Hz, 250 Hz to 2.5 kHz, and above 2.5 kHz. Low end in the galaxy is `sub_frac + low_frac`. |
| `stereo_width` | RMS of the side signal `(L-R)/2` divided by RMS of the mid signal `(L+R)/2`. 0 for mono. Capped at 1.5 in the galaxy. |
| `onsets` | Spectral flux (sum of positive changes in log magnitude between windows). An onset is a local peak above median + 2 standard deviations, at least 70 ms after the last one. |
| `tempo_bpm`, `bars` | Only for files 1.8 s or longer. The onset envelope is autocorrelated and every tempo from 60 to 180 BPM in 0.5 steps is scored at 1, 2 and 4 beats (weights 1, 0.5, 0.25). A tempo gets a bonus of 0.35 when the file length is within 4% of a whole number of bars. |
| `key` | Only for tonal files (flatness under 0.2). Spectral energy from 55 Hz to 5 kHz is folded into a 12-note chroma vector and correlated with the 24 rotated Krumhansl-Kessler major and minor profiles. A key is kept only if the best correlation is at least 0.5 and beats the runner-up by 0.02. |
| `note`, `f0_hz` | Tonal files up to 6 s long: harmonic product spectrum (harmonics 2, 3 and 4) over the first 1.5 s, searched from 30 Hz to 2 kHz. |

### Sound type (`sound_guess`)

Rule-based, checked in this order:

1. **Loop**: 1.8 s or longer, at least 4 onsets, more than 1.2 onsets a second, and either a whole number of bars or longer than 4 s.
2. One-shots up to 1.2 s:
   - **Kick**: at least 50% of energy under 250 Hz and centroid under 900 Hz.
   - **Hat**: at least 50% of energy above 2.5 kHz and flatness over 0.15.
   - **Snare or clap**: at least 30% of energy between 250 Hz and 2.5 kHz and flatness over 0.06.
   - Otherwise **perc**.
3. Longer sounds:
   - **Bass**: at least 60% of energy under 250 Hz and flatness under 0.05.
   - **FX and noise**: flatness over 0.3.
   - **Melodic**: flatness under 0.08.
   - Otherwise **texture**.

### Character tags

| Tag | Rule |
|---|---|
| dark / warm / bright | centroid under 900 Hz / between / over 3.5 kHz |
| punchy | crest 18 dB or more and attack 10 ms or less |
| squashed | crest under 9 dB and longer than 1 s |
| lofi | 85% rolloff under 4.5 kHz |
| noisy | flatness over 0.25 |
| subby | more than 25% of energy under 60 Hz |
| wide | stereo width over 0.35 |

## 2. Building the map (galaxy/make_galaxy.py)

### Scales

Seven measures make up a sound's profile: brightness, punch, low end, length, noisiness, stereo width and snap. Tempo is kept separately.

- **For the islands**, each measure is scaled from 0 to 1 between its 1st and 99th percentile, with values outside clipped.
- **For what the page shows** (the "Choose the axes" layout, the side-panel bars, neighbor search and gaps), each measure is turned into a rank from 1 to 255. The middle of an axis is the median sound in your library.
- **Ties.** Some measures stop at a resolution limit, so many sounds read exactly the same: about 19% of sounds have a low-end share of 0.000 (mostly hats), about half reach their peak in the first analysis window, and most files are mono. Inside a tie, sounds are ordered by a related measure before ranking: low end ties by brightness (brightest at the low edge) and snap ties by punch (punchiest at the high edge). Sounds equal on both share the average rank. Stereo width is not tie-broken, because mono is a real, exact value. The page reports any value shared by 5% or more of sounds in its caption.
- **Shadows.** On "Choose the axes", the visible sounds are binned into a 96 x 96 grid on the back wall, floor and left wall (each wall drops one axis). Brightness follows the log of the count and color is the average color of the sounds in that cell.

### Similarity islands

1. Each sound becomes a vector of its 7 scaled measures plus a one-hot sound type weighted 0.55, so islands stay mostly one kind of sound.
2. K-means (25 iterations) groups the library into 360 islands, or fewer for small libraries (about one island per 50 sounds, at least 8).
3. t-SNE (perplexity 20, 900 iterations) places the 360 island centers in 3D so that similar islands sit near each other.
4. Each sound is placed around its island center, offset along the three principal directions of how sounds differ within islands (from an SVD of the residuals), scaled so islands do not overlap much.
5. An island's name is its most common sound type plus any character tag shared by at least 30% of its members.

Distance on this map means difference. The directions do not have fixed meanings, and the space between islands is approximate (t-SNE keeps neighbors, not global distances).

### Sound-type galaxies

Each sound type gets its own box, in one of two modes:

- **Measured axes** (default): three measures chosen for that kind of sound, each ranked within the type only (ties share their average rank), so the center of a box is a typical sound of that type.

  | Type | Left to right | Bottom to top | Far to near |
  |---|---|---|---|
  | Kick, snare or clap | brightness | punch | length |
  | Hat | brightness | length | noisiness |
  | Perc | brightness | length | punch |
  | Bass | brightness | length | punch |
  | Melodic | brightness | length | noisiness |
  | Loop | tempo | brightness | punch |
  | Texture | noisiness | length | stereo width |
  | FX and noise | noisiness | length | brightness |

- **Similarity islands**: the island layout, split by type and rescaled to fill each box (2nd to 98th percentile on each direction). Directions have no fixed meaning.

Focusing a type lists the islands that hold at least 8 of its sounds (copies counted once). Each island is named by how it differs from the rest of its type on the three axes (for example "bright, punchy, short kicks", using a word when its average rank is in the outer third), and shows its size, its two biggest packs, and its most typical sound: the member closest to the island's average on the three axes. Clicking an island sends all its sounds to Find a sound, most typical first.

### Neighbors ("Sounds like this")

Euclidean distance over the 7 ranked measures. When both sounds have a tempo, `(2 x BPM difference)^2` is added. Exact copies of the selected sound are skipped. The 14 closest are listed.

### Extra copies

Files with identical duration, RMS level, peak level, spectral centroid and sample rate are treated as the same sample. This catches a sample shipped in more than one pack, even under a different name.

### Gaps in your collection

On the chosen axes, the ranked space is cut into a 6 x 6 x 6 grid. A gap is a cell that holds at least 25 library sounds but 2 or fewer of the chosen type, and that touches at least one cell that does hold that type. The 12 gaps with the most of that type next door are shown.

### Sound families

Many packs ship several versions of one sound: clean, saturated, through tape, at 15 ips, or pitched across notes. Within each pack and sound type, sounds are sorted by file name length (the plainest name leads) and each joins the first family leader that measures within 0.03 of it (squared distance on the 7 standardized measures) and shares at least 40% of the words in its file name. Otherwise it starts a new family. "Collapse versions" shows one dot per family; selecting a sound lists its other versions, and "Sounds like this" skips them so the neighbors are genuinely different sounds.

### Vendor bridges

For every sound, the closest-measuring sound of the same type from a different vendor (Source) is found by exact nearest-neighbor search on the 7 standardized measures. The side panel names it when the distance is small ("nearly identical" up to 0.05, "very close" up to 0.15, "close" up to 0.4); otherwise the sound is marked as a signature sound, with nothing close from your other vendors. "Show vendor bridges" draws up to 5,000 of the closest pairs (distance 0.15 or less) as lines.

### Zoom lens ("What's here")

When the camera is within 150 units of the point it orbits, a ring with a radius of 16% of that distance is drawn around the center of the view. Every visible sound inside the ring (3D distance in the current layout) is summarized: type mix, average rank on the 7 measures (named when the average is in the outer third), top packs, keys, loop tempo range, how many you have used and how many are extra copies. Eight "range" picks are chosen by farthest-point sampling on the 7 ranked measures, starting from the sound closest to the ring's average, so they show the spread of what is there rather than eight near-copies. "Next door" lists the four nearest island centers outside the ring and how each differs from the ring's average (a difference of more than 35 rank steps on a measure).



Every `.rpp` file under your project folders (`project_folders` in soundlab.json, or `projects` if that is not set) is read, skipping `Backups` and `_upgrades` folders and anything matching `exclude` or `exclude_projects`. Every `FILE "..."` line is matched to the library by exact file path, ignoring case. A sound counts as used once per project that references it.

### Key and tempo matching

Keys match when they are the same, a fifth apart, or relative major and minor. Tempos match within 6%, also at double or half time.

## 3. Limits

- Types, keys and tempos are educated guesses. Key detection on drums and noise is unreliable, which is why it is skipped when the spectrum is too flat.
- Only the first 30 seconds of a long file are measured.
- The measures describe a sound, not whether it is good. Use the map to find candidates, then trust your ears.
- Used vs owned only sees samples referenced by path. Sounds you printed, resampled or recorded into new files are not linked back.

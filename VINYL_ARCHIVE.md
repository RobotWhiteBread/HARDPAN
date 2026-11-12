# VINYL_ARCHIVE: record the collection, build a sample library, open it to guests

This document has four parts.

1. **Recording from the record player.** Manual steps for a person. No code.
2. **Archive pipeline (phases A1 to A6).** Build instructions for the coding
   agent.
3. **Guest mode (phases Q1 to Q5).** A QR code that lets guests DJ and play
   from their phones. Build instructions for the coding agent.
4. **Purchased downloads (phases D1 to D4).** A fast path for buying one
   specific song and using it right away. Buying notes for a person, then build
   instructions for the coding agent.

It sits alongside `BUILD_PROMPT.md` and `GUITAR_RIG.md` and assumes the sample
component (ingest, sampler voice, granular engine, track player, export) exists
or is being built. Guest mode depends on it. The archive pipeline does not.

**Scope of use.** The archive is a personal copy of records already owned, for
listening and experimenting at home. Everything derived from it is marked not
cleared for release, and the mix export warns when such material is used.

---

## Part 1: Recording from the record player

### What you need

| Item | Notes |
|---|---|
| Turntable into the receiver's PHONO input | The normal listening hookup. Leave it as is |
| Receiver with REC OUT or TAPE OUT jacks | Carries the selected source at a fixed level |
| Stereo RCA to dual 1/4" TS cable | The one purchase. Example: Hosa CPR-203 (about 10 ft). Measure the run from receiver to desk first |
| MOTU M2 | Both inputs are used, left and right |
| Recording software | Audacity (free) is simplest for long straight captures. Ableton Live Lite also works. `vinyl.py record` (phase A1) replaces this step |
| Closed-back headphones | For monitoring while recording |

Never connect the turntable straight to the M2 unless the turntable has a
built-in preamp with a LINE output. A record needs the receiver's phono stage
for gain and playback EQ. Without it the recording is thin and quiet.

### Hookup

1. Leave the turntable connected to the receiver's PHONO input, with its ground
   wire on the receiver's GND post.
2. Connect the RCA ends of the new cable to the receiver's REC OUT (or TAPE
   OUT) jacks. White or black is left, red is right.
3. Connect the 1/4" ends to the M2: left into input 1, right into input 2.
4. On the M2, make sure 48V phantom power is off for both inputs and turn both
   gain knobs all the way down.
5. Set the receiver's source selector to PHONO.
6. Turn the receiver's speaker volume down. REC OUT is not affected by the
   volume knob, and quiet speakers keep bass from shaking the turntable and
   getting back into the recording.
7. Plug headphones into the M2 and press the MON button on both inputs so you
   hear the record directly.

### Software settings

- Sample rate: 48 kHz. Bit depth: 24-bit.
- Input device: MOTU M Series, channels 1 and 2 as one stereo track.
- File format for the archive: FLAC. It is lossless and about half the size of
  WAV.
- Record with no effects, no noise reduction, and no normalizing.

### Setting levels (do this once per session)

1. Put on a loud record and play its loudest passage.
2. Raise both M2 gain knobs together until the meters peak around -6 dB.
3. Check that the two knobs sit at the same position so left and right stay
   balanced.
4. If a meter ever hits red, lower the gain and start that side again. Clipping
   cannot be repaired afterward.
5. Leave the gain alone for the rest of the session so every record in the
   batch matches.

### Recording a side

1. Clean the record with a carbon fiber brush and check the stylus for dust.
2. Press record in the software first.
3. Lower the needle. Let a few seconds of lead-in groove record before the
   music starts.
4. Record the whole side without stopping. Do not pause between tracks.
5. After the last track, let the runout groove record for a few seconds, then
   stop.
6. Save as `Artist - Album - Side A.flac`. Flip the record and repeat for each
   side.
7. Spot check: listen to 20 seconds near the start, middle, and end. Confirm
   both channels are present and nothing is distorted.

### Keep a log

One line per side: artist, album, side, date, and notes such as skips, warps,
or a section worth sampling. The pipeline in Part 2 can read this.

### Rules for the masters

- The raw side recordings are the masters. Never edit, clean, or overwrite
  them.
- Click removal, normalizing, and track splitting all happen on copies.
- Back up the masters folder to a second drive. A collection takes weeks to
  record and seconds to lose.

### Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Steady low hum | Turntable ground wire loose, or a ground loop between computer and receiver. Reseat the ground wire, then try plugging computer and receiver into the same power strip |
| Thin, quiet, tinny sound | The signal skipped the phono stage. Confirm the turntable is on PHONO and the cable is on REC OUT |
| Distorted on loud parts | Gain too high on the M2. Lower it and redo the side |
| One channel missing | A loose RCA plug, or a gain knob at zero |
| No signal at REC OUT | Some receivers mute it when a TAPE MONITOR button is engaged. Turn that off |
| Receiver has no REC OUT | Use a turntable with a built-in preamp and LINE output straight into the M2, or add a standalone phono preamp between turntable and M2 |

### Time budget

Recording runs at playing speed. Allow about 50 minutes per LP including
cleaning, flipping, and the spot check. A few records a week gets through a
large collection over a winter.

---

## Part 2: Archive pipeline (for the coding agent)

Before writing code, read `BUILD_PROMPT.md`, `GUITAR_RIG.md`, and the existing
source so this matches the project's structure, naming, and dependencies. Show
a short plan, then build one phase at a time and stop after each phase for
review. Rename phases if they collide with existing names.

### Phase A1: side recorder (built: `vinyl.py record`)

A command line tool that replaces the manual software step.

- Record stereo from the M2 inputs to 24-bit, 48 kHz FLAC.
- Show live peak meters for both channels.
- Flag clipping the moment it happens, with the timestamp.
- Prompt for artist, album, and side, and name the file from them.
- Write a sidecar metadata file with date, gain note, and free text notes.
- Optional: stop automatically after a set period of runout groove.

### Phase A2: Discogs catalog sync

- Pull the user's Discogs collection through the Discogs API.
- Read the access token from an environment variable or an untracked config
  file. Never commit it and never print it.
- Cache each release locally: artist, title, year, label, catalog number,
  release ID, and track list with side positions and durations.
- Respect the API rate limit and resume cleanly if interrupted.
- Provide search so a recorded side can be matched to its release, with the
  user confirming the match.

### Phase A3: split and tag

- Detect gaps between tracks by analyzing level over time.
- Reconcile detected gaps against the Discogs track list for that side. Discogs
  durations are often missing or approximate, so treat them as hints.
- Show the proposed split points and wait for confirmation. Allow manual
  adjustment. Never split without approval.
- Write one FLAC per track into a separate tracks folder, tagged with artist,
  album, title, track position, year, label, and Discogs release ID.
- Leave the raw side master untouched.

### Phase A4: library layout and integrity

- Folder structure: `masters/` for raw sides, `tracks/` for split files,
  `listening/` for cleaned copies, each organized by artist and album.
- Write a checksum manifest for the masters and provide a verify command.
- Provide a progress report: releases in the Discogs collection, releases
  recorded, sides still missing.
- Provide a backup command that copies new masters to a second location and
  verifies the copy.

### Phase A5: listening copies (optional)

- Produce cleaned copies with click reduction and loudness normalization.
- Write them only to `listening/`. The masters and tracks stay raw.
- Keep the processing settings in the sidecar file so results can be
  reproduced.

### Phase A6: bridge to the sample component

- Register archived tracks in the sample library with source type "commercial
  recording" and `cleared_for_release` set to false.
- Let the user mark regions of a track as named samples with start and end
  points, without altering the track file.
- Send marked regions to the sampler voice, the granular engine, or the
  hardware export (mono, 16-bit, 32 kHz, 24 seconds maximum each, 3.5 minutes
  total).
- Import the notes from the recording log so passages flagged while recording
  show up as sample candidates.

---

## Part 3: Guest mode (for the coding agent)

Guests scan a QR code and get a control page on their phone. The phones are
remote controls only. All sound is made by HARDPAN on the host computer and
comes out of the host's speakers.

### Phase Q1: local session server

- A command starts and stops a guest session. It is off by default.
- Serve only on the home network address. Do not bind to a public interface, do
  not open router ports, and do not use a relay service.
- Generate a session URL with a random token, and show it as a QR code in the
  terminal and as a printable image.
- The token stops working when the session ends.

### Phase Q2: control protocol

- Use a WebSocket connection between each phone and the host.
- Messages: pad trigger, crossfader position, touch pad X and Y, and a small
  set of named macro controls.
- Validate and clamp every value on the host. Ignore anything unexpected.
- Limit message rate per guest and cap the number of connected guests.
- Give each guest a name and color so the host can see who is doing what.

### Phase Q3: guest page

- One mobile page with no install and no account.
- Eight sample pads, a crossfader, and a touch pad, with large touch targets.
- Show the current loop or deck names so guests know what they control.
- Must work in Safari on iPhone and Chrome on Android.
- No audio plays on the phone.

### Phase Q4: host controls

- A curated guest set: the host picks which samples and loops guests can reach.
  Nothing else in the library is visible to them.
- A limiter and a volume ceiling on the guest layer that guests cannot change.
- Quantize option: pad triggers snap to the tempo grid so guests stay in time
  with the running loop.
- Lock or remove an individual guest.
- A panic control that mutes the guest layer at once.
- The host's own playing (keyboard, guitar, bass) always stays independent of
  guest input.

### Phase Q5: safety and tests

- The web layer has no file upload, no file browsing, and no access to
  settings, the library, or the archive beyond the curated guest set.
- Guests cannot change host settings or start exports.
- Test with several simulated guests sending rapid and malformed messages.
- Document one known limit: Wi-Fi networks with client isolation, common on
  guest networks, block phones from reaching the host. Guests need to be on the
  main home network.

---

## Part 4: Purchased downloads

For the times a specific song is wanted now and it is not in the record
collection, or recording a whole side is more than the moment calls for.

### Buying notes (for a person)

| Store | Typical formats | Notes |
|---|---|---|
| Bandcamp | FLAC, WAV, MP3 and others, your choice after purchase | Best first stop. Albums arrive as a zip file |
| Qobuz download store | FLAC, including high resolution | Strong catalog for major label releases |
| iTunes Store | AAC, 256 kbps | Lossy. Files bought long ago may be copy protected |
| Amazon digital music | MP3 | Lossy |

- Choose lossless (FLAC or WAV) whenever it is offered. Granular stretching and
  pitch shifting expose the artifacts in MP3 and AAC files far more than normal
  listening does.
- Standard quality lossless (16-bit, 44.1 kHz) is plenty. High resolution costs
  more and adds nothing for this use.
- Save downloads into the inbox folder described in phase D1 and run the import
  command.
- Buying a download is a license to listen. It is not a license to release
  music containing pieces of it. Imported purchases are marked not cleared for
  release, the same as the vinyl archive.

### Phase D1: purchase inbox and import

- Create a `purchases/inbox/` folder. The user drops downloaded files or zip
  archives there.
- An import command unpacks archives and accepts FLAC, WAV, AIFF, ALAC, AAC,
  and MP3.
- Read the embedded tags for artist, album, title, track number, and year. Ask
  the user to fill in anything missing.
- Copy each file unchanged into `purchases/` organized by artist and album.
  Never re-encode the original.
- Write a sidecar file with: store, purchase date, an optional order reference,
  original format, bit depth, sample rate, and whether the file is lossless or
  lossy.
- Mark lossy files clearly in the library so they are not mistaken for lossless
  sources. Never convert a lossy file to FLAC and label it lossless.
- If a file is copy protected and cannot be decoded, report that plainly and
  skip it. Do not attempt to remove the protection.
- Include purchases in the checksum manifest and the backup command from phase
  A4.

### Phase D2: use it now

One command that takes a file from the inbox to playable in a single step.

- Import it as in D1.
- Register it in the sample library with source type "purchased download" and
  `cleared_for_release` set to false.
- Run the reference analysis: tempo, key, and loudness over time.
- Open it in the track player, ready to loop a region and play over.
- Report what it did in a few lines, including the detected tempo and key.

### Phase D3: sample extraction and hardware export

- Let the user mark named regions with start and end points, without altering
  the stored file.
- Offer suggested regions: the quietest sustained passages and the cleanest
  isolated hits, since those make the best drone and one-shot material.
- Send regions to the sampler voice or the granular engine.
- Hardware export: mono, 16-bit, 32 kHz, 24 seconds maximum each, with the
  running total reported against the 3.5 minute budget.

### Phase D4: one library across both sources

- If a purchased track matches a release in the Discogs cache or the vinyl
  archive, link the two and show both versions side by side.
- Search covers vinyl tracks, purchases, own recordings, and AI-generated files
  together, with a filter for source type.
- Keep a plain text wishlist of songs to buy or record. Importing a matching
  file checks the item off.
- No store integration of any kind: no purchasing, no logging in, no scraping,
  and no downloading on the user's behalf. The user buys in a browser and the
  tool starts at the inbox folder.

---

## Rules for all phases

- Never load, decode, or allocate inside the audio callback. Do that work on
  another thread and hand over finished buffers.
- Do not add any feature that downloads or records audio from streaming
  services. Sources are the record player and files on disk, including
  purchased downloads.
- Keep secrets such as the Discogs token out of the repository.
- Tell the user about any new dependency before adding it.
- Add tests for file naming, split point handling, tag writing, checksum
  verification, and guest message validation.
- Everything derived from the vinyl archive or from purchased downloads carries
  `cleared_for_release: false`, and the mix export must warn when it is used.

## Suggested order

1. Record three or four records by hand using Part 1 to settle the routine.
2. Build A1 and A2.
3. Build A3 and A4, then work through the collection.
4. Build A6 once the sample component exists.
5. Build D1 and D2 at any point after the sample component exists. They are
   small and give the quickest payoff. Add D3 and D4 afterward.
6. Build Q1 to Q5 for the first listening party.

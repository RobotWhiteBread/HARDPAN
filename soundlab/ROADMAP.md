# soundlab roadmap

What is done, what is next, and ideas being weighed. Suggestions and pull requests are welcome.

## Done

- Library builder and analyzer (tempo, key, note, brightness, punch, low end, width, noise, attack)
- Sound Galaxy: similarity islands, per-type galaxies with measured axes, choose-your-own axes with rank scaling, tie bands and wall shadows
- Find a sound: role, sliders, key and tempo, keyboard audition, more or less like this, box select
- Named carts with missing slots, feel, key and tempo readout, stars and play through
- Used vs owned from REAPER projects, unused island roulette
- Zoom lens: a summary of whatever area you zoom into
- Sound families (near-identical versions grouped) and vendor bridges (closest match from another vendor, signature sounds)
- Axis labels, How this works panel, METHODS.md
- REAPER scripts: kit builder, cart to project, project upgrade
- Serve mode and the beat playground: a looping 16-step beat that every auditioned sound swaps into, with locks, rerolls and loops varispeeded to tempo
- Weighted "sounds like this" sliders, drop-a-file search measured in the browser, Color by vendor for your own recordings
- Setup wizard: `soundlab.py setup` finds sample and project folders and writes soundlab.json
- Mix Stage: a song drawn by pan, pitch and depth, with clashes, open spots and library sounds that fit them
- Mix Stage over time (a stage per section), suggestions heard inside the song, and an own-recordings-first switch
- Voice Pads: beatbox and hum into the mic to play your own samples and a synth, match your mouth sounds to the library, record loops and export MIDI

## Next

1. Pitch auditioned sounds to the beat's key, and export the playground pattern to REAPER as a kit plus MIDI
2. Send to REAPER: a small REAPER script that drops the selected sound on the selected track at the edit cursor
3. Better instrument tags (which instrument, not only which drum role)
4. License badges for packs whose loops are compositions that may need clearance
5. Used vs owned for Ableton Live (.als) and FL Studio (.flp) projects

## Later

- A browser demo on GitHub Pages with a free, openly licensed sample set
- Re-measure low end in decibels and attack to the sample, to remove most ties on those axes
- Shape map: group sounds by how they move over time (swell, pluck, stab, reverse, drone)
- Machine timeline: drum machines laid out by year of release
- Loop chops: slice a loop and audition or send single hits
- Export a cart for hardware samplers (bit depth, length and naming limits, 16-pad layout)
- Split the Galaxy page into modules

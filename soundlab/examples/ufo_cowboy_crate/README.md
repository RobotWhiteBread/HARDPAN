# My Crate demo: Roswell Ranch to Midnight Rodeo

A made-up sample crate for showing how soundlab's My Crate view works, without anyone else's sounds.

Five invented vendors (Roswell Ranch Recordings, Dust Devil Audio, Area 51 Instruments, Midnight Rodeo Sound Co, and a "Mine" folder of back porch field recordings) with 134 UFO and cowboy sounds: abduction kicks, saloon door snares, antenna hats, spur jingles, whip cracks, hoof gallops, theremin notes, saucer sub bass, saloon piano chords, lasso laser zaps, tractor beam risers, crickets and coyote calls.

Every sound was synthesized by `make_sounds.py` (numpy, no recordings), then run through the real soundlab steps (build, analyze, galaxy) so the types, keys, tempos and character words are what the analyzer actually heard. Some of its guesses are wrong on pure synth sounds (long piano chords read as loops), which is a fair picture of what it does with a real library.

Open `index.html` in a browser (or any static web server) to explore: the tree on the left is vendor, pack and folder; filter by type, key, tempo, character and whether a sound was used in a song; click a row to hear it.

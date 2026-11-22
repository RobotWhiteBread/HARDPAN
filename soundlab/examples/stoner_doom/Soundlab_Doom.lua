-- Soundlab_Doom.lua  (EXAMPLE: needs the LoudFoundry "Stoner Doom Rock" sample pack)
-- Six stoner doom songs from the LoudFoundry Stoner Doom Rock pack.
-- Each song is slowed with VARISPEED (playback rate down, pitch drops with it,
-- like slowing a tape), so everything gets slower, lower and heavier together.
-- Arrangement: drone intro, riff A verse, riff B chorus with lead, half-time
-- breakdown, final chorus, outro. Bass is each riff doubled an octave down.
-- Open tracks wait for your Jaguar (M2 input 1) and bass (M2 input 2).
-- Run: Actions > New action > Load ReaScript, pick this file, Run.

local R = reaper
local SEP = package.config:sub(1, 1)
local HERE = (({ R.get_action_context() })[2] or ""):match("^(.*[\\/])") or ""
pcall(dofile, HERE .. "../../reaper/soundlab_paths.lua")
SOUNDLAB = SOUNDLAB or {}
-- where your copy of the pack lives (the folder holding "Guitar Loops", "Drum Loops" and so on)
local PACK = SOUNDLAB.stoner_doom or (os.getenv("USERPROFILE") or os.getenv("HOME") or "") .. SEP .. "Music" .. SEP .. "Samples" .. SEP .. "stoner_doom" .. SEP
local OUT = (SOUNDLAB.projects or (R.GetResourcePath() .. SEP .. "soundlab_projects" .. SEP)) .. "doom" .. SEP
local NOTES = { "C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B" }

local SONGS = {
  { name = "doom1_iron_wake",        title = "Iron Wake",        bpm = 85,  rate = 0.90, key = "F",
    riffs = { 9, 27, 9 },  lead = nil, drums = { 17, 18 }, fills = { 13, 14 }, drone = 1, amb = 3 },
  { name = "doom2_sabbath_sundown",  title = "Sabbath Sundown",  bpm = 100, rate = 0.80, key = "D",
    riffs = { 2, 7, 5 },   lead = 19,  drums = { 1, 2 },   fills = { 1, 2 },   drone = 1, amb = 7 },
  { name = "doom3_black_mesa",       title = "Black Mesa",       bpm = 120, rate = 0.75, key = "C#",
    riffs = { 15, 19, 28 }, lead = 1,  drums = { 4, 7 },   fills = { 23, 24 }, drone = 2, amb = 12 },
  { name = "doom4_desert_hex",       title = "Desert Hex",       bpm = 110, rate = 0.85, key = "F#",
    riffs = { 6, 18, 6 },  lead = 2,   drums = { 15, 16 }, fills = { 11, 12 }, drone = 1, amb = 18 },
  { name = "doom5_bong_moon_ritual", title = "Bong Moon Ritual", bpm = 90,  rate = 0.85, key = "F#",
    riffs = { 8, 40, 8 },  lead = 5,   drums = { 22, 22 }, fills = { 22, 22 }, drone = 1, amb = 22 },
  { name = "doom6_lowland_furnace",  title = "Lowland Furnace",  bpm = 130, rate = 0.70, key = "F",
    riffs = { 16, 21, 16 }, lead = nil, drums = { 5, 8 },  fills = { 4, 5 },   drone = 3, amb = 27 },
}

-- find a pack file by folder and number prefix (skips the MIDI files)
local function find(folder, prefix)
  local dir = PACK .. folder:gsub("\\", SEP) .. SEP
  local i = 0
  while true do
    local f = R.EnumerateFiles(dir, i)
    if not f then return nil end
    if f:find(prefix, 1, true) and f:lower():match("%.wav$") and not f:find("MIDI", 1, true) then return dir .. f end
    i = i + 1
  end
end
local function nth(folder, stem, n) return find(folder, string.format("%s_%02d_", stem, n)) end
local function oneshot(folder, stem, n) return find(folder, string.format("%s_%02d.", stem, n)) end

local built, problems = {}, {}

local function build(S)
  local BPM = S.bpm * S.rate                      -- project tempo after varispeed
  local BAR = 240 / BPM
  local STEP = BAR / 16
  local semis = 12 * math.log(S.rate) / math.log(2)
  local root = 0
  for i, n in ipairs(NOTES) do if n == S.key then root = i - 1 end end
  local newkey = NOTES[((root + math.floor(semis + 0.5)) % 12 + 12) % 12 + 1]

  R.Main_OnCommand(40859, 0)
  R.SetCurrentBPM(0, BPM, false)
  R.GetSetProjectNotes(0, true, string.format("%s\nStoner doom, %d BPM (pack riffs at %d, varispeed %.2f, %.1f semitones down)\nKey: about %s minor",
    S.title, math.floor(BPM + 0.5), S.bpm, S.rate, -semis, newkey))

  local function track(name, db, pan)
    local i = R.CountTracks(0)
    R.InsertTrackAtIndex(i, true)
    local tr = R.GetTrack(0, i)
    R.GetSetMediaTrackInfo_String(tr, "P_NAME", name, true)
    R.SetMediaTrackInfo_Value(tr, "D_VOL", 10 ^ (db / 20))
    R.SetMediaTrackInfo_Value(tr, "D_PAN", pan or 0)
    return tr
  end
  local function item(tr, file, bar, bars, vol, loop, pitch, fadein, fadeout)
    if not file then return end
    local it = R.AddMediaItemToTrack(tr)
    local tk = R.AddTakeToMediaItem(it)
    local src = R.PCM_Source_CreateFromFile(file)
    R.SetMediaItemTake_Source(tk, src)
    R.GetSetMediaItemTakeInfo_String(tk, "P_NAME", file:match("([^\\]+)$"), true)
    R.SetMediaItemTakeInfo_Value(tk, "D_PLAYRATE", S.rate)
    R.SetMediaItemTakeInfo_Value(tk, "B_PPITCH", 0)            -- varispeed: pitch follows the rate
    if pitch then R.SetMediaItemTakeInfo_Value(tk, "D_PITCH", pitch) end
    local len = bars and bars * BAR or R.GetMediaSourceLength(src) / S.rate
    R.SetMediaItemInfo_Value(it, "D_POSITION", (bar - 1) * BAR)
    R.SetMediaItemInfo_Value(it, "D_LENGTH", len)
    R.SetMediaItemInfo_Value(it, "B_LOOPSRC", loop and 1 or 0)
    R.SetMediaItemInfo_Value(it, "D_VOL", vol or 1)
    R.SetMediaItemInfo_Value(it, "D_FADEINLEN", fadein or 0.005)
    R.SetMediaItemInfo_Value(it, "D_FADEOUTLEN", fadeout or (loop and 0.05 or 0.02))
  end
  local function hit(tr, file, bar, step, vol) if file then
    local it = R.AddMediaItemToTrack(tr)
    local tk = R.AddTakeToMediaItem(it)
    local src = R.PCM_Source_CreateFromFile(file)
    R.SetMediaItemTake_Source(tk, src)
    R.SetMediaItemTakeInfo_Value(tk, "D_PLAYRATE", S.rate)
    R.SetMediaItemTakeInfo_Value(tk, "B_PPITCH", 0)
    R.SetMediaItemInfo_Value(it, "D_POSITION", (bar - 1) * BAR + step * STEP)
    R.SetMediaItemInfo_Value(it, "D_LENGTH", R.GetMediaSourceLength(src) / S.rate)
    R.SetMediaItemInfo_Value(it, "D_VOL", vol or 1)
  end end
  local function eq(tr, band, hz, gain)
    local fx = R.TrackFX_AddByName(tr, "ReaEQ", false, -1)
    if fx < 0 then return end
    R.TrackFX_SetEQParam(tr, fx, band, 0, 0, hz, false); R.TrackFX_SetEQParam(tr, fx, band, 0, 1, gain, false)
  end

  local riff = {}
  for i, n in ipairs(S.riffs) do riff[i] = nth("Guitar Loops", "LFSDR_Guitar_Loop", n) end
  local lead = S.lead and nth("Lead Guitar Loops", "LFSDR_Guitar_Lead", S.lead)
  local drum = { nth("Drum Loops", "LFSDR_Drum_Loop", S.drums[1]), nth("Drum Loops", "LFSDR_Drum_Loop", S.drums[2]) }
  local fill = { nth("Drum Fills", "LFSDR_Drum_Fill", S.fills[1]), nth("Drum Fills", "LFSDR_Drum_Fill", S.fills[2]) }
  local drone = nth("Industrial Drones", "LFSDR_Industrial_Drone", S.drone)
  local amb = nth("Industrial Ambiences", "LFSDR_Industrial_Ambience", S.amb)
  local noise = nth("Electric Noises", "LFSDR_Electric_Noise", 1)
  local static = nth("Static Noises", "LFSDR_Static_Noise", 2)
  local OS = "Drum One Shots\\"
  local kick = oneshot(OS .. "Kick Drum One Shots", "LFSDR_Kick_Drum", 3)
  local snare = oneshot(OS .. "Snare Drum One Shots", "LFSDR_Snare_Drum", 2)
  local crash = oneshot(OS .. "Overhead Drum One Shots", "LFSDR_Overhead", 1)
  local ride = oneshot(OS .. "Overhead Drum One Shots", "LFSDR_Overhead", 7)
  local tom = { oneshot(OS .. "Tom Drum One Shots", "LFSDR_Tom", 1), oneshot(OS .. "Tom Drum One Shots", "LFSDR_Tom", 3), oneshot(OS .. "Tom Drum One Shots", "LFSDR_Tom", 5) }
  local impact = { oneshot("Impact Hits", "LFSDR_Impact_Hit", 4), oneshot("Impact Hits", "LFSDR_Impact_Hit", 1) }
  local metal = oneshot("Metal Hits", "LFSDR_Metal_Hit", 3)
  local glass = oneshot("Glass Smashes", "LFSDR_Glass_Smash", 2)
  for _, f in ipairs({ riff[1], riff[2], drum[1], drum[2] }) do if not f then problems[#problems + 1] = S.title .. ": a core loop is missing" break end end

  -- sections (bars): intro 1-8, verse 9-24, chorus 25-40, breakdown 41-48, final chorus 49-64, outro 65-72
  local SECT = { { 1, "Intro" }, { 9, "Verse" }, { 25, "Chorus" }, { 41, "Breakdown" }, { 49, "Final chorus" }, { 65, "Outro" } }
  for _, m in ipairs(SECT) do R.AddProjectMarker(0, false, (m[1] - 1) * BAR, 0, m[2], -1) end

  -- guitars
  local gA = track("Riff A (verse)", -6)
  item(gA, riff[1], 1, 8, 0.75, true, nil, 6 * BAR, nil)       -- fades in out of the drone
  item(gA, riff[1], 9, 16, 1, true)
  item(gA, riff[1], 65, 8, 1, true, nil, nil, 6 * BAR)         -- rings out
  local gB = track("Riff B (chorus)", -6)
  item(gB, riff[2], 25, 16, 1, true)
  item(gB, riff[2], 49, 16, 1, true)
  local gC = track("Riff C (breakdown)", -6)
  item(gC, riff[3], 41, 8, 1, true)
  if lead then
    local ld = track("Lead", -9, 0.18)
    item(ld, lead, 33, 8, 0.9, true)
    item(ld, lead, 49, 16, 1, true)
    eq(ld, 1, 250, -12)
  end

  -- bass: every riff an octave down, low-passed
  local bs = track("Bass (riffs an octave down)", -4)
  for _, p in ipairs({ { riff[1], 9, 16 }, { riff[2], 25, 16 }, { riff[3], 41, 8 }, { riff[2], 49, 16 }, { riff[1], 65, 4 } }) do
    item(bs, p[1], p[2], p[3], 1, true, -12)
  end
  eq(bs, 4, 380, -18)

  -- drums: loops with fills on the last two bars of each section
  local dr = track("Drums", -3)
  item(dr, drum[1], 9, 14, 1, true);  item(dr, fill[1], 23, 2, 1, false)
  item(dr, drum[2], 25, 14, 1, true); item(dr, fill[2], 39, 2, 1, false)
  item(dr, drum[2], 49, 14, 1, true); item(dr, fill[1], 63, 2, 1, false)

  -- breakdown: half-time one-shots, crash on every bar, toms rolling into the last chorus
  local hk = track("Breakdown kit", -3)
  for b = 41, 48 do
    hit(hk, kick, b, 0, 1); hit(hk, crash, b, 0, 0.8); hit(hk, snare, b, 8, 1)
    if b % 2 == 0 then hit(hk, kick, b, 11, 0.8) end
    for st = 4, 12, 4 do if st ~= 8 then hit(hk, ride, b, st, 0.35) end end
  end
  for k, st in ipairs({ 8, 10, 12, 14 }) do hit(hk, tom[(k - 1) % 3 + 1], 48, st, 0.9) end

  -- crashes and impacts on every drop
  local fx = track("Impacts and crashes", -6)
  for _, b in ipairs({ 9, 25, 49, 65 }) do hit(fx, crash, b, 0, 1); hit(fx, impact[(b % 2) + 1], b, 0, 0.9) end
  hit(fx, glass, 41, 0, 0.8)
  hit(fx, metal, 41, 0, 0.9); hit(fx, metal, 45, 0, 0.7)

  -- atmosphere
  local at = track("Drone and ambience", -12)
  item(at, drone, 1, 8, 1, true, nil, 2 * BAR, 2 * BAR)
  item(at, amb, 1, 8, 0.7, true, nil, nil, 2 * BAR)
  item(at, amb, 41, 8, 0.6, true)
  item(at, drone, 65, 8, 1, true, nil, nil, 7 * BAR)
  local nz = track("Feedback and static", -14, -0.2)
  item(nz, noise, 7, 2, 0.8, true, nil, 2 * BAR, 0.05)
  item(nz, static, 40, 1, 0.7, true)
  item(nz, noise, 69, 4, 0.8, true, nil, 0.5 * BAR, 3 * BAR)

  -- your instruments: arm and play over it
  local jag = track("Jaguar (M2 input 1)", -6, -0.25)
  R.SetMediaTrackInfo_Value(jag, "I_RECINPUT", 0)
  R.SetMediaTrackInfo_Value(jag, "I_RECMON", 1)
  local bg = track("Bass guitar (M2 input 2)", -6, 0)
  R.SetMediaTrackInfo_Value(bg, "I_RECINPUT", 1)
  R.SetMediaTrackInfo_Value(bg, "I_RECMON", 1)

  R.TrackFX_AddByName(R.GetMasterTrack(0), "ReaLimit", false, -1)
  R.GetSet_LoopTimeRange(true, false, 0, 72 * BAR, false)
  R.SetEditCurPos(0, true, false)
  R.UpdateArrange()
  if R.RecursiveCreateDirectory then R.RecursiveCreateDirectory(OUT, 0) end
  R.Main_SaveProjectEx(0, OUT .. S.name .. ".rpp", 0)
  built[#built + 1] = string.format("%-18s %3d BPM, about %s minor (%.1f semitones down)", S.title, math.floor(BPM + 0.5), newkey, -semis)
end

if R.ShowMessageBox("Build 6 stoner doom songs, each in its own tab?\nSaved to " .. OUT, "Soundlab Doom", 1) ~= 1 then return end
for _, S in ipairs(SONGS) do
  local ok, err = pcall(build, S)
  if not ok then problems[#problems + 1] = S.title .. ": " .. tostring(err) end
end
local msg = table.concat(built, "\n") .. "\n\nClick a tab, press SPACE. Arm the Jaguar track and play along."
if #problems > 0 then msg = msg .. "\n\nProblems:\n" .. table.concat(problems, "\n") end
R.ShowMessageBox(msg, "Soundlab Doom: built " .. #built .. " songs", 0)

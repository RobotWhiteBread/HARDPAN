-- HARDPAN_Kit_Builder.lua
-- Turns a kit (saved from the Sound Galaxy, or written by make_kits.py) into a
-- 32 bar session: intro, verse, hook, verse, outro, drums patterned in a groove
-- style, loops stretched to one tempo, and a starting mix.
-- Run: Actions > New action > Load ReaScript, pick this file, Run.
--
-- A kit file may start with header lines:
--   # title=Machine Tour 1: SP-1200
--   # style=boombap        (boombap, house, electro, halftime)
--   # bpm=90
--   # save=machine1_sp1200
-- HARDPAN_Build_All_Kits.lua sets HP_KITFILE and HP_BATCH to build many at once.

local R = reaper
local OUT = [[C:\HARDPAN\studio_setup\projects\]]
local DL = (os.getenv("USERPROFILE") or [[C:\Users\basch]]) .. [[\Downloads\]]

local function exists(p) local f = io.open(p, "rb"); if f then f:close(); return true end; return false end

-- which kit ---------------------------------------------------------------
local kitfile = HP_KITFILE
if not kitfile then
  kitfile = DL .. "hardpan_kit.txt"
  for n = 1, 60 do local p = DL .. "hardpan_kit (" .. n .. ").txt"; if exists(p) then kitfile = p end end
  if R.GetUserFileNameForRead then
    local ok, pick = R.GetUserFileNameForRead(kitfile, "Pick a kit", "txt")
    if not ok then return end
    kitfile = pick
  end
end
if not exists(kitfile) then
  if not HP_BATCH then R.ShowMessageBox("No kit found. Save one from the Sound Galaxy first.", "HARDPAN", 0) end
  HP_RESULT = "no kit file"; return
end

-- read it -----------------------------------------------------------------
local kit, missing, head = {}, {}, {}
for raw in io.lines(kitfile) do
  local line = raw:gsub("\r", "")
  local hk, hv = line:match("^#%s*(%w+)%s*=%s*(.-)%s*$")
  if hk then head[hk] = hv
  else
    local ty, bpm, key, path = line:match("^([^\t]*)\t([^\t]*)\t([^\t]*)\t(.+)$")
    if ty and ty ~= "type" then
      if exists(path) then
        kit[ty] = kit[ty] or {}
        table.insert(kit[ty], { path = path, bpm = tonumber(bpm), key = key })
      else missing[#missing + 1] = path end
    end
  end
end
local function has(t) return kit[t] and #kit[t] > 0 end
if not next(kit) then
  if not HP_BATCH then R.ShowMessageBox("None of the kit files were found on disk.", "HARDPAN", 0) end
  HP_RESULT = "no sounds found"; return
end

-- groove styles -------------------------------------------------------------
-- steps are 16ths in a bar; numbers are velocities
local STYLES = {
  boombap  = { bpm = 88,  swing = 0.58, kick = { [0] = 1, [7] = 0.7, [10] = 0.9 }, kick_fill = { [0] = 1, [3] = 0.55, [7] = 0.75, [10] = 0.9 },
               snare = { [4] = 1, [12] = 1 }, hat = 2, hat_hook = 1, bass = { { 0, 1.5 }, { 10, 1.25 } } },
  house    = { bpm = 122, swing = 0.54, kick = { [0] = 1, [4] = 1, [8] = 1, [12] = 1 }, kick_fill = { [0] = 1, [4] = 1, [8] = 1, [12] = 1, [14] = 0.6 },
               snare = { [4] = 0.9, [12] = 0.9 }, hat = "off", hat_hook = 1, bass = { { 2, 0.4 }, { 6, 0.4 }, { 10, 0.4 }, { 14, 0.4 } } },
  electro  = { bpm = 112, swing = 0.5,  kick = { [0] = 1, [6] = 0.8, [10] = 0.9 }, kick_fill = { [0] = 1, [3] = 0.7, [6] = 0.8, [10] = 0.9, [13] = 0.6 },
               snare = { [4] = 1, [12] = 1 }, hat = 1, hat_hook = 1, bass = { { 0, 0.4 }, { 3, 0.4 }, { 6, 0.4 }, { 10, 0.6 } } },
  halftime = { bpm = 140, swing = 0.5,  kick = { [0] = 1, [11] = 0.8 }, kick_fill = { [0] = 1, [7] = 0.6, [11] = 0.8, [14] = 0.7 },
               snare = { [8] = 1 }, hat = 1, hat_hook = 1, roll = true, bass = { { 0, 2.0 }, { 11, 1.5 } } },
}
local ST = STYLES[(head.style or "boombap"):lower()] or STYLES.boombap

-- tempo: header, else first loop with a tempo, else the style's
local BPM = tonumber(head.bpm)
if not BPM then for _, l in ipairs(kit.loop or {}) do if l.bpm and l.bpm >= 60 and l.bpm <= 180 then BPM = l.bpm; break end end end
BPM = BPM or ST.bpm
local BEAT, BAR, STEP = 60 / BPM, 240 / BPM, 15 / BPM
local seed = 4242
for c in kitfile:gmatch(".") do seed = (seed * 31 + c:byte()) % 2147483648 end
local function rnd() seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648 end

R.Main_OnCommand(40859, 0)            -- new project tab
R.SetCurrentBPM(0, BPM, false)

local function newtrack(name, db, pan)
  local i = R.CountTracks(0)
  R.InsertTrackAtIndex(i, true)
  local tr = R.GetTrack(0, i)
  R.GetSetMediaTrackInfo_String(tr, "P_NAME", name, true)
  R.SetMediaTrackInfo_Value(tr, "D_VOL", 10 ^ (db / 20))
  R.SetMediaTrackInfo_Value(tr, "D_PAN", pan or 0)
  return tr
end
local function lowcut(tr, hz)        -- low shelf cut keeps mud out of the kick and bass range
  local fx = R.TrackFX_AddByName(tr, "ReaEQ", false, -1)
  if fx < 0 then return end
  R.TrackFX_SetEQParam(tr, fx, 1, 0, 0, hz, false); R.TrackFX_SetEQParam(tr, fx, 1, 0, 1, -18, false)
end
local function item(tr, file, pos, len, vol, loop, rate)
  local it = R.AddMediaItemToTrack(tr)
  local tk = R.AddTakeToMediaItem(it)
  local src = R.PCM_Source_CreateFromFile(file)
  R.SetMediaItemTake_Source(tk, src)
  R.GetSetMediaItemTakeInfo_String(tk, "P_NAME", file:match("([^\\]+)$"), true)
  rate = rate or 1
  if math.abs(rate - 1) > 0.001 then
    R.SetMediaItemTakeInfo_Value(tk, "D_PLAYRATE", rate)
    R.SetMediaItemTakeInfo_Value(tk, "B_PPITCH", 1)
  end
  local srclen = R.GetMediaSourceLength(src) / rate
  R.SetMediaItemInfo_Value(it, "D_POSITION", pos)
  R.SetMediaItemInfo_Value(it, "D_LENGTH", len and math.min(len, loop and len or srclen) or srclen)
  R.SetMediaItemInfo_Value(it, "B_LOOPSRC", loop and 1 or 0)
  R.SetMediaItemInfo_Value(it, "D_VOL", vol or 1)
  R.SetMediaItemInfo_Value(it, "D_FADEINLEN", 0.002)
  R.SetMediaItemInfo_Value(it, "D_FADEOUTLEN", loop and 0.02 or 0.008)
  return srclen
end
local function stepTime(bar, st)      -- bar from 1, step 0..15 (fractions allowed), swing on the off 8ths
  local t = (bar - 1) * BAR + st * STEP
  if math.floor(st) == st and st % 4 == 2 then t = t + (ST.swing - 0.5) * 4 * STEP end
  return t
end
local function pick(t, n) local l = kit[t]; return l[((n - 1) % #l) + 1].path end

-- sections: {first bar, last bar, name, drums?, energy}
local SECT = { {1, 4, "Intro", false, 1}, {5, 12, "Verse 1", true, 2}, {13, 20, "Hook", true, 3},
               {21, 28, "Verse 2", true, 2}, {29, 32, "Outro", false, 1} }
if head.title then R.GetSetProjectNotes(0, true, head.title .. "\n" .. (head.style or "boombap") .. " at " .. BPM .. " BPM") end
for _, s in ipairs(SECT) do R.AddProjectMarker(0, false, (s[1] - 1) * BAR, 0, s[3], -1) end

-- drums
if has("kick") then
  local tr = newtrack("Kick", -3)
  for si, s in ipairs(SECT) do if s[4] then
    local f = pick("kick", si)
    for b = s[1], s[2] do for st, v in pairs((b % 4 == 0) and ST.kick_fill or ST.kick) do item(tr, f, stepTime(b, st), nil, v) end end
  end end
end
if has("snare_clap") then
  local tr = newtrack("Snare and clap", -5)
  for _, s in ipairs(SECT) do if s[4] then
    for b = s[1], s[2] do
      for st, v in pairs(ST.snare) do
        item(tr, pick("snare_clap", 1), stepTime(b, st), nil, v)
        if s[5] == 3 and #kit.snare_clap > 1 then item(tr, pick("snare_clap", 2), stepTime(b, st), nil, 0.6 * v) end
      end
      if ST.swing > 0.52 and b % 2 == 0 and rnd() < 0.5 then item(tr, pick("snare_clap", 1), stepTime(b, 15), nil, 0.25) end  -- ghost
    end
  end end
  lowcut(tr, 120)
end
if has("hat") then
  local tr = newtrack("Hats", -12, 0.22)
  for _, s in ipairs(SECT) do if s[4] then
    for b = s[1], s[2] do
      local every = (s[5] == 3) and ST.hat_hook or ST.hat
      local steps = {}
      if every == "off" then steps = { 2, 6, 10, 14 } else for st = 0, 15, every do steps[#steps + 1] = st end end
      for _, st in ipairs(steps) do
        local v = (st % 4 == 0) and 0.85 or ((st % 2 == 0) and 0.6 or 0.32)
        if every == "off" then v = 0.8 end
        item(tr, pick("hat", (st % 8 == 6 and #kit.hat > 1) and 2 or 1), stepTime(b, st), nil, v * (0.85 + 0.3 * rnd()))
      end
      if ST.roll and b % 2 == 0 then          -- triplet roll into the next bar
        for k = 0, 5 do item(tr, pick("hat", 1), stepTime(b, 14) + k * STEP / 3, nil, 0.35 + 0.08 * k) end
      end
    end
  end end
  lowcut(tr, 300)
end
if has("perc") then
  local tr = newtrack("Perc", -11, -0.3)
  for _, s in ipairs(SECT) do if s[4] then
    for b = s[1], s[2] do if s[5] == 3 or b % 2 == 0 then item(tr, pick("perc", b), stepTime(b, 14), nil, 0.8) end end
  end end
  lowcut(tr, 200)
end

-- loops stretched to the project tempo
if has("loop") then
  for li, l in ipairs(kit.loop) do
    if li > 3 then break end
    local tr = newtrack("Loop " .. li, li == 1 and -8 or -12, (li == 2 and -0.35) or (li == 3 and 0.35) or 0)
    local src = R.PCM_Source_CreateFromFile(l.path)
    local len = R.GetMediaSourceLength(src)
    local rate
    if l.bpm and l.bpm >= 60 then
      rate = BPM / l.bpm
      if rate < 0.6 then rate = rate * 2 elseif rate > 1.7 then rate = rate / 2 end
    else local bars = math.max(1, math.floor(len / BAR + 0.5)); rate = len / (bars * BAR) end
    for _, s in ipairs(SECT) do
      if li == 1 or s[5] >= 2 then item(tr, l.path, (s[1] - 1) * BAR, (s[2] - s[1] + 1) * BAR, 1, true, rate) end
    end
    if li > 1 then lowcut(tr, 150) end
  end
end

-- bass, melodic, texture, fx
if has("bass") then
  local tr = newtrack("Bass", -6)
  for _, s in ipairs(SECT) do if s[4] then
    for b = s[1], s[2] do
      for n, hit in ipairs(ST.bass) do
        item(tr, pick("bass", (n % 2 == 0 and #kit.bass > 1) and 2 or 1), stepTime(b, hit[1]), hit[2] * BEAT, n == 1 and 1 or 0.8)
      end
    end
  end end
end
if has("melodic") then
  local tr = newtrack("Melodic", -10, 0.1)
  for b = 1, 32, 2 do item(tr, pick("melodic", math.floor(b / 8) + 1), (b - 1) * BAR, 2 * BAR, (b >= 13 and b <= 20) and 1 or 0.75) end
  lowcut(tr, 180)
end
if has("texture") then
  local tr = newtrack("Texture", -16, -0.15)
  item(tr, pick("texture", 1), 0, 32 * BAR, 0.8, true)
  lowcut(tr, 250)
end
if has("fx_noise") then
  local tr = newtrack("FX", -12)
  for _, b in ipairs({ 4, 12, 20, 28 }) do item(tr, pick("fx_noise", b), (b - 0.5) * BAR, nil, 0.8) end
  lowcut(tr, 200)
end

-- record-ready track for you
local keys = newtrack("Keys (you)", -8)
R.SetMediaTrackInfo_Value(keys, "I_RECINPUT", 4096 + 63 * 32)
R.SetMediaTrackInfo_Value(keys, "I_RECMON", 1)
if R.TrackFX_AddByName(keys, "Stage-73 V2", false, -1) < 0 then R.TrackFX_AddByName(keys, "ReaSynth", false, -1) end

R.TrackFX_AddByName(R.GetMasterTrack(0), "ReaLimit", false, -1)
R.GetSet_LoopTimeRange(true, true, 0, 32 * BAR, false)
R.GetSetRepeat(1)
R.SetEditCurPos(0, true, false)
R.UpdateArrange()

local name = (head.save or ("galaxy_kit_" .. os.date("%m%d_%H%M"))) .. ".rpp"
if R.Main_SaveProjectEx then R.Main_SaveProjectEx(0, OUT .. name, 0) end
HP_RESULT = string.format("%s: %s at %d BPM", name, head.style or "boombap", math.floor(BPM + 0.5))
if not HP_BATCH then
  local msg = string.format("Built %s at %d BPM from %s\nSaved as %s\nPress SPACE to play.",
    head.style or "boombap", math.floor(BPM + 0.5), kitfile:match("([^\\/]+)$"), name)
  if #missing > 0 then msg = msg .. "\n\n" .. #missing .. " kit file(s) not found on disk." end
  R.ShowMessageBox(msg, "HARDPAN Kit Builder", 0)
end

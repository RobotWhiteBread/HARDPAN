-- Soundlab_Cart_To_Project.lua
-- Lays out the sounds you put in the Sound Galaxy cart (soundlab_cart.txt in
-- Downloads) on tracks grouped by type, one after another, so you can audition,
-- drag and reference them in a session. Saves as cart_<date>.rpp.
-- Run: Actions > New action > Load ReaScript, pick this file, Run.

local R = reaper
local SEP = package.config:sub(1, 1)
local HERE = (({ R.get_action_context() })[2] or ""):match("^(.*[\\/])") or ""
pcall(dofile, HERE .. "soundlab_paths.lua")
SOUNDLAB = SOUNDLAB or {}
local HOMEDIR = os.getenv("USERPROFILE") or os.getenv("HOME") or ""
local OUT = SOUNDLAB.projects or (R.GetResourcePath() .. SEP .. "soundlab_projects" .. SEP)
local DL = SOUNDLAB.downloads or (HOMEDIR .. SEP .. "Downloads" .. SEP)

local function exists(p) local f = io.open(p, "rb"); if f then f:close(); return true end; return false end

local cartfile = DL .. "soundlab_cart.txt"
for n = 1, 60 do local p = DL .. "soundlab_cart (" .. n .. ").txt"; if exists(p) then cartfile = p end end
if R.GetUserFileNameForRead then
  local ok, pick = R.GetUserFileNameForRead(cartfile, "Pick a Sound Galaxy cart", "txt")
  if not ok then return end
  cartfile = pick
end
if not exists(cartfile) then R.ShowMessageBox("No cart found. Fill the cart in the Sound Galaxy and press Save cart.", "Soundlab", 0); return end

local GROUPS = {
  { "Kicks", { kick = true } }, { "Snares and claps", { snare_clap = true } }, { "Hats", { hat = true } },
  { "Perc", { perc = true } }, { "Bass", { bass = true } }, { "Loops", { loop = true } },
  { "Melodic", { melodic = true } }, { "Texture and FX", { texture = true, fx_noise = true } },
}
local sounds, missing = {}, 0
for raw in io.lines(cartfile) do
  local line = raw:gsub("\r", "")
  local ty, bpm, key, path = line:match("^([^\t]*)\t([^\t]*)\t([^\t]*)\t(.+)$")
  if ty and ty ~= "type" then
    if exists(path) then sounds[#sounds + 1] = { type = ty, bpm = bpm, key = key, path = path } else missing = missing + 1 end
  end
end
if #sounds == 0 then R.ShowMessageBox("None of the cart's sounds were found on disk.", "Soundlab", 0); return end

R.Main_OnCommand(40859, 0)                       -- new project tab
local notes = { "Sound Galaxy cart: " .. #sounds .. " sounds", "" }
local GAP = 0.75                                 -- seconds of silence between sounds
for _, g in ipairs(GROUPS) do
  local list = {}
  for _, s in ipairs(sounds) do if g[2][s.type] then list[#list + 1] = s end end
  for _, s in ipairs(sounds) do                  -- anything without a group lands in the last one
    if g[1] == "Texture and FX" then
      local grouped = false
      for _, gg in ipairs(GROUPS) do if gg[2][s.type] then grouped = true end end
      if not grouped then list[#list + 1] = s end
    end
  end
  if #list > 0 then
    local i = R.CountTracks(0)
    R.InsertTrackAtIndex(i, true)
    local tr = R.GetTrack(0, i)
    R.GetSetMediaTrackInfo_String(tr, "P_NAME", g[1] .. " (" .. #list .. ")", true)
    local t = 0
    for _, s in ipairs(list) do
      local it = R.AddMediaItemToTrack(tr)
      local tk = R.AddTakeToMediaItem(it)
      local src = R.PCM_Source_CreateFromFile(s.path)
      R.SetMediaItemTake_Source(tk, src)
      local name = s.path:match("([^\\/]+)$")
      local label = name .. ((s.bpm ~= "" and ("  " .. s.bpm .. " BPM") or "") .. (s.key ~= "" and ("  " .. s.key) or ""))
      R.GetSetMediaItemTakeInfo_String(tk, "P_NAME", label, true)
      local len = R.GetMediaSourceLength(src)
      R.SetMediaItemInfo_Value(it, "D_POSITION", t)
      R.SetMediaItemInfo_Value(it, "D_LENGTH", len)
      t = t + len + GAP
      notes[#notes + 1] = g[1] .. ": " .. label .. "\n    " .. s.path
    end
  end
end
R.GetSetProjectNotes(0, true, table.concat(notes, "\n"))
R.SetEditCurPos(0, true, false)
R.UpdateArrange()
local name = "cart_" .. os.date("%Y%m%d_%H%M") .. ".rpp"
if R.RecursiveCreateDirectory then R.RecursiveCreateDirectory(OUT, 0) end
R.Main_SaveProjectEx(0, OUT .. name, 0)
R.ShowMessageBox(#sounds .. " sounds laid out by type and saved as " .. name ..
  (missing > 0 and ("\n" .. missing .. " sound(s) not found on disk.") or "") ..
  "\n\nThe full list with paths is in the project notes (View > Project notes).", "Soundlab cart", 0)

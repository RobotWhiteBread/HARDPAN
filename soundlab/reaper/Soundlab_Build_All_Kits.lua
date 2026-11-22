-- Soundlab_Build_All_Kits.lua
-- Builds every kit in projects\kits (for example the Machine Tour from
-- make_kits.py) into its own REAPER project tab and saves each one.
-- Keep Soundlab_Kit_Builder.lua in the same folder as this script.
-- Run: Actions > New action > Load ReaScript, pick this file, Run.

local R = reaper
local SEP = package.config:sub(1, 1)
local HERE = (({ R.get_action_context() })[2] or ""):match("^(.*[\\/])") or ""
pcall(dofile, HERE .. "soundlab_paths.lua")           -- written by setup from soundlab.json
SOUNDLAB = SOUNDLAB or {}
local HOMEDIR = os.getenv("USERPROFILE") or os.getenv("HOME") or ""
local KITS = SOUNDLAB.kits or (R.GetResourcePath() .. SEP .. "soundlab_projects" .. SEP .. "kits" .. SEP)
local builder = HERE .. "Soundlab_Kit_Builder.lua"

local function exists(p) local f = io.open(p, "rb"); if f then f:close(); return true end; return false end
if not exists(builder) then R.ShowMessageBox("Soundlab_Kit_Builder.lua must sit next to this script.", "Soundlab", 0); return end

local files, i = {}, 0
while true do
  local f = R.EnumerateFiles(KITS, i)
  if not f then break end
  if f:lower():match("%.txt$") then files[#files + 1] = f end
  i = i + 1
end
table.sort(files)
if #files == 0 then R.ShowMessageBox("No kits in " .. KITS .. "\nSave a kit from the Sound Galaxy into this folder, or run an example kit maker.", "Soundlab", 0); return end

local ok = R.ShowMessageBox("Build " .. #files .. " songs from " .. KITS .. "?\nEach opens in its own tab and is saved to the projects folder.", "Soundlab", 1)
if ok ~= 1 then return end

local report = {}
HP_BATCH = true
for _, f in ipairs(files) do
  HP_KITFILE, HP_RESULT = KITS .. f, nil
  local good, err = pcall(dofile, builder)
  report[#report + 1] = good and (HP_RESULT or f) or (f .. ": " .. tostring(err))
end
HP_BATCH, HP_KITFILE = nil, nil
R.ShowMessageBox(table.concat(report, "\n") .. "\n\nClick through the tabs and press SPACE to play.", "Soundlab: built " .. #files .. " songs", 0)

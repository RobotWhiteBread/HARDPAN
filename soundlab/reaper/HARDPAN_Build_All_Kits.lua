-- HARDPAN_Build_All_Kits.lua
-- Builds every kit in projects\kits (for example the Machine Tour from
-- make_kits.py) into its own REAPER project tab and saves each one.
-- Keep HARDPAN_Kit_Builder.lua in the same folder as this script.
-- Run: Actions > New action > Load ReaScript, pick this file, Run.

local R = reaper
local KITS = [[C:\HARDPAN\studio_setup\projects\kits\]]
local here = ({ R.get_action_context() })[2]:match("^(.*[\\/])") or ""
local builder = here .. "HARDPAN_Kit_Builder.lua"

local function exists(p) local f = io.open(p, "rb"); if f then f:close(); return true end; return false end
if not exists(builder) then R.ShowMessageBox("HARDPAN_Kit_Builder.lua must sit next to this script.", "HARDPAN", 0); return end

local files, i = {}, 0
while true do
  local f = R.EnumerateFiles(KITS, i)
  if not f then break end
  if f:lower():match("%.txt$") then files[#files + 1] = f end
  i = i + 1
end
table.sort(files)
if #files == 0 then R.ShowMessageBox("No kits in " .. KITS .. "\nRun make_kits.py first.", "HARDPAN", 0); return end

local ok = R.ShowMessageBox("Build " .. #files .. " songs from " .. KITS .. "?\nEach opens in its own tab and is saved to the projects folder.", "HARDPAN", 1)
if ok ~= 1 then return end

local report = {}
HP_BATCH = true
for _, f in ipairs(files) do
  HP_KITFILE, HP_RESULT = KITS .. f, nil
  local good, err = pcall(dofile, builder)
  report[#report + 1] = good and (HP_RESULT or f) or (f .. ": " .. tostring(err))
end
HP_BATCH, HP_KITFILE = nil, nil
R.ShowMessageBox(table.concat(report, "\n") .. "\n\nClick through the tabs and press SPACE to play.", "HARDPAN: built " .. #files .. " songs", 0)

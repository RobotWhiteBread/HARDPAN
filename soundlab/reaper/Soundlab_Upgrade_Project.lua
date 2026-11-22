-- Soundlab_Upgrade_Project.lua
-- Adds two library alternatives to every sample in a project as extra TAKES,
-- so you can audition new sounds without losing the originals.
-- The original stays the active take. Select an item and press T to cycle
-- takes (Shift+T goes back). The result is saved as <name>_upgrade.rpp;
-- your original project file is never overwritten.
-- Needs: run project_audit.py first (it writes projects\_upgrades\*.tsv).

local R = reaper
local SEP = package.config:sub(1, 1)
local HERE = (({ R.get_action_context() })[2] or ""):match("^(.*[\\/])") or ""
pcall(dofile, HERE .. "soundlab_paths.lua")           -- written by setup from soundlab.json
SOUNDLAB = SOUNDLAB or {}
local HOMEDIR = os.getenv("USERPROFILE") or os.getenv("HOME") or ""
local DIR = SOUNDLAB.projects or (R.GetResourcePath() .. SEP .. "soundlab_projects" .. SEP)
local UP = DIR .. "_upgrades" .. SEP

local function exists(p) local f = io.open(p, "rb"); if f then f:close(); return true end; return false end
local function stem(p) return (p:match("([^\\/]+)%.[Rr][Pp][Pp]$") or ""):gsub("_upgrade$", "") end

local function load_alts(name)
  local f = UP .. name .. ".tsv"
  if not exists(f) then return nil end
  local map = {}
  for line in io.lines(f) do
    local orig, _, alt, bpm, atype = line:match("^([^\t]*)\t([^\t]*)\t([^\t]*)\t([^\t]*)\t([^\t\r]*)")
    if orig and orig ~= "original" and exists(alt) then
      local k = orig:lower()
      map[k] = map[k] or {}
      table.insert(map[k], { path = alt, bpm = tonumber(bpm), type = atype })
    end
  end
  return map
end

local function upgrade_current()
  local _, fn = R.EnumProjects(-1, "")
  local name = stem(fn or "")
  if name == "" then return nil, "Save the project first." end
  local map = load_alts(name)
  if not map then return nil, "No alternatives for " .. name .. ". Run project_audit.py first." end
  local tempo = R.Master_GetTempo()
  local done, items = 0, 0
  R.Undo_BeginBlock()
  for i = 0, R.CountMediaItems(0) - 1 do
    local it = R.GetMediaItem(0, i)
    local tk = R.GetActiveTake(it)
    if tk and R.CountTakes(it) == 1 and not R.TakeIsMIDI(tk) then
      local src = R.GetMediaItemTake_Source(tk)
      local file = R.GetMediaSourceFileName(src, "")
      local alts = map[(file or ""):lower()]
      if alts then
        items = items + 1
        local rate = R.GetMediaItemTakeInfo_Value(tk, "D_PLAYRATE")
        local pp = R.GetMediaItemTakeInfo_Value(tk, "B_PPITCH")
        local vol = R.GetMediaItemTakeInfo_Value(tk, "D_VOL")
        for n, a in ipairs(alts) do
          local t2 = R.AddTakeToMediaItem(it)
          R.SetMediaItemTake_Source(t2, R.PCM_Source_CreateFromFile(a.path))
          R.GetSetMediaItemTakeInfo_String(t2, "P_NAME", "ALT " .. n .. ": " .. a.path:match("([^\\/]+)$"), true)
          local r2, p2 = rate, pp
          if a.type == "loop" and a.bpm and a.bpm > 0 then
            local fit = tempo / a.bpm
            if fit < 0.5 then fit = fit * 2 elseif fit > 2 then fit = fit / 2 end
            r2, p2 = fit, 1                       -- stretch the new loop to the song tempo, keep its pitch
          end
          R.SetMediaItemTakeInfo_Value(t2, "D_PLAYRATE", r2)
          R.SetMediaItemTakeInfo_Value(t2, "B_PPITCH", p2)
          R.SetMediaItemTakeInfo_Value(t2, "D_VOL", vol)
          done = done + 1
        end
        R.SetActiveTake(tk)                       -- original stays on
      end
    end
  end
  R.Undo_EndBlock("Soundlab: add library alternatives as takes", -1)
  R.UpdateArrange()
  local out = DIR .. name .. "_upgrade.rpp"
  R.Main_SaveProjectEx(0, out, 0)
  return string.format("%s: %d alternatives on %d items", name, done, items)
end

local choice = R.ShowMessageBox(
  "Add library alternatives as takes.\n\nYes = only the open project\nNo = every project in the projects folder (save your work first; projects open one by one in this tab)\n\nOriginals are never overwritten; each result saves as <name>_upgrade.rpp.",
  "Soundlab Upgrade", 3)
if choice == 2 then return end

local report = {}
if choice == 6 then
  local msg, err = upgrade_current()
  report[1] = msg or err
else
  local i = 0
  while true do
    local f = R.EnumerateFiles(DIR, i)
    if not f then break end
    if f:lower():match("%.rpp$") and not f:lower():match("_upgrade%.rpp$") and exists(UP .. stem(f) .. ".tsv")
       and not exists(DIR .. stem(f) .. "_upgrade.rpp") then
      R.Main_openProject("noprompt:" .. DIR .. f)
      local msg, err = upgrade_current()
      report[#report + 1] = msg or err
    end
    i = i + 1
  end
end
if #report > 12 then local n = #report; report = { n .. " projects upgraded. Open any *_upgrade.rpp to listen." } end
R.ShowMessageBox(table.concat(report, "\n") .. "\n\nSelect an item and press T to hear the next take, Shift+T to go back.\nAlternatives are named ALT 1 and ALT 2 on each item.", "Soundlab Upgrade", 0)

-- Project Supernova client engine: the Lua side of the native API (AGPL-3.0, see LICENSE).
-- `native` is a Python object; everything the game calls goes through these tables.

local native = ...

-- Functions the engine does not implement yet log their first call and return nil, so the
-- game keeps running; the list of them shows what is still missing.
local function api_table(path)
  return setmetatable({}, {__index = function(t, key)
    local f = function(...) return native.missing(path .. "." .. key, ...) end
    rawset(t, key, f)
    return f
  end})
end

ext = api_table("ext")
for _, sub in ipairs({"http", "video", "audio", "keyboard", "json", "socialShare", "helpshift", "webview",
                      "localpush", "GameCenter", "DB"}) do
  ext[sub] = api_table("ext." .. sub)
end

-- device and app
function ext.GetPlatform() return "Android" end
function ext.GetIOSOpenUdid() return native.udid() end
function ext.GetDeviceToken() return native.udid() end
function ext.GetAndroidId() return string.sub(native.udid(), 1, 16) end
function ext.GetIOSMacAddress() return "02:00:00:00:00:00" end
function ext.GetBundleIdentifier() return "com.tap4fun.ge" end
function ext.GetAppID() return "500099330" end
function ext.GetAppVersion() return "1.9.36" end
function ext.GetOSVersion() return "14" end
function ext.GetDeviceVersion() return "Supernova engine" end
function ext.GetDeviceFullName() return "Supernova engine" end
function ext.GetDeviceLanguage() return native.language() end
function ext.GetGameLocale() return native.language() end
function ext.DeviceToGameLocale(locale) return locale end
function ext.GetCountryCode() return "DE" end
function ext.GetTimeZone() return "UTC" end
function ext.GetChannel() return "supernova" end
function ext.GetGameName() return "Galaxy Empire" end
function ext.isAlipay() return false end
function ext.isAmazon() return false end
function ext.isLangFR() return false end
function ext.IsArm() return false end
function ext.getSysTime() return native.now() end
-- the game's screen coordinates: native text boxes are placed in them (GameTextEdit)
ext.SCREEN_WIDTH = 640
ext.SCREEN_HEIGHT = 1136
-- the screen's shape picks the layout (GameUtils:GetStageSize, the *_16x9 / *_4x3 screens):
-- the engine shows the 16:9 one, as on phones
function ext.is3x2() return false end
function ext.is4x3() return false end
function ext.is16x9() return true end
function ext.QueryAppConfig(key) return nil end
function ext._QueryAppConfig(key) return nil end
function ext.GetAndroidDeviceTotalMem() return 4096 end
function ext.GetAndroidDeviceAvailMem() return 2048 end
function ext.video.isMovieFinished() return true end
function ext.__debugLog(text) native.log(text) end
function ext.Logi(...) end
function ext.showAlert(title, text, ...) native.alert(tostring(title), tostring(text)) end

-- the game dumps debug tables with io.write; they go to the debug log
io.write = function(...)
  for i = 1, select("#", ...) do native.log(tostring(select(i, ...))) end
end

-- storage and files
function ext.SaveGameData(name, text) native.save(name, text) end
function ext.UpdateAutoUpdateFile(...) end
function ext.crc(text) return native.crc(text) end

-- encodings used by requests (ENC2 is the client's AES; the server decrypts it)
function ext.ENC1(text) return native.md5(text) end
function ext.ENC3(text) return text end
function ext.ENC4(text) return native.md5(text) end
-- ENC2(key, text): AES-256-CBC with the first 32 bytes of key, zero IV, base64 (the game
-- passes its key itself, see ext.getENC2 in AutoUpdateInit.lua)
function ext.ENC2(key, text) return native.enc2(key, text) end
function ext.DisableIdleTimer() end
function ext.keyboard.Copy(text) native.copy(text) end

-- JSON
function ext.json.parse(text) return native.json_parse(text) end
function ext.json.generate(value) return native.json_generate(value) end

-- HTTP: the game builds headers itself (ext.http.request in AutoUpdateInit.lua)
local requests = {}
local next_request = 0
function ext.http.requestBasic(t)
  next_request = next_request + 1
  local name = "request" .. next_request
  requests[name] = t.callback
  native.http(name, t[1], t.param or "", t.body or "", t.method or "get", t.mode or "normal",
              t.header or {}, t.localpath or "")
  return name
end
function ext.http.cancelRequest(name) requests[name] = nil end
function __sn_http_done(name, status, content, err)
  local callback = requests[name]
  requests[name] = nil
  if callback then callback(status, content, err) end
end

-- Flash objects: `ext.RegisteFlashClass(name, super)` makes _G[super][name] with :new()
-- The Flash objects record what the game asks of them (native.flash); a renderer replaces them.
__sn_flash_classes = {}
local function flash_class(class_name)
  local class = __sn_flash_classes[class_name]
  if class then return class end
  class = {}
  __sn_flash_classes[class_name] = class
  function class:new()
    local object = {__class = class, __name = class_name}
    return setmetatable(object, {__index = function(t, method)
      local f = function(self, ...) return native.flash(class_name, method, ...) end
      rawset(t, method, f)
      return f
    end})
  end
  return class
end
__sn_flash_class = flash_class

function ext.RegisteFlashClass(class_name, super_name)
  local class = flash_class(class_name)
  if super_name then
    rawset(_G, super_name, rawget(_G, super_name) or {})
    _G[super_name][class_name] = class
  else
    rawset(_G, class_name, class)
  end
  return class
end

-- Text input: the native text box the game opens over a Flash text field (TextEditInput_t,
-- see GameTextEdit.lua). The engine shows the keyboard and hands typed text to
-- __sn_text_input; the text shows in the screen's own field while it is typed.
local active_input
local UTF8_CHAR = "[%z\1-\127\194-\244][\128-\191]*"
local function utf8_chars(text)
  return string.gmatch(text, UTF8_CHAR)
end
local function utf8_len(text)
  local n = 0
  for _ in utf8_chars(text) do n = n + 1 end
  return n
end
local function show_input(input)
  if not (input.fx and input.field) then return end
  local text = input.text
  if input.input_type == "Password" then text = string.rep("*", utf8_len(text)) end
  -- a cursor while the box is open
  if active_input == input then text = text .. "|" end
  input.fx:SetEditText(input.field, text)
end
local function finish_input(input)
  if input.finished then return end
  input.finished = true
  if input.endEditing then input:endEditing() end
  input:Destroy()
  -- the screen hides its field while the native box is open; the game does not show it again
  if input.fx and input.field then input.fx:SetEditText(input.field) end
  ext.keyboard.onKeyboardHide()
end

function ext.RegisteTextInputClass(class_name)
  local class = {}
  local meta = {__index = class}
  function class:new() return setmetatable({text = "", max_length = 0}, meta) end
  function class:Init(class_type, text, x, y, w, h, _, _, font_size, font_color, input_type)
    self.text = tostring(text or "")
    self.input_type = input_type
    -- the screen object and field the game opens the box for (GameTextEdit:fscommand)
    local edit = rawget(_G, "GameTextEdit")
    if edit then self.fx, self.field = edit.m_currentRenderFx, edit.m_textfiled end
  end
  function class:SetMaxLength(n) self.max_length = tonumber(n) or 0 end
  function class:SetAlign() end
  function class:SetPos() end
  function class:Show()
    if active_input and active_input ~= self then finish_input(active_input) end
    active_input = self
    self.shown = false
    native.keyboard(true, self.input_type)
  end
  function class:Hide()
    if active_input == self then finish_input(self) end
  end
  function class:GetText() return self.text, utf8_len(self.text) end
  function class:SetText(text)
    self.text = tostring(text or "")
    show_input(self)
  end
  function class:Destroy()
    if active_input == self then
      active_input = nil
      native.keyboard(false)
    end
  end
  rawset(_G, class_name, class)
  return class
end

-- typed text from the engine: "\b" deletes, "\n" ends the input, as the done key does
function __sn_text_input(text)
  local input = active_input
  if not input then return end
  for ch in utf8_chars(text) do
    if ch == "\n" or ch == "\r" then
      finish_input(input)
      return
    end
    local length = utf8_len(input.text)
    if ch == "\b" then
      if length > 0 and input:changeCharacter("\b", length - 1) ~= false then
        input.text = string.gsub(input.text, UTF8_CHAR .. "$", "")
      end
    elseif ch ~= "\t" and (input.max_length <= 0 or length < input.max_length) then
      if input:changeCharacter(ch, length) ~= false then input.text = input.text .. ch end
    end
    if active_input ~= input then return end
  end
  show_input(input)
end

-- once a frame: the field shows the text from the first frame of the input on (the screen
-- hides it right after it opened the box)
function __sn_text_input_frame()
  local input = active_input
  if input and not input.shown then
    input.shown = true
    show_input(input)
  end
end

-- Native classes the game extends with its own callbacks (StoreObject: in-app purchases).
-- Their objects answer every native method with nil, like a store with no products.
local function native_class(name)
  local class = {}
  function class:new()
    return setmetatable({}, {__index = function(t, key)
      local own = class[key]
      if own ~= nil then return own end
      return function(...) return native.missing(name .. ":" .. key) end
    end})
  end
  return class
end
StoreObject = native_class("StoreObject")

-- the game text loader: GetGameText("LC_MENU_X") reads key X of data2/text/menu.<language>
LuaLoader = {}
function LuaLoader:new()
  local loader = {}
  function loader:LoadGameText(locale, tables)
    local ok = true
    for _, name in ipairs(tables) do ok = native.load_text(locale, name) and ok end
    return ok
  end
  function loader:GetGameText(key) return native.text(key) end
  return loader
end

-- scripts: require("GameUtils.tfl"), loadfile("data1/X.tfl"), saved files ("ACCOUNT.tfl")
table.insert(package.loaders, 2, function(name)
  local chunk = native.script(name)
  if chunk == nil then return "\n\tno script " .. name end
  return assert(loadstring(chunk, name))
end)
local lua_loadfile = loadfile
function loadfile(name)
  local text = native.script(name) or native.load(name)
  if text == nil then return nil, "cannot open " .. tostring(name) end
  return loadstring(text, name)
end

-- report globals the native engine would have provided; Flash classes ("X_t") are made on use
setmetatable(_G, {__index = function(_, key)
  if type(key) == "string" and string.sub(key, -2) == "_t" then
    local class = flash_class(key)
    rawset(_G, key, class)
    return class
  end
  native.missing_global(key)
  return nil
end})

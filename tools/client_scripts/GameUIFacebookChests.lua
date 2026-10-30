-- Project Supernova: the chest screen without Facebook (AGPL-3.0, see LICENSE).
--
-- The original screen needs the Facebook SDK for login, friends and pictures. This version keeps
-- the screen's Flash file (fb_chests.tfs) and plays the chest with alliance members instead:
-- they help each other fill their chests. It also puts the "gift" entry back into the menu at
-- the bottom of the city screen, which the client had removed. The server side is
-- server/supernova/chests.py; the patcher puts this file into the game (client_tweaks.py).

local GameUIFacebookChests = LuaObjectManager:GetLuaObject("GameUIFacebookChests")
local GameCommonBG = LuaObjectManager:GetLuaObject("GameCommonBG")
local GameCommonInterface = LuaObjectManager:GetLuaObject("GameCommonInterface")
local GameUIGlobalScreen = LuaObjectManager:GetLuaObject("GameUIGlobalScreen")
local GameUIBuildingInfo = LuaObjectManager:GetLuaObject("GameUIBuildingInfo")
local GameUICityUIDown = LuaObjectManager:GetLuaObject("GameUICityUIDown")
local GameTip = LuaObjectManager:GetLuaObject("GameTip")

local AVATAR = "Buff_AffairsOfficer_people.png"
local HELP_TEXT = "Your chest fills by itself, one point every 6 hours, and faster when alliance members help: "
  .. "ask for help, and every member can help you once a day. Tap a member with a mark to help them.\n\n"
  .. "Open a full chest for rewards; the higher its level, the better. Opening gives chest experience "
  .. "and raises the level.\n\n"
  .. "The lower bar fills when you help others (+1) and when somebody joins with your invitation code "
  .. "(+3). When it is full, your next chest also gives the extra rewards shown."

GameUIFacebookChests.chestUserInfo = nil
GameUIFacebookChests.friends = {}

-- the bottom menu: show the chest entry again, before "rank"
local originalMenuInit = GameUICityUIDown.Init
function GameUICityUIDown:Init(...)
  local names = self.listsName or {}
  local has = false
  for _, name in ipairs(names) do
    if name == "gift" then
      has = true
    end
  end
  if not has then
    local at = #names + 1
    for i, name in ipairs(names) do
      if name == "rank" then
        at = i
      end
    end
    table.insert(names, at, "gift")
    if self.listsLocalization then
      table.insert(self.listsLocalization, at, "LC_MENU_Gift")
    end
  end
  return originalMenuInit(self, ...)
end

-- the bar's icons go to the frame named after the entry; the strip of pressed icons has no
-- "gift" frame (its frame 11, the chest, is mislabelled "fleetup"), so a pressed chest showed
-- whatever icon the slot had before: both strips have the chest at frame 11
local originalCallAS = GameUICityUIDown.CallAS
function GameUICityUIDown:CallAS(target, name, ...)
  if name == "setItemText" then
    local args = {...}
    if type(args[2]) == "string" then
      args[2] = string.gsub("\001" .. args[2], "\001gift\001", "\00111\001"):sub(2)
    end
    return originalCallAS(self, target, name, unpack(args))
  end
  return originalCallAS(self, target, name, ...)
end

function GameUIFacebookChests:OnAddToGameState()
  if not self:GetFlashObject() then
    self:LoadFlashObject()
  end
  self:Init()
end

function GameUIFacebookChests:Init()
  GameCommonInterface:MoveIn(self, GameCommonInterface.TYPE_ONE_TITLE, GameCommonInterface.LEFT_BUTTON_CLOSE, GameLoader:GetGameText("LC_MENU_Gift"))
  self:CallAS(nil, "showNotOAuth")
  self:Request("chest/enter", nil, function(content)
    self:CallAS(nil, "showOAuthed")
    self:ShowContent(content)
  end)
end

function GameUIFacebookChests:Request(url, params, onSuccess)
  GameUtils:SendMsg(url, params, self, function(content)
    if content then
      onSuccess(content)
    end
  end, nil, true, false, "post")
end

function GameUIFacebookChests:ShowContent(content)
  self.chestUserInfo = content.chest_user_info
  self.inviteCode = content.invite_code
  self.friends = {}
  for _, friend in ipairs(content.friends or {}) do
    self.friends[tostring(friend.id)] = friend
  end
  self:RefreshText()
  self:RefreshExtraGifts()
  self:CallAS(nil, "clearList")
  self:CallAS(nil, "initList")
  for _, friend in ipairs(content.friends or {}) do
    self:CallAS(nil, "addList", tostring(friend.id))
  end
end

function GameUIFacebookChests:RefreshText()
  local info = self.chestUserInfo
  local text = tostring(info.chest_level) .. "|||" .. tostring(info.chest_exp) .. "/" .. tostring(info.help_config.level_up_exp)
    .. "|||" .. tostring(info.progress) .. "|||" .. tostring(info.help_config.need_progress)
    .. "|||" .. tostring(info.extra_progress) .. "|||" .. tostring(info.extra_config.need_progress)
  self:CallAS(nil, "updateTextInfo", text)
end

function GameUIFacebookChests:SetTexture(textureName, image)
  local flash = self:GetFlashObject()
  if flash then
    local w, h = flash:ReplaceTexture(textureName, image)
    if w == 0 and h == 0 then
      flash:ReplaceTexture(textureName, "Item_default.png")
    end
  end
end

function GameUIFacebookChests:RefreshExtraGifts()
  local gifts = self.chestUserInfo.extra_config.client_gift or {}
  if #gifts == 0 then
    self:CallAS(nil, "setExtraListHiden")
    return
  end
  for i, gift in ipairs(gifts) do
    local image = string.match(gift[4], ".+/([^/]*%w+)$") or gift[4]
    self:SetTexture("NA_item_fix_" .. tostring(i) .. ".png", image)
  end
end

function GameUIFacebookChests:IsAsking(id)
  for _, asking in ipairs(self.chestUserInfo.requests or {}) do
    if tostring(asking) == tostring(id) then
      return true
    end
  end
  return false
end

function GameUIFacebookChests:RefreshRequests()
  for id, _ in pairs(self.friends) do
    self:CallAS(nil, "updateInviteStatue", id, self:IsAsking(id))
  end
end

function GameUIFacebookChests:OnFSCommand(cmd, arg)
  if cmd == "FadeOut" then
    GameUIBuildingInfo:init(GameUIBuildingInfo.TYPE_OEM_DATA, GameUIBuildingInfo.From.FB_CHESTS, self.infoData)
  elseif cmd == "MoveOut" then
    GameStateManager:GetCurrentGameState():EraseObject(GameUIFacebookChests)
    GameStateManager:GetCurrentGameState():EraseObject(GameCommonBG)
    GameStateManager:GetCurrentGameState():EraseObject(GameCommonInterface)
  elseif cmd == "btnFbOAuth" then
    self:Init()
  elseif cmd == "btnFbGiftInfo" then
    self.infoData = {title = GameLoader:GetGameText("LC_MENU_Gift"), info = HELP_TEXT}
    GameCommonInterface:FadeOut()
  elseif cmd == "btnFbOpenGift" then
    self:Request("chest/open_chest", nil, function(content)
      GameUIGlobalScreen:ShowMessageBox(GameUIGlobalScreen.MessageBoxType_OK, "", content.result)
      self:ShowContent(content)
    end)
  elseif cmd == "btnFbHelp" then
    self:Request("chest/ask_for_help", nil, function(content)
      GameUIGlobalScreen:ShowMessageBox(GameUIGlobalScreen.MessageBoxType_OK, "", "Your alliance members can now help you with your chest.")
      self:ShowContent(content)
    end)
  elseif cmd == "btnFbInvite" then
    if self.inviteCode then
      ext.keyboard.Copy(self.inviteCode)
      GameUIGlobalScreen:ShowMessageBox(GameUIGlobalScreen.MessageBoxType_OK, "", "Your invitation code " .. self.inviteCode
        .. " was copied. New players enter it under Settings > Redeem invitation code; each one adds 3 points to this bar.")
    end
  elseif cmd == "btnHelpFriend" then
    if self:IsAsking(arg) then
      self:Request("chest/help_other", {target_id = arg}, function(content)
        GameTip:Show(string.format(GameLoader:GetGameText("LC_MENU_FBHelpedHint"), content.helped or ""))
        self:ShowContent(content)
      end)
    elseif self.friends[tostring(arg)] then
      GameTip:Show(self.friends[tostring(arg)].name)
    end
  elseif cmd == "needUpdateItemMC" then
    local param = LuaUtils:string_split(arg, "\001")
    self:SetTexture("NA_item_" .. tostring(param[2]) .. ".png", AVATAR)
    self:RefreshRequests()
  end
end

function GameUIFacebookChests:Update(dt)
  local flash = self:GetFlashObject()
  if flash then
    flash:Update(dt)
    self:CallAS(nil, "OnUpdate")
  end
end

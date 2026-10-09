-- Run with Lua: lua tests/addon_camera.lua addon/ThumbTalk/ThumbTalk.lua
local frames, now, combat = {}, 0, false
SlashCmdList = {}
GetTime = function() return now end
CreateFrame = function()
    local frame = {shown = true, scripts = {}, propagate = true}
    function frame:SetFrameStrata(value) self.strata = value end
    function frame:EnableKeyboard(value) self.keyboard = value end
    function frame:EnableGamePadStick(value)
        assert(not combat, "Gamepad configuration changed during combat")
        self.sticks = value
    end
    function frame:SetPropagateKeyboardInput(value)
        assert(not combat, "Propagation changed during combat")
        self.propagate = value
    end
    function frame:SetScript(name, callback) self.scripts[name] = callback end
    function frame:RegisterEvent() end
    function frame:Show() self.shown = true end
    function frame:Hide() self.shown = false end
    function frame:SetAutoFocus() end
    function frame:SetMultiLine() end
    function frame:SetSize() end
    function frame:SetPoint() end
    function frame:SetFontObject() end
    function frame:SetMaxLetters() end
    function frame:EnableMouse() end
    function frame:SetAlpha() end
    function frame:ClearFocus() end
    function frame:SetText() end
    function frame:SetAllPoints() end
    function frame:GetFrameLevel() return 1 end
    function frame:SetFrameLevel() end
    function frame:CreateTexture()
        return {SetAllPoints=function() end,SetColorTexture=function() end}
    end
    function frame:CreateFontString()
        return {SetPoint=function() end,SetText=function() end}
    end
    table.insert(frames, frame)
    return frame
end

dofile(assert(arg[1]))
local camera, observer, blocker = frames[1], frames[2], frames[3]
local function key(value) camera.scripts.OnKeyDown(camera, value) end
local function tick()
    if camera.scripts.OnUpdate then camera.scripts.OnUpdate(camera, 0.016) end
end
local function stick(name)
    -- Event recipients are selected before the event is dispatched.
    local receiver = blocker.shown and blocker or (observer.shown and observer)
    if receiver then
        receiver.scripts.OnGamePadStick(receiver, name, 1, 1, 1)
        return receiver.propagate
    end
    return true
end
local function choose()
    tick()
    stick("Left")
    stick("Right")
    assert(stick("Movement"), "Movement must remain available")
    assert(not stick("Camera"), "Camera was not intercepted")
    assert(not blocker.shown)
end
assert(stick("Camera"), "Idle camera was blocked")
combat = true
key("F11")
choose()
now = 0.7
key("F11")
now = 1.3
choose()
key("F12")
assert(stick("Camera"), "Explicit close did not restore camera")
key("F11")
tick()
stick("Right")
assert(not stick("Camera"), "Right-only input was not blocked")
now = 3
tick()
assert(stick("Camera"), "Lost heartbeat did not restore camera")
assert(camera.scripts.OnUpdate == nil, "Idle addon still polls")
key("F13")
choose()
key("F14")
assert(stick("Camera"), "Windows/macOS close did not restore camera")
key("F11")
camera.scripts.OnEvent(camera, "PLAYER_LEAVING_WORLD")
assert(stick("Camera"), "Leaving world did not release camera")
print("Addon camera interception, movement, combat-safe configuration, release and timeout passed.")

-- Channel numbers can differ between characters and must be resolved each time.
local joined, sent = {}, {}
GetChannelList = function() return (unpack or table.unpack)(joined) end
GetZoneText = function() return "Elwynn Forest" end
SendChatMessage = function(text, kind, language, target)
    table.insert(sent, {text = text, kind = kind, target = target})
end
joined = {8, "General - Elwynn Forest", false, 3, "Trade - City", false, 9, "LookingForGroup", false}
SlashCmdList.THUMBTALKSEND("general Hello everyone")
assert(#sent == 1 and sent[1].target == "8" and sent[1].text == "Hello everyone")
SlashCmdList.THUMBTALKSEND("trade Selling herbs")
assert(#sent == 2 and sent[2].target == "3")
SlashCmdList.THUMBTALKSEND("lfg Looking for a healer")
assert(#sent == 3 and sent[3].target == "9")
joined = {6, "Trade - Services", false, 5, "General", true}
SlashCmdList.THUMBTALKSEND("trade Must not reach services")
SlashCmdList.THUMBTALKSEND("general Must not reach disabled channel")
assert(#sent == 3, "Unavailable channels fell back to a different destination")
joined = {7, "Trade", false}
SlashCmdList.THUMBTALKSEND("trade /logout")
SlashCmdList.THUMBTALKSEND("trade bad\ntext")
SlashCmdList.THUMBTALKSEND("other hi")
assert(#sent == 3, "Invalid message was sent")
C_ChatInfo = {GetChannelShortcut = function(id) return "Trade" end, SendChatMessage = SendChatMessage}
joined = {12, "localized channel and zone", false}
SlashCmdList.THUMBTALKSEND("trade New channel number")
assert(#sent == 4 and sent[4].target == "12")
print("Public channel lookup, changed numbers, missing channels and message validation passed.")

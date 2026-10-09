-- ThumbTalk: public-channel routing and a temporary native gamepad camera lock.
local ADDON_BUILD = "1"
local diagnosticPhase = "idle"
local lastBlockedAction
local cameraStatus = "No radial signal received yet."
SLASH_THUMBTALK1 = "/tt"
SLASH_THUMBTALK2 = "/thumbtalk"
SlashCmdList.THUMBTALK = function()
    print("|cff70e2caThumbTalk|r: Open the ThumbTalk app to change your microphone, language, or buttons.")
    print("Hold Talk to record; release to prepare a draft; tap Talk to send.")
    print("Hold Menu for channels, languages, speech models, and sending mode. Tap Menu to cancel a draft.")
    print("ThumbTalk camera: " .. cameraStatus)
end

-- Public channels are resolved at confirmation time, never by a hard-coded /1 or /2.
-- The companion types this command into the ordinary chat edit box. The user
-- confirms it with Talk/Enter; opening the wheel never sends or joins a channel.
local publicChannels = {
    general = {"General", GENERAL},
    trade = {"Trade", TRADE},
    lfg = {"LookingForGroup", LOOKING_FOR_GROUP},
}
local function channelKey(name)
    return type(name) == "string" and name:lower():gsub("%s+", "") or ""
end
local function matchesChannel(name, aliases)
    for _, alias in ipairs(aliases) do
        if channelKey(name) == channelKey(alias) then return true end
    end
    return false
end
local function joinedPublicChannel(aliases)
    if not GetChannelList then return nil end
    local joined = {GetChannelList()}
    for index = 1, #joined, 3 do
        local id, name, disabled = joined[index], joined[index + 1], joined[index + 2]
        if type(id) == "number" and id > 0 and type(name) == "string" and not disabled then
            if matchesChannel(name, aliases) then return id end
            -- Newer clients expose the localized shortcut without a zone suffix.
            if C_ChatInfo and C_ChatInfo.GetChannelShortcut then
                local ok, shortcut = pcall(C_ChatInfo.GetChannelShortcut, id)
                if ok and matchesChannel(shortcut, aliases) then return id end
            end
            -- Older clients may return "General - <current zone>" or "Trade - City".
            -- Only remove a known zone, never arbitrary text such as "Services".
            local zones = {"City"}
            if GetZoneText then table.insert(zones, GetZoneText()) end
            if GetRealZoneText then table.insert(zones, GetRealZoneText()) end
            for _, zone in ipairs(zones) do
                local suffix = " - " .. zone
                if zone ~= "" and name:sub(-#suffix) == suffix and
                    matchesChannel(name:sub(1, -#suffix - 1), aliases) then return id end
            end
        end
    end
end
SLASH_THUMBTALKSEND1 = "/tts"
SlashCmdList.THUMBTALKSEND = function(command)
    local destination, message = command:match("^(%a+)%s+(.+)$")
    local aliases = destination and publicChannels[destination:lower()]
    if not aliases or not message or message:find("^%s*/") or message:find("[%c]") or #message > 255 then
        print("|cff70e2caThumbTalk|r: Not sent. Choose a channel and record your message again.")
        return
    end
    local id = joinedPublicChannel(aliases)
    if not id then
        print("|cff70e2caThumbTalk|r: Not sent. Join " .. aliases[1] .. " in WoW's Chat Channels first. It may not be available in this location or version.")
        return
    end
    local send = C_ChatInfo and C_ChatInfo.SendChatMessage or SendChatMessage
    if not send then
        print("|cff70e2caThumbTalk|r: Not sent. This WoW client does not expose channel chat.")
        return
    end
    local ok, reason = pcall(send, message, "CHANNEL", nil, tostring(id))
    if not ok then
        print("|cff70e2caThumbTalk|r: WoW could not send this message: " .. tostring(reason))
    end
end

-- F11/F12 renew/release a short lease; F13/F14 remain accepted for older apps. The app sends these only to a
-- verified foreground WoW window. No bindings or camera CVars are changed.
-- Native gamepad raw events precede their virtual Movement/Camera events.
-- Consume Camera only, leaving the movement stick available while choosing.
local camera = CreateFrame("Frame")
local observer = CreateFrame("Frame")
local blocker = CreateFrame("Frame")
if camera.EnableKeyboard and observer.EnableGamePadStick and blocker.SetPropagateKeyboardInput then
    local expires, sawLeft, waitingRight = 0, false, false
    local function releaseCamera()
        expires, sawLeft, waitingRight = 0, false, false
        blocker:Hide()
        observer:Hide()
        camera:SetScript("OnUpdate", nil)
    end

    observer:SetFrameStrata("BACKGROUND")
    observer:EnableGamePadStick(true)
    observer:SetPropagateKeyboardInput(true)
    observer:SetScript("OnGamePadStick", function(_, stick)
        if stick == "Left" then
            sawLeft = true
        elseif stick == "Right" then
            waitingRight = true
            if not sawLeft then blocker:Show() end
        elseif stick == "Movement" and waitingRight then
            -- Allow Movement to propagate before consuming Camera.
            blocker:Show()
        end
    end)
    blocker:SetFrameStrata("TOOLTIP")
    blocker:EnableGamePadStick(true)
    blocker:SetPropagateKeyboardInput(false)
    blocker:SetScript("OnGamePadStick", function(_, stick)
        if stick == "Camera" then blocker:Hide() end
    end)

    camera:EnableKeyboard(true)
    camera:SetPropagateKeyboardInput(true)
    camera:SetScript("OnKeyDown", function(_, key)
        if key == "F12" or key == "F14" then
            cameraStatus = "Release signal received (" .. key .. ")."
            releaseCamera()
        elseif key == "F11" or key == "F13" then
            cameraStatus = "Radial signal received (" .. key .. ")."
            expires = GetTime() + 1.2
            observer:Show()
            camera:SetScript("OnUpdate", function()
                blocker:Hide()
                sawLeft, waitingRight = false, false
                if GetTime() >= expires then releaseCamera() end
            end)
        end
    end)
    camera:RegisterEvent("PLAYER_LEAVING_WORLD")
    camera:RegisterEvent("PLAYER_ENTERING_WORLD")
    camera:SetScript("OnEvent", releaseCamera)
    releaseCamera()
end

-- Review is a LOCAL chat-history message, never a focused edit box. The
-- Linux companion uses the standalone inbox below; older companions retain /ttr.
-- No Blizzard editbox text/focus methods or native chat handlers are invoked.
-- Bounded, read-only chat state for /tt. Never inspect or retain editor text.
-- No hooks, protected calls, network sends, or focus changes are installed.
local chatSnapshots = {}
local lastReviewEditor
local snapshotGeneration = 0
local function safeState(callback)
    local ok, value = pcall(callback)
    if not ok or (issecretvalue and issecretvalue(value)) then return "unknown" end
    if type(value) == "boolean" then return value and "yes" or "no" end
    if type(value) == "string" and (value == "classic" or value == "im") then return value end
    return "unknown"
end
local function snapshotChat(phase, editor)
    if not editor then
        local ok, active = pcall(function()
            if ChatEdit_GetActiveWindow then return ChatEdit_GetActiveWindow() end
            return ChatFrameUtil.GetActiveWindow()
        end)
        if ok and not (issecretvalue and issecretvalue(active)) then editor = active end
    end
    local style = safeState(function() return GetCVar("chatStyle") end)
    local gamepad = safeState(function() return InputUtil.IsGamepadUIEnabled() end)
    local focused = safeState(function() return editor:HasFocus() end)
    local shown = safeState(function() return editor:IsShown() end)
    local autocomplete = safeState(function() return AutoCompleteBox:IsShown() end)
    local row = string.format("%.1f %s: style=%s gamepad=%s editorShown=%s focus=%s autocomplete=%s",
        GetTime(), phase, style, gamepad, shown, focused, autocomplete)
    table.insert(chatSnapshots, row)
    if #chatSnapshots > 8 then table.remove(chatSnapshots, 1) end
end
local function snapshotAfterInput(phase, editor)
    snapshotChat(phase, editor)
    if C_Timer and C_Timer.After then
        local generation = snapshotGeneration
        C_Timer.After(0.75, function()
            if generation == snapshotGeneration then snapshotChat(phase .. " +0.75s", editor) end
        end)
    end
end
local reviewStatus = "No local review yet."
local pendingReview
local reviewLines = {}
local function removeReviewLines()
    for _, entry in ipairs(reviewLines) do
        if entry.frame.RemoveMessagesByPredicate then
            pcall(entry.frame.RemoveMessagesByPredicate, entry.frame, function(text)
                return type(text) == "string" and not (issecretvalue and issecretvalue(text)) and text == entry.text
            end)
        end
    end
    reviewLines = {}
end
local function finishReview(status, quiet)
    removeReviewLines()
    if pendingReview and not quiet then
        print("|cff70e2caThumbTalk|r: " .. status)
    end
    pendingReview = nil
    reviewStatus = status
end
local function showLocalReview(payload, editBox)
    removeReviewLines()
    pendingReview = {ready=true}
    -- Escape WoW markup. The full destination is shown so whisper/channel
    -- review cannot be mistaken for Say. No transcript is broadcast here.
    local line = "|cff70e2caThumbTalk:|r " .. payload:gsub("|", "||")
        .. " |cffaaaaaa(Talk to accept · Menu to discard)|r"
    -- The slash dispatcher supplies its editor. Read its owning chat frame;
    -- never focus, show or modify the editor. DEFAULT_CHAT_FRAME can be a
    -- hidden tab, so prefer the chat window where this command was entered.
    local frame = (editBox and editBox.chatFrame) or SELECTED_CHAT_FRAME
        or DEFAULT_CHAT_FRAME or ChatFrame1
    if frame and frame.AddMessage then
        frame:AddMessage(line)
        reviewLines = {{frame=frame, text=line}}
    else
        print(line)
    end
    reviewStatus = "Local review shown; nothing sent."
    if editBox then snapshotAfterInput("review complete", editBox) end
end
SLASH_THUMBTALKREVIEW1 = "/ttr"
SlashCmdList.THUMBTALKREVIEW = function(command, editBox)
    local id, part, total, text = command:match("^(%x+)%s+(%d+)%s+(%d+)%s+(.+)$")
    part, total = tonumber(part), tonumber(total)
    if not id or #id ~= 8 or not part or not total or total < 1 or total > 4 or
        part < 1 or part > total or #text > 200 or #text % 2 ~= 0 or text:find("[^%x]") then return end
    if part == 1 then
        lastReviewEditor = editBox
        snapshotChat("review input", editBox)
        removeReviewLines()
        pendingReview = {id=id, total=total, parts={}, nextPart=1, time=GetTime()}
    end
    local pending = pendingReview
    if not pending or pending.id ~= id or pending.total ~= total or
        pending.nextPart ~= part or GetTime() - pending.time > 30 then return end
    pending.parts[part] = text
    pending.nextPart = part + 1
    if part ~= total then return end
    local payload = table.concat(pending.parts):gsub("%x%x", function(pair) return string.char(tonumber(pair, 16)) end)
    if #payload > 255 or payload:sub(1,1) ~= "/" or payload:find("[%c]") then pendingReview=nil; return end
    showLocalReview(payload, editBox)
end
-- One-way review receiver. Only this addon-owned EditBox takes temporary focus.
-- Paste is decoded on a later frame, then displayed locally. There is no copy
-- request/reply, slash dispatcher, Enter, or call to Blizzard's chat editor.
local inbox = CreateFrame("EditBox", "ThumbTalkReviewInbox", UIParent)
inbox:SetAutoFocus(false)
inbox:SetMultiLine(false)
inbox:SetSize(400, 32)
inbox:SetPoint("BOTTOMLEFT", DEFAULT_CHAT_FRAME or UIParent, "TOPLEFT", 0, 6)
inbox:SetFrameStrata("TOOLTIP")
inbox:SetFontObject(ChatFontNormal or GameFontNormal)
inbox:SetMaxLetters(600)
if inbox.SetMaxBytes then inbox:SetMaxBytes(600) end
inbox:EnableMouse(false)
inbox:SetAlpha(1)
inbox:Hide()

local inboxCover = CreateFrame("Frame", "ThumbTalkReviewInboxCover", inbox)
inboxCover:SetAllPoints(inbox)
inboxCover:SetFrameLevel(inbox:GetFrameLevel() + 1)
inboxCover:EnableMouse(false)
local inboxBackground = inboxCover:CreateTexture(nil, "BACKGROUND")
inboxBackground:SetAllPoints(inboxCover)
inboxBackground:SetColorTexture(0.04, 0.07, 0.09, 1)
local inboxLabel = inboxCover:CreateFontString(nil, "OVERLAY", "GameFontNormal")
inboxLabel:SetPoint("CENTER")
inboxLabel:SetText("ThumbTalk: Preparing review…")

local capture, changing
local seenReviews = {}
local function stopInbox(reason)
    local stopped = capture
    capture = nil
    changing = true
    inbox:ClearFocus()
    inbox:Hide()
    inbox:SetText("")
    changing = false
    if stopped and reason then
        reviewStatus = reason .. ". Nothing sent."
        print("|cff70e2caThumbTalk " .. ADDON_BUILD .. "|r: " .. reviewStatus)
    end
end

local function checksum(text)
    local a, b = 1, 0
    for i = 1, #text do
        a = (a + string.byte(text, i)) % 65521
        b = (b + a) % 65521
    end
    return string.format("%08x", b * 65536 + a)
end

local function beginInbox()
    if capture then return end
    local ok, focus = pcall(GetCurrentKeyBoardFocus)
    if not ok or (issecretvalue and issecretvalue(focus)) or focus then
        reviewStatus = "Review inbox refused: close the current typing box first."
        return
    end
    capture = {expires=GetTime() + 3}
    changing = true
    inbox:SetText("")
    inbox:Show()
    inbox:SetFocus()
    changing = false
    reviewStatus = "Receiving local review; nothing sent."
end

inbox:SetScript("OnTextChanged", function(_, userInput)
    if not changing and userInput and capture then capture.dirty = true end
end)
inbox:SetScript("OnEditFocusLost", function()
    if not changing then stopInbox("Review inbox lost focus") end
end)
inbox:SetScript("OnEscapePressed", function() stopInbox("Review canceled") end)
inbox:SetScript("OnEnterPressed", function() stopInbox("Review canceled") end)
inbox:SetScript("OnUpdate", function()
    if not capture then return end
    if GetTime() >= capture.expires then
        stopInbox("Review text was not received")
        return
    end
    if capture.dirty then
        capture.dirty = nil
        capture.payload = nil
        local text = inbox:GetText()
        if type(text) ~= "string" or (issecretvalue and issecretvalue(text)) or #text > 600 then
            stopInbox("Invalid review input"); return
        end
        if text:sub(-1) ~= ";" then return end
        local id, encoded, digest = text:match("^TTREVIEW2:([0-9a-f]+):([0-9a-f]+):([0-9a-f]+);$")
        if not id or #id ~= 8 or #digest ~= 8 or #encoded > 510 or #encoded % 2 ~= 0 then
            stopInbox("Invalid review packet"); return
        end
        local payload = encoded:gsub("%x%x", function(pair) return string.char(tonumber(pair, 16)) end)
        if payload:sub(1,1) ~= "/" or payload:find("[%c]") or checksum(payload) ~= digest then
            stopInbox("Invalid review checksum"); return
        end
        for _, previous in ipairs(seenReviews) do
            if previous == id then stopInbox(); return end
        end
        capture.id, capture.payload = id, payload
    end
    -- Avoid releasing focus in the native paste callback or while Ctrl+V is held.
    if capture.payload and not IsControlKeyDown() and not IsShiftKeyDown() and not IsAltKeyDown() then
        local id, payload = capture.id, capture.payload
        stopInbox()
        table.insert(seenReviews, id)
        if #seenReviews > 8 then table.remove(seenReviews, 1) end
        showLocalReview(payload)
    end
end)

local function inboxKey(key)
    if not (IsControlKeyDown() and IsShiftKeyDown()) or IsAltKeyDown() then return end
    if key == "F8" then
        beginInbox()
    elseif key == "F6" then
        stopInbox("Review canceled")
        removeReviewLines()
        pendingReview = nil
    end
end
inbox:SetScript("OnKeyDown", function(_, key)
    if IsControlKeyDown() and IsShiftKeyDown() and not IsAltKeyDown()
        and (key == "F9" or key == "F10") then
        stopInbox()
        finishReview(key == "F9" and "Send requested. Check chat for delivery."
            or "Review discarded. Nothing sent.", key == "F9")
    else
        inboxKey(key)
    end
end)

local reviewKeys = CreateFrame("Frame")
if reviewKeys.EnableKeyboard then
    reviewKeys:SetFrameStrata("TOOLTIP")
    reviewKeys:EnableKeyboard(true)
    reviewKeys:SetPropagateKeyboardInput(true)
    reviewKeys:SetScript("OnKeyDown", function(_, key)
        if not (IsControlKeyDown() and IsShiftKeyDown()) or IsAltKeyDown() then return end
        inboxKey(key)
        if key == "F9" then
            if lastReviewEditor then snapshotAfterInput("send cleanup", lastReviewEditor) end
            finishReview("Send requested. Check chat for delivery.", true)
        elseif key == "F10" then
            if lastReviewEditor then snapshotAfterInput("discard", lastReviewEditor) end
            finishReview("Review discarded. Nothing sent.")
        end
    end)
    reviewKeys:RegisterEvent("PLAYER_LEAVING_WORLD")
    reviewKeys:RegisterEvent("PLAYER_ENTERING_WORLD")
    reviewKeys:SetScript("OnEvent", function()
        stopInbox()
        removeReviewLines()
        pendingReview = nil
        reviewStatus = "Review reset after a world change."
        lastReviewEditor = nil
        snapshotGeneration = snapshotGeneration + 1
        chatSnapshots = {}
    end)
end
-- WoW dispatches these events synchronously. Record the phase BEFORE each
-- chat operation above; this listener only reports, never touches protected UI.
-- Keep this frame after reviewKeys so existing input frames retain their order.
local diagnostics = CreateFrame("Frame")
diagnostics:RegisterEvent("ADDON_ACTION_BLOCKED")
diagnostics:RegisterEvent("ADDON_ACTION_FORBIDDEN")
local lastPrinted, lastPrintedAt
local function diagnosticName(value)
    if type(value) ~= "string" or (issecretvalue and issecretvalue(value)) then return "unknown" end
    return value:gsub("[%c|]", "?"):sub(1, 160)
end
diagnostics:SetScript("OnEvent", function(_, event, addonName, functionName)
    if event ~= "ADDON_ACTION_BLOCKED" and event ~= "ADDON_ACTION_FORBIDDEN" then return end
    if type(addonName) ~= "string" or (issecretvalue and issecretvalue(addonName)) then return end
    if addonName:lower() ~= "thumbtalk" then return end
    lastBlockedAction = "ThumbTalk diagnostic 30: " .. event .. " / " .. diagnosticName(functionName)
        .. " / " .. diagnosticPhase
    local now = GetTime()
    if lastBlockedAction ~= lastPrinted or not lastPrintedAt or now - lastPrintedAt >= 5 then
        print(lastBlockedAction)
        print("ThumbTalk: Please photograph the diagnostic line above. /tt shows it again.")
        lastPrinted, lastPrintedAt = lastBlockedAction, now
    end
end)
-- Ordinary review status stays opt-in; blocked actions are printed automatically.
local showHelp = SlashCmdList.THUMBTALK
SlashCmdList.THUMBTALK = function(...)
    showHelp(...)
    print("ThumbTalk addon " .. ADDON_BUILD .. " · " .. reviewStatus)
    print("ThumbTalk input snapshots (local; no message text):")
    for _, row in ipairs(chatSnapshots) do print(row) end
    if lastBlockedAction then print(lastBlockedAction) end
end

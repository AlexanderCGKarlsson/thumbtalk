-- Local-history review must never mutate protected chat editors or send text.
local frames,now,lines,printed = {},1,{},{}
SlashCmdList={}
GetTime=function() return now end
IsControlKeyDown=function() return true end
IsShiftKeyDown=IsControlKeyDown
IsAltKeyDown=function() return false end
local forbidden=function() error("Protected editor/network operation during local review") end
ChatFrameUtil=setmetatable({},{__index=forbidden})
ChatEdit_SendText=forbidden;ChatEdit_ActivateChat=forbidden
SendChatMessage=forbidden;C_ChatInfo={SendChatMessage=forbidden}
local editor=setmetatable({},{__index=forbidden})
ChatFrame1={editBox=editor}
function ChatFrame1:AddMessage(text) table.insert(lines,text) end
function ChatFrame1:RemoveMessagesByPredicate(predicate)
    for i=#lines,1,-1 do if predicate(lines[i]) then table.remove(lines,i) end end
end
DEFAULT_CHAT_FRAME=ChatFrame1
print=function(text) table.insert(printed,text) end
CreateFrame=function()
    local frame={scripts={},events={}}
    function frame:SetFrameStrata() end
    function frame:EnableKeyboard() end
    function frame:SetPropagateKeyboardInput() end
    function frame:SetScript(k,v) self.scripts[k]=v end
    function frame:RegisterEvent(e) self.events[e]=true end
    function frame:Show() end
    function frame:Hide() end
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
    table.insert(frames,frame);return frame
end
assert(loadfile(arg[1]))()
local review=SlashCmdList.THUMBTALKREVIEW
local keys=frames[6]
local function key(k) keys.scripts.OnKeyDown(keys,k) end
local function hex(text) return (text:gsub(".",function(c) return string.format("%02x",string.byte(c)) end)) end
local function stage(text) review("aabbccdd 1 1 "..hex(text)) end
lines={"another player's message"}
stage("/s hello |Hfake|hlink|h")
assert(#lines==2 and lines[2]:find("ThumbTalk:",1,true))
assert(lines[2]:find("Menu to discard",1,true) and not lines[2]:find("B/Menu",1,true))
assert(lines[2]:find("||Hfake||hlink||h",1,true),"Markup was not escaped")
key("F10")
assert(#lines==1 and lines[1]=="another player's message","Discard removed unrelated chat")
assert(printed[#printed]:find("Nothing sent",1,true))
stage("/p move left")
local beforeSend=#printed
key("F9")
assert(#lines==1)
assert(#printed==beforeSend,"Send added an unwanted chat-panel status line")
local count=#printed
key("F9");key("F10")
assert(#printed==count,"Duplicate finish notice")
local payload="/s "..string.rep("hello ø ",25)
local encoded=hex(payload)
local total=math.ceil(#encoded/200)
for i=1,total do
    review("11223344 "..i.." "..total.." "..encoded:sub((i-1)*200+1,i*200))
    if i<total then assert(#lines==1,"Incomplete review displayed") end
end
assert(lines[2]:find(payload,1,true),"UTF8/whitespace chunk round trip failed")
keys.scripts.OnEvent(keys,"PLAYER_LEAVING_WORLD")
assert(#lines==1)
for _,command in ipairs({"bad", "aabbccdd 0 1 aa", "aabbccdd 1 5 aa", "aabbccdd 1 1 zz", "aabbccdd 1 1 a", "aabbccdd 1 1 "..hex("/s bad\n/s bad")}) do
 review(command);assert(#lines==1)
end
review("aabbccdd 1 2 "..hex("/s first "))
review("aabbccdd 2 3 "..hex("bad"));assert(#lines==1)
now=40
review("aabbccdd 2 2 "..hex("late"));assert(#lines==1)
-- Reviews go to the originating editor's chat tab, never its protected methods.
local activeLines = {"existing guild message"}
local activeFrame = {}
function activeFrame:AddMessage(text) table.insert(activeLines, text) end
function activeFrame:RemoveMessagesByPredicate(predicate)
    for i=#activeLines,1,-1 do if predicate(activeLines[i]) then table.remove(activeLines,i) end end
end
local activeEditor = setmetatable({chatFrame=activeFrame}, {__index=forbidden})
review("aabbccdd 1 1 "..hex("/s active tab"), activeEditor)
assert(#activeLines==2 and #lines==1, "Review went to an inactive default tab")
key("F9")
assert(#activeLines==1 and activeLines[1]=="existing guild message")
SELECTED_CHAT_FRAME=activeFrame
stage("/g selected tab")
assert(#activeLines==2 and #lines==1)
key("F10")
assert(#activeLines==1)
SELECTED_CHAT_FRAME=nil
-- Diagnostic listener only reports; no editor access even on blocked actions.
local diagnostics=frames[7]
diagnostics.scripts.OnEvent(diagnostics,"ADDON_ACTION_BLOCKED","ThumbTalk","DangerousFunction")
assert(printed[#printed-1]:find("ThumbTalk diagnostic 30",1,true))
-- Read-only snapshots expose retained IM focus without reading transcript text.
local timers = {}
C_Timer = {After=function(delay, callback) assert(delay==0.75);table.insert(timers,callback) end}
GetCVar=function(name) assert(name=="chatStyle");return "im" end
InputUtil={IsGamepadUIEnabled=function() return true end}
AutoCompleteBox={IsShown=function() return true end}
local focused=true
local observed=setmetatable({
    chatFrame=activeFrame,
    HasFocus=function() return focused end,
    IsShown=function() return true end,
    GetText=forbidden,SetText=forbidden,ClearFocus=forbidden,
}, {__index=forbidden})
review("aabbccdd 1 1 "..hex("/s private diagnostic test"), observed)
assert(#timers==1)
timers[1]();timers={}
local mark=#printed
SlashCmdList.THUMBTALK()
local report=table.concat(printed,"\n",mark+1)
assert(report:find("style=im gamepad=yes editorShown=yes focus=yes autocomplete=yes",1,true))
assert(not report:find("private diagnostic test",1,true),"Diagnostics leaked the transcript")
focused=false
key("F9");timers[1]();timers={}
mark=#printed
SlashCmdList.THUMBTALK()
report=table.concat(printed,"\n",mark+1)
assert(report:find("send cleanup +0.75s",1,true) and report:find("focus=no",1,true))
-- Secret-valued and missing client APIs remain unknown; never stringify secrets.
issecretvalue=function(value) return value=="im" end
review("aabbccdd 1 1 "..hex("/s hidden"), observed)
mark=#printed
SlashCmdList.THUMBTALK()
report=table.concat(printed,"\n",mark+1)
assert(report:find("style=unknown",1,true))
keys.scripts.OnEvent(keys,"PLAYER_LEAVING_WORLD")
for _,callback in ipairs(timers) do callback() end
mark=#printed
SlashCmdList.THUMBTALK()
report=table.concat(printed,"\n",mark+1)
assert(not report:find("review complete",1,true),"Old world timers repopulated snapshots")
issecretvalue=nil
io.write("Addon local review, discard isolation, private snapshots and protected-editor boundary passed.\n")

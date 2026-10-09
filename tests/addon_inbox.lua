-- Exercise actual addon code with a standalone EditBox and forbidden Blizzard editor.
local frames, focus, now, lines, output = {}, nil, 0, {}, {}
SlashCmdList = {}
UIParent = {}; ChatFontNormal = {}
GetTime = function() return now end
GetCurrentKeyBoardFocus = function() return focus end
IsControlKeyDown = function() return true end
IsShiftKeyDown = IsControlKeyDown
IsAltKeyDown = function() return false end
local forbidden = function() error("Touched Blizzard chat/editor/network") end
ChatEdit_GetActiveWindow = forbidden
ChatFrameUtil = setmetatable({}, {__index=forbidden})
SendChatMessage = forbidden; C_ChatInfo = {SendChatMessage=forbidden}
local protectedEditor = setmetatable({}, {__index=forbidden})
ChatFrame1 = {editBox=protectedEditor}
function ChatFrame1:AddMessage(text) assert(focus==nil, "Printed before releasing inbox"); table.insert(lines, text) end
function ChatFrame1:RemoveMessagesByPredicate(predicate)
    for i=#lines,1,-1 do if predicate(lines[i]) then table.remove(lines,i) end end
end
DEFAULT_CHAT_FRAME=ChatFrame1
print=function(text) table.insert(output,text) end
CreateFrame = function(kind, name)
    local frame={kind=kind,name=name,scripts={},text="",shown=true}
    for _,method in ipairs({"SetFrameStrata","EnableKeyboard","SetPropagateKeyboardInput","RegisterEvent","SetAutoFocus","SetMultiLine","SetSize","SetPoint","SetFontObject","SetMaxLetters","EnableMouse","SetAlpha"}) do frame[method]=function() end end
    function frame:SetScript(name,fn) self.scripts[name]=fn end
    function frame:Show() self.shown=true end
    function frame:Hide() self.shown=false end
    function frame:SetFocus() assert(not focus or focus==self);focus=self end
    function frame:ClearFocus()
        if focus==self then focus=nil; if self.scripts.OnEditFocusLost then self.scripts.OnEditFocusLost(self) end end
    end
    function frame:HasFocus() return focus==self end
    function frame:SetCursorPosition(value) self.cursor=value;self.highlight=false end
    function frame:HighlightText(first,last)
        assert(focus==self and first==0 and last==-1, "Reply selected before focus or incompletely")
        self.highlight=true
    end
    function frame:SetSize(w,h) self.width=w;self.height=h end
    function frame:SetAlpha(a) self.alpha=a end
    function frame:GetText() return self.text end
    function frame:SetText(text)
        self.text=text
        if self.scripts.OnTextChanged then self.scripts.OnTextChanged(self,false) end
    end
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
local inbox, keys=frames[4],frames[6]
assert(inbox.name=="ThumbTalkReviewInbox")
assert(inbox.width>=300 and inbox.height>=24 and inbox.alpha==1)
assert(frames[5].name=="ThumbTalkReviewInboxCover")
local control,shift=false,false
IsControlKeyDown=function() return control end
IsShiftKeyDown=function() return shift end
local function frame() inbox.scripts.OnUpdate(inbox) end
local function key(k)
    control,shift=true,true
    if focus==inbox then inbox.scripts.OnKeyDown(inbox,k) else keys.scripts.OnKeyDown(keys,k) end
    control,shift=false,false
end
local function paste(text,held)
    assert(focus==inbox)
    inbox.text=text;inbox.scripts.OnTextChanged(inbox,true)
    assert(inbox.text==text and focus==inbox, "Mutated native paste inside OnTextChanged")
    control=held or false;frame()
end
local function hex(text) return (text:gsub(".",function(c) return string.format("%02x",string.byte(c)) end)) end
local function packet(text,id)
    local a,b=1,0
    for i=1,#text do a=(a+string.byte(text,i))%65521;b=(b+a)%65521 end
    return "TTREVIEW2:"..id..":"..hex(text)..":"..string.format("%08x",b*65536+a)..";"
end
local function start()
    key("F8");assert(focus==inbox and inbox.shown and inbox.text=="")
end
start();paste(packet("/p move æøå |Hfake|h","aabbccdd"),true)
assert(focus==inbox and #lines==0, "Released focus while paste shortcut held")
control=false;frame()
assert(focus==nil and not inbox.shown and #lines==1)
assert(lines[1]:find("ThumbTalk:",1,true) and lines[1]:find("/p move æøå ||Hfake||h",1,true))
assert(lines[1]:find("Talk to accept",1,true) and not lines[1]:find("B/Menu",1,true))
frame();assert(#lines==1,"Repeated review on update")
start();paste(packet("/p move æøå |Hfake|h","aabbccdd"));assert(#lines==1 and focus==nil,"Duplicate packet displayed")
table.insert(lines,"another player's message")
key("F10");assert(#lines==1 and lines[1]=="another player's message")
start();paste(packet("/s send once","aabbccde"))
local before=#output;key("F9");assert(#lines==1 and #output==before,"Extra sent notice")
focus=protectedEditor;key("F8");assert(focus==protectedEditor and not inbox.shown);focus=nil
start();paste("TTREVIEW2:aabbccdf:2f");assert(focus==inbox and #lines==1)
paste(packet("/s complete later","aabbccdf"));assert(focus==nil and #lines==2)
key("F10")
start();paste("TTREVIEW2:aabbccd0:2f");now=4;frame();assert(focus==nil and #lines==1)
assert(output[#output]:find("Review text was not received",1,true))
start();paste((packet("/s bad checksum","aabbccd1"):gsub(":%x%x%x%x%x%x%x%x;",":00000000;")));assert(focus==nil and #lines==1)
start();paste(packet("/s canceled","aabbccd2"),true);key("F6");frame();assert(#lines==1 and focus==nil)
start();paste(packet("/s cancel before modifier release","eeff0011"),true);key("F10");frame();assert(#lines==1 and focus==nil)
start();paste(packet("/s lost focus","aabbccd3"),true);inbox:ClearFocus();control=false;frame();assert(#lines==1)
start();paste(packet("/s old world","aabbccd4"),true);keys.scripts.OnEvent(keys,"PLAYER_LEAVING_WORLD");control=false;frame();assert(focus==nil and #lines==1)
SlashCmdList.THUMBTALK()
assert(table.concat(output,"\n"):find("ThumbTalk addon 1",1,true))
io.write("One-way addon review: local history, focus release, accept/discard, unicode, partial/duplicate/corrupt paste, timeout and world loss passed.\n")

use framework "Foundation"
use scripting additions

property supportPath : ((POSIX path of (path to application support from user domain)) & "PG3D Mac Client/")
property requestPath : supportPath & "helper_request.txt"
property resultPath : supportPath & "helper_result.txt"

on lowerText(t)
    try
        set ns to current application's NSString's stringWithString:(t as text)
        return (ns's lowercaseString()) as text
    on error
        return t as text
    end try
end lowerText

on writeResult(t)
    try
        do shell script "/bin/mkdir -p " & quoted form of supportPath
        set f to open for access POSIX file resultPath with write permission
        set eof f to 0
        write (t as text) to f as «class utf8»
        close access f
    on error
        try
            close access POSIX file resultPath
        end try
    end try
end writeResult

on elementText(e)
    set bits to {}
    tell application "System Events"
        try
            set end of bits to (name of e as text)
        end try
        try
            set v to value of e
            if v is not missing value then set end of bits to (v as text)
        end try
        try
            set end of bits to (description of e as text)
        end try
        try
            set end of bits to (role description of e as text)
        end try
    end tell
    set AppleScript's text item delimiters to " "
    set joined to bits as text
    set AppleScript's text item delimiters to ""
    return my lowerText(joined)
end elementText

on containsAny(haystack, needles)
    set h to my lowerText(haystack)
    repeat with n in needles
        set q to my lowerText(contents of n)
        if q is not "" and h contains q then return true
    end repeat
    return false
end containsAny

on steamProcess()
    tell application "System Events"
        repeat 48 times
            if exists process "Steam" then return process "Steam"
            delay 0.25
        end repeat
    end tell
    return missing value
end steamProcess

on getAllElements(p)
    tell application "System Events"
        try
            return entire contents of p
        on error
            return {}
        end try
    end tell
end getAllElements

on findMatchingElement(p, needles)
    set allItems to my getAllElements(p)
    repeat with itemRef in allItems
        try
            set e to contents of itemRef
            set t to my elementText(e)
            if my containsAny(t, needles) then return e
        end try
    end repeat
    return missing value
end findMatchingElement

on tryPress(e)
    tell application "System Events"
        try
            perform action "AXPress" of e
            return true
        end try
        try
            click e
            return true
        end try
    end tell
    return false
end tryPress

on activateMatchedAccount(p, needles)
    tell application "System Events" to tell p to set frontmost to true
    delay 0.2
    set matchRef to my findMatchingElement(p, needles)
    if matchRef is missing value then return "TARGET_NOT_FOUND"

    -- Steam's account name is often static text inside a larger account tile.
    -- Walk upward and press the first parent that behaves like a control.
    set currentRef to matchRef
    set didPress to false
    repeat 9 times
        if my tryPress(currentRef) then
            set didPress to true
            exit repeat
        end if
        tell application "System Events"
            try
                set currentRef to parent of currentRef
            on error
                exit repeat
            end try
        end tell
    end repeat

    if didPress is false then
        tell application "System Events"
            try
                click matchRef
                set didPress to true
            end try
        end tell
    end if

    -- A single click can only highlight the tile in current Steam builds.
    -- Return activates the currently highlighted/focused tile.
    delay 0.35
    tell application "System Events"
        tell p to set frontmost to true
        key code 36
    end tell
    delay 0.3
    return "ACTIVATION_SENT"
end activateMatchedAccount

on findGuardField(p)
    set allItems to my getAllElements(p)
    set fallbackField to missing value
    repeat with itemRef in allItems
        try
            set e to contents of itemRef
            tell application "System Events" to set r to role of e as text
            if r is "AXTextField" or r is "AXTextArea" or r is "AXSecureTextField" then
                if fallbackField is missing value then set fallbackField to e
                set t to my elementText(e)
                if t contains "code" or t contains "guard" or t contains "verification" or t contains "security" then return e
            end if
        end try
    end repeat
    return fallbackField
end findGuardField

on findSubmitButton(p)
    set needles to {"continue", "verify", "submit", "next", "sign in", "ok"}
    set allItems to my getAllElements(p)
    repeat with itemRef in allItems
        try
            set e to contents of itemRef
            tell application "System Events" to set r to role of e as text
            if r is "AXButton" then
                set t to my elementText(e)
                if my containsAny(t, needles) then return e
            end if
        end try
    end repeat
    return missing value
end findSubmitButton

on submitGuardCode(p, codeText)
    tell application "System Events" to tell p to set frontmost to true
    delay 0.2
    set fieldRef to my findGuardField(p)
    if fieldRef is missing value then return "GUARD_FIELD_NOT_FOUND"

    set wroteValue to false
    tell application "System Events"
        try
            set focused of fieldRef to true
        end try
        try
            set value of fieldRef to codeText
            set wroteValue to true
        end try
        if wroteValue is false then
            try
                click fieldRef
                keystroke "a" using command down
                key code 51
                keystroke codeText
                set wroteValue to true
            end try
        end if
    end tell
    if wroteValue is false then return "GUARD_FIELD_WRITE_FAILED"

    delay 0.15
    set buttonRef to my findSubmitButton(p)
    if buttonRef is not missing value then
        if my tryPress(buttonRef) is false then
            tell application "System Events" to key code 36
        end if
    else
        tell application "System Events" to key code 36
    end if
    return "GUARD_SUBMITTED"
end submitGuardCode

on run
    set resultText to "UNKNOWN"
    try
        set requestText to read POSIX file requestPath as «class utf8»
        try
            do shell script "/bin/rm -f " & quoted form of requestPath
        end try
        set AppleScript's text item delimiters to linefeed
        set requestLines to text items of requestText
        set AppleScript's text item delimiters to ""
        if (count of requestLines) < 1 then error "Empty helper request"
        set commandName to item 1 of requestLines

        if commandName is "CHECK" then
            set p to my steamProcess()
            if p is missing value then
                -- Access a UI process anyway so macOS can establish the helper's Accessibility grant.
                tell application "System Events"
                    if exists process "Finder" then tell process "Finder" to set dummyWindowCount to count of windows
                end tell
            else
                tell application "System Events" to tell p to set dummyWindowCount to count of windows
            end if
            set resultText to "TRUSTED"

        else if commandName is "SELECT" then
            set targetNames to {}
            if (count of requestLines) > 1 then
                repeat with i from 2 to count of requestLines
                    set n to item i of requestLines
                    if n is not "" then set end of targetNames to n
                end repeat
            end if
            if (count of targetNames) is 0 then
                set resultText to "NO_TARGET"
            else
                set p to my steamProcess()
                if p is missing value then
                    set resultText to "NO_STEAM"
                else
                    set resultText to my activateMatchedAccount(p, targetNames)
                end if
            end if

        else if commandName is "GUARD" then
            if (count of requestLines) < 2 then
                set resultText to "NO_GUARD_CODE"
            else
                set codeText to item 2 of requestLines
                set p to my steamProcess()
                if p is missing value then
                    set resultText to "NO_STEAM"
                else
                    set resultText to my submitGuardCode(p, codeText)
                end if
            end if
        else
            set resultText to "UNKNOWN_COMMAND"
        end if

    on error errMsg number errNum
        set lowErr to my lowerText(errMsg)
        if errNum is -1743 or lowErr contains "not authorized to send apple events" then
            set resultText to "AUTOMATION_REQUIRED"
        else if errNum is -1719 or lowErr contains "assistive access" or lowErr contains "accessibility" then
            set resultText to "ACCESSIBILITY_REQUIRED"
        else
            set resultText to "ERROR:" & errNum & ":" & errMsg
        end if
    end try
    my writeResult(resultText)
end run

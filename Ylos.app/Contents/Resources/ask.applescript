-- ask.applescript — the native dialog behind Ylos.app (called by Contents/MacOS/Ylos).
--
--   osascript ask.applescript MESSAGE "Btn1,Btn2,.." DEFAULT [stop|note]
--
-- Prints the clicked button, CANCEL (Esc / window closed) or ERROR (anything else went wrong).
-- The text arrives as argv, never spliced into a script, so quotes and newlines in a message
-- cannot break it.
on run argv
	set msg to item 1 of argv
	set AppleScript's text item delimiters to ","
	set btns to text items of (item 2 of argv)
	set dflt to item 3 of argv
	set icon_kind to "note"
	if (count of argv) > 3 then set icon_kind to item 4 of argv
	try
		-- this script runs faceless (launched by a bundle with no window): come to the front
		tell current application to activate
	end try
	try
		if icon_kind is "stop" then
			set r to display dialog msg with title "Ylos" buttons btns default button dflt with icon stop
		else
			set r to display dialog msg with title "Ylos" buttons btns default button dflt with icon note
		end if
		return button returned of r
	on error errMsg number errNum
		if errNum is -128 then return "CANCEL"
		return "ERROR"
	end try
end run

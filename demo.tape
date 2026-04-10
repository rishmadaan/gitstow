# gitstow demo — record with: vhs demo.tape
# Install vhs: brew install charmbracelet/tap/vhs

Output demo.gif

Set FontSize 14
Set Width 900
Set Height 500
Set Theme "Dracula"

Type "# Add repos with gitstow"
Enter
Sleep 500ms

Type "gitstow add anthropic/claude-code facebook/react"
Enter
Sleep 3s

Type "# See your collection"
Enter
Sleep 500ms

Type "gitstow list"
Enter
Sleep 2s

Type "# Git status across all repos"
Enter
Sleep 500ms

Type "gitstow status"
Enter
Sleep 2s

Type "# Update everything"
Enter
Sleep 500ms

Type "gitstow pull"
Enter
Sleep 3s

Sleep 1s

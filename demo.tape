# gitstow demo — record with: vhs demo.tape
# Install vhs: brew install charmbracelet/tap/vhs
#
# NOTE: This clones real repos. Run in a temp workspace or clean up after.
# Before recording, ensure gitstow is configured with a test workspace.

Output demo.gif

Set FontSize 14
Set Width 900
Set Height 500
Set Theme "Dracula"

Type "# Add repos with gitstow"
Enter
Sleep 500ms

Type "gitstow -w demo add charmbracelet/vhs charmbracelet/gum --shallow"
Enter
Sleep 12s

Type ""
Enter
Type "# See your collection"
Enter
Sleep 500ms

Type "gitstow -w demo list"
Enter
Sleep 2s

Type ""
Enter
Type "# Git status across all repos"
Enter
Sleep 500ms

Type "gitstow -w demo status"
Enter
Sleep 2s

Type ""
Enter
Type "# Update everything"
Enter
Sleep 500ms

Type "gitstow -w demo pull"
Enter
Sleep 4s

Sleep 1s

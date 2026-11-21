Option Explicit
Dim shell, fso, root, python
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
python = fso.BuildPath(root, ".venv\Scripts\pythonw.exe")
If Not fso.FileExists(python) Then
    MsgBox "The Il-Kelma project venv is missing.", vbExclamation, "Il-Kelma"
    WScript.Quit 1
End If
shell.CurrentDirectory = root
shell.Run Chr(34) & python & Chr(34) & " -m ilkelma.app", 0, False

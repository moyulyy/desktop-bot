Option Explicit
' Silent launcher: starts the assistant with pythonw.exe so no console window appears.
Dim fso, sh, base
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base
sh.Environment("PROCESS")("PYTHONPATH") = base & "\src"

Function ExitCode(command)
    Dim e
    On Error Resume Next
    Set e = sh.Exec(command)
    If Err.Number <> 0 Then
        Err.Clear
        ExitCode = -1
        Exit Function
    End If
    Do While e.Status = 0
        WScript.Sleep 80
    Loop
    ExitCode = e.ExitCode
    On Error GoTo 0
End Function

Dim launch, checkMode
checkMode = (WScript.Arguments.Count > 0)
If ExitCode("cmd /c py -3 -c ""import PySide6, requests""") = 0 Then
    launch = "pyw -3 -m desktop_assistant"
ElseIf ExitCode("cmd /c python -c ""import PySide6, requests""") = 0 Then
    launch = "pythonw -m desktop_assistant"
Else
    If checkMode Then
        WScript.Echo "dependencies missing"
        WScript.Quit 1
    End If
    MsgBox "Dependencies are not installed. Please run run.bat once.", vbExclamation, "AI Desktop Assistant"
    WScript.Quit 1
End If

If checkMode Then
    WScript.Echo "dependencies ok"
    WScript.Quit 0
End If

On Error Resume Next
sh.Run launch, 0, False
If Err.Number <> 0 Then
    Err.Clear
    sh.Run "pythonw -m desktop_assistant", 0, False
End If
If Err.Number <> 0 Then
    MsgBox "pythonw.exe was not found. Please run run.bat once.", vbExclamation, "AI Desktop Assistant"
End If

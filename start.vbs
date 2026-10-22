Option Explicit
' Silent launcher. Prefer the prepared conda environment (desktop-rag),
' then fall back to the system Python if that environment is unavailable.
' If a local Umi-OCR distribution is present, start its HTTP service too.
Dim fso, sh, base, q, envPython, candidate, launch, checkMode, checkCmd
Dim umiExe, umiCandidate, umiCandidates, umiUrl
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base
sh.Environment("PROCESS")("PYTHONPATH") = base & "\src"
q = Chr(34)

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

Function ProcessRunning(processName)
    Dim e, text
    ProcessRunning = False
    On Error Resume Next
    Set e = sh.Exec("cmd /c tasklist /FI " & q & "IMAGENAME eq " & processName & q & " /NH")
    If Err.Number <> 0 Then
        Err.Clear
        Exit Function
    End If
    Do While e.Status = 0
        WScript.Sleep 50
    Loop
    text = e.StdOut.ReadAll
    If InStr(1, text, processName, vbTextCompare) > 0 Then
        ProcessRunning = True
    End If
    On Error GoTo 0
End Function

' The environment created during setup. Keep this list portable across common
' Miniconda/Anaconda install locations.
envPython = ""
Dim candidates
candidates = Array( _
    "C:\ProgramData\miniconda3\envs\desktop-rag\pythonw.exe", _
    sh.ExpandEnvironmentStrings("%USERPROFILE%") & "\miniconda3\envs\desktop-rag\pythonw.exe", _
    sh.ExpandEnvironmentStrings("%USERPROFILE%") & "\anaconda3\envs\desktop-rag\pythonw.exe", _
    sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\miniconda3\envs\desktop-rag\pythonw.exe", _
    sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Continuum\anaconda3\envs\desktop-rag\pythonw.exe" _
)
For Each candidate In candidates
    If envPython = "" And fso.FileExists(candidate) Then
        envPython = candidate
    End If
Next

checkMode = (WScript.Arguments.Count > 0)

If envPython <> "" Then
    checkCmd = "cmd /c " & q & envPython & q & " -c " & q & "import PySide6, requests" & q
    If ExitCode(checkCmd) = 0 Then
        launch = q & envPython & q & " -m desktop_assistant"
    End If
End If

If launch = "" Then
    If ExitCode("cmd /c py -3 -c " & q & "import PySide6, requests" & q) = 0 Then
        launch = "pyw -3 -m desktop_assistant"
    ElseIf ExitCode("cmd /c python -c " & q & "import PySide6, requests" & q) = 0 Then
        launch = "pythonw -m desktop_assistant"
    End If
End If

If launch = "" Then
    If checkMode Then
        WScript.Echo "dependencies missing (desktop-rag conda env not found)"
        WScript.Quit 1
    End If
    MsgBox "desktop-rag conda environment or Python dependencies are missing." & vbCrLf & _
           "Please run run.bat once, or activate/create desktop-rag.", _
           vbExclamation, "AI Desktop Assistant"
    WScript.Quit 1
End If

' Locate the bundled/local Umi-OCR distribution. The service itself is optional:
' if it is absent, the assistant still starts and uses its OCR fallback.
umiExe = ""
umiCandidates = Array( _
    base & "\Umi-OCR_Paddle_v2.0.0\Umi-OCR.exe", _
    base & "\Umi-OCR\Umi-OCR.exe", _
    sh.ExpandEnvironmentStrings("%USERPROFILE%") & "\Desktop\Umi-OCR_Paddle_v2.0.0\Umi-OCR.exe", _
    sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Umi-OCR\Umi-OCR.exe", _
    "C:\Program Files\Umi-OCR\Umi-OCR.exe", _
    "C:\Program Files (x86)\Umi-OCR\Umi-OCR.exe" _
)
For Each umiCandidate In umiCandidates
    If umiExe = "" And fso.FileExists(umiCandidate) Then
        umiExe = umiCandidate
    End If
Next

If not checkMode And umiExe <> "" Then
    umiUrl = sh.Environment("PROCESS")("UMI_OCR_URL")
    If umiUrl = "" Then
        umiUrl = "http://127.0.0.1:1224"
        sh.Environment("PROCESS")("UMI_OCR_URL") = umiUrl
    End If
    If Not ProcessRunning("Umi-OCR.exe") Then
        On Error Resume Next
        sh.Run q & umiExe & q & " --hide", 0, False
        If Err.Number <> 0 Then
            Err.Clear
            sh.Run q & umiExe & q, 0, False
        End If
        On Error GoTo 0
        ' Give the local HTTP server a moment to bind before the assistant loads.
        WScript.Sleep 1200
    End If
End If

If checkMode Then
    WScript.Echo "dependencies ok"
    WScript.Quit 0
End If

On Error Resume Next
sh.Run launch, 0, False
If Err.Number <> 0 Then
    Err.Clear
    If envPython <> "" Then
        sh.Run q & envPython & q & " -m desktop_assistant", 0, False
    End If
End If
If Err.Number <> 0 Then
    MsgBox "pythonw.exe was not found. Please run run.bat once.", vbExclamation, "AI Desktop Assistant"
End If

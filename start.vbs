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

Function CaptureOutput(command)
    Dim e, text
    CaptureOutput = ""
    On Error Resume Next
    Set e = sh.Exec(command)
    If Err.Number <> 0 Then
        Err.Clear
        Exit Function
    End If
    Do While e.Status = 0
        WScript.Sleep 80
    Loop
    text = e.StdOut.ReadAll
    On Error GoTo 0
    CaptureOutput = Trim(text)
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

' The environment created during setup. Probe conda dynamically first
' (CONDA_EXE / `conda info --base`), then fall back to the usual install
' locations so the launcher keeps working after moving the project.
envPython = ""
Dim candidates, condaBase
candidates = Array( _
    sh.ExpandEnvironmentStrings("%USERPROFILE%") & "\.conda\envs\desktop-rag\pythonw.exe", _
    sh.ExpandEnvironmentStrings("%USERPROFILE%") & "\miniconda3\envs\desktop-rag\pythonw.exe", _
    sh.ExpandEnvironmentStrings("%USERPROFILE%") & "\anaconda3\envs\desktop-rag\pythonw.exe", _
    sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\miniconda3\envs\desktop-rag\pythonw.exe", _
    sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Continuum\anaconda3\envs\desktop-rag\pythonw.exe", _
    "C:\ProgramData\miniconda3\envs\desktop-rag\pythonw.exe", _
    "C:\ProgramData\anaconda3\envs\desktop-rag\pythonw.exe", _
    "D:\miniconda3\envs\desktop-rag\pythonw.exe", _
    "D:\anaconda3\envs\desktop-rag\pythonw.exe" _
)
For Each candidate In candidates
    If envPython = "" And fso.FileExists(candidate) Then
        envPython = candidate
    End If
Next

' Last resort: ask conda itself where its base lives, then look for the env.
If envPython = "" Then
    condaBase = CaptureOutput("cmd /c conda info --base 2>nul")
    If condaBase <> "" Then
        candidate = condaBase & "\envs\desktop-rag\pythonw.exe"
        If fso.FileExists(candidate) Then
            envPython = candidate
        End If
    End If
End If

' Keep the app self-contained: ignore packages installed in the user's
' global Python site-packages, which would otherwise shadow the env.
sh.Environment("PROCESS")("PYTHONNOUSERSITE") = "1"

checkMode = (WScript.Arguments.Count > 0)

If envPython <> "" Then
    ' Call the interpreter directly; nesting quotes inside `cmd /c "exe" ...`
    ' makes cmd.exe truncate the path on some systems.
    checkCmd = q & envPython & q & " -c " & q & "import PySide6, requests" & q
    If ExitCode(checkCmd) = 0 Then
        launch = q & envPython & q & " -m desktop_assistant"
    End If
End If

If launch = "" Then
    If ExitCode("py -3 -c " & q & "import PySide6, requests" & q) = 0 Then
        launch = "pyw -3 -m desktop_assistant"
    ElseIf ExitCode("python -c " & q & "import PySide6, requests" & q) = 0 Then
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

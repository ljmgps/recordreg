' RecordReg 无窗口启动器
' 直接用这个文件启动，不会出现任何命令行黑窗口。
' （启动.bat 保留作为排错入口，会闪一下 cmd 窗口）

Option Explicit

Dim fso, sh, base, pyw, app, model
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

base = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = base & "\.venv\Scripts\pythonw.exe"
app = base & "\app\main.py"
model = base & "\models\SenseVoiceSmall\model.pt"

If Not fso.FileExists(pyw) Then
    MsgBox "虚拟环境不存在。" & vbCrLf & vbCrLf & "请先双击运行「安装环境.bat」。", 16, "RecordReg"
    WScript.Quit 1
End If

If Not fso.FileExists(model) Then
    MsgBox "模型还没下载。" & vbCrLf & vbCrLf & "请先双击运行「下载模型.bat」。", 16, "RecordReg"
    WScript.Quit 1
End If

sh.CurrentDirectory = base
' 第二个参数 0 = 隐藏窗口，第三个参数 False = 不等待
sh.Run """" & pyw & """ """ & app & """", 0, False

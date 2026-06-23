param(
    [Parameter(Mandatory=$true)]
    [int]$TargetPid,

    [string]$Text = "continue`r"
)

$ErrorActionPreference = "Stop"

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class ConsoleInputWriter {
    const short KEY_EVENT = 0x0001;
    const int STD_INPUT_HANDLE = -10;

    [StructLayout(LayoutKind.Sequential)]
    public struct KEY_EVENT_RECORD {
        [MarshalAs(UnmanagedType.Bool)]
        public bool bKeyDown;
        public ushort wRepeatCount;
        public ushort wVirtualKeyCode;
        public ushort wVirtualScanCode;
        public char UnicodeChar;
        public uint dwControlKeyState;
    }

    [StructLayout(LayoutKind.Explicit)]
    public struct INPUT_RECORD {
        [FieldOffset(0)]
        public short EventType;
        [FieldOffset(4)]
        public KEY_EVENT_RECORD KeyEvent;
    }

    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool AttachConsole(uint dwProcessId);

    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool FreeConsole();

    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern IntPtr GetStdHandle(int nStdHandle);

    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool WriteConsoleInputW(IntPtr hConsoleInput, INPUT_RECORD[] lpBuffer, uint nLength, out uint lpNumberOfEventsWritten);

    public static void WriteText(int pid, string text) {
        FreeConsole();
        if (!AttachConsole((uint)pid)) {
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "AttachConsole failed");
        }

        try {
            IntPtr input = GetStdHandle(STD_INPUT_HANDLE);
            if (input == IntPtr.Zero || input == new IntPtr(-1)) {
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "GetStdHandle(STD_INPUT_HANDLE) failed");
            }

            INPUT_RECORD[] records = new INPUT_RECORD[text.Length];
            for (int i = 0; i < text.Length; i++) {
                char ch = text[i];
                records[i].EventType = KEY_EVENT;
                records[i].KeyEvent.bKeyDown = true;
                records[i].KeyEvent.wRepeatCount = 1;
                records[i].KeyEvent.wVirtualKeyCode = ch == '\r' ? (ushort)13 : (ushort)0;
                records[i].KeyEvent.wVirtualScanCode = 0;
                records[i].KeyEvent.UnicodeChar = ch;
                records[i].KeyEvent.dwControlKeyState = 0;
            }

            uint written;
            if (!WriteConsoleInputW(input, records, (uint)records.Length, out written)) {
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "WriteConsoleInput failed");
            }
            if (written != records.Length) {
                throw new Exception("WriteConsoleInput wrote " + written + " of " + records.Length + " input records");
            }
        } finally {
            FreeConsole();
        }
    }
}
"@

[ConsoleInputWriter]::WriteText($TargetPid, $Text)

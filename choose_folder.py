"""Native folder picker used by the local Windows application."""
import json
import sys
import tkinter as tk
from tkinter import filedialog

if __name__ == '__main__':
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    folder = filedialog.askdirectory(title='选择保存已保留照片的文件夹', parent=root)
    root.destroy()
    sys.stdout.buffer.write(json.dumps({'path': folder}, ensure_ascii=False).encode('utf-8'))

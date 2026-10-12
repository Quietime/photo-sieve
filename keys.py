"""Keyboard shortcuts: the review actions, their default keys and the user's own choices, kept in QSettings.
Like Lightroom or Photo Mechanic, every action takes a main key and an optional second one (↑ or P to keep, say)."""
import json

from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit, QPushButton, QVBoxLayout

# id, label, default keys, repeats while held (↑ ↓ → ← follow the keyboard repeat rate, about 30 per second on Windows)
ACTIONS = [
    ('keep', '保留', ('Up',), True),
    ('delete', '删除', ('Down',), True),
    ('next', '下一张', ('Right',), True),
    ('restore', '恢复最近删除', ('Left',), True),
    ('undo', '撤回上一步', ('Ctrl+Z',), False),
    ('zoom', '放大到 100%', ('Z',), False),
    ('unzoom', '还原缩放', ('Esc',), False),
]
SLOTS = 2
ARROWS = {'Up': '↑', 'Down': '↓', 'Left': '←', 'Right': '→'}


def normal(text):
    """One key (with modifiers) in Qt's portable spelling; '' for nothing or for a multi-key sequence."""
    sequence = QKeySequence(text or '')
    return sequence.toString(QKeySequence.SequenceFormat.PortableText) if sequence.count() == 1 else ''


def defaults():
    return {action: [normal(key) for key in keys] + [''] * (SLOTS - len(keys)) for action, _, keys, _ in ACTIONS}


def load(settings):
    chosen = defaults()
    for action in chosen:
        try:
            stored = json.loads(settings.value('shortcuts/' + action, '') or 'null')
        except (TypeError, ValueError):
            stored = None
        if isinstance(stored, list):
            keys = [normal(key) for key in stored if isinstance(key, str)][:SLOTS]
            chosen[action] = keys + [''] * (SLOTS - len(keys))
    return chosen


def save(settings, chosen):
    for action, keys in chosen.items():
        settings.setValue('shortcuts/' + action, json.dumps([key for key in keys if key]))
    settings.sync()


def conflicts(chosen):
    """Keys given to more than one action (or twice to the same one)."""
    seen, clashes = set(), set()
    for keys in chosen.values():
        for key in keys:
            if key:
                (clashes if key in seen else seen).add(key)
    return clashes


def label(key):
    """How a key reads on buttons and in hints: arrows as ↑ ↓ ← →, the rest as Windows writes it."""
    if not key:
        return ''
    return ARROWS.get(key) or QKeySequence(key).toString(QKeySequence.SequenceFormat.NativeText)


def first(chosen, action):
    return next((label(key) for key in chosen.get(action, ()) if key), '')


class ShortcutDialog(QDialog):
    """Settings sheet: press the key you want in a field; its clear button empties it. Duplicates are shown and cannot be saved."""
    saved = Signal(dict)

    def __init__(self, parent, chosen):
        super().__init__(parent); self.setWindowTitle('快捷键'); self.setModal(True); self.setMinimumWidth(520)
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 22, 24, 20); layout.setSpacing(12)
        title = QLabel('快捷键'); title.setObjectName('heading'); layout.addWidget(title)
        hint = QLabel('点一下输入框，再按下想用的键。每个操作可以设两个键，例如保留用 ↑ 或 P、删除用 ↓ 或 X。')
        hint.setObjectName('caption'); hint.setWordWrap(True); layout.addWidget(hint)
        grid = QGridLayout(); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(8)
        for column, text in ((1, '按键'), (2, '备用按键')):
            heading = QLabel(text); heading.setObjectName('caption'); grid.addWidget(heading, 0, column)
        self.fields = {}
        for row, (action, name, _, _) in enumerate(ACTIONS, 1):
            grid.addWidget(QLabel(name), row, 0)
            for slot in range(SLOTS):
                field = QKeySequenceEdit(QKeySequence(chosen[action][slot])); field.setMaximumSequenceLength(1)
                field.setClearButtonEnabled(True); field.setMinimumWidth(170); field.keySequenceChanged.connect(self.check)
                field.findChild(QLineEdit).setPlaceholderText('未设置')   # Qt's own hint is in English
                grid.addWidget(field, row, 1 + slot); self.fields[action, slot] = field
        layout.addLayout(grid)
        self.problem = QLabel(); self.problem.setStyleSheet('color:#FF453A; font-size:12px;'); self.problem.setWordWrap(True); layout.addWidget(self.problem)
        buttons = QHBoxLayout()
        reset = QPushButton('恢复默认'); reset.setObjectName('plain'); reset.clicked.connect(self.reset); buttons.addWidget(reset)
        buttons.addStretch()
        cancel = QPushButton('取消'); cancel.clicked.connect(self.reject); buttons.addWidget(cancel)
        self.save_button = QPushButton('保存'); self.save_button.setObjectName('primary'); self.save_button.clicked.connect(self.accept_keys)
        buttons.addWidget(self.save_button); layout.addLayout(buttons)
        self.check()

    def chosen(self):
        result = {}
        for action, _, _, _ in ACTIONS:
            keys = [normal(self.fields[action, slot].keySequence().toString(QKeySequence.SequenceFormat.PortableText)) for slot in range(SLOTS)]
            result[action] = [key for key in keys if key] + [''] * keys.count('')
        return result

    def check(self, *_):
        chosen = self.chosen(); clashes = conflicts(chosen)
        names = {action: name for action, name, _, _ in ACTIONS}
        missing = [names[action] for action, _, _, _ in ACTIONS[:4] if not any(chosen[action])]
        for (action, slot), field in self.fields.items():
            edge = '#FF453A' if chosen[action][slot] in clashes else 'transparent'
            field.setStyleSheet(f'QLineEdit {{ background:#2C2C2E; border:1px solid {edge}; border-radius:8px; padding:5px 8px; }}'
                                'QLineEdit:focus { border-color:#0A84FF; }' if edge == 'transparent' else
                                f'QLineEdit {{ background:#2C2C2E; border:1px solid {edge}; border-radius:8px; padding:5px 8px; }}')
        if clashes:
            self.problem.setText('这些键被设给了多个操作：' + '、'.join(label(key) for key in sorted(clashes)))
        elif missing:
            self.problem.setText('请至少给这些操作设一个键：' + '、'.join(missing))
        else:
            self.problem.setText('')
        self.problem.setVisible(bool(clashes or missing)); self.save_button.setEnabled(not clashes and not missing)

    def reset(self):
        for (action, slot), field in self.fields.items():
            field.setKeySequence(QKeySequence(defaults()[action][slot]))

    def accept_keys(self):
        self.check()
        if self.save_button.isEnabled():
            self.saved.emit(self.chosen()); self.accept()
